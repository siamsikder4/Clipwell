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

# Google Drive OAuth Imports
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
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

# Google Drive OAuth Configs
GDRIVE_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "203426313347-c4lv00u3rgr9e35upvani6mjhqf8jsap.apps.googleusercontent.com").strip()
GDRIVE_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "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").strip()
GDRIVE_REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "1//04oj5g9drum_wCYIARAAGAQSNwF-L9Irb2WkEfSNazDQDF1SPldbINrtvurWmt3uEuGDrd1qUcIeSrEQm8BZ7LsjFakY6Z6xR9o").strip()
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

# ----------------- GOOGLE DRIVE OAUTH ENGINE ----------------- #

def get_drive_service():
    if not (GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET and GDRIVE_REFRESH_TOKEN):
        return None, "OAuth 2.0 Credentials missing"
    try:
        creds = Credentials(
            token=None,
            refresh_token=GDRIVE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GDRIVE_CLIENT_ID,
            client_secret=GDRIVE_CLIENT_SECRET,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        creds.refresh(Request())
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        return service, None
    except Exception as e:
        logger.error(f"Google Drive Auth Error: {e}")
        return None, str(e)

def upload_file_to_drive(file_path: str, file_name: str):
    if not os.path.exists(file_path):
        return None, "File does not exist on disk"

    service, auth_err = get_drive_service()
    if not service:
        logger.error(f"Drive Service Auth Failed: {auth_err}")
        return None, f"Drive Init Error: {auth_err}"

    try:
        file_metadata = {'name': file_name}
        if GDRIVE_FOLDER_ID:
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
            return None, "Drive did not return File ID."

        try:
            permission = {'type': 'anyone', 'role': 'reader'}
            service.permissions().create(
                fileId=file_id,
                body=permission,
                supportsAllDrives=True
            ).execute()
        except Exception as p_err:
            logger.warning(f"Permission create skipped: {p_err}")

        web_link = file.get('webViewLink') or f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        logger.info(f"Drive upload successful! Link: {web_link}")
        return web_link, None
    except Exception as e:
        logger.error(f"G-Drive Upload Exception: {e}", exc_info=True)
        return None, str(e)

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

def sync_cleanup_expired_urls():
    if not db:
        return
    try:
        one_day_ago = time.time() - 86400
        docs = db.collection("submitted_urls").where("timestamp", "<", one_day_ago).stream()
        deleted_count = 0
        for doc in docs:
            doc.reference.delete()
            deleted_count += 1
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} expired URLs.")
    except Exception as e:
        logger.error(f"Cleanup URLs Error: {e}")

async def url_cleanup_daemon():
    while True:
        await asyncio.sleep(3600)
        await asyncio.to_thread(sync_cleanup_expired_urls)

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
        logger.error(f"User Metric Error for {user_id}: {e}")

def sync_get_user_info(user_id: str):
    if not db:
        return None
    try:
        doc = db.collection("bot_users").document(str(user_id)).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        logger.error(f"Get User Info Error: {e}")
        return None

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
    drive_status = "Online (OAuth 2.0) 🟢" if GDRIVE_REFRESH_TOKEN else "Offline 🔴"

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

    if "instagram.com" in clean_url or "instagr.am" in clean_url:
        try:
            api_url = f"https://api.vkrdown.com/api/insta?url={urllib.parse.quote(clean_url)}"
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get("status") == "success" and data.get("data"):
                    media_url = data["data"][0].get("url")
                    out_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_{timestamp}_insta.mp4")
                    urllib.request.urlretrieve(media_url, out_path)
                    if os.path.exists(out_path):
                        return out_path, "Instagram Reel", 0, 0, 0
        except Exception as api_err:
            logger.warning(f"Instagram Direct API fallback: {api_err}")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'max_filesize': 1900 * 1024 * 1024,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
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

                if not os.path.exists(file_path):
                    prefix = f"{user_id}_{timestamp}_"
                    for f in os.listdir(DOWNLOAD_DIR):
                        if f.startswith(prefix) and not f.endswith(".part"):
                            file_path = os.path.join(DOWNLOAD_DIR, f)
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

    async def track_and_notify():
        is_new = await asyncio.to_thread(sync_track_user, user_id, user.username, user.first_name)
        if is_new and user_id != OWNER_ID:
            alert_text = (
                "🔔 **#New_User_Alert**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"• **Name:** {user.first_name}\n"
                f"• **Username:** @{user.username if user.username else 'N/A'}\n"
                f"• **User ID:** `{user_id}`\n"
                f"• **Profile:** [Click Here](tg://user?id={user_id})\n"
                "━━━━━━━━━━━━━━━━━━"
            )
            try:
                await client.send_message(chat_id=OWNER_ID, text=alert_text)
            except Exception:
                pass

    asyncio.create_task(track_and_notify())

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
        test_file = "test_drive.txt"
        with open(test_file, "w") as f:
            f.write("Google drive connection test file.")
            
        link, err = await asyncio.to_thread(upload_file_to_drive, test_file, "test_drive.txt")
        if os.path.exists(test_file):
            os.remove(test_file)
            
        if link:
            await status.edit_text(f"✅ **Google Drive Working!**\n\n🔗 **Link:** {link}")
        else:
            await status.edit_text(f"❌ **Google Drive Failed!**\n\n**Error:**\n`{err}`")
        return

    # /user Info
    if text_str.startswith("/user"):
        if user_id != OWNER_ID:
            return
        parts = text_str.split()
        if len(parts) < 2:
            await message.reply_text("💡 **Usage:** `/user <User_ID>`")
            return

        target_id = parts[1].strip()
        user_info = await asyncio.to_thread(sync_get_user_info, target_id)
        if not user_info:
            await message.reply_text("❌ User not found in database.")
            return

        tg_dl = user_info.get("telegram_downloads", 0)
        tg_photos = user_info.get("tg_photos", 0)
        tg_videos = user_info.get("tg_videos", 0)
        social_dl = user_info.get("social_downloads", 0)
        total_dl = user_info.get("total_downloads", 0)
        last_seen = time.strftime('%Y-%m-%d %I:%M %p', time.localtime(user_info.get('last_active', 0)))

        response_text = (
            f"👤 **User Analytics**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• **Name:** {user_info.get('name', 'N/A')}\n"
            f"• **Username:** @{user_info.get('username', 'N/A')}\n"
            f"• **User ID:** `{user_info.get('user_id')}`\n"
            f"• **Last Active:** `{last_seen}`\n\n"
            f"📊 **Download Breakdown:**\n"
            f"• 📂 **Telegram Total:** `{tg_dl:,}`\n"
            f"   └ 🖼️ Photos: `{tg_photos:,}` | 🎥 Videos: `{tg_videos:,}`\n"
            f"• 🌐 **Social Media:** `{social_dl:,}`\n"
            f"• 🚀 **Total Items:** `{total_dl:,}`\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await message.reply_text(response_text)
        return

    # Admin Login States
    if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
        admin_states.pop(user_id, None)
        status_msg = await message.reply_text("Validating session string...")

        test_client = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
        try:
            await test_client.start()
            me = await test_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await test_client.stop()

            success, err_msg = await asyncio.to_thread(sync_add_session, text_str, acc_name)
            if success:
                await status_msg.edit_text(f"✅ **Session saved!**\n\n• Account: `{acc_name}`")
            else:
                await status_msg.edit_text(f"❌ Error: `{err_msg}`")
        except Exception as e:
            await status_msg.edit_text(f"❌ Invalid session: `{str(e)}`")
        return

    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_PHONE":
        admin_states.pop(user_id, None)
        phone_number = text_str.replace(" ", "").strip()
        status_msg = await message.reply_text("⏳ Sending Telegram login OTP code...")

        temp_client = Client(f"login_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await temp_client.connect()
            code_info = await temp_client.send_code(phone_number)
            login_clients[user_id] = {
                "client": temp_client,
                "phone": phone_number,
                "phone_code_hash": code_info.phone_code_hash
            }
            admin_states[user_id] = "LOGIN_OTP"
            await status_msg.edit_text(
                "📩 **Send the Telegram OTP code:**\n\n💡 *Tip:* Separate digits with space (e.g. `1 2 3 4 5`).",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            await status_msg.edit_text(f"❌ Failed to send OTP: `{str(e)}`")
        return

    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_OTP":
        otp_code = text_str.replace(" ", "").replace("-", "").strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("❌ Session timed out.")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ Verifying OTP code...")

        try:
            await temp_client.sign_in(session_data["phone"], session_data["phone_code_hash"], otp_code)
            session_string = await temp_client.export_session_string()
            me = await temp_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)

            result_text = (
                f"✅ **Session generated!**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• **Account:** `{acc_name}`\n"
                f"• **User ID:** `{me.id}`\n\n"
                f"📋 **Pyrogram Session String (Tap to Copy):**\n\n"
                f"`{session_string}`\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 Save this via Admin Panel -> **'➕ Paste Session'**."
            )
            await status_msg.edit_text(result_text)

        except SessionPasswordNeeded:
            admin_states[user_id] = "LOGIN_2FA"
            await status_msg.edit_text("🔐 **Send your 2FA password:**", reply_markup=ForceReply(selective=True))
        except (PhoneCodeInvalid, PhoneCodeExpired):
            await status_msg.edit_text("❌ Invalid or expired OTP. Please try again:")
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ Login error: `{str(e)}`")
        return

    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_2FA":
        password = text_str.strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("❌ Session timed out.")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ Verifying 2FA...")

        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            me = await temp_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)

            result_text = (
                f"✅ **Session generated!**\n\n"
                f"• **Account:** `{acc_name}`\n"
                f"`{session_string}`"
            )
            await status_msg.edit_text(result_text)

        except PasswordHashInvalid:
            await status_msg.edit_text("❌ Incorrect 2FA password.")
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ Verification failed: `{str(e)}`")
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

    # ----------------- SOCIAL MEDIA HANDLER ----------------- #
    social_pattern = r"(https?://(?:[a-zA-Z0-9-_]+\.)*(?:youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|facebook\.com|fb\.watch)/[^\s]+)"
    social_match = re.search(social_pattern, text_str)

    if social_match:
        target_url = social_match.group(0)
        platform_name = detect_social_platform(target_url)

        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, target_url, platform_name))
        status = await message.reply_text("⏳ Processing video...")

        try:
            file_path, title, duration, width, height = await asyncio.to_thread(
                extract_and_download_social, target_url, user_id
            )

            if not file_path or not os.path.exists(file_path):
                await status.edit_text("❌ Failed to download video. Post might be private or restricted.")
                return

            drive_link = None
            if GDRIVE_REFRESH_TOKEN:
                fname = os.path.basename(file_path)
                await status.edit_text("⏳ Uploading to Google Drive...")
                drive_link, drive_err = await asyncio.to_thread(upload_file_to_drive, file_path, fname)

            progress_status[user_id] = {"last_time": time.time(), "start_time": time.time()}
            caption_txt = f"**{title[:60]}**" if title else ""
            
            reply_markup = None
            if drive_link:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("☁️ Google Drive Link", url=drive_link)]
                ])

            send_kwargs = {
                "chat_id": message.chat.id,
                "video": file_path,
                "caption": caption_txt,
                "reply_markup": reply_markup,
                "supports_streaming": True,
                "progress": progress_bar,
                "progress_args": (status, "Uploading Video to Telegram", user_id)
            }
            if duration > 0:
                send_kwargs["duration"] = duration
            if width > 0:
                send_kwargs["width"] = width
            if height > 0:
                send_kwargs["height"] = height

            sent_msg = await client.send_video(**send_kwargs)

            if os.path.exists(file_path):
                os.remove(file_path)

            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, platform_name, 1))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, platform_name, 1))
            await status.delete()

            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id], delay_seconds=300))

        except Exception as e:
            logger.error(f"Social download error: {e}", exc_info=True)
            await status.edit_text(f"Download Error: `{str(e)[:120]}`")
        return

    # ----------------- TELEGRAM URL HANDLER ----------------- #
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
        tg_url = text_str.strip()
        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, tg_url, "telegram"))

        status = await message.reply_text("Fetching Telegram post...")
        target_msg = None
        working_client = None
        is_temp_client = False

        try:
            active_sessions = await asyncio.to_thread(sync_get_all_sessions)

            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            if not active_sessions:
                await status.edit_text("To download from Telegram channels, add a User Session via /admin first.")
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
                    await asyncio.wait_for(temp_client.start(), timeout=8)
                    msg = await asyncio.wait_for(temp_client.get_messages(chat_id, msg_id), timeout=8)
                    if has_media(msg):
                        target_msg = msg
                        working_client = temp_client
                        is_temp_client = True
                        break
                    await temp_client.stop()
                except Exception as ex:
                    logger.warning(f"Session test failed: {ex}")
                    if temp_client.is_connected:
                        await temp_client.stop()

            if not target_msg or not working_client:
                await status.edit_text("Media not found. Make sure the connected session account has joined this channel.")
                return

            progress_status[user_id] = {"last_time": time.time(), "start_time": time.time()}

            # Handle Single File / Photo / Video
            is_gif = is_gif_message(target_msg)
            caption = target_msg.caption.strip() if target_msg.caption else ""
            media_type = "GIF" if is_gif else ("Video" if target_msg.video else ("Photo" if target_msg.photo else "Document"))

            progress_status[user_id]["start_time"] = time.time()
            file_path = await working_client.download_media(
                target_msg,
                progress=progress_bar,
                progress_args=(status, f"Downloading {media_type}", user_id)
            )

            if not file_path or not os.path.exists(file_path):
                await status.edit_text("Failed to download media file.")
                return

            # Direct Google Drive Upload (OAuth 2.0)
            drive_link = None
            if GDRIVE_REFRESH_TOKEN:
                fname = os.path.basename(file_path)
                await status.edit_text(f"⏳ Uploading {media_type} to Google Drive...")
                drive_link, drive_err = await asyncio.to_thread(upload_file_to_drive, file_path, fname)
                if not drive_link:
                    logger.warning(f"Drive upload failed: {drive_err}")

            reply_markup = None
            if drive_link:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("☁️ Google Drive Link", url=drive_link)]
                ])

            progress_status[user_id]["start_time"] = time.time()
            sent_msg = None

            photos_count = 1 if target_msg.photo or (target_msg.document and target_msg.document.mime_type and "image" in target_msg.document.mime_type) else 0
            videos_count = 1 if (target_msg.video or is_gif or (target_msg.document and target_msg.document.mime_type and "video" in target_msg.document.mime_type)) else 0

            if is_gif:
                sent_msg = await client.send_animation(
                    chat_id=message.chat.id, animation=file_path, caption=caption, reply_markup=reply_markup,
                    progress=progress_bar, progress_args=(status, f"Uploading {media_type} to Telegram", user_id)
                )
            elif target_msg.video:
                sent_msg = await client.send_video(
                    chat_id=message.chat.id, video=file_path, caption=caption, reply_markup=reply_markup,
                    supports_streaming=True, progress=progress_bar, progress_args=(status, f"Uploading {media_type} to Telegram", user_id)
                )
            elif target_msg.photo:
                sent_msg = await client.send_photo(
                    chat_id=message.chat.id, photo=file_path, caption=caption, reply_markup=reply_markup,
                    progress=progress_bar, progress_args=(status, f"Uploading {media_type} to Telegram", user_id)
                )
            elif target_msg.document or target_msg.audio or target_msg.voice:
                sent_msg = await client.send_document(
                    chat_id=message.chat.id, document=file_path, caption=caption, reply_markup=reply_markup,
                    progress=progress_bar, progress_args=(status, f"Uploading {media_type} to Telegram", user_id)
                )

            if os.path.exists(file_path):
                os.remove(file_path)

            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, photos_count, videos_count))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, photos_count, videos_count))
            await status.delete()

            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id], 300))

        except Exception as e:
            logger.error(f"Download/Upload error: {e}", exc_info=True)
            await status.edit_text(f"Error occurred: `{str(e)}`")

        finally:
            if is_temp_client and working_client and working_client.is_connected:
                await working_client.stop()
        return

    await message.reply_text("Invalid link. Send Telegram, YouTube, TikTok, Instagram, or Facebook URL.")

# ----------------- CALLBACK QUERY HANDLER ----------------- #

@bot.on_callback_query()
async def callback_handler(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id

    if data == "btn_ping":
        start_ping = time.time()
        msg = await callback_query.message.edit_text("Checking...")
        latency = (time.time() - start_ping) * 1000
        await msg.edit_text(
            f"**Latency:** `{latency:.1f} ms`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_home")]])
        )
        await callback_query.answer()
        return

    elif data == "btn_help":
        await callback_query.message.edit_text(
            "**How to use:**\n\n"
            "Send any supported link (Telegram, YouTube, TikTok, Instagram, Facebook) to download media directly.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_home")]])
        )
        await callback_query.answer()
        return

    elif data == "btn_back_home":
        buttons = [[
            InlineKeyboardButton("Ping", callback_data="btn_ping"),
            InlineKeyboardButton("Help", callback_data="btn_help")
        ]]
        if user_id == OWNER_ID:
            buttons.append([InlineKeyboardButton("Admin Panel", callback_data="btn_admin_shortcut")])

        await callback_query.message.edit_text(
            "Send any supported link to download media.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback_query.answer()
        return

    if user_id != OWNER_ID:
        await callback_query.answer("Unauthorized.", show_alert=True)
        return

    if data in ["btn_admin_shortcut", "btn_back_admin", "btn_refresh_admin"]:
        dash_text, dash_markup = await generate_admin_dashboard()
        await callback_query.message.edit_text(dash_text, reply_markup=dash_markup)
        await callback_query.answer("Dashboard Refreshed")

    elif data == "btn_login_account":
        admin_states[user_id] = "LOGIN_PHONE"
        await callback_query.message.reply(
            "📱 **Send phone number with country code:**\n\n**Example:** `+8801700000000`",
            reply_markup=ForceReply(selective=True)
        )
        await callback_query.answer()

    elif data == "btn_add_session":
        admin_states[user_id] = "WAITING_SESSION"
        await callback_query.message.reply(
            "Send the Pyrogram `SESSION_STRING` in reply to this message.",
            reply_markup=ForceReply(selective=True)
        )
        await callback_query.answer()

    elif data == "btn_list_sessions":
        all_sess = await asyncio.to_thread(sync_get_all_sessions)
        if not all_sess:
            await callback_query.message.edit_text(
                "No active sessions found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
            )
        else:
            lines = ["**Active Sessions:**\n"]
            for idx, s in enumerate(all_sess, 1):
                lines.append(f"{idx}. `{s['account_name']}` (ID: `{s['doc_id']}`)")
            await callback_query.message.edit_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
            )
        await callback_query.answer()

    elif data == "btn_del_menu":
        all_sess = await asyncio.to_thread(sync_get_all_sessions)
        if not all_sess:
            await callback_query.answer("No sessions found.", show_alert=True)
            return

        buttons = [[InlineKeyboardButton(s['account_name'], callback_data=f"del_{s['doc_id']}")] for s in all_sess]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")])
        await callback_query.message.edit_text("Select session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback_query.answer()

    elif data.startswith("del_"):
        doc_id = data.split("del_")[1]
        success = await asyncio.to_thread(sync_delete_session, doc_id)
        msg = "✅ Session deleted." if success else "❌ Failed to delete session."
        await callback_query.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
        )
        await callback_query.answer()

    elif data == "btn_view_urls":
        urls = await asyncio.to_thread(sync_get_active_urls, 25)
        if not urls:
            await callback_query.message.edit_text(
                "📭 No URLs submitted in the last 24 hours.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
            )
        else:
            lines = ["🔗 **Recent Submitted URLs (Last 24h):**\n"]
            for idx, item in enumerate(urls, 1):
                t_str = time.strftime('%I:%M %p', time.localtime(item.get("timestamp", 0)))
                lines.append(
                    f"{idx}. **User:** {item.get('user_name')} (`{item.get('user_id')}`)\n"
                    f"   • **Platform:** `{item.get('platform')}` | **Time:** `{t_str}`\n"
                    f"   • **URL:** {item.get('url')}\n"
                )
            await callback_query.message.edit_text(
                "\n".join(lines),
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
            )
        await callback_query.answer()

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
    loop.create_task(url_cleanup_daemon())
    logger.info("Starting bot using native runner...")
    bot.run()