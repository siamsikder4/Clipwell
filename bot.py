import os
import re
import time
import json
import asyncio
import logging
from aiohttp import web
import yt_dlp
from hydrogram import Client, filters
from hydrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ForceReply
)
from hydrogram.errors import SessionPasswordNeeded

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
        import firebase_admin
        from firebase_admin import credentials, firestore
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

def sync_get_active_urls(limit: int = 10):
    if not db:
        return []
    try:
        from firebase_admin import firestore
        docs = db.collection("submitted_urls").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [doc.to_dict() for doc in docs]
    except Exception:
        return []

def sync_increment_downloads(platform: str, count: int = 1, photos: int = 0, videos: int = 0):
    if not db or count <= 0:
        return
    try:
        from firebase_admin import firestore
        update_payload = {
            "total_downloads": firestore.Increment(count),
            f"count_{platform.lower()}": firestore.Increment(count)
        }
        if platform.lower() == "telegram":
            if photos > 0:
                update_payload["tg_photos"] = firestore.Increment(photos)
            if videos > 0:
                update_payload["tg_videos"] = firestore.Increment(videos)
        db.collection("bot_stats").document("global_analytics").set(update_payload, merge=True)
    except Exception:
        pass

def sync_increment_user_downloads(user_id: int, platform: str, count: int = 1, photos: int = 0, videos: int = 0):
    if not db or count <= 0:
        return
    try:
        from firebase_admin import firestore
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
        "⚙️ **Admin Dashboard**\n"
        "────────────────────\n"
        f"• **Database:** `{db_status}`\n"
        f"• **Active Sessions:** `{len(all_sess)}`\n"
        f"• **Total Downloads:** `{total_dl:,}`\n\n"
        "📊 **Platform Insights**\n"
        f"• **Telegram:** `{platforms.get('Telegram', 0):,}`\n"
        f"  └ 🖼️ `{tg_photos:,}`  •  🎥 `{tg_videos:,}`\n"
        f"• **YouTube:** `{platforms.get('YouTube', 0):,}`\n"
        f"• **TikTok:** `{platforms.get('TikTok', 0):,}`\n"
        f"• **Instagram:** `{platforms.get('Instagram', 0):,}`\n"
        f"• **Facebook:** `{platforms.get('Facebook', 0):,}`\n"
        f"• **Others:** `{platforms.get('Others', 0):,}`\n"
        "────────────────────"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Add via Phone (OTP)", callback_data="btn_login_account"),
            InlineKeyboardButton("➕ Paste String", callback_data="btn_add_session")
        ],
        [
            InlineKeyboardButton(f"📁 Sessions ({len(all_sess)})", callback_data="btn_list_sessions"),
            InlineKeyboardButton("🗑️ Remove Session", callback_data="btn_del_menu")
        ],
        [
            InlineKeyboardButton("🔗 Activity Log", callback_data="btn_view_urls"),
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

    if (now - last_update < 3.0) and current < total:
        return

    progress_status[user_id] = {"last_time": now}
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "▰" * filled + "▱" * (10 - filled)

    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)

    text = (
        f"⏳ **{action_name}**\n\n"
        f"`{bar}` **{percentage:.1f}%**\n"
        f"⚡ **Size:** `{curr_mb:.1f} MB / {tot_mb:.1f} MB`"
    )
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass

async def auto_delete_messages(chat_id: int, message_ids: list, delay_seconds: int = 120):
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception:
        pass

def extract_and_download_social(url: str, user_id: int):
    clean_url = url.strip()
    timestamp = int(time.time())
    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_{timestamp}_%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'concurrent_fragment_downloads': 4,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        },
        'postprocessor_args': {
            'Merger': ['-movflags', '+faststart']
        },
        'buffersize': 1024 * 1024 * 16,
        'max_filesize': 1950 * 1024 * 1024,
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
                    return file_path, str(info.get('title') or "Media Video")
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

    # Admin Panel
    if text_str.startswith("/admin") or text_str.startswith("/panel"):
        if user_id != OWNER_ID:
            await message.reply_text("⛔ **Access restricted.** Admin only.")
            return
        dash_text, dash_markup = await generate_admin_dashboard()
        await message.reply_text(dash_text, reply_markup=dash_markup)
        return

    # Waiting Session String
    if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
        admin_states.pop(user_id, None)
        status_msg = await message.reply_text("🔄 Verifying session string...")

        test_client = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
        try:
            await test_client.start()
            me = await test_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await test_client.stop()

            success, err_msg = await asyncio.to_thread(sync_add_session, text_str, acc_name)
            if success:
                asyncio.create_task(init_session_pool())
                await status_msg.edit_text(f"✅ **Session saved successfully!**\n\n👤 **Account:** `{acc_name}`")
            else:
                await status_msg.edit_text(f"⚠️ **Database Error:** `{err_msg}`")
        except Exception as e:
            await status_msg.edit_text(f"❌ **Invalid Session String:**\n`{str(e)}`")
        return

    # Login Phone
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_PHONE":
        admin_states.pop(user_id, None)
        phone_number = text_str.replace(" ", "").strip()
        status_msg = await message.reply_text("⏳ Requesting Telegram OTP code...")

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
                "📩 **Enter the OTP code received:**\n\n"
                "💡 *Tip:* Put a space between digits (e.g. `1 2 3 4 5`).",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            await status_msg.edit_text(f"❌ **Failed to send OTP:**\n`{str(e)}`")
        return

    # Login OTP
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_OTP":
        otp_code = text_str.replace(" ", "").replace("-", "").strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("⚠️ **Session expired.** Please restart login.")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ Verifying code...")

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
            await status_msg.edit_text(f"✅ **Account Connected!**\n\n👤 **Name:** `{acc_name}`")
        except SessionPasswordNeeded:
            admin_states[user_id] = "LOGIN_2FA"
            await status_msg.edit_text("🔐 **Two-Step Verification Enabled**\n\nPlease reply with your 2FA password:", reply_markup=ForceReply(selective=True))
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ **Login Failed:**\n`{str(e)}`")
        return

    # Login 2FA
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_2FA":
        password = text_str.strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("⚠️ **Session expired.** Please restart login.")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ Checking password...")

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
            await status_msg.edit_text(f"✅ **Account Connected!**\n\n👤 **Name:** `{acc_name}`")
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ **2FA Verification Failed:**\n`{str(e)}`")
        return

    # Start Command
    if text_str.startswith("/start"):
        buttons = [[
            InlineKeyboardButton("⚡ Ping", callback_data="btn_ping"),
            InlineKeyboardButton("📖 Help", callback_data="btn_help")
        ]]
        if user_id == OWNER_ID:
            buttons.append([InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="btn_admin_shortcut")])

        text = (
            f"👋 **Hello {user.first_name}!**\n\n"
            "Send any supported link to download media directly:\n\n"
            "• **Telegram:** Public / Restricted post links\n"
            "• **Socials:** YouTube, TikTok, Instagram, Facebook\n\n"
            "⏱️ *Telegram media files auto-delete in 2 minutes.*"
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ----------------- TELEGRAM POST DOWNLOADER ----------------- #
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
        tg_url = text_str.strip()
        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, tg_url, "telegram"))

        status = await message.reply_text("⚡ **Connecting to Telegram...**")

        try:
            if not loaded_user_clients:
                await init_session_pool()

            if not loaded_user_clients:
                await status.edit_text("❌ **No active sessions found.** Please add one in `/admin`.")
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
                    msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=6)
                    if msg and not msg.empty:
                        target_msg = msg
                        working_client = u_client
                        break
                except Exception:
                    continue

            if not target_msg or target_msg.empty:
                await status.edit_text("❌ **Post unavailable.** Ensure the connected account has access to this chat/channel.")
                return

            if not (target_msg.media or target_msg.video or target_msg.photo or target_msg.document or target_msg.audio):
                if target_msg.text:
                    await status.edit_text(f"📝 **Text Content:**\n\n{target_msg.text}")
                else:
                    await status.edit_text("⚠️ **No downloadable media found in this message.**")
                return

            await status.edit_text("📥 **Fetching media file...**")
            download_path = await working_client.download_media(
                target_msg,
                progress=progress_bar,
                progress_args=(status, "Downloading from Telegram", user_id)
            )

            if not download_path or not os.path.exists(download_path):
                await status.edit_text("❌ **Failed to download media.**")
                return

            await status.edit_text("📤 **Uploading to Telegram...**")

            delete_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Delete Now", callback_data="btn_delete_this")
            ]])

            caption_header = f"**{target_msg.caption}**\n\n" if target_msg.caption else ""
            caption = f"{caption_header}⏱️ *Auto-deletes in 2 minutes.*"
            sent_msg = None

            if target_msg.video:
                sent_msg = await message.reply_video(
                    video=download_path,
                    caption=caption,
                    supports_streaming=True,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Video", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 0, 1))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, 0, 1))
            elif target_msg.photo:
                sent_msg = await message.reply_photo(
                    photo=download_path,
                    caption=caption,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Photo", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 1, 0))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, 1, 0))
            else:
                sent_msg = await message.reply_document(
                    document=download_path,
                    caption=caption,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Document", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 0, 0))

            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id, status.id, message.id], delay_seconds=120))
            else:
                await status.delete()

            if os.path.exists(download_path):
                os.remove(download_path)

        except Exception as e:
            logger.error(f"Telegram Download Error: {e}")
            await status.edit_text(f"❌ **Error:** `{str(e)}`")
        return

    # ----------------- SOCIAL MEDIA DOWNLOADER ----------------- #
    url_pattern = re.search(r'(https?://[^\s]+)', text_str)
    if url_pattern:
        url = url_pattern.group(0).strip()
        status = await message.reply_text("⚡ **Analyzing video link...**")

        platform = "Others"
        if "youtu" in url:
            platform = "YouTube"
        elif "tiktok" in url:
            platform = "TikTok"
        elif "instagram" in url:
            platform = "Instagram"
        elif "facebook" in url or "fb.watch" in url:
            platform = "Facebook"

        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, url, platform))

        file_path, title = await asyncio.to_thread(extract_and_download_social, url, user_id)

        if not file_path or not os.path.exists(file_path):
            await status.edit_text("❌ **Download Failed.** Link might be private, geo-blocked, or unsupported.")
            return

        try:
            await status.edit_text("📤 **Uploading video...**")
            caption = f"🎬 **{title}**\n\n📌 **Source:** `{platform}`"
            
            await message.reply_video(
                video=file_path,
                caption=caption,
                supports_streaming=True,
                progress=progress_bar,
                progress_args=(status, f"Uploading ({platform})", user_id)
            )

            await status.delete()
            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, platform, 1))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, platform, 1))
        except Exception as e:
            logger.error(f"Social Upload Error: {e}")
            await status.edit_text(f"❌ **Upload Failed:** `{str(e)}`")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

# ----------------- CALLBACK QUERY HANDLER ----------------- #

@bot.on_callback_query()
async def callback_query_handler(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data == "btn_delete_this":
        try:
            await query.message.delete()
        except Exception:
            await query.answer("Couldn't delete message.", show_alert=False)
        return

    if data == "btn_ping":
        await query.answer("⚡ Bot is running smoothly!", show_alert=True)
        return

    if data == "btn_help":
        help_text = (
            "📖 **User Guide**\n"
            "────────────────────\n"
            "• **Telegram:** Send any post link (`t.me/c/...` or `t.me/...`). Files auto-delete in 2 minutes.\n"
            "• **Social Media:** Send links from YouTube, TikTok, Instagram, or Facebook.\n"
            "• **Admin:** Manage account sessions with `/admin`."
        )
        await query.message.edit_text(
            help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_start_back")]])
        )
        return

    if data == "btn_start_back":
        buttons = [[
            InlineKeyboardButton("⚡ Ping", callback_data="btn_ping"),
            InlineKeyboardButton("📖 Help", callback_data="btn_help")
        ]]
        if user_id == OWNER_ID:
            buttons.append([InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="btn_admin_shortcut")])
        
        text = (
            f"👋 **Hello {query.from_user.first_name}!**\n\n"
            "Send any supported link to download media directly:\n\n"
            "• **Telegram:** Public / Restricted post links\n"
            "• **Socials:** YouTube, TikTok, Instagram, Facebook\n\n"
            "⏱️ *Telegram media files auto-delete in 2 minutes.*"
        )
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if user_id != OWNER_ID:
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    if data in ["btn_admin_shortcut", "btn_refresh_admin"]:
        text, markup = await generate_admin_dashboard()
        await query.message.edit_text(text, reply_markup=markup)
        return

    if data == "btn_add_session":
        admin_states[user_id] = "WAITING_SESSION"
        await query.message.reply_text(
            "📝 **Send your Pyrogram/Hydrogram Session String:**",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()
        return

    if data == "btn_login_account":
        admin_states[user_id] = "LOGIN_PHONE"
        await query.message.reply_text(
            "📱 **Send phone number with country code** (e.g. `+8801XXXXXXXXX`):",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()
        return

    if data == "btn_list_sessions":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("No active sessions found.", show_alert=True)
            return
        text = "📁 **Active Sessions**\n────────────────────\n"
        for idx, s in enumerate(sessions, 1):
            text += f"`{idx}.` **{s['account_name']}**\n    ID: `{s['doc_id'][:8]}`\n"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_admin")]]))
        return

    if data == "btn_del_menu":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("No sessions available to delete.", show_alert=True)
            return
        buttons = []
        for s in sessions:
            buttons.append([InlineKeyboardButton(f"❌ {s['account_name']}", callback_data=f"del_{s['doc_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_admin")])
        await query.message.edit_text("Select an account session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("del_"):
        doc_id = data.split("_", 1)[1]
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("Session removed successfully.", show_alert=True)
        text, markup = await generate_admin_dashboard()
        await query.message.edit_text(text, reply_markup=markup)
        return

    if data == "btn_view_urls":
        urls = await asyncio.to_thread(sync_get_active_urls, 8)
        if not urls:
            await query.answer("No recent requests logged.", show_alert=True)
            return
        text = "🔗 **Recent Activity**\n────────────────────\n"
        for u in urls:
            text += f"• **{u.get('platform', 'Link')}:** `{u.get('url')[:38]}...`\n  └ User: `{u.get('user_name')}`\n"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_admin")]]))
        return

# ----------------- AIOHTTP DUMMY WEB SERVER ----------------- #

async def web_server():
    async def handle_ping(request):
        return web.Response(text="Bot is running!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Aiohttp web server running on port {PORT}")

# ----------------- BOT STARTUP ----------------- #

async def main():
    await bot.start()
    logger.info("Bot started successfully!")
    await init_session_pool()
    await web_server()
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())