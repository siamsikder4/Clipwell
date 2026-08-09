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

# Credentials & Constants
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()
OWNER_ID = 6142774415  # Admin Telegram ID
PORT = int(os.environ.get("PORT", "8080"))

# Initialize Firebase Database
db = None
if FIREBASE_KEY_RAW:
    try:
        cred_dict = json.loads(FIREBASE_KEY_RAW)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print(">>> FIREBASE CONNECTED SUCCESSFULLY <<<", flush=True)
    except Exception as e:
        print(f"❌ Firebase Connection Error: {e}", flush=True)

# Firebase Helper Functions
def add_session_to_db(session_str: str, name: str):
    if not db:
        return False, "Firebase is not connected!"
    try:
        docs = db.collection("telegram_sessions").where("session_string", "==", session_str).stream()
        if any(docs):
            return False, "Session already exists in Firebase!"

        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "created_at": time.time()
        })
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
                "account_name": data.get("account_name", "Unknown Account")
            })
        return sessions
    except Exception as e:
        print(f"Error fetching sessions: {e}", flush=True)
        return []

def delete_session_from_db(doc_id: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").document(doc_id).delete()
        return True
    except Exception as e:
        print(f"Error deleting session: {e}", flush=True)
        return False

# Analytics & User Tracking Functions
def track_user_in_db(user_id: int, username: str, name: str):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        doc_ref.set({
            "user_id": user_id,
            "username": username or "N/A",
            "name": name,
            "last_active": time.time()
        }, merge=True)
    except Exception as e:
        print(f"User track error: {e}", flush=True)

def increment_download_count():
    if not db:
        return
    try:
        doc_ref = db.collection("bot_stats").document("global_analytics")
        doc_ref.set({"total_downloads": firestore.Increment(1)}, merge=True)
    except Exception as e:
        print(f"Download stat error: {e}", flush=True)

def get_analytics_stats():
    if not db:
        return 0, 0
    try:
        users_docs = db.collection("bot_users").stream()
        total_users = sum(1 for _ in users_docs)

        stat_doc = db.collection("bot_stats").document("global_analytics").get()
        total_downloads = stat_doc.to_dict().get("total_downloads", 0) if stat_doc.exists else 0

        return total_users, total_downloads
    except Exception as e:
        print(f"Analytics fetch error: {e}", flush=True)
        return 0, 0

# UI & State Trackers
admin_states = {}
last_update_time = {}

# Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell High-Speed Engine Active!")

# Auto-delete helper (5 minutes = 300 seconds)
async def auto_delete_messages(bot_client, chat_id: int, message_ids: list, delay_seconds: int = 300):
    await asyncio.sleep(delay_seconds)
    try:
        await bot_client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception as e:
        print(f"Auto delete error: {e}", flush=True)

# --- GIF Detection Helper -------------------------------------------------
# A "GIF" on Telegram can arrive either as a native `animation` object, or
# as a plain `document` whose mime_type is image/gif (common when users
# forward .gif files instead of letting Telegram auto-convert them).
def is_gif_message(msg):
    if not msg:
        return False
    if msg.animation:
        return True
    if msg.document and msg.document.mime_type and "gif" in msg.document.mime_type.lower():
        return True
    return False

def has_downloadable_media(msg):
    return bool(
        msg and (msg.video or msg.photo or msg.document or msg.animation or msg.media_group_id)
    )

# Aesthetic Progress Bar Callback
async def progress_bar(current, total, status_msg, action_name, user_id):
    now = time.time()
    last_time = last_update_time.get(user_id, 0)

    if (now - last_time < 2.5) and current < total:
        return

    last_update_time[user_id] = now
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "█" * filled + "░" * (10 - filled)

    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)

    text = (
        f"╭─ ⚡ **{action_name.upper()}** ⚡\n"
        f"│\n"
        f"├ 📊 `[{bar}] {percentage:.1f}%`\n"
        f"├ 📁 `📦 {curr_mb:.1f} MB / {tot_mb:.1f} MB`\n"
        f"╰──────────────────────────"
    )
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    bot = Client(
        "bot_instance",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )

    # Command: /start
    @bot.on_message(filters.command(["start"]) & filters.private)
    async def start_cmd(client: Client, message: Message):
        user = message.from_user
        track_user_in_db(user.id, user.username, user.first_name)

        text = (
            "╭─ 🚀 **CLIPWELL DOWNLOADER** 🚀\n"
            "│\n"
            "├ 📥 Send any **Public** or **Private** Telegram link.\n"
            "├ 🎞️ Supports Video, Photo, Document & **GIF**.\n"
            "├ ⚡ Fast download with live progress bar.\n"
            "├ ⏳ **Auto-Delete:** Links & media auto-delete after 5 minutes.\n"
            "╰──────────────────────────"
        )
        await message.reply_text(text)

    # Command: /admin (Admin Control Panel)
    @bot.on_message(filters.command(["admin", "panel"]) & filters.private)
    async def admin_panel(client: Client, message: Message):
        if message.from_user.id != OWNER_ID:
            await message.reply_text("❌ **Access Denied:** You are not authorized.")
            return

        all_sess = get_all_sessions()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Analytics & Stats", callback_data="btn_stats")],
            [InlineKeyboardButton("➕ Add New Session", callback_data="btn_add_session")],
            [InlineKeyboardButton(f"📋 View All Sessions ({len(all_sess)})", callback_data="btn_list_sessions")],
            [InlineKeyboardButton("🗑️ Remove Session", callback_data="btn_del_menu")]
        ])

        db_status = "🟢 Connected" if db else "🔴 Not Configured"
        text = (
            "╭─ ⚙️ **ADMIN CONTROL PANEL** ⚙️\n"
            "│\n"
            f"├ 💾 **Firebase DB:** `{db_status}`\n"
            f"├ 🟢 **Active Sessions:** `{len(all_sess)}`\n"
            "├ Select an option below to manage.\n"
            "╰──────────────────────────"
        )
        await message.reply_text(text, reply_markup=keyboard)

    # Callback Query Handler
    @bot.on_callback_query()
    async def callback_handler(client: Client, callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        if user_id != OWNER_ID:
            await callback_query.answer("❌ Unauthorized!", show_alert=True)
            return

        data = callback_query.data

        if data == "btn_stats":
            total_users, total_downloads = get_analytics_stats()
            all_sess = get_all_sessions()

            text = (
                "╭─ 📊 **BOT REAL-TIME ANALYTICS** 📊\n"
                "│\n"
                f"├ 👥 **Total Users:** `{total_users}`\n"
                f"├ 📥 **Total Downloads:** `{total_downloads}`\n"
                f"├ 🔑 **Active Sessions:** `{len(all_sess)}`\n"
                f"├ 💾 **Firebase DB:** `Connected`\n"
                "╰──────────────────────────"
            )
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="btn_back_admin")]])
            await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_add_session":
            admin_states[user_id] = "WAITING_SESSION"
            await callback_query.message.reply(
                "╭─ ➕ **ADD SESSION STRING** ➕\n"
                "│\n"
                "├ Please **reply to this message** with your Pyrogram `SESSION_STRING`:\n"
                "╰──────────────────────────",
                reply_markup=ForceReply(selective=True)
            )
            await callback_query.answer()

        elif data == "btn_list_sessions":
            all_sess = get_all_sessions()
            if not all_sess:
                await callback_query.message.edit_text("ℹ️ **No active sessions found in Firebase DB!**")
            else:
                text = f"╭─ 📋 **FIREBASE SESSIONS ({len(all_sess)})** 📋\n│\n"
                for idx, s in enumerate(all_sess, 1):
                    text += f"├ **#{idx} Account:** `{s['account_name']}`\n"
                    text += f"│  🔑 **Doc ID:** `{s['doc_id']}`\n│\n"
                text += "╰──────────────────────────"

                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="btn_back_admin")]])
                await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_back_admin":
            all_sess = get_all_sessions()
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Analytics & Stats", callback_data="btn_stats")],
                [InlineKeyboardButton("➕ Add New Session", callback_data="btn_add_session")],
                [InlineKeyboardButton(f"📋 View All Sessions ({len(all_sess)})", callback_data="btn_list_sessions")],
                [InlineKeyboardButton("🗑️ Remove Session", callback_data="btn_del_menu")]
            ])
            db_status = "🟢 Connected" if db else "🔴 Not Configured"
            text = (
                "╭─ ⚙️ **ADMIN CONTROL PANEL** ⚙️\n"
                "│\n"
                f"├ 💾 **Firebase DB:** `{db_status}`\n"
                f"├ 🟢 **Active Sessions:** `{len(all_sess)}`\n"
                "├ Select an option below to manage.\n"
                "╰──────────────────────────"
            )
            await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_del_menu":
            all_sess = get_all_sessions()
            if not all_sess:
                await callback_query.answer("No sessions found in Firebase!", show_alert=True)
                return

            buttons = []
            for s in all_sess:
                buttons.append([InlineKeyboardButton(f"❌ {s['account_name']}", callback_data=f"del_{s['doc_id']}")])
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_back_admin")])

            await callback_query.message.edit_text(
                "╭─ 🗑️ **DELETE SESSION FROM FIREBASE** 🗑️\n│\n├ Select the account to remove:\n╰──────────────────────────",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()

        elif data.startswith("del_"):
            doc_id = data.split("del_")[1]
            success = delete_session_from_db(doc_id)
            if success:
                await callback_query.message.edit_text("✅ **Successfully deleted session from Firebase!**")
            else:
                await callback_query.message.edit_text("❌ **Failed to delete session.**")
            await callback_query.answer()

    # Text Input & Link Handler
    @bot.on_message(filters.text & filters.private)
    async def text_handler(client: Client, message: Message):
        user_id = message.from_user.id
        text_str = message.text.strip()

        if text_str.startswith("/"):
            return

        track_user_in_db(user_id, message.from_user.username, message.from_user.first_name)

        # 1. Handle Admin Session Input
        if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
            admin_states.pop(user_id, None)
            status_msg = await message.reply_text("⏳ **Validating session string with Telegram...**")

            test_client = Client(f"test_{time.time()}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
            try:
                await test_client.start()
                me = await test_client.get_me()
                acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
                await test_client.stop()

                success, err_msg = add_session_to_db(text_str, acc_name)
                if success:
                    await status_msg.edit_text(f"🎉 **Session saved permanently to Firebase!**\n👤 **Account:** `{acc_name}`")
                else:
                    await status_msg.edit_text(f"⚠️ **Save Error:** `{err_msg}`")
            except Exception as e:
                await status_msg.edit_text(f"❌ **Invalid Session String!**\nError: `{str(e)}`")
            return

        # 2. Process Telegram Links
        private_pattern = r"t\.me/c/(\d+)/(\d+)"
        public_pattern = r"t\.me/([^/]+)/(\d+)"

        private_match = re.search(private_pattern, text_str)
        public_match = re.search(public_pattern, text_str)

        if not (private_match or public_match):
            await message.reply_text("⚠️ **Invalid Link:** Please send a valid Telegram post link.")
            return

        active_sessions = get_all_sessions()
        if not active_sessions:
            await message.reply_text("⚠️ **Bot Error:** No active user sessions found in Firebase. Add sessions via `/admin`.")
            return

        status = await message.reply_text("🔍 **Checking post link across active Firebase sessions...**")

        if private_match:
            chat_id = int("-100" + private_match.group(1))
            msg_id = int(private_match.group(2))
        else:
            chat_id = public_match.group(1)
            msg_id = int(public_match.group(2))

        target_msg = None
        working_user_client = None

        for sess in active_sessions:
            temp_client = Client(
                f"sess_run_{time.time()}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=sess['session_string'],
                in_memory=True
            )
            try:
                await temp_client.start()

                try:
                    await temp_client.get_chat(chat_id)
                except Exception as chat_err:
                    print(f"Chat resolve notice for {sess['account_name']}: {chat_err}", flush=True)

                msg = await temp_client.get_messages(chat_id, msg_id)
                if has_downloadable_media(msg):
                    target_msg = msg
                    working_user_client = temp_client
                    break
                else:
                    await temp_client.stop()
            except Exception as e:
                print(f"Session {sess['account_name']} error: {e}", flush=True)
                if temp_client.is_connected:
                    await temp_client.stop()

        if not target_msg or not working_user_client:
            await status.edit_text(
                "❌ **Post Not Found!**\n"
                "Make sure at least one connected account in Firebase sessions is an active **member** of that private channel."
            )
            return

        try:
            caption_note = "⏳ *This media and link will auto-delete in 5 minutes.*"

            # Case 1: Album
            if target_msg.media_group_id:
                group_messages = await working_user_client.get_media_group(chat_id, msg_id)
                downloaded_files = []
                media_list = []
                gif_files = []  # GIFs can't go inside sendMediaGroup, sent separately below

                for idx, msg in enumerate(group_messages):
                    if msg.video or msg.photo or msg.document or msg.animation:
                        file_path = await working_user_client.download_media(
                            msg,
                            progress=progress_bar,
                            progress_args=(status, f"Downloading album ({idx+1}/{len(group_messages)})", user_id)
                        )
                        downloaded_files.append(file_path)

                        if is_gif_message(msg):
                            gif_files.append(file_path)
                            continue

                        cap = caption_note if (len(media_list) == 0 and not gif_files) else ""
                        if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                            media_list.append(InputMediaVideo(file_path, caption=cap))
                        elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                            media_list.append(InputMediaPhoto(file_path, caption=cap))

                sent_msgs = []
                if media_list:
                    await status.edit_text("⬆️ **Uploading album...**")
                    sent_msgs = await client.send_media_group(chat_id=message.chat.id, media=media_list)

                if gif_files:
                    await status.edit_text("⬆️ **Uploading GIF(s)...**")
                    for g_idx, gpath in enumerate(gif_files):
                        gcap = caption_note if (not media_list and g_idx == 0) else ""
                        gif_msg = await client.send_animation(
                            chat_id=message.chat.id,
                            animation=gpath,
                            caption=gcap
                        )
                        sent_msgs.append(gif_msg)

                for path in downloaded_files:
                    if os.path.exists(path):
                        os.remove(path)

                if media_list or gif_files:
                    increment_download_count()
                    await status.delete()

                    # Schedule 5-minute Auto-Delete (Deletes User Link + Sent Album/GIFs)
                    delete_msg_ids = [message.id] + [m.id for m in sent_msgs]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=300))
                else:
                    await status.edit_text("❌ **No downloadable media found in this album.**")

            # Case 2: Single Media (Video / Photo / Document / GIF)
            else:
                is_gif = is_gif_message(target_msg)

                file_path = await working_user_client.download_media(
                    target_msg,
                    progress=progress_bar,
                    progress_args=(status, "Downloading GIF" if is_gif else "Downloading media", user_id)
                )

                last_update_time[user_id] = 0

                sent_msg = None
                if is_gif:
                    sent_msg = await client.send_animation(
                        chat_id=message.chat.id,
                        animation=file_path,
                        caption=caption_note,
                        progress=progress_bar,
                        progress_args=(status, "Uploading GIF", user_id)
                    )
                elif target_msg.video:
                    sent_msg = await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        caption=caption_note,
                        supports_streaming=True,
                        progress=progress_bar,
                        progress_args=(status, "Uploading video", user_id)
                    )
                elif target_msg.photo:
                    sent_msg = await client.send_photo(chat_id=message.chat.id, photo=file_path, caption=caption_note)
                elif target_msg.document:
                    sent_msg = await client.send_document(
                        chat_id=message.chat.id,
                        document=file_path,
                        caption=caption_note,
                        progress=progress_bar,
                        progress_args=(status, "Uploading document", user_id)
                    )

                if os.path.exists(file_path):
                    os.remove(file_path)

                increment_download_count()
                await status.delete()

                # Schedule 5-minute Auto-Delete (Deletes User Link + Sent Media)
                if sent_msg:
                    delete_msg_ids = [message.id, sent_msg.id]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=300))

        except Exception as e:
            await status.edit_text(f"❌ **Error:** `{str(e)}`")
        finally:
            if working_user_client and working_user_client.is_connected:
                await working_user_client.stop()

    # Start Main Bot
    try:
        await bot.start()
        print(">>> CLIPWELL FAST DOWNLOADER STARTED SUCCESSFULLY <<<", flush=True)
    except FloodWait as e:
        print(f"⚠️ Telegram FloodWait detected! Waiting for {e.value} seconds...", flush=True)
        await asyncio.sleep(e.value + 5)
        await bot.start()
        print(">>> CLIPWELL FAST DOWNLOADER STARTED SUCCESSFULLY AFTER FLOODWAIT <<<", flush=True)

    await idle()

    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())