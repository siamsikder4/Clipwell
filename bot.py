import asyncio

# Python 3.12+ Asyncio Loop Fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import re
import sqlite3
from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto
from pyrogram.errors import SessionPasswordNeeded
from aiohttp import web

# Environment Credentials
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_NEW_BOT_TOKEN_HERE").strip()
PORT = int(os.environ.get("PORT", "8080"))

# SQLite Database Setup
DB_PATH = "sessions.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_session(user_id: int, session_str: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_sessions (user_id, session_string) VALUES (?, ?)", (user_id, session_str))
    conn.commit()
    conn.close()

def get_session(user_id: int) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT session_string FROM user_sessions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

init_db()

# Main Bot Client
bot = Client(
    "bot_instance",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

login_state = {}

# Auto-delete Helper Function (10 minutes)
async def auto_delete_messages(chat_id: int, message_ids: list, delay_seconds: int = 600):
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception as e:
        print(f"Auto delete error: {e}", flush=True)

# Render Health Check
async def handle_ping(request):
    return web.Response(text="Clipwell Bot is live and active!")

# Command Handlers
@bot.on_message(filters.command(["start", "Start"]) & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    saved_session = get_session(user_id)
    
    if not saved_session:
        text = (
            "🚀 **Telegram Private Media Downloader**\n\n"
            "⚠️ **You are not logged in!**\n"
            "Connect your account to download videos from restricted or private channels.\n\n"
            "📲 **To log in, send:**\n"
            "`/login +1234567890`"
        )
    else:
        text = (
            "✨ **Bot is Ready!** ✨\n\n"
            "📥 Send me any video or media group link from a public or private channel.\n\n"
            "📌 **Features:**\n"
            "• Single Video & Media Album Support\n"
            "• Original link attached in caption\n"
            "• ⏳ **Auto-deletes messages after 10 minutes for privacy**"
        )
    await message.reply_text(text)

@bot.on_message(filters.command(["login", "Login"]) & filters.private)
async def login_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    
    if len(args) < 2:
        await message.reply_text("❌ **Please provide your phone number!**\nExample: `/login +1234567890`")
        return

    phone_number = args[1]
    temp_user = Client(f"temp_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await temp_user.connect()

    try:
        code_info = await temp_user.send_code(phone_number)
        login_state[user_id] = {
            "client": temp_user,
            "phone": phone_number,
            "hash": code_info.phone_code_hash
        }
        await message.reply_text("📩 **OTP code sent to your Telegram account!**\n\nTo verify, send: `/otp 12345`")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")

@bot.on_message(filters.command(["otp", "Otp"]) & filters.private)
async def otp_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    state = login_state.get(user_id)
    
    if not state:
        await message.reply_text("⚠️ Please send your phone number first using `/login +1234567890`.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ **Please provide the OTP code!**\nExample: `/otp 12345`")
        return

    otp = args[1]
    temp_user = state["client"]

    try:
        await temp_user.sign_in(state["phone"], state["hash"], otp)
        session_str = await temp_user.export_session_string()
        
        save_session(user_id, session_str)
        await temp_user.disconnect()
        login_state.pop(user_id, None)

        await message.reply_text("🎉 **Account connected successfully!**\nYou can now send private media or album links.")
    except SessionPasswordNeeded:
        await message.reply_text("🔐 **Two-Step Verification Password Required!**\nSend: `/password your_password`")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")

@bot.on_message(filters.command(["password", "Password"]) & filters.private)
async def password_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    state = login_state.get(user_id)
    
    if not state:
        await message.reply_text("⚠️ No active login session found.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("❌ **Please provide your password!**\nExample: `/password mypassword123`")
        return

    password = args[1]
    temp_user = state["client"]

    try:
        await temp_user.check_password(password)
        session_str = await temp_user.export_session_string()
        
        save_session(user_id, session_str)
        await temp_user.disconnect()
        login_state.pop(user_id, None)

        await message.reply_text("🎉 **Account connected successfully!**")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{str(e)}`")

# Download & Auto-delete Processor
@bot.on_message(filters.text & filters.private)
async def process_link(client: Client, message: Message):
    if message.text.startswith("/"):
        return

    user_id = message.from_user.id
    user_session_str = get_session(user_id)

    if not user_session_str:
        await message.reply_text("⚠️ **Not Logged In!**\nPlease connect your account first: `/login +1234567890`")
        return

    link = message.text.strip()
    private_pattern = r"t\.me/c/(\d+)/(\d+)"
    public_pattern = r"t\.me/([^/]+)/(\d+)"

    private_match = re.search(private_pattern, link)
    public_match = re.search(public_pattern, link)

    if not (private_match or public_match):
        await message.reply_text("⚠️ **Please send a valid Telegram message link.**")
        return

    status = await message.reply_text("🔍 **Checking message link...**")

    user_client = Client(f"user_{user_id}", api_id=API_ID, api_hash=API_HASH, session_string=user_session_str, in_memory=True)

    try:
        await user_client.start()

        if private_match:
            chat_id = int("-100" + private_match.group(1))
            msg_id = int(private_match.group(2))
            fetch_client = user_client
        else:
            chat_id = public_match.group(1)
            msg_id = int(public_match.group(2))
            fetch_client = bot

        target_msg = await fetch_client.get_messages(chat_id, msg_id)

        if not target_msg:
            await status.edit_text("❌ **Post or message not found!**")
            await user_client.stop()
            return

        if target_msg.media_group_id:
            await status.edit_text("🖼️ **Album detected! Fetching media group...**")
            group_messages = await fetch_client.get_media_group(chat_id, msg_id)
            
            downloaded_files = []
            media_list = []
            
            await status.edit_text(f"⬇️ **Downloading {len(group_messages)} files from album...**")

            for idx, msg in enumerate(group_messages):
                if msg.video or msg.photo or msg.document or msg.animation:
                    file_path = await fetch_client.download_media(msg)
                    downloaded_files.append(file_path)

                    caption_text = ""
                    if len(media_list) == 0:
                        orig_caption = msg.caption or ""
                        caption_text = (
                            (f"📄 {orig_caption}\n\n" if orig_caption else "") +
                            f"🔗 **Original Link:** {link}\n"
                            f"⏳ *This post will be automatically deleted in 10 minutes.*"
                        )

                    if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                        media_list.append(InputMediaVideo(file_path, caption=caption_text))
                    elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                        media_list.append(InputMediaPhoto(file_path, caption=caption_text))

            if media_list:
                await status.edit_text("⬆️ **Uploading album to chat...**")
                sent_msgs = await client.send_media_group(chat_id=message.chat.id, media=media_list)
                
                for path in downloaded_files:
                    if os.path.exists(path):
                        os.remove(path)
                
                await status.delete()

                delete_msg_ids = [message.id] + [m.id for m in sent_msgs]
                asyncio.create_task(auto_delete_messages(message.chat.id, delete_msg_ids, delay_seconds=600))
            else:
                await status.edit_text("❌ **No supported video or photo found in the album.**")

        else:
            if not (target_msg.video or target_msg.photo or target_msg.document or target_msg.animation):
                await status.edit_text("❌ **No downloadable video or media found at this link.**")
                await user_client.stop()
                return

            await status.edit_text("⬇️ **Downloading video...**")
            file_path = await fetch_client.download_media(target_msg)

            await status.edit_text("⬆️ **Uploading video to chat...**")
            orig_caption = target_msg.caption or ""
            final_caption = (
                (f"📄 {orig_caption}\n\n" if orig_caption else "") +
                f"🔗 **Original Link:** {link}\n"
                f"⏳ *This video will be automatically deleted in 10 minutes.*"
            )

            sent_msg = None
            if target_msg.video:
                sent_msg = await client.send_video(chat_id=message.chat.id, video=file_path, caption=final_caption, supports_streaming=True)
            elif target_msg.photo:
                sent_msg = await client.send_photo(chat_id=message.chat.id, photo=file_path, caption=final_caption)
            elif target_msg.document:
                sent_msg = await client.send_document(chat_id=message.chat.id, document=file_path, caption=final_caption)
            elif target_msg.animation:
                sent_msg = await client.send_animation(chat_id=message.chat.id, animation=final_caption, caption=final_caption)

            if os.path.exists(file_path):
                os.remove(file_path)

            await status.delete()

            if sent_msg:
                delete_msg_ids = [message.id, sent_msg.id]
                asyncio.create_task(auto_delete_messages(message.chat.id, delete_msg_ids, delay_seconds=600))

    except Exception as e:
        await status.edit_text(f"❌ **Error:** `{str(e)}`")
    finally:
        if user_client.is_connected:
            await user_client.stop()

# Server & Bot Entry Point
async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server active on port {PORT}", flush=True)

    await bot.start()
    print(">>> BOT IS ONLINE AND LISTENING FOR MESSAGES <<<", flush=True)

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())