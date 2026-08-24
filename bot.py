import os
import re
import time
import json
import asyncio
import logging
import urllib.request
import urllib.parse
from datetime import datetime
from aiohttp import web
import yt_dlp
from hydrogram import Client, filters
from hydrogram.types import (
    Message, InputMediaVideo, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ForceReply
)
from hydrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, PasswordHashInvalid
)
import firebase_admin
from firebase_admin import credentials, firestore

# Google Drive Service Account Imports
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Auto Setup FFmpeg
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8952918726:AAGnKZm-S8hmBaWzltPfrdWRcyVHGVx44d0").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()

SERVICE_ACCOUNT_RAW = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "1aCU7K2Kd-pIdibVY-TSYX2qHaoG65bHR").strip()

OWNER_ID = 6142774415
PORT = int(os.environ.get("PORT", "8080"))

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Database Initialization
db = None
if FIREBASE_KEY_RAW:
    try:
        cred_dict = json.loads(FIREBASE_KEY_RAW)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase: Connected successfully")
    except Exception as e:
        logger.error(f"Firebase Init Error: {e}")

# Bot Client
bot = Client(
    "bot_instance",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# Global States
admin_states = {}
login_clients = {}
progress_status = {}

# ----------------- GOOGLE DRIVE ENGINE ----------------- #

def get_drive_service():
    if not SERVICE_ACCOUNT_RAW:
        return None, "GDRIVE_SERVICE_ACCOUNT_JSON variable missing"
    try:
        service_account_info = json.loads(SERVICE_ACCOUNT_RAW)
        creds = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        return service, None
    except Exception as e:
        logger.error(f"Drive Service Account Auth Error: {e}")
        return None, str(e)

def upload_file_to_drive(file_path: str, file_name: str):
    if not os.path.exists(file_path):
        return None, "File does not exist"

    service, auth_err = get_drive_service()
    if not service:
        return None, f"Drive Init Error: {auth_err}"

    try:
        file_metadata = {'name': file_name}
        if GDRIVE_FOLDER_ID and GDRIVE_FOLDER_ID.strip():
            file_metadata['parents'] = [GDRIVE_FOLDER_ID.strip()]

        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True,
            fields='id, webViewLink'
        ).execute()

        file_id = file.get('id')
        if not file_id:
            return None, "No file ID returned."

        try:
            service.permissions().create(
                fileId=file_id,
                body={'type': 'anyone', 'role': 'reader'},
                supportsAllDrives=True
            ).execute()
        except Exception:
            pass

        web_link = file.get('webViewLink') or f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        return web_link, None
    except Exception as e:
        logger.error(f"G-Drive Upload Error: {e}")
        return None, f"{type(e).__name__}: {str(e)}"

# ----------------- DATABASE HELPERS ----------------- #

def sync_add_session(session_str: str, name: str):
    if not db:
        return False, "Database offline"
    try:
        docs = db.collection("telegram_sessions").where("session_string", "==", session_str).stream()
        if any(docs):
            return False, "Session already exists"
        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "created_at": time.time()
        })
        return True, "Success"
    except Exception as e:
        return False, str(e)

def sync_get_all_sessions():
    if not db:
        return []
    try:
        docs = db.collection("telegram_sessions").stream()
        sessions = []
        for doc in docs:
            data = doc.to_dict()
            sessions.append({
                "doc_id": doc.id,
                "session_string": data.get("session_string"),
                "account_name": data.get("account_name", "Unknown")
            })
        return sessions
    except Exception as e:
        logger.error(f"Get Sessions Error: {e}")
        return []

def sync_delete_session(doc_id: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").document(doc_id).delete()
        return True
    except Exception as e:
        logger.error(f"Delete Session Error: {e}")
        return False

def sync_track_user(user_id: int, username: str, name: str) -> bool:
    if not db:
        return False
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        doc = doc_ref.get()
        is_new_user = not doc.exists

        data = {
            "user_id": int(user_id),
            "username": username or "N/A",
            "name": name or "N/A",
            "last_active": time.time()
        }
        if is_new_user:
            data["joined_at"] = time.time()

        doc_ref.set(data, merge=True)
        return is_new_user
    except Exception as e:
        logger.error(f"Tracking Error: {e}")
        return False

def sync_log_url(user_id: int, user_name: str, url: str, platform: str):
    if not db:
        return
    try:
        db.collection("submitted_urls").add({
            "user_id": user_id,
            "user_name": user_name or "Unknown",
            "url": url,
            "platform": platform,
            "timestamp": time.time()
        })
    except Exception as e:
        logger.error(f"URL Log Error: {e}")

def sync_get_active_urls(limit: int = 25):
    if not db:
        return []
    try:
        one_day_ago = time.time() - 86400
        docs = db.collection("submitted_urls").where("timestamp", ">=", one_day_ago).order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
        results = []
        for doc in docs:
            results.append(doc.to_dict())
        return results
    except Exception as e:
        logger.error(f"Get URLs Error: {e}")
        return []

def sync_increment_downloads(platform: str, count: int = 1, photos: int = 0, videos: int = 0):
    if not db or count <= 0:
        return
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        update_payload = {
            "total_downloads": firestore.Increment(count),
            f"count_{platform.lower()}": firestore.Increment(count)
        }
        if platform.lower() == "telegram":
            if photos > 0:
                update_payload["tg_photos"] = firestore.Increment(photos)
            if videos > 0:
                update_payload["tg_videos"] = firestore.Increment(videos)

        doc_ref = db.collection("bot_stats").document("global_analytics")
        doc_ref.set(update_payload, merge=True)

        daily_ref = db.collection("bot_stats").document("daily_analytics").collection("days").document(today_str)
        daily_ref.set({
            "date": today_str,
            "downloads": firestore.Increment(count)
        }, merge=True)
    except Exception as e:
        logger.error(f"Metric Error: {e}")

def sync_increment_user_downloads(user_id: int, platform: str, count: int = 1, photos: int = 0, videos: int = 0):
    if not db or count <= 0:
        return
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        update_data = {
            "total_downloads": firestore.Increment(count),
            "last_active": time.time()
        }
        if platform.lower() == "telegram":
            update_data["telegram_downloads"] = firestore.Increment(count)
            if photos > 0:
                update_data["tg_photos"] = firestore.Increment(photos)
            if videos > 0:
                update_data["tg_videos"] = firestore.Increment(videos)
        else:
            update_data["social_downloads"] = firestore.Increment(count)
            update_data[f"count_{platform.lower()}"] = firestore.Increment(count)

        doc_ref.set(update_data, merge=True)
    except Exception as e:
        logger.error(f"User Metric Error: {e}")

def sync_get_stats():
    if not db:
        return 0, 0, {}, 0, 0.0, 0, 0
    try:
        users = sum(1 for _ in db.collection("bot_users").stream())
        stat_doc = db.collection("bot_stats").document("global_analytics").get()

        total_dl = 0
        tg_photos, tg_videos = 0, 0
        platform_counts = {
            "Telegram": 0, "YouTube": 0, "TikTok": 0,
            "Instagram": 0, "Facebook": 0, "Others": 0
        }

        if stat_doc.exists:
            data = stat_doc.to_dict()
            total_dl = data.get("total_downloads", 0)
            tg_photos = data.get("tg_photos", 0)
            tg_videos = data.get("tg_videos", 0)
            platform_counts = {
                "Telegram": data.get("count_telegram", 0),
                "YouTube": data.get("count_youtube", 0),
                "TikTok": data.get("count_tiktok", 0),
                "Instagram": data.get("count_instagram", 0),
                "Facebook": data.get("count_facebook", 0),
                "Others": data.get("count_others", 0)
            }

        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_docs = list(db.collection("bot_stats").document("daily_analytics").collection("days").stream())

        today_downloads = 0
        total_days = len(daily_docs)
        sum_daily_dl = 0

        for doc in daily_docs:
            d_data = doc.to_dict()
            d_count = d_data.get("downloads", 0)
            sum_daily_dl += d_count
            if doc.id == today_str:
                today_downloads = d_count

        daily_avg = (sum_daily_dl / max(total_days, 1)) if total_days > 0 else float(total_dl)

        return users, total_dl, platform_counts, today_downloads, daily_avg, tg_photos, tg_videos
    except Exception as e:
        logger.error(f"Stats Error: {e}")
        return 0, 0, {}, 0, 0.0, 0, 0

async def generate_admin_dashboard():
    users, downloads, platforms, today_dl, daily_avg, tg_photos, tg_videos = await asyncio.to_thread(sync_get_stats)
    all_sess = await asyncio.to_thread(sync_get_all_sessions)
    db_status = "Online 🟢" if db else "Offline 🔴"
    drive_status = "Online (Service Account) 🟢" if SERVICE_ACCOUNT_RAW else "Offline 🔴"

    text = (
        "👑 **Admin Management Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Database:** `{db_status}`\n"
        f"• **Google Drive:** `{drive_status}`\n"
        f"• **Total Users:** `{users:,}`\n"
        f"• **Total Media Downloaded:** `{downloads:,}`\n"
        f"• **Today's Downloads:** `{today_dl:,}`\n"
        f"• **Daily Average:** `{daily_avg:.1f} / day`\n"
        f"• **Active Sessions:** `{len(all_sess)}`\n\n"
        "📊 **Platform Breakdown:**\n"
        f"• 📂 Telegram: `{platforms.get('Telegram', 0):,}`\n"
        f"   └ 🖼️ Photos: `{tg_photos:,}` | 🎥 Videos: `{tg_videos:,}`\n"
        f"• 🔴 YouTube: `{platforms.get('YouTube', 0):,}`\n"
        f"• 🎵 TikTok: `{platforms.get('TikTok', 0):,}`\n"
        f"• 📸 Instagram: `{platforms.get('Instagram', 0):,}`\n"
        f"• 🔵 Facebook: `{platforms.get('Facebook', 0):,}`\n"
        f"• 🌐 Others: `{platforms.get('Others', 0):,}`\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Generate Session (OTP)", callback_data="btn_login_account"),
            InlineKeyboardButton("➕ Paste Session", callback_data="btn_add_session")
        ],
        [
            InlineKeyboardButton(f"📁 Sessions ({len(all_sess)})", callback_data="btn_list_sessions"),
            InlineKeyboardButton("🗑️ Delete Session", callback_data="btn_del_menu")
        ],
        [
            InlineKeyboardButton("🔗 Recent URLs (24h)", callback_data="btn_view_urls"),
            InlineKeyboardButton("🔄 Refresh Stats", callback_data="btn_refresh_admin")
        ]
    ])
    return text, keyboard

# ----------------- UTILITY & HELPERS ----------------- #

def format_time(seconds: int) -> str:
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"

async def progress_bar(current, total, status_msg, action_name, user_id):
    if not total or total <= 0:
        return

    now = time.time()
    user_data = progress_status.get(user_id, {})
    last_update = user_data.get("last_time", 0)
    start_time = user_data.get("start_time", now)

    if (now - last_update < 2.5) and current < total:
        return

    progress_status[user_id] = {"last_time": now, "start_time": start_time}
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "■" * filled + "□" * (10 - filled)

    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)
    elapsed = max(now - start_time, 0.1)
    speed = (current / elapsed) / (1024 * 1024)
    eta = (total - current) / (current / elapsed) if current > 0 else 0

    text = (
        f"**{action_name}**\n"
        f"`[{bar}]` {percentage:.1f}%\n"
        f"• `{curr_mb:.1f}/{tot_mb:.1f} MB` | `{speed:.1f} MB/s` | `{format_time(eta)}`"
    )
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass

async def auto_delete_messages(chat_id: int, message_ids: list, delay_seconds: int = 300):
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception:
        pass

def is_gif_message(msg):
    if not msg:
        return False
    return bool(msg.animation or (msg.document and msg.document.mime_type and "gif" in msg.document.mime_type.lower()))

def has_media(msg):
    return bool(msg and (msg.video or msg.photo or msg.document or msg.audio or msg.voice or msg.animation or msg.media_group_id))

def detect_social_platform(url: str) -> str:
    url_lower = url.lower()
    if "tiktok.com" in url_lower:
        return "tiktok"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "instagram.com" in url_lower or "instagr.am" in url_lower:
        return "instagram"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower:
        return "facebook"
    return "others"

def extract_and_download_social(url: str, user_id: int):
    clean_url = url.split("?")[0].strip()
    timestamp = int(time.time())
    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_{timestamp}_%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'max_filesize': 1900 * 1024 * 1024,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            if info:
                file_path = ydl.prepare_filename(info)
                if not os.path.exists(file_path):
                    base, _ = os.path.splitext(file_path)
                    for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                        if os.path.exists(base + ext):
                            file_path = base + ext
                            break
                if os.path.exists(file_path):
                    return file_path, str(info.get('title') or "Video"), int(info.get('duration') or 0), int(info.get('width') or 0), int(info.get('height') or 0)
    except Exception as e:
        logger.error(f"yt-dlp failure: {e}")

    return None, None, 0, 0, 0

# ----------------- MESSAGE HANDLER ----------------- #

@bot.on_message(filters.private)
async def private_message_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    text_str = (message.text or message.caption or "").strip()

    logger.info(f"Update from {user_id} ({user.first_name}): {text_str}")

    asyncio.create_task(asyncio.to_thread(sync_track_user, user_id, user.username, user.first_name))

    # /admin Dashboard
    if text_str.startswith("/admin") or text_str.startswith("/panel"):
        if user_id != OWNER_ID:
            await message.reply_text("Access denied. Owner only.")
            return

        dash_text, dash_markup = await generate_admin_dashboard()
        await message.reply_text(dash_text, reply_markup=dash_markup)
        return

    # /testdrive Diagnostic Tool
    if text_str == "/testdrive":
        if user_id != OWNER_ID:
            return
        status = await message.reply_text("🔍 Testing Google Drive connection...")
        test_file = f"test_{int(time.time())}.txt"
        with open(test_file, "w") as f:
            f.write("Google drive connection test.")

        link, err = await asyncio.to_thread(upload_file_to_drive, test_file, test_file)
        if os.path.exists(test_file):
            os.remove(test_file)

        if link:
            await status.edit_text(f"✅ **Google Drive Working!**\n\n🔗 **Link:** {link}")
        else:
            await status.edit_text(f"❌ **Google Drive Failed!**\n\n**Error:** `{err}`")
        return

    # /start
    if text_str.startswith("/start"):
        buttons = [[
            InlineKeyboardButton("Ping", callback_data="btn_ping"),
            InlineKeyboardButton("Help", callback_data="btn_help")
        ]]
        if user_id == OWNER_ID:
            buttons.append([InlineKeyboardButton("Admin Panel", callback_data="btn_admin_shortcut")])

        text = (
            f"**Hello {user.first_name},**\n\n"
            "Send any supported link to download:\n"
            "• **Supported:** Telegram, YouTube, TikTok, Instagram, Facebook\n"
            "• Sent media auto-deletes in 5 minutes.\n"
            "• Google Drive backup included."
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ----------------- TELEGRAM URL HANDLER ----------------- #
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
        tg_url = text_str.strip()
        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, tg_url, "telegram"))

        status = await message.reply_text("⏳ Fetching Telegram post...")
        target_msg = None
        working_client = None

        try:
            active_sessions = await asyncio.to_thread(sync_get_all_sessions)

            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            if not active_sessions:
                await status.edit_text("❌ No user session active. Add one via /admin.")
                return

            for sess in active_sessions:
                temp_client = Client(
                    f"sess_{int(time.time()*1000)}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=sess['session_string'],
                    in_memory=True
                )
                try:
                    await asyncio.wait_for(temp_client.start(), timeout=6)
                    msg = await asyncio.wait_for(temp_client.get_messages(chat_id, msg_id), timeout=6)
                    if has_media(msg):
                        target_msg = msg
                        working_client = temp_client
                        break
                    await temp_client.stop()
                except Exception as ex:
                    logger.warning(f"Session test skipped: {ex}")
                    if temp_client.is_connected:
                        await temp_client.stop()

            if not target_msg or not working_client:
                await status.edit_text("❌ Media not found or account hasn't joined this channel.")
                return

            progress_status[user_id] = {"last_time": time.time(), "start_time": time.time()}

            # Handle Album
            if target_msg.media_group_id:
                group_messages = await asyncio.wait_for(working_client.get_media_group(target_msg.chat.id, target_msg.id), timeout=8)
                downloaded_files, media_list, gif_files = [], [], []
                photos_count, videos_count = 0, 0

                for idx, msg in enumerate(group_messages):
                    if has_media(msg):
                        file_path = await working_client.download_media(
                            msg,
                            progress=progress_bar,
                            progress_args=(status, f"Downloading ({idx+1}/{len(group_messages)})", user_id)
                        )
                        if file_path:
                            downloaded_files.append(file_path)
                            if SERVICE_ACCOUNT_RAW:
                                asyncio.create_task(asyncio.to_thread(upload_file_to_drive, file_path, os.path.basename(file_path)))

                            cap = msg.caption.strip() if msg.caption else ""
                            if is_gif_message(msg):
                                gif_files.append((file_path, cap))
                                videos_count += 1
                            elif msg.video:
                                media_list.append(InputMediaVideo(file_path, caption=cap))
                                videos_count += 1
                            elif msg.photo:
                                media_list.append(InputMediaPhoto(file_path, caption=cap))
                                photos_count += 1

                sent_msgs = []
                if media_list:
                    sent_msgs = await client.send_media_group(chat_id=message.chat.id, media=media_list)
                for gpath, gcap in gif_files:
                    gmsg = await client.send_animation(chat_id=message.chat.id, animation=gpath, caption=gcap)
                    sent_msgs.append(gmsg)

                for path in downloaded_files:
                    if os.path.exists(path):
                        os.remove(path)

                await status.delete()
                del_ids = [m.id for m in sent_msgs]
                asyncio.create_task(auto_delete_messages(message.chat.id, del_ids, 300))

            # Handle Single File
            else:
                is_gif = is_gif_message(target_msg)
                caption = target_msg.caption.strip() if target_msg.caption else ""
                media_type = "GIF" if is_gif else ("Video" if target_msg.video else ("Photo" if target_msg.photo else "Document"))

                file_path = await working_client.download_media(
                    target_msg,
                    progress=progress_bar,
                    progress_args=(status, f"Downloading {media_type}", user_id)
                )

                if not file_path or not os.path.exists(file_path):
                    await status.edit_text("❌ Failed to download media.")
                    return

                drive_link = None
                if SERVICE_ACCOUNT_RAW:
                    drive_link, _ = await asyncio.to_thread(upload_file_to_drive, file_path, os.path.basename(file_path))

                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("☁️ Drive Link", url=drive_link)]]) if drive_link else None

                sent_msg = None
                if is_gif:
                    sent_msg = await client.send_animation(chat_id=message.chat.id, animation=file_path, caption=caption, reply_markup=reply_markup)
                elif target_msg.video:
                    sent_msg = await client.send_video(chat_id=message.chat.id, video=file_path, caption=caption, reply_markup=reply_markup)
                elif target_msg.photo:
                    sent_msg = await client.send_photo(chat_id=message.chat.id, photo=file_path, caption=caption, reply_markup=reply_markup)
                elif target_msg.document:
                    sent_msg = await client.send_document(chat_id=message.chat.id, document=file_path, caption=caption, reply_markup=reply_markup)

                if os.path.exists(file_path):
                    os.remove(file_path)

                await status.delete()
                if sent_msg:
                    asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id], 300))

        except Exception as e:
            logger.error(f"Telegram error: {e}")
            await status.edit_text(f"❌ Error: `{str(e)}`")
        finally:
            if working_client and working_client.is_connected:
                await working_client.stop()
        return

    # ----------------- SOCIAL MEDIA HANDLER ----------------- #
    social_pattern = r"(https?://(?:[a-zA-Z0-9-_]+\.)*(?:youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|facebook\.com|fb\.watch)/[^\s]+)"
    social_match = re.search(social_pattern, text_str)

    if social_match:
        target_url = social_match.group(0)
        platform_name = detect_social_platform(target_url)

        status = await message.reply_text("⏳ Processing video...")
        try:
            file_path, title, duration, width, height = await asyncio.to_thread(
                extract_and_download_social, target_url, user_id
            )

            if not file_path or not os.path.exists(file_path):
                await status.edit_text("❌ Failed to download.")
                return

            drive_link = None
            if SERVICE_ACCOUNT_RAW:
                drive_link, _ = await asyncio.to_thread(upload_file_to_drive, file_path, os.path.basename(file_path))

            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("☁️ Drive Link", url=drive_link)]]) if drive_link else None

            sent_msg = await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=f"**{title[:60]}**" if title else "",
                reply_markup=reply_markup
            )

            if os.path.exists(file_path):
                os.remove(file_path)

            await status.delete()
            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id], 300))
        except Exception as e:
            await status.edit_text(f"❌ Error: `{str(e)}`")
        return

    await message.reply_text("Send a valid Telegram or Social Media link.")

# Background Web Server
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running."))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    logger.info("Starting bot using native runner...")
    bot.run()