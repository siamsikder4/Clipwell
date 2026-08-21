import os
import re
import time
import json
import asyncio
import firebase_admin
from firebase_admin import credentials, firestore
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message, InputMediaVideo, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ForceReply
)
from pyrogram.errors import FloodWait
from aiohttp import web
import yt_dlp

# Configuration
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
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
        print("Firebase: Connected", flush=True)
    except Exception as e:
        print(f"Firebase Error: {e}", flush=True)

# Database Operations
def add_session_to_db(session_str: str, name: str, session_user_id: int):
    if not db:
        return False, "Database offline"
    try:
        docs = db.collection("telegram_sessions").where("session_string", "==", session_str).stream()
        if any(docs):
            return False, "Session already exists"

        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "user_id": session_user_id,
            "created_at": time.time()
        })
        # Mark user in users list
        db.collection("bot_users").document(str(session_user_id)).set({"has_session": True}, merge=True)
        return True, "Success"
    except Exception as e:
        return False, str(e)

def get_all_sessions():
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
                "account_name": data.get("account_name", "Unknown"),
                "user_id": data.get("user_id")
            })
        return sessions
    except Exception as e:
        print(f"Fetch Sessions Error: {e}", flush=True)
        return []

def get_user_sessions(user_id: int):
    if not db:
        return []
    try:
        # Owner has access to all active sessions
        if user_id == OWNER_ID:
            return get_all_sessions()
        
        docs = db.collection("telegram_sessions").where("user_id", "==", user_id).stream()
        sessions = []
        for doc in docs:
            data = doc.to_dict()
            sessions.append({
                "doc_id": doc.id,
                "session_string": data.get("session_string"),
                "account_name": data.get("account_name", "Unknown"),
                "user_id": data.get("user_id")
            })
        return sessions
    except Exception as e:
        print(f"Fetch User Sessions Error: {e}", flush=True)
        return []

def delete_session_from_db(doc_id: str):
    if not db:
        return False
    try:
        doc = db.collection("telegram_sessions").document(doc_id).get()
        if doc.exists:
            uid = doc.to_dict().get("user_id")
            db.collection("telegram_sessions").document(doc_id).delete()
            if uid:
                remaining = list(db.collection("telegram_sessions").where("user_id", "==", uid).stream())
                if not remaining and uid != OWNER_ID:
                    db.collection("bot_users").document(str(uid)).set({"has_session": False}, merge=True)
        return True
    except Exception as e:
        print(f"Delete Session Error: {e}", flush=True)
        return False

def track_user(user_id: int, username: str, name: str):
    if not db:
        return
    try:
        # Verify session presence directly from database
        sessions = get_user_sessions(user_id)
        has_session = bool(sessions) or (user_id == OWNER_ID)

        doc_ref = db.collection("bot_users").document(str(user_id))
        doc_ref.set({
            "user_id": user_id,
            "username": username or "N/A",
            "name": name,
            "has_session": has_session,
            "last_active": time.time()
        }, merge=True)
    except Exception as e:
        print(f"User Tracking Error: {e}", flush=True)

def increment_downloads(platform: str = "others"):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_stats").document("global_analytics")
        doc_ref.set({
            "total_downloads": firestore.Increment(1),
            f"count_{platform}": firestore.Increment(1)
        }, merge=True)
    except Exception as e:
        print(f"Download Metric Error: {e}", flush=True)

def get_stats():
    if not db:
        return 0, 0, 0, 0, {}
    try:
        users_docs = list(db.collection("bot_users").stream())
        sessions = get_all_sessions()
        
        session_user_ids = {s.get("user_id") for s in sessions if s.get("user_id")}
        session_user_ids.add(OWNER_ID)

        total_users = len(users_docs)
        session_users = 0

        for u in users_docs:
            u_data = u.to_dict()
            uid = u_data.get("user_id")
            if uid in session_user_ids or u_data.get("has_session", False):
                session_users += 1

        regular_users = max(0, total_users - session_users)

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

        return total_users, regular_users, session_users, total_dl, platform_counts
    except Exception as e:
        print(f"Stats Error: {e}", flush=True)
        return 0, 0, 0, 0, {}

# Progress Tracker
admin_states = {}
progress_status = {}

def format_time(seconds: int) -> str:
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"

async def progress_bar(current, total, status_msg, action_name, user_id):
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

async def handle_ping(request):
    return web.Response(text="Bot is running.")

async def auto_delete_messages(bot_client, chat_id: int, message_ids: list, delay_seconds: int = 300):
    await asyncio.sleep(delay_seconds)
    try:
        await bot_client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception:
        pass

def is_gif_message(msg):
    if not msg:
        return False
    return bool(msg.animation or (msg.document and msg.document.mime_type and "gif" in msg.document.mime_type.lower()))

def has_media(msg):
    return bool(msg and (msg.video or msg.photo or msg.document or msg.animation or msg.media_group_id))

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
    timestamp = int(time.time())
    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_{timestamp}_%(id)s.%(ext)s")
    
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'max_filesize': 1900 * 1024 * 1024,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)
        
        if not os.path.exists(file_path):
            base, _ = os.path.splitext(file_path)
            if os.path.exists(base + ".mp4"):
                file_path = base + ".mp4"

        title = info.get('title', 'Downloaded Video')
        duration = int(info.get('duration', 0) or 0)
        return file_path, title, duration

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    bot = Client("bot_instance", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

    @bot.on_message(filters.command(["start"]) & filters.private)
    async def start_cmd(client: Client, message: Message):
        user = message.from_user
        track_user(user.id, user.username, user.first_name)

        # Direct database session verification
        user_sessions = get_user_sessions(user.id)
        status_tag = "Session Active" if user_sessions else "Regular User"

        buttons = [[
            InlineKeyboardButton("Ping", callback_data="btn_ping"),
            InlineKeyboardButton("Help", callback_data="btn_help")
        ]]
        if user.id == OWNER_ID:
            buttons.append([InlineKeyboardButton("Admin Panel", callback_data="btn_admin_shortcut")])

        text = (
            f"**Hello {user.first_name},**\n"
            f"Account Status: `{status_tag}`\n\n"
            "Send any supported link to download:\n"
            "• **Supported:** YouTube, TikTok, Instagram, Facebook\n"
            "• **Telegram Links:** Requires registered session\n"
            "• Sent media auto-deletes in 5 minutes."
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    @bot.on_message(filters.command(["admin", "panel"]) & filters.private)
    async def admin_panel(client: Client, message: Message):
        if message.from_user.id != OWNER_ID:
            await message.reply_text("Access denied.")
            return

        all_sess = get_all_sessions()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Stats", callback_data="btn_stats")],
            [InlineKeyboardButton("Add Session", callback_data="btn_add_session")],
            [InlineKeyboardButton(f"Sessions ({len(all_sess)})", callback_data="btn_list_sessions")],
            [InlineKeyboardButton("Delete Session", callback_data="btn_del_menu")]
        ])

        db_status = "Online" if db else "Offline"
        text = (
            "**Admin Panel**\n\n"
            f"• DB: `{db_status}`\n"
            f"• Active Sessions: `{len(all_sess)}`"
        )
        await message.reply_text(text, reply_markup=keyboard)

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
                "1. Send video URL (YouTube, TikTok, Instagram, Facebook) to download directly.\n"
                "2. Telegram private channel links require your account session registered in the bot.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_home")]])
            )
            await callback_query.answer()
            return

        elif data == "btn_back_home":
            user_sessions = get_user_sessions(user_id)
            status_tag = "Session Active" if user_sessions else "Regular User"

            buttons = [[
                InlineKeyboardButton("Ping", callback_data="btn_ping"),
                InlineKeyboardButton("Help", callback_data="btn_help")
            ]]
            if user_id == OWNER_ID:
                buttons.append([InlineKeyboardButton("Admin Panel", callback_data="btn_admin_shortcut")])

            await callback_query.message.edit_text(
                f"Account Status: `{status_tag}`\n\n"
                "Send any supported link to download.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()
            return

        if user_id != OWNER_ID:
            await callback_query.answer("Unauthorized.", show_alert=True)
            return

        if data in ["btn_admin_shortcut", "btn_back_admin"]:
            all_sess = get_all_sessions()
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
            total_u, reg_u, sess_u, downloads, platforms = get_stats()
            all_sess = get_all_sessions()

            stats_lines = [
                "**Bot Statistics**\n",
                f"• Total Users: `{total_u}`",
                f"• Regular Users: `{reg_u}`",
                f"• Session Users: `{sess_u}`",
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
            all_sess = get_all_sessions()
            if not all_sess:
                await callback_query.message.edit_text(
                    "No active sessions found.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
                )
            else:
                lines = ["**Active Sessions:**\n"]
                for idx, s in enumerate(all_sess, 1):
                    lines.append(f"{idx}. `{s['account_name']}` (UID: `{s['user_id']}`)")
                await callback_query.message.edit_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
                )
            await callback_query.answer()

        elif data == "btn_del_menu":
            all_sess = get_all_sessions()
            if not all_sess:
                await callback_query.answer("No sessions found.", show_alert=True)
                return

            buttons = [[InlineKeyboardButton(s['account_name'], callback_data=f"del_{s['doc_id']}")] for s in all_sess]
            buttons.append([InlineKeyboardButton("Back", callback_data="btn_back_admin")])
            await callback_query.message.edit_text("Select session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
            await callback_query.answer()

        elif data.startswith("del_"):
            doc_id = data.split("del_")[1]
            success = delete_session_from_db(doc_id)
            msg = "Session deleted." if success else "Failed to delete session."
            await callback_query.message.edit_text(
                msg,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back_admin")]])
            )
            await callback_query.answer()

    @bot.on_message(filters.text & filters.private)
    async def text_handler(client: Client, message: Message):
        user_id = message.from_user.id
        text_str = message.text.strip()

        if text_str.startswith("/"):
            return

        track_user(user_id, message.from_user.username, message.from_user.first_name)

        # 1. Admin Session Input
        if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
            admin_states.pop(user_id, None)
            status_msg = await message.reply_text("Validating session...")

            test_client = Client(f"test_{time.time()}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
            try:
                await test_client.start()
                me = await test_client.get_me()
                acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
                session_owner_id = me.id  # Extract real Telegram User ID of this session
                await test_client.stop()

                success, err_msg = add_session_to_db(text_str, acc_name, session_owner_id)
                if success:
                    await status_msg.edit_text(
                        f"Session saved permanently:\n"
                        f"• Name: `{acc_name}`\n"
                        f"• Owner User ID: `{session_owner_id}`"
                    )
                else:
                    await status_msg.edit_text(f"Error: `{err_msg}`")
            except Exception as e:
                await status_msg.edit_text(f"Invalid session: `{str(e)}`")
            return

        # 2. Social Media Links (Open to Everyone)
        social_pattern = r"(https?://(?:[a-zA-Z0-9-_]+\.)*(?:youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|facebook\.com|fb\.watch)/[^\s]+)"
        social_match = re.search(social_pattern, text_str)

        if social_match:
            target_url = social_match.group(0)
            platform_name = detect_social_platform(target_url)
            status = await message.reply_text("Processing link...")

            try:
                file_path, title, duration = await asyncio.to_thread(extract_and_download_social, target_url, user_id)
                
                if not file_path or not os.path.exists(file_path):
                    await status.edit_text("Failed to download video.")
                    return

                progress_status[user_id] = {"last_time": time.time(), "start_time": time.time()}

                sent_msg = await client.send_video(
                    chat_id=message.chat.id,
                    video=file_path,
                    caption=f"**{title[:60]}**",
                    duration=duration,
                    supports_streaming=True,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Video", user_id)
                )

                if os.path.exists(file_path):
                    os.remove(file_path)

                increment_downloads(platform_name)
                await status.delete()

                if sent_msg:
                    del_ids = [message.id, sent_msg.id]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, del_ids, 300))

            except Exception as e:
                await status.edit_text(f"Download Error: `{str(e)[:100]}`")
            return

        # 3. Telegram Post Links (Strictly checked against user's session)
        private_pattern = r"t\.me/c/(\d+)/(\d+)"
        public_pattern = r"t\.me/([^/]+)/(\d+)"

        private_match = re.search(private_pattern, text_str)
        public_match = re.search(public_pattern, text_str)

        if private_match or public_match:
            user_sessions = get_user_sessions(user_id)
            if not user_sessions:
                await message.reply_text(
                    "**Access Restricted**\n\n"
                    "Telegram downloads require your own registered session ID.\n"
                    "You can download videos from YouTube, TikTok, Instagram, and Facebook freely."
                )
                return

            status = await message.reply_text("Fetching post...")

            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            target_msg = None
            working_user_client = None

            for sess in user_sessions:
                temp_client = Client(
                    f"sess_{time.time()}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=sess['session_string'],
                    in_memory=True
                )
                try:
                    await temp_client.start()
                    try:
                        await temp_client.get_chat(chat_id)
                    except Exception:
                        pass

                    msg = await temp_client.get_messages(chat_id, msg_id)
                    if has_media(msg):
                        target_msg = msg
                        working_user_client = temp_client
                        break
                    await temp_client.stop()
                except Exception:
                    if temp_client.is_connected:
                        await temp_client.stop()

            if not target_msg or not working_user_client:
                await status.edit_text("Post not found or your session account is not a member of this channel.")
                return

            progress_status[user_id] = {"last_time": time.time(), "start_time": time.time()}

            try:
                # Telegram Media Group (Album)
                if target_msg.media_group_id:
                    group_messages = await working_user_client.get_media_group(chat_id, msg_id)
                    downloaded_files, media_list, gif_files = [], [], []

                    for idx, msg in enumerate(group_messages):
                        if msg.video or msg.photo or msg.document or msg.animation:
                            progress_status[user_id]["start_time"] = time.time()
                            file_path = await working_user_client.download_media(
                                msg,
                                progress=progress_bar,
                                progress_args=(status, f"Downloading ({idx+1}/{len(group_messages)})", user_id)
                            )
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
                        increment_downloads("telegram")
                        await status.delete()
                        del_ids = [message.id] + [m.id for m in sent_msgs]
                        asyncio.create_task(auto_delete_messages(bot, message.chat.id, del_ids, 300))
                    else:
                        await status.edit_text("No downloadable media in this album.")

                # Telegram Single Media
                else:
                    is_gif = is_gif_message(target_msg)
                    caption = target_msg.caption.strip() if target_msg.caption else ""
                    media_type = "GIF" if is_gif else ("Video" if target_msg.video else ("Photo" if target_msg.photo else "Document"))

                    progress_status[user_id]["start_time"] = time.time()
                    file_path = await working_user_client.download_media(
                        target_msg,
                        progress=progress_bar,
                        progress_args=(status, f"Downloading {media_type}", user_id)
                    )

                    progress_status[user_id]["start_time"] = time.time()
                    sent_msg = None

                    if is_gif:
                        sent_msg = await client.send_animation(
                            chat_id=message.chat.id, animation=file_path, caption=caption,
                            progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                        )
                    elif target_msg.video:
                        sent_msg = await client.send_video(
                            chat_id=message.chat.id, video=file_path, caption=caption, supports_streaming=True,
                            progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                        )
                    elif target_msg.photo:
                        sent_msg = await client.send_photo(
                            chat_id=message.chat.id, photo=file_path, caption=caption,
                            progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                        )
                    elif target_msg.document:
                        sent_msg = await client.send_document(
                            chat_id=message.chat.id, document=file_path, caption=caption,
                            progress=progress_bar, progress_args=(status, f"Uploading {media_type}", user_id)
                        )

                    if os.path.exists(file_path):
                        os.remove(file_path)

                    increment_downloads("telegram")
                    await status.delete()

                    if sent_msg:
                        del_ids = [message.id, sent_msg.id]
                        asyncio.create_task(auto_delete_messages(bot, message.chat.id, del_ids, 300))

            except Exception as e:
                await status.edit_text(f"Error: `{str(e)}`")
            finally:
                if working_user_client and working_user_client.is_connected:
                    await working_user_client.stop()
            return

        await message.reply_text("Invalid link. Send YouTube, TikTok, Instagram, or Facebook URL.")

    try:
        await bot.start()
        print("Bot started successfully.", flush=True)
    except FloodWait as e:
        print(f"FloodWait: Sleeping {e.value}s", flush=True)
        await asyncio.sleep(e.value + 5)
        await bot.start()

    await idle()
    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())