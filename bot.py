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
from aiohttp import web

# Environment Credentials
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

# Firebase Database Helper Functions
def add_session_to_db(session_str: str, name: str):
    if not db:
        return False, "Firebase is not configured!"
    try:
        # Check if session already exists
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
        print(f"Error fetching sessions from Firebase: {e}", flush=True)
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

# Tracker for UI updates
admin_states = {}
last_update_time = {}

# Render Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell Firebase Engine Active!")

# Aesthetic Progress Bar Callback
async def progress_bar(current, total, status_msg, action_name, user_id):
    now = time.time()
    last_time = last_update_time.get(user_id, 0)
    
    if (now - last_time < 3.0) and current < total:
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
        text = (
            "╭─ 🚀 **CLIPWELL DOWNLOADER** 🚀\n"
            "│\n"
            "├ 📥 Send any **Public** or **Private** Telegram link.\n"
            "├ ⚡ Fast download with live progress bar.\n"
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

        if data == "btn_add_session":
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
                    text += f"│  🔑 **ID:** `{s['doc_id']}`\n│\n"
                text += "╰──────────────────────────"
                
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="btn_back_admin")]])
                await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_back_admin":
            all_sess = get_all_sessions()
            keyboard = InlineKeyboardMarkup([
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

    # Text Handler for Admin Input & Links
    @bot.on_message(filters.text & filters.private)
    async def text_handler(client: Client, message: Message):
        user_id = message.from_user.id
        text_str = message.text.strip()

        if text_str.startswith("/"):
            return

        # 1. Admin Session Submission
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

        # 2. Telegram Media Link Processing
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

        # Loop through Firebase sessions
        for sess in active_sessions:
            temp_client = Client(f"sess_run_{time.time()}", api_id=API_ID, api_hash=API_HASH, session_string=sess['session_string'], in_memory=True)
            try:
                await temp_client.start()
                msg = await temp_client.get_messages(chat_id, msg_id)
                if msg and (msg.video or msg.photo or msg.document or msg.animation or msg.media_group_id):
                    target_msg = msg
                    working_user_client = temp_client
                    break
                else:
                    await temp_client.stop()
            except Exception:
                if temp_client.is_connected:
                    await temp_client.stop()

        if not target_msg or not working_user_client:
            await status.edit_text(
                "❌ **Post Not Found!**\n"
                "Make sure at least one connected account in Firebase sessions has **joined** that private channel."
            )
            return

        try:
            # Case 1: Album / Media Group
            if target_msg.media_group_id:
                group_messages = await working_user_client.get_media_group(chat_id, msg_id)
                downloaded_files = []
                media_list = []
                
                for idx, msg in enumerate(group_messages):
                    if msg.video or msg.photo or msg.document or msg.animation:
                        file_path = await working_user_client.download_media(
                            msg,
                            progress=progress_bar,
                            progress_args=(status, f"Downloading album ({idx+1}/{len(group_messages)})", user_id)
                        )
                        downloaded_files.append(file_path)

                        if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                            media_list.append(InputMediaVideo(file_path))
                        elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                            media_list.append(InputMediaPhoto(file_path))

                if media_list:
                    await status.edit_text("⬆️ **Uploading album...**")
                    await client.send_media_group(chat_id=message.chat.id, media=media_list)
                    
                    for path in downloaded_files:
                        if os.path.exists(path):
                            os.remove(path)
                    
                    await status.delete()
                else:
                    await status.edit_text("❌ **No downloadable media found in this album.**")

            # Case 2: Single Media
            else:
                file_path = await working_user_client.download_media(
                    target_msg,
                    progress=progress_bar,
                    progress_args=(status, "Downloading video", user_id)
                )

                last_update_time[user_id] = 0

                if target_msg.video:
                    await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        supports_streaming=True,
                        progress=progress_bar,
                        progress_args=(status, "Uploading video", user_id)
                    )
                elif target_msg.photo:
                    await client.send_photo(chat_id=message.chat.id, photo=file_path)
                elif target_msg.document:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=file_path,
                        progress=progress_bar,
                        progress_args=(status, "Uploading document", user_id)
                    )
                elif target_msg.animation:
                    await client.send_animation(chat_id=message.chat.id, animation=file_path)

                if os.path.exists(file_path):
                    os.remove(file_path)

                await status.delete()

        except Exception as e:
            await status.edit_text(f"❌ **Error:** `{str(e)}`")
        finally:
            if working_user_client and working_user_client.is_connected:
                await working_user_client.stop()

    # Start Main Bot
    await bot.start()
    print(">>> CLIPWELL FIREBASE BOT STARTED SUCCESSFULLY <<<", flush=True)

    await idle()

    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())