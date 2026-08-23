import os
import re
import time
import json
import asyncio
import logging
import urllib.request
from aiohttp import web
import yt_dlp
from hydrogram import Client, filters
from hydrogram.types import (
    Message, InputMediaVideo, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ForceReply
)
from hydrogram.enums import ChatMemberStatus
from hydrogram.errors import (
    UserNotParticipant, ChatAdminRequired, PeerIdInvalid,
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
    in_memory=True
)

# Global States
admin_states = {}
login_clients = {}  # In-bot session login cache
progress_status = {}

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

# F-Sub Functions
def sync_get_fsub_channels():
    if not db:
        return []
    try:
        docs = db.collection("fsub_channels").stream()
        channels = []
        for doc in docs:
            data = doc.to_dict()
            channels.append({
                "doc_id": doc.id,
                "chat_id": data.get("chat_id"),
                "invite_link": data.get("invite_link"),
                "channel_name": data.get("channel_name", "Join Channel")
            })
        return channels
    except Exception as e:
        logger.error(f"Get F-Sub Channels Error: {e}")
        return []

def sync_add_fsub_channel(chat_id: str, invite_link: str, channel_name: str):
    if not db:
        return False, "Database offline"
    try:
        db.collection("fsub_channels").add({
            "chat_id": chat_id.strip(),
            "invite_link": invite_link.strip(),
            "channel_name": channel_name.strip(),
            "created_at": time.time()
        })
        return True, "Success"
    except Exception as e:
        return False, str(e)

def sync_delete_fsub_channel(doc_id: str):
    if not db:
        return False
    try:
        db.collection("fsub_channels").document(doc_id).delete()
        return True
    except Exception as e:
        logger.error(f"Delete F-Sub Channel Error: {e}")
        return False

# User Tracking & Analytics
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

def sync_increment_downloads(platform: str):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_stats").document("global_analytics")
        doc_ref.set({
            "total_downloads": firestore.Increment(1),
            f"count_{platform.lower()}": firestore.Increment(1)
        }, merge=True)
    except Exception as e:
        logger.error(f"Metric Error: {e}")

def sync_increment_user_downloads(user_id: int, platform: str):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        update_data = {
            "total_downloads": firestore.Increment(1),
            "last_active": time.time()
        }
        if platform.lower() == "telegram":
            update_data["telegram_downloads"] = firestore.Increment(1)
        else:
            update_data["social_downloads"] = firestore.Increment(1)
            update_data[f"count_{platform.lower()}"] = firestore.Increment(1)

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
        return 0, 0, {}
    try:
        users = sum(1 for _ in db.collection("bot_users").stream())
        stat_doc = db.collection("bot_stats").document("global_analytics").get()
        if stat_doc.exists:
            data = stat_doc.to_dict()
            total_dl = data.get("total_downloads", 0)
            platform_counts = {
                "Telegram": data.get("count_telegram", 0),
                "YouTube": data.get("count_youtube", 0),
                "TikTok": data.get("count_tiktok", 0),
                "Instagram": data.get("count_instagram", 0),
                "Facebook": data.get("count_facebook", 0),
                "Others": data.get("count_others", 0)
            }
        else:
            total_dl = 0
            platform_counts = {}
        return users, total_dl, platform_counts
    except Exception as e:
        logger.error(f"Stats Error: {e}")
        return 0, 0, {}

async def generate_admin_dashboard():
    users, downloads, platforms = await asyncio.to_thread(sync_get_stats)
    all_sess = await asyncio.to_thread(sync_get_all_sessions)
    fsub_list = await asyncio.to_thread(sync_get_fsub_channels)
    db_status = "Online 🟢" if db else "Offline 🔴"

    text = (
        "👑 **Admin Management Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Database Status:** `{db_status}`\n"
        f"• **Total Users:** `{users}`\n"
        f"• **Total Downloads:** `{downloads}`\n"
        f"• **Active Sessions:** `{len(all_sess)}`\n"
        f"• **F-Sub Channels:** `{len(fsub_list)}`\n\n"
        "📊 **Platform Breakdown:**\n"
        f"• 📂 Telegram: `{platforms.get('Telegram', 0)}`\n"
        f"• 🔴 YouTube: `{platforms.get('YouTube', 0)}`\n"
        f"• 🎵 TikTok: `{platforms.get('TikTok', 0)}`\n"
        f"• 📸 Instagram: `{platforms.get('Instagram', 0)}`\n"
        f"• 🔵 Facebook: `{platforms.get('Facebook', 0)}`\n"
        f"• 🌐 Others: `{platforms.get('Others', 0)}`\n"
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
            InlineKeyboardButton("📢 Force Sub Menu", callback_data="btn_fsub_menu"),
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

def unshorten_url(url: str) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.geturl()
    except Exception:
        return url

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
    real_url = unshorten_url(url)
    timestamp = int(time.time())
    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_{timestamp}_%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'max_filesize': 1900 * 1024 * 1024,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(real_url, download=True)
        if not info:
            return None, None, 0, 0, 0

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

        if not os.path.exists(file_path):
            return None, None, 0, 0, 0

        title = str(info.get('title') or "Video")
        duration = int(info.get('duration') or 0)
        width = int(info.get('width') or 0)
        height = int(info.get('height') or 0)

        return file_path, title, duration, width, height

async def check_user_fsub(client: Client, user_id: int):
    if user_id == OWNER_ID:
        return []
    
    fsub_channels = await asyncio.to_thread(sync_get_fsub_channels)
    if not fsub_channels:
        return []

    unjoined_channels = []
    for ch in fsub_channels:
        try:
            chat_id = int(ch['chat_id']) if ch['chat_id'].startswith("-100") or ch['chat_id'].isdigit() or ch['chat_id'].startswith("-") else ch['chat_id']
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in [ChatMemberStatus.BANNED, ChatMemberStatus.LEFT]:
                unjoined_channels.append(ch)
        except UserNotParticipant:
            unjoined_channels.append(ch)
        except (ChatAdminRequired, PeerIdInvalid) as e:
            logger.warning(f"Bot cannot check status for {ch['chat_id']}: {e}. Make sure the bot is an Admin.")
        except Exception as e:
            logger.error(f"FSub check error for {ch['chat_id']}: {e}")

    return unjoined_channels

def get_fsub_keyboard(unjoined_channels):
    buttons = []
    for ch in unjoined_channels:
        buttons.append([InlineKeyboardButton(f"📢 {ch['channel_name']}", url=ch['invite_link'])])
    buttons.append([InlineKeyboardButton("🔄 Joined / Try Again", callback_data="btn_check_fsub")])
    return InlineKeyboardMarkup(buttons)

# ----------------- MESSAGE HANDLER ----------------- #

@bot.on_message(filters.private)
async def private_message_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    text_str = (message.text or message.caption or "").strip()

    logger.info(f"Incoming update from {user_id} ({user.first_name}): {text_str}")

    # Track User & Send Admin Alert for New Users
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
            except Exception as ex:
                logger.error(f"Admin alert failed: {ex}")

    asyncio.create_task(track_and_notify())

    # /admin handler (Shows Live Dashboard with all details)
    if text_str.startswith("/admin") or text_str.startswith("/panel"):
        if user_id != OWNER_ID:
            await message.reply_text("Access denied.")
            return

        dash_text, dash_markup = await generate_admin_dashboard()
        await message.reply_text(dash_text, reply_markup=dash_markup)
        return

    # Check Specific User Details (/user <ID>)
    if text_str.startswith("/user"):
        if user_id != OWNER_ID:
            return
        parts = text_str.split()
        if len(parts) < 2:
            await message.reply_text("💡 **ব্যবহারের নিয়ম:** `/user <User_ID>`\n\n**উদাহরণ:** `/user 1234567890`")
            return
        
        target_id = parts[1].strip()
        user_info = await asyncio.to_thread(sync_get_user_info, target_id)
        if not user_info:
            await message.reply_text("❌ এই ইউজার ডাটাবেজে পাওয়া যায়নি!")
            return

        tg_dl = user_info.get("telegram_downloads", 0)
        social_dl = user_info.get("social_downloads", 0)
        total_dl = user_info.get("total_downloads", 0)
        last_seen = time.strftime('%d-%m-%Y %I:%M %p', time.localtime(user_info.get('last_active', 0)))

        response_text = (
            f"👤 **User Analytics**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• **Name:** {user_info.get('name', 'N/A')}\n"
            f"• **Username:** @{user_info.get('username', 'N/A')}\n"
            f"• **User ID:** `{user_info.get('user_id')}`\n"
            f"• **Last Active:** `{last_seen}`\n\n"
            f"📊 **ডাউনলোড পরিসংখ্যান:**\n"
            f"• 📂 **Telegram Downloads:** `{tg_dl}`\n"
            f"• 🌐 **Social Downloads:** `{social_dl}`\n"
            f"• 🚀 **Total Downloads:** `{total_dl}`\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await message.reply_text(response_text)
        return

    # ----------------- ADMIN STATES (ONLY OWNER) ----------------- #

    # 1. Manual Session Input State (Admin Pastes Session String)
    if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
        admin_states.pop(user_id, None)
        status_msg = await message.reply_text("Validating session...")

        test_client = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
        try:
            await test_client.start()
            me = await test_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await test_client.stop()

            success, err_msg = await asyncio.to_thread(sync_add_session, text_str, acc_name)
            if success:
                await status_msg.edit_text(f"✅ **সেশন সফলভাবে ডাটাবেজে সেভ হয়েছে!**\n\n• অ্যাকাউন্ট: `{acc_name}`")
            else:
                await status_msg.edit_text(f"❌ Error: `{err_msg}`")
        except Exception as e:
            await status_msg.edit_text(f"❌ Invalid session: `{str(e)}`")
        return

    # 2. In-Bot Session Generator - Step 1: Phone Number
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_PHONE":
        admin_states.pop(user_id, None)
        phone_number = text_str.replace(" ", "").strip()
        status_msg = await message.reply_text("⏳ টেলিগ্রাম ওটিপি (OTP) কোড পাঠানো হচ্ছে...")

        temp_client = Client(
            f"login_{int(time.time())}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
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
                "📩 **টেলিগ্রামে আসা OTP কোডটি পাঠান:**\n\n"
                "💡 *টিপস:* টেলিগ্রাম কোড ব্লক এড়াতে স্পেস দিয়ে লিখুন (যেমন: `1 2 3 4 5`)",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            await status_msg.edit_text(f"❌ ওটিপি পাঠাতে সমস্যা হয়েছে: `{str(e)}`")
        return

    # 3. In-Bot Session Generator - Step 2: OTP Code
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_OTP":
        otp_code = text_str.replace(" ", "").replace("-", "").strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("❌ সেশন টাইমআউট হয়ে গেছে। আবার চেষ্টা করুন।")
            return

        temp_client = session_data["client"]
        phone = session_data["phone"]
        phone_code_hash = session_data["phone_code_hash"]
        status_msg = await message.reply_text("⏳ ওটিপি যাচাই করা হচ্ছে...")

        try:
            await temp_client.sign_in(phone, phone_code_hash, otp_code)
            session_string = await temp_client.export_session_string()
            me = await temp_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)

            # Auto-save হবে না; নিচে কোডটি কপি করার জন্য পাঠিয়ে দেওয়া হবে
            result_text = (
                f"✅ **সেশন সফলভাবে তৈরি হয়েছে!**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• **অ্যাকাউন্ট:** `{acc_name}`\n"
                f"• **ইউজার আইডি:** `{me.id}`\n\n"
                f"📋 **আপনার সেশন কোড (নিচে ট্যাপ করে কপি করুন):**\n\n"
                f"`{session_string}`\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 এটি কপি করে Admin প্যানেলের **'➕ Paste Session'** বাটনে গিয়ে যুক্ত করে নিন।"
            )
            await status_msg.edit_text(result_text)

        except SessionPasswordNeeded:
            admin_states[user_id] = "LOGIN_2FA"
            await status_msg.edit_text(
                "🔐 এই অ্যাকাউন্টে **Two-Step Verification (2FA)** চালু আছে।\n\nঅনুগ্রহ করে ২-স্টেপ পাসওয়ার্ডটি পাঠান:",
                reply_markup=ForceReply(selective=True)
            )
        except (PhoneCodeInvalid, PhoneCodeExpired):
            await status_msg.edit_text("❌ ভুল বা মেয়াদোত্তীর্ণ ওটিপি! সঠিক কোডটি স্পেস দিয়ে লিখুন:")
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ লগইন এরর: `{str(e)}`")
        return

    # 4. In-Bot Session Generator - Step 3: 2FA Password
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_2FA":
        password = text_str.strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("❌ সেশন টাইমআউট হয়ে গেছে।")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ পাসওয়ার্ড যাচাই করা হচ্ছে...")

        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            me = await temp_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)

            result_text = (
                f"✅ **সেশন সফলভাবে তৈরি হয়েছে!**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• **অ্যাকাউন্ট:** `{acc_name}`\n"
                f"• **ইউজার আইডি:** `{me.id}`\n\n"
                f"📋 **আপনার সেশন কোড (নিচে ট্যাপ করে কপি করুন):**\n\n"
                f"`{session_string}`\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 এটি কপি করে Admin প্যানেলের **'➕ Paste Session'** বাটনে গিয়ে যুক্ত করে নিন।"
            )
            await status_msg.edit_text(result_text)

        except PasswordHashInvalid:
            await status_msg.edit_text("❌ ভুল ২-স্টেপ পাসওয়ার্ড! আবার সঠিক পাসওয়ার্ড দিন:")
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ ভেরিফিকেশন ফেইলড: `{str(e)}`")
        return

    # 5. Admin F-Sub Channel Input
    if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_FSUB":
        admin_states.pop(user_id, None)
        parts = [p.strip() for p in text_str.split("|")]
        if len(parts) < 3:
            await message.reply_text(
                "❌ **Invalid Format!**\n\nPlease send like this:\n`Chat_ID | Invite_Link | Channel Name`\n\nExample:\n`-100123456789 | https://t.me/+AbCdEf | Join Updates`"
            )
            return
        
        c_id, c_link, c_name = parts[0], parts[1], parts[2]
        success, err = await asyncio.to_thread(sync_add_fsub_channel, c_id, c_link, c_name)
        if success:
            await message.reply_text(f"✅ **Force Sub channel added successfully!**\n\n• Name: `{c_name}`\n• Chat ID: `{c_id}`")
        else:
            await message.reply_text(f"❌ Failed to add channel: `{err}`")
        return

    # --- FORCE SUBSCRIBE CHECK (FOR GENERAL USERS) ---
    unjoined = await check_user_fsub(client, user_id)
    if unjoined:
        await message.reply_text(
            "⚠️ **You must join our channel(s) first to use this bot!**\n\nPlease join the channels below and click **'Joined / Try Again'**.",
            reply_markup=get_fsub_keyboard(unjoined)
        )
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
            "Send any media link to download:\n"
            "• **Supported:** Telegram, YouTube, TikTok, Instagram, Facebook\n"
            "• Sent media auto-deletes in 5 minutes."
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Social Media URL
    social_pattern = r"(https?://(?:[a-zA-Z0-9-_]+\.)*(?:youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|facebook\.com|fb\.watch)/[^\s]+)"
    social_match = re.search(social_pattern, text_str)

    if social_match:
        target_url = social_match.group(0)
        platform_name = detect_social_platform(target_url)
        status = await message.reply_text("Processing video...")

        try:
            file_path, title, duration, width, height = await asyncio.to_thread(
                extract_and_download_social, target_url, user_id
            )

            if not file_path or not os.path.exists(file_path):
                await status.edit_text("Failed to download video. Post might be private or link is expired.")
                return

            progress_status[user_id] = {"last_time": time.time(), "start_time": time.time()}

            send_kwargs = {
                "chat_id": message.chat.id,
                "video": file_path,
                "caption": f"**{title[:60]}**" if title else "",
                "supports_streaming": True,
                "progress": progress_bar,
                "progress_args": (status, "Uploading Video", user_id)
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

            # Metrics Tracking
            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, platform_name))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, platform_name))
            await status.delete()

            if sent_msg:
                del_ids = [message.id, sent_msg.id]
                asyncio.create_task(auto_delete_messages(message.chat.id, del_ids, 300))

        except Exception as e:
            logger.error(f"Social download error: {e}", exc_info=True)
            await status.edit_text(f"Download Error: `{str(e)[:120]}`")
        return

    # Telegram URL
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
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
                await status.edit_text("To download from Telegram channels, you must add a User Session via /admin first.")
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

            # Handle Album
            if target_msg.media_group_id:
                group_messages = await working_client.get_media_group(target_msg.chat.id, target_msg.id)
                downloaded_files, media_list, gif_files = [], [], []

                for idx, msg in enumerate(group_messages):
                    if has_media(msg):
                        progress_status[user_id]["start_time"] = time.time()
                        file_path = await working_client.download_media(
                            msg,
                            progress=progress_bar,
                            progress_args=(status, f"Downloading ({idx+1}/{len(group_messages)})", user_id)
                        )
                        if file_path:
                            downloaded_files.append(file_path)

                            if is_gif_message(msg):
                                gif_files.append((file_path, msg.caption or ""))
                                continue

                            cap = msg.caption.strip() if msg.caption else ""
                            if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                                media_list.append(InputMediaVideo(file_path, caption=cap))
                            elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                                media_list.append(InputMediaPhoto(file_path, caption=cap))

                sent_msgs = []
                if media_list:
                    await status.edit_text("Uploading album...")
                    sent_msgs = await client.send_media_group(chat_id=message.chat.id, media=media_list)

                if gif_files:
                    await status.edit_text("Uploading GIF(s)...")
                    for gpath, gcap in gif_files:
                        gmsg = await client.send_animation(chat_id=message.chat.id, animation=gpath, caption=gcap)
                        sent_msgs.append(gmsg)

                for path in downloaded_files:
                    if os.path.exists(path):
                        os.remove(path)

                if media_list or gif_files:
                    # Metrics Tracking
                    asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram"))
                    asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram"))
                    await status.delete()
                    del_ids = [message.id] + [m.id for m in sent_msgs]
                    asyncio.create_task(auto_delete_messages(message.chat.id, del_ids, 300))
                else:
                    await status.edit_text("No downloadable media in this album.")

            # Handle Single File
            else:
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

                progress_status[user_id]["start_time"] = time.time()
                sent_msg = None

                if is_gif:
                    sent_msg = await client.send_animation(
                        chat_id=message.chat.id, animation=file_path, caption=caption,
                        progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                    )
                elif target_msg.video:
                    sent_msg = await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        caption=caption,
                        supports_streaming=True,
                        progress=progress_bar,
                        progress_args=(status, f"Uploading {media_type}", user_id)
                    )
                elif target_msg.photo:
                    sent_msg = await client.send_photo(
                        chat_id=message.chat.id, photo=file_path, caption=caption,
                        progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                    )
                elif target_msg.document or target_msg.audio or target_msg.voice:
                    sent_msg = await client.send_document(
                        chat_id=message.chat.id, document=file_path, caption=caption,
                        progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                    )

                if os.path.exists(file_path):
                    os.remove(file_path)

                # Metrics Tracking
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram"))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram"))
                await status.delete()

                if sent_msg:
                    del_ids = [message.id, sent_msg.id]
                    asyncio.create_task(auto_delete_messages(message.chat.id, del_ids, 300))

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

    # F-Sub Check
    if data == "btn_check_fsub":
        unjoined = await check_user_fsub(client, user_id)
        if not unjoined:
            await callback_query.answer("✅ Verified! You can now use the bot.", show_alert=True)
            await callback_query.message.edit_text(
                "✅ **Verification Successful!**\n\nYou can now send any video/media link to download."
            )
        else:
            await callback_query.answer("❌ You still haven't joined all required channels!", show_alert=True)
        return

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
            "Send any supported link (Telegram, YouTube, TikTok, Instagram, Facebook) to download directly.",
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

    # Admin Verification
    if user_id != OWNER_ID:
        await callback_query.answer("Unauthorized.", show_alert=True)
        return

    # Refresh / Open Admin Dashboard
    if data in ["btn_admin_shortcut", "btn_back_admin", "btn_refresh_admin"]:
        dash_text, dash_markup = await generate_admin_dashboard()
        await callback_query.message.edit_text(dash_text, reply_markup=dash_markup)
        await callback_query.answer("Dashboard Refreshed")

    # In-Bot Session Generator Trigger
    elif data == "btn_login_account":
        admin_states[user_id] = "LOGIN_PHONE"
        await callback_query.message.reply(
            "📱 **দেশের কোডসহ ফোন নম্বর পাঠান:**\n\n**যেমন:** `+8801700000000`",
            reply_markup=ForceReply(selective=True)
        )
        await callback_query.answer()

    # Manual Session Paste Trigger
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

    # --- F-Sub Management ---
    elif data == "btn_fsub_menu":
        fsub_list = await asyncio.to_thread(sync_get_fsub_channels)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add F-Sub Channel", callback_data="btn_add_fsub")],
            [InlineKeyboardButton(f"📋 List Channels ({len(fsub_list)})", callback_data="btn_list_fsub")],
            [InlineKeyboardButton("🗑️ Remove Channel", callback_data="btn_del_fsub_menu")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="btn_back_admin")]
        ])
        await callback_query.message.edit_text(
            f"**Force Subscribe Settings**\n\nConfigured Channels: `{len(fsub_list)}`",
            reply_markup=keyboard
        )
        await callback_query.answer()

    elif data == "btn_add_fsub":
        admin_states[user_id] = "WAITING_FSUB"
        await callback_query.message.reply(
            "Send channel details in the following format:\n\n"
            "`Chat_ID | Invite_Link | Button Title`\n\n"
            "**Example:**\n`-100234567890 | https://t.me/+AbCdEf | Join Main Channel`\n\n"
            "⚠️ *Make sure the bot is an Admin in that channel.*",
            reply_markup=ForceReply(selective=True)
        )
        await callback_query.answer()

    elif data == "btn_list_fsub":
        fsub_list = await asyncio.to_thread(sync_get_fsub_channels)
        if not fsub_list:
            text = "No Force Subscribe channels configured."
        else:
            text = "**Configured F-Sub Channels:**\n\n"
            for idx, ch in enumerate(fsub_list, 1):
                text += f"{idx}. **{ch['channel_name']}**\n• Chat ID: `{ch['chat_id']}`\n• Link: {ch['invite_link']}\n\n"

        await callback_query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_fsub_menu")]])
        )
        await callback_query.answer()

    elif data == "btn_del_fsub_menu":
        fsub_list = await asyncio.to_thread(sync_get_fsub_channels)
        if not fsub_list:
            await callback_query.answer("No channels to remove.", show_alert=True)
            return

        buttons = [[InlineKeyboardButton(f"🗑️ {ch['channel_name']}", callback_data=f"delfsub_{ch['doc_id']}")] for ch in fsub_list]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_fsub_menu")])

        await callback_query.message.edit_text(
            "Select a channel to remove from Force Sub:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback_query.answer()

    elif data.startswith("delfsub_"):
        doc_id = data.split("delfsub_")[1]
        success = await asyncio.to_thread(sync_delete_fsub_channel, doc_id)
        msg = "✅ Channel removed successfully." if success else "❌ Failed to remove channel."
        await callback_query.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_fsub_menu")]])
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
    logger.info("Starting bot using native runner...")
    bot.run()