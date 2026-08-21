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
from hydrogram.errors import FloodWait
import firebase_admin
from firebase_admin import credentials, firestore

# Auto Setup FFmpeg for audio-video merging
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()
OWNER_ID = 6142774415
PORT = int(os.environ.get("PORT", "8080"))

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Purge any stuck webhooks
if BOT_TOKEN:
    try:
        purge_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
        req = urllib.request.urlopen(purge_url, timeout=10)
        logger.info(f"Webhook Purge: {req.read().decode('utf-8')}")
    except Exception as e:
        logger.warning(f"Webhook Warning: {e}")

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

# Database Helpers
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

def sync_track_user(user_id: int, username: str, name: str):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        doc_ref.set({
            "user_id": int(user_id),
            "username": username or "N/A",
            "name": name,
            "last_active": time.time()
        }, merge=True)
    except Exception as e:
        logger.error(f"Tracking Error: {e}")

def sync_increment_downloads(platform: str):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_stats").document("global_analytics")
        doc_ref.set({
            "total_downloads": firestore.Increment(1),
            f"count_{platform}": firestore.Increment(1)
        }, merge=True)
    except Exception as e:
        logger.error(f"Metric Error: {e}")

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

admin_states = {}
progress_status = {}

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

# Facebook Audio+Video Guaranteed Extractor
def extract_and_download_social(url: str, user_id: int):
    timestamp = int(time.time())
    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_{timestamp}_%(id)s.%(ext)s")

    # Priority to combined format (audio+video together) to prevent GIF behavior
    ydl_opts = {
        'format': 'best[ext=mp4][vcodec!=none][acodec!=none]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
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
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None, None, 0, 0, 0

        # Resolve Final File Path
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

# 1. /start Command Handler
@bot.on_message(filters.command(["start"]) & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    logger.info(f"Received /start from {user_id} ({user.first_name})")
    
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
    asyncio.create_task(asyncio.to_thread(sync_track_user, user_id, user.username, user.first_name))

# 2. /admin Command Handler
@bot.on_message(filters.command(["admin", "panel"]) & filters.private)
async def admin_handler(client: Client, message: Message):
    user = message.from_user
    if not user or user.id != OWNER_ID:
        await message.reply_text("Access denied.")
        return

    all_sess = await asyncio.to_thread(sync_get_all_sessions)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Stats", callback_data="btn_stats")],
        [InlineKeyboardButton("Add Session", callback_data="btn_add_session")],
        [InlineKeyboardButton(f"Sessions ({len(all_sess)})", callback_data="btn_list_sessions")],
        [InlineKeyboardButton("Delete Session", callback_data="btn_del_menu")]
    ])

    db_status = "Online" if db else "Offline"
    text = f"**Admin Panel**\n\n• DB: `{db_status}`\n• Active Sessions: `{len(all_sess)}`"
    await message.reply_text(text, reply_markup=keyboard)

# 3. Message Router (Social Media + Telegram Links)
@bot.on_message(filters.text & filters.private)
async def message_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    text_str = message.text.strip()
    
    if text_str.startswith("/"):
        return

    logger.info(f"Processing message from {user_id}: {text_str}")
    asyncio.create_task(asyncio.to_thread(sync_track_user, user_id, user.username, user.first_name))

    # A. Admin Session Input
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
                await status_msg.edit_text(f"Session saved: `{acc_name}`")
            else:
                await status_msg.edit_text(f"Error: `{err_msg}`")
        except Exception as e:
            await status_msg.edit_text(f"Invalid session: `{str(e)}`")
        return

    # B. Social Media Link Matcher
    social_pattern = r"(https?://(?:[a-zA-Z0-9-_]+\.)*(?:youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|facebook\.com|fb\.watch)/[^\s]+)"
    social_match = re.search(social_pattern, text_str)

    if social_match:
        target_url = social_match.group(0)
        platform_name = detect_social_platform(target_url)
        status = await message.reply_text("Processing link...")

        try:
            file_path, title, duration, width, height = await asyncio.to_thread(
                extract_and_download_social, target_url, user_id
            )

            if not file_path or not os.path.exists(file_path):
                await status.edit_text("Failed to download video. Link might be private, broken, or unsupported.")
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

            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, platform_name))
            await status.delete()

            if sent_msg:
                del_ids = [message.id, sent_msg.id]
                asyncio.create_task(auto_delete_messages(message.chat.id, del_ids, 300))

        except Exception as e:
            logger.error(f"Social download error: {e}", exc_info=True)
            await status.edit_text(f"Download Error: `{str(e)[:120]}`")
        return

    # C. Telegram Link Matcher
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
        status = await message.reply_text("Fetching post...")
        target_msg = None
        working_client = None
        is_temp_client = False

        try:
            # Step 1: Try public fetch directly with bot
            if public_match:
                chat_username = public_match.group(1)
                msg_id = int(public_match.group(2))
                try:
                    msg = await bot.get_messages(chat_username, msg_id)
                    if has_media(msg):
                        target_msg = msg
                        working_client = bot
                        is_temp_client = False
                except Exception as e:
                    logger.info(f"Direct bot fetch bypass: {e}")

            # Step 2: Try connected User Sessions for private / restricted channels
            if not target_msg:
                active_sessions = await asyncio.to_thread(sync_get_all_sessions)
                if not active_sessions:
                    await status.edit_text("This post requires a User Session. Add via /admin.")
                    return

                if private_match:
                    chat_id = int("-100" + private_match.group(1))
                    msg_id = int(private_match.group(2))
                else:
                    chat_id = public_match.group(1)
                    msg_id = int(public_match.group(2))

                for sess in active_sessions:
                    temp_client = Client(
                        f"sess_{int(time.time()*1000)}",
                        api_id=API_ID,
                        api_hash=API_HASH,
                        session_string=sess['session_string'],
                        in_memory=True
                    )
                    try:
                        await asyncio.wait_for(temp_client.start(), timeout=10)
                        msg = await asyncio.wait_for(temp_client.get_messages(chat_id, msg_id), timeout=10)
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
                await status.edit_text("Media not found. Make sure the link is correct and the connected session account has joined this channel.")
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
                    asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram"))
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

                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram"))
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

# 4. Callback Query Handler
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

    if user_id != OWNER_ID:
        await callback_query.answer("Unauthorized.", show_alert=True)
        return

    if data in ["btn_admin_shortcut", "btn_back_admin"]:
        all_sess = await asyncio.to_thread(sync_get_all_sessions)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Stats", callback_data="btn_stats")],
            [InlineKeyboardButton("Add Session", callback_data="btn_add_session")],
            [InlineKeyboardButton(f"Sessions ({len(all_sess)})", callback_data="btn_list_sessions")],
            [InlineKeyboardButton("Delete Session", callback_data="btn_del_menu")]
        ])
        db_status = "Online" if db else "Offline"
        await callback_query.message.edit_text(
            f"**Admin Panel**\n\n• DB: `{db_status}`\n• Sessions: `{len(all_sess)}`",
            reply_markup=keyboard
        )
        await callback_query.answer()

    elif data == "btn_stats":
        users, downloads, platforms = await asyncio.to_thread(sync_get_stats)
        all_sess = await asyncio.to_thread(sync_get_all_sessions)

        stats_lines = [
            "**Bot Statistics**\n",
            f"• Total Users: `{users}`",
            f"• Total Downloads: `{downloads}`",
            f"• Active Sessions: `{len(all_sess)}`\n",
            "**Platform Breakdown:**"
        ]
        for p_name, p_count in platforms.items():
            stats_lines.append(f"• {p_name}: `{p_count}`")

        await callback_query.message.edit_text(
            "\n".join(stats_lines),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
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
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
            )
        else:
            lines = ["**Active Sessions:**\n"]
            for idx, s in enumerate(all_sess, 1):
                lines.append(f"{idx}. `{s['account_name']}` (ID: `{s['doc_id']}`)")
            await callback_query.message.edit_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
            )
        await callback_query.answer()

    elif data == "btn_del_menu":
        all_sess = await asyncio.to_thread(sync_get_all_sessions)
        if not all_sess:
            await callback_query.answer("No sessions found.", show_alert=True)
            return

        buttons = [[InlineKeyboardButton(s['account_name'], callback_data=f"del_{s['doc_id']}")] for s in all_sess]
        buttons.append([InlineKeyboardButton("Back", callback_data="btn_back_admin")])
        await callback_query.message.edit_text("Select session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
        await callback_query.answer()

    elif data.startswith("del_"):
        doc_id = data.split("del_")[1]
        success = await asyncio.to_thread(sync_delete_session, doc_id)
        msg = "Session deleted." if success else "Failed to delete session."
        await callback_query.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
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
    logger.info("Telegram Bot Starting...")
    bot.run()