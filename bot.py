import os
import re
import time
import json
import asyncio
import logging
import aiohttp
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
    in_memory=True,
    workers=16
)

# Global States
admin_states = {}
login_clients = {}
progress_status = {}
loaded_user_clients = []

# ----------------- GOFILE UPLOADER HELPER ----------------- #

async def upload_to_gofile(file_path: str) -> str:
    """GoFile API-তে ফাইল আপলোড করে ডাউনলোড লিংক দেয়"""
    try:
        timeout = aiohttp.ClientTimeout(total=1800) # ৩০ মিনিট পর্যন্ত টাইমআউট (বড় ফাইলের জন্য)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # ১. সেরা সার্ভার নিয়ে আসা
            async with session.get("https://api.gofile.io/servers") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data.get("status") != "ok":
                    return None
                server = data["data"]["servers"][0]["name"]

            # ২. ফাইল আপলোড করা
            upload_url = f"https://{server}.gofile.io/contents/uploadfile"
            with open(file_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("file", f, filename=os.path.basename(file_path))
                async with session.post(upload_url, data=form) as upload_resp:
                    if upload_resp.status == 200:
                        res = await upload_resp.json()
                        if res.get("status") == "ok":
                            return res["data"]["downloadPage"]
    except Exception as e:
        logger.error(f"GoFile Upload Error: {e}")
    return None

# ----------------- SESSION POOL MANAGER ----------------- #

async def init_session_pool():
    global loaded_user_clients
    if not db:
        return
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    new_pool = []
    for s in sessions:
        try:
            cl = Client(
                f"pool_{s['doc_id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=s['session_string'],
                in_memory=True
            )
            await cl.start()
            new_pool.append((s['doc_id'], s['account_name'], cl))
        except Exception as e:
            logger.warning(f"Failed to start pool client {s['doc_id']}: {e}")
    
    for _, _, old_cl in loaded_user_clients:
        try:
            if old_cl.is_connected:
                await old_cl.stop()
        except Exception:
            pass
    loaded_user_clients = new_pool
    logger.info(f"Session Pool Ready: {len(loaded_user_clients)} active userbots.")

# ----------------- DATABASE HELPERS ----------------- #

def sync_add_session(session_str: str, name: str):
    if not db:
        return False, "Database offline"
    try:
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
        return False

def sync_track_user(user_id: int, username: str, name: str) -> bool:
    if not db:
        return False
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        doc_ref.set({
            "user_id": int(user_id),
            "username": username or "N/A",
            "name": name or "N/A",
            "last_active": time.time()
        }, merge=True)
        return True
    except Exception:
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
    except Exception:
        pass

def sync_get_active_urls(limit: int = 15):
    if not db:
        return []
    try:
        docs = db.collection("submitted_urls").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [doc.to_dict() for doc in docs]
    except Exception:
        return []

def sync_increment_downloads(platform: str, count: int = 1, photos: int = 0, videos: int = 0):
    if not db or count <= 0:
        return
    try:
        update_payload = {
            "total_downloads": firestore.Increment(count),
            f"count_{platform.lower()}": firestore.Increment(count)
        }
        if platform.lower() == "telegram":
            if photos > 0: update_payload["tg_photos"] = firestore.Increment(photos)
            if videos > 0: update_payload["tg_videos"] = firestore.Increment(videos)
        db.collection("bot_stats").document("global_analytics").set(update_payload, merge=True)
    except Exception:
        pass

def sync_increment_user_downloads(user_id: int, platform: str, count: int = 1, photos: int = 0, videos: int = 0):
    if not db or count <= 0:
        return
    try:
        update_data = {
            "total_downloads": firestore.Increment(count),
            "last_active": time.time()
        }
        if platform.lower() == "telegram":
            update_data["telegram_downloads"] = firestore.Increment(count)
            if photos > 0: update_data["tg_photos"] = firestore.Increment(photos)
            if videos > 0: update_data["tg_videos"] = firestore.Increment(videos)
        else:
            update_data["social_downloads"] = firestore.Increment(count)
        db.collection("bot_users").document(str(user_id)).set(update_data, merge=True)
    except Exception:
        pass

def sync_get_stats():
    if not db:
        return 0, {}, 0, 0
    try:
        stat_doc = db.collection("bot_stats").document("global_analytics").get()
        total_dl = 0
        tg_photos, tg_videos = 0, 0
        platform_counts = {"Telegram": 0, "YouTube": 0, "TikTok": 0, "Instagram": 0, "Facebook": 0, "Others": 0}

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
        return total_dl, platform_counts, tg_photos, tg_videos
    except Exception:
        return 0, {}, 0, 0

async def generate_admin_dashboard():
    total_dl, platforms, tg_photos, tg_videos = await asyncio.to_thread(sync_get_stats)
    all_sess = await asyncio.to_thread(sync_get_all_sessions)
    db_status = "Online 🟢" if db else "Offline 🔴"

    text = (
        "👑 **Admin Management Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Database:** `{db_status}`\n"
        f"• **Total Downloads:** `{total_dl:,}`\n"
        f"• **Active Sessions:** `{len(all_sess)}`\n\n"
        "📊 **Platform Breakdown:**\n"
        f"• 📂 Telegram: `{platforms.get('Telegram', 0):,}`\n"
        f"   └ 🖼️ Photos: `{tg_photos:,}` | 🎥 Videos: `{tg_videos:,}`\n"
        f"• 🔴 YouTube: `{platforms.get('YouTube', 0):,}`\n"
        f"• 🎵 TikTok: `{platforms.get('TikTok', 0):,}`\n"
        f"• 📸 Instagram: `{platforms.get('Instagram', 0):,}`\n"
        f"• 🔵 Facebook: `{platforms.get('Facebook', 0):,}`\n"
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
            InlineKeyboardButton("🔗 Recent URLs", callback_data="btn_view_urls"),
            InlineKeyboardButton("🔄 Refresh", callback_data="btn_refresh_admin")
        ]
    ])
    return text, keyboard

# ----------------- UTILITY & DOWNLOADER ----------------- #

async def progress_bar(current, total, status_msg, action_name, user_id):
    if not total or total <= 0:
        return
    now = time.time()
    user_data = progress_status.get(user_id, {})
    last_update = user_data.get("last_time", 0)

    if (now - last_update < 3.5) and current < total:
        return

    progress_status[user_id] = {"last_time": now}
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "■" * filled + "□" * (10 - filled)

    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)

    text = f"**{action_name}**\n`[{bar}]` {percentage:.1f}%\n• `{curr_mb:.1f}/{tot_mb:.1f} MB`"
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
    return bool(msg and (msg.animation or (msg.document and msg.document.mime_type and "gif" in msg.document.mime_type.lower())))

def has_media(msg):
    return bool(msg and (msg.video or msg.photo or msg.document or msg.audio or msg.voice or msg.animation or msg.media_group_id))

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
        'concurrent_fragment_downloads': 4,
        'buffersize': 1024 * 1024 * 16,
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
                    return file_path, str(info.get('title') or "Video")
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")

    return None, None

# ----------------- MESSAGE HANDLER ----------------- #

@bot.on_message(filters.private)
async def private_message_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    text_str = (message.text or message.caption or "").strip()

    asyncio.create_task(asyncio.to_thread(sync_track_user, user_id, user.username, user.first_name))

    # /admin Dashboard
    if text_str.startswith("/admin") or text_str.startswith("/panel"):
        if user_id != OWNER_ID:
            await message.reply_text("Access denied. Owner only.")
            return

        dash_text, dash_markup = await generate_admin_dashboard()
        await message.reply_text(dash_text, reply_markup=dash_markup)
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
                asyncio.create_task(init_session_pool())
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

            await asyncio.to_thread(sync_add_session, session_string, acc_name)
            asyncio.create_task(init_session_pool())
            await status_msg.edit_text(f"✅ **Account Connected & Saved!**\n\n• Name: `{acc_name}`")

        except SessionPasswordNeeded:
            admin_states[user_id] = "LOGIN_2FA"
            await status_msg.edit_text("🔐 **Send your 2FA password:**", reply_markup=ForceReply(selective=True))
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

            await asyncio.to_thread(sync_add_session, session_string, acc_name)
            asyncio.create_task(init_session_pool())
            await status_msg.edit_text(f"✅ **Account Connected & Saved!**\n\n• Name: `{acc_name}`")

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
            "Send any supported link to download instantly:\n"
            "• **Supported:** Telegram, YouTube, TikTok, Instagram, Facebook\n"
            "• Sent media auto-deletes in 5 minutes.\n"
            "• Large files (>45 MB) are uploaded directly to GoFile for fast downloading."
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ----------------- TELEGRAM POST DOWNLOADER ----------------- #
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
        tg_url = text_str.strip()
        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, tg_url, "telegram"))

        status = await message.reply_text("⚡ Fetching...")

        try:
            if not loaded_user_clients:
                await init_session_pool()

            if not loaded_user_clients:
                await status.edit_text("❌ No user session active. Add one via /admin.")
                return

            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            target_msg = None
            working_client = None

            for _, _, u_client in loaded_user_clients:
                try:
                    msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=3)
                    if has_media(msg):
                        target_msg = msg
                        working_client = u_client
                        break
                except Exception:
                    continue

            if not target_msg or not working_client:
                await status.edit_text("❌ Media not found or account hasn't joined this channel.")
                return

            # Single File / Large File Download
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

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

            # ৪৫ MB এর বেশি হলে GoFile-এ যাবে
            if file_size_mb > 45:
                await status.edit_text("🚀 Uploading to GoFile Cloud...")
                gofile_link = await upload_to_gofile(file_path)
                
                if gofile_link:
                    btn = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Fast Download (GoFile)", url=gofile_link)]])
                    sent_msg = await client.send_message(
                        chat_id=message.chat.id,
                        text=f"📁 **File Ready!**\n\n• **Size:** `{file_size_mb:.1f} MB`\n• **Direct Download:** Click below.",
                        reply_markup=btn
                    )
                else:
                    await status.edit_text("❌ Failed to upload to GoFile.")
                    sent_msg = None
            else:
                sent_msg = None
                if is_gif:
                    sent_msg = await client.send_animation(chat_id=message.chat.id, animation=file_path, caption=caption)
                elif target_msg.video:
                    sent_msg = await client.send_video(chat_id=message.chat.id, video=file_path, caption=caption)
                elif target_msg.photo:
                    sent_msg = await client.send_photo(chat_id=message.chat.id, photo=file_path, caption=caption)
                elif target_msg.document:
                    sent_msg = await client.send_document(chat_id=message.chat.id, document=file_path, caption=caption)

            # Local file delete
            if os.path.exists(file_path):
                os.remove(file_path)

            photos_count = 1 if target_msg.photo else 0
            videos_count = 1 if (target_msg.video or is_gif) else 0
            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, photos_count, videos_count))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, photos_count, videos_count))

            await status.delete()
            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id], 300))

        except Exception as e:
            logger.error(f"Telegram Handler Error: {e}")
            await status.edit_text(f"❌ Error: `{str(e)}`")
        return

    # ----------------- SOCIAL MEDIA HANDLER ----------------- #
    social_pattern = r"(https?://(?:[a-zA-Z0-9-_]+\.)*(?:youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|facebook\.com|fb\.watch)/[^\s]+)"
    social_match = re.search(social_pattern, text_str)

    if social_match:
        target_url = social_match.group(0)
        status = await message.reply_text("⚡ Processing...")
        try:
            file_path, title = await asyncio.to_thread(extract_and_download_social, target_url, user_id)
            if not file_path or not os.path.exists(file_path):
                await status.edit_text("❌ Failed to download.")
                return

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

            # সোশ্যাল মিডিয়া ফাইলও ৪৫ MB এর বড় হলে সরাসরি GoFile লিংকে পাঠাবে
            if file_size_mb > 45:
                await status.edit_text("🚀 Uploading to GoFile Cloud...")
                gofile_link = await upload_to_gofile(file_path)
                
                if gofile_link:
                    btn = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Fast Download (GoFile)", url=gofile_link)]])
                    sent_msg = await client.send_message(
                        chat_id=message.chat.id,
                        text=f"🎥 **{title[:60]}**\n\n• **Size:** `{file_size_mb:.1f} MB`",
                        reply_markup=btn
                    )
                else:
                    await status.edit_text("❌ Failed to upload to GoFile.")
                    sent_msg = None
            else:
                sent_msg = await client.send_video(
                    chat_id=message.chat.id,
                    video=file_path,
                    caption=f"**{title[:60]}**" if title else ""
                )

            # Local file delete
            if os.path.exists(file_path):
                os.remove(file_path)

            await status.delete()
            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id], 300))
        except Exception as e:
            await status.edit_text(f"❌ Error: `{str(e)}`")
        return

    await message.reply_text("Send a valid Telegram or Social Media link.")

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
            "**How to use:**\n\nSend any supported link to download media directly.",
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

        await callback_query.message.edit_text("Send any supported link to download media.", reply_markup=InlineKeyboardMarkup(buttons))
        await callback_query.answer()
        return

    if user_id != OWNER_ID:
        await callback_query.answer("Unauthorized.", show_alert=True)
        return

    if data in ["btn_admin_shortcut", "btn_back_admin", "btn_refresh_admin"]:
        dash_text, dash_markup = await generate_admin_dashboard()
        await callback_query.message.edit_text(dash_text, reply_markup=dash_markup)
        await callback_query.answer("Refreshed")

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
                lines.append(f"{idx}. `{s['account_name']}`")
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
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await callback_query.message.edit_text(
            "✅ Session deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
        )
        await callback_query.answer()

    elif data == "btn_view_urls":
        urls = await asyncio.to_thread(sync_get_active_urls, 15)
        if not urls:
            await callback_query.message.edit_text(
                "📭 No recent URLs submitted.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")]])
            )
        else:
            lines = ["🔗 **Recent Submitted URLs:**\n"]
            for idx, item in enumerate(urls, 1):
                lines.append(f"{idx}. **{item.get('user_name')}:** {item.get('url')}")
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
    loop.create_task(init_session_pool())
    logger.info("Starting bot using native runner...")
    bot.run()