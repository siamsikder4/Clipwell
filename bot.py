import os
import re
import sqlite3
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto
from pyrogram.errors import SessionPasswordNeeded
from aiohttp import web

# Environment Credentials
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
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

def delete_session(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Initialize Database
init_db()

# Temp Login State Tracker
login_state = {}

# Render Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell Multi-User Bot is live!")

# Auto-delete helper (10 minutes)
async def auto_delete_messages(bot_client, chat_id: int, message_ids: list, delay_seconds: int = 600):
    await asyncio.sleep(delay_seconds)
    try:
        await bot_client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception as e:
        print(f"Auto delete error: {e}", flush=True)

async def main():
    # 1. Web Server for Render
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server active on port {PORT}", flush=True)

    # 2. Main Bot Client (Inside active asyncio loop)
    bot = Client(
        "bot_instance",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )

    # Command: /start
    @bot.on_message(filters.command(["start", "Start"]) & filters.private)
    async def start_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        saved_session = get_session(user_id)
        
        if not saved_session:
            text = (
                "🚀 **Clipwell Multi-User Downloader Bot**\n\n"
                "⚠️ **আপনি লগইন করেননি!**\n"
                "প্রাইভেট বা রেস্ট্রিক্টেড চ্যানেল থেকে ভিডিও ডাউনলোড করতে আপনার টেলিগ্রাম অ্যাকাউন্ট যুক্ত করুন।\n\n"
                "📲 **লগইন করতে পাঠান:**\n"
                "`/login +8801XXXXXXXXX` (আপনার ফোন নম্বর)"
            )
        else:
            text = (
                "✨ **আপনার অ্যাকাউন্ট যুক্ত রয়েছে!** ✨\n\n"
                "📥 যেকোনো পাবলিক বা প্রাইভেট চ্যানেলের ভিডিও বা অ্যালবামের লিংক পাঠান।\n\n"
                "📌 **কমান্ডস:**\n"
                "• `/logout` - আপনার অ্যাকাউন্ট রিমুভ করতে\n"
                "• ⏳ **প্রাইভেসি রক্ষায় ১০ মিনিট পর অটো-ডিলিট হবে।**"
            )
        await message.reply_text(text)

    # Command: /login
    @bot.on_message(filters.command(["login", "Login"]) & filters.private)
    async def login_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        args = message.text.split()
        
        if len(args) < 2:
            await message.reply_text("❌ **অনুগ্রহ করে ফোন নম্বর দিন!**\nউদাহরণ: `/login +8801XXXXXXXXX`")
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
            await message.reply_text("📩 **আপনার টেলিগ্রাম অ্যাকাউন্টে OTP কোড পাঠানো হয়েছে!**\n\nযাচাই করতে পাঠান: `/otp 12345`")
        except Exception as e:
            await temp_user.disconnect()
            await message.reply_text(f"❌ **এরর:** `{str(e)}`")

    # Command: /otp
    @bot.on_message(filters.command(["otp", "Otp"]) & filters.private)
    async def otp_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        state = login_state.get(user_id)
        
        if not state:
            await message.reply_text("⚠️ প্রথমে নম্বর দিন: `/login +8801XXXXXXXXX`")
            return

        args = message.text.split()
        if len(args) < 2:
            await message.reply_text("❌ **অনুগ্রহ করে OTP কোডটি দিন!**\nউদাহরণ: `/otp 12345`")
            return

        otp = args[1]
        temp_user = state["client"]

        try:
            await temp_user.sign_in(state["phone"], state["hash"], otp)
            session_str = await temp_user.export_session_string()
            
            save_session(user_id, session_str)
            await temp_user.disconnect()
            login_state.pop(user_id, None)

            await message.reply_text("🎉 **অ্যালকাউন্ট সফলভাবে কানেক্ট হয়েছে!**\nএখন প্রাইভেট বা পাবলিক যেকোনো ভিডিও লিংক পাঠাতে পারেন।")
        except SessionPasswordNeeded:
            await message.reply_text("🔐 **টু-স্টেপ ভেরিফিকেশন পাসওয়ার্ড প্রয়োজন!**\nপাঠান: `/password আপনার_পাসওয়ার্ড`")
        except Exception as e:
            await message.reply_text(f"❌ **এরর:** `{str(e)}`")

    # Command: /password
    @bot.on_message(filters.command(["password", "Password"]) & filters.private)
    async def password_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        state = login_state.get(user_id)
        
        if not state:
            await message.reply_text("⚠️ কোনো সেশন পাওয়া যায়নি।")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **পাসওয়ার্ড দিন!**\nউদাহরণ: `/password mypassword123`")
            return

        password = args[1]
        temp_user = state["client"]

        try:
            await temp_user.check_password(password)
            session_str = await temp_user.export_session_string()
            
            save_session(user_id, session_str)
            await temp_user.disconnect()
            login_state.pop(user_id, None)

            await message.reply_text("🎉 **অ্যালকাউন্ট সফলভাবে কানেক্ট হয়েছে!**")
        except Exception as e:
            await message.reply_text(f"❌ **এরর:** `{str(e)}`")

    # Command: /logout
    @bot.on_message(filters.command(["logout", "Logout"]) & filters.private)
    async def logout_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        delete_session(user_id)
        await message.reply_text("🚪 **আপনার অ্যাকাউন্ট রিমুভ করা হয়েছে!**")

    # Link Processing
    @bot.on_message(filters.text & filters.private)
    async def process_link(client: Client, message: Message):
        if message.text.startswith("/"):
            return

        user_id = message.from_user.id
        user_session_str = get_session(user_id)

        if not user_session_str:
            await message.reply_text("⚠️ **আপনি লগইন করেননি!**\nভিডিও ডাউনলোড করতে প্রথমে কানেক্ট করুন: `/login +8801XXXXXXXXX`")
            return

        link = message.text.strip()
        private_pattern = r"t\.me/c/(\d+)/(\d+)"
        public_pattern = r"t\.me/([^/]+)/(\d+)"

        private_match = re.search(private_pattern, link)
        public_match = re.search(public_pattern, link)

        if not (private_match or public_match):
            await message.reply_text("⚠️ **অনুগ্রহ করে একটি বৈধ টেলিগ্রাম লিংক পাঠান।**")
            return

        status = await message.reply_text("🔍 **মেসেজ চেক করা হচ্ছে...**")

        user_client = Client(f"user_{user_id}", api_id=API_ID, api_hash=API_HASH, session_string=user_session_str, in_memory=True)

        try:
            await user_client.start()

            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            target_msg = await user_client.get_messages(chat_id, msg_id)

            if not target_msg:
                await status.edit_text("❌ **পোস্টটি পাওয়া যায়নি! নিশ্চিত হন আপনি ওই প্রাইভেট চ্যানেলে জয়েন আছেন।**")
                await user_client.stop()
                return

            # Album Case
            if target_msg.media_group_id:
                await status.edit_text("🖼️ **অ্যালবাম ডিটেক্ট হয়েছে! ফালসমূহ আনা হচ্ছে...**")
                group_messages = await user_client.get_media_group(chat_id, msg_id)
                
                downloaded_files = []
                media_list = []
                
                await status.edit_text(f"⬇️ **{len(group_messages)} টি ফাইল ডাউনলোড হচ্ছে...**")

                for idx, msg in enumerate(group_messages):
                    if msg.video or msg.photo or msg.document or msg.animation:
                        file_path = await user_client.download_media(msg)
                        downloaded_files.append(file_path)

                        caption_text = ""
                        if len(media_list) == 0:
                            orig_caption = msg.caption or ""
                            caption_text = (
                                (f"📄 {orig_caption}\n\n" if orig_caption else "") +
                                f"🔗 **মূল লিংক:** {link}\n"
                                f"⏳ *এই পোস্টটি ১০ মিনিট পর স্বয়ংক্রিয়ভাবে মুছে যাবে।*"
                            )

                        if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                            media_list.append(InputMediaVideo(file_path, caption=caption_text))
                        elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                            media_list.append(InputMediaPhoto(file_path, caption=caption_text))

                if media_list:
                    await status.edit_text("⬆️ **অ্যালবাম আপলোড করা হচ্ছে...**")
                    sent_msgs = await client.send_media_group(chat_id=message.chat.id, media=media_list)
                    
                    for path in downloaded_files:
                        if os.path.exists(path):
                            os.remove(path)
                    
                    await status.delete()

                    delete_msg_ids = [message.id] + [m.id for m in sent_msgs]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=600))
                else:
                    await status.edit_text("❌ **অ্যালবামে কোনো সাপোর্টেড ভিডিও বা ফটো পাওয়া যায়নি।**")

            # Single Media Case
            else:
                if not (target_msg.video or target_msg.photo or target_msg.document or target_msg.animation):
                    await status.edit_text("❌ **এই লিংকে ডাউনলোডযোগ্য কোনো মিডিয়া নেই।**")
                    await user_client.stop()
                    return

                await status.edit_text("⬇️ **মিডিয়া ডাউনলোড হচ্ছে...**")
                file_path = await user_client.download_media(target_msg)

                await status.edit_text("⬆️ **মিডিয়া আপলোড হচ্ছে...**")
                orig_caption = target_msg.caption or ""
                final_caption = (
                    (f"📄 {orig_caption}\n\n" if orig_caption else "") +
                    f"🔗 **মূল লিংক:** {link}\n"
                    f"⏳ *এই পোস্টটি ১০ মিনিট পর স্বয়ংক্রিয়ভাবে মুছে যাবে।*"
                )

                sent_msg = None
                if target_msg.video:
                    sent_msg = await client.send_video(chat_id=message.chat.id, video=file_path, caption=final_caption, supports_streaming=True)
                elif target_msg.photo:
                    sent_msg = await client.send_photo(chat_id=message.chat.id, photo=final_caption)
                elif target_msg.document:
                    sent_msg = await client.send_document(chat_id=message.chat.id, document=final_caption)
                elif target_msg.animation:
                    sent_msg = await client.send_animation(chat_id=message.chat.id, animation=final_caption)

                if os.path.exists(file_path):
                    os.remove(file_path)

                await status.delete()

                if sent_msg:
                    delete_msg_ids = [message.id, sent_msg.id]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=600))

        except Exception as e:
            await status.edit_text(f"❌ **এরর:** `{str(e)}`")
        finally:
            if user_client.is_connected:
                await user_client.stop()

    # 3. Start main bot
    await bot.start()
    print(">>> MULTI-USER BOT IS ONLINE AND LISTENING <<<", flush=True)

    await idle()

    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())