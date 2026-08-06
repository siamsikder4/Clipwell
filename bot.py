import os
import re
import sqlite3
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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

# State Tracker for Interactive Login
user_states = {}

# Render Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell Smart Bot is active!")

# Auto-delete helper (10 minutes)
async def auto_delete_messages(bot_client, chat_id: int, message_ids: list, delay_seconds: int = 600):
    await asyncio.sleep(delay_seconds)
    try:
        await bot_client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception as e:
        print(f"Auto delete error: {e}", flush=True)

# Helper function to send OTP Code
async def request_otp_code(bot_client, message: Message, phone_number: str):
    user_id = message.from_user.id
    
    # Auto-format phone number (Convert 017XXXXXXXX to +88017XXXXXXXX)
    phone_clean = re.sub(r"[^\d+]", "", phone_number)
    if phone_clean.startswith("01"):
        phone_clean = "+88" + phone_clean
    elif not phone_clean.startswith("+"):
        phone_clean = "+" + phone_clean

    status_msg = await message.reply_text("⏳ **ওটিপি কোড পাঠানো হচ্ছে...**", reply_markup=ReplyKeyboardRemove())

    temp_user = Client(f"temp_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await temp_user.connect()

    try:
        code_info = await temp_user.send_code(phone_clean)
        user_states[user_id] = {
            "state": "WAITING_OTP",
            "client": temp_user,
            "phone": phone_clean,
            "hash": code_info.phone_code_hash
        }
        await status_msg.edit_text(
            f"📩 **`{phone_clean}` নম্বরে টেলিগ্রাম ওটিপি কোড পাঠানো হয়েছে!**\n\n"
            "👇 **নিচে সরাসরি ওটিপি কোডটি টাইপ করে পাঠান (যেমন: 12345):**"
        )
    except Exception as e:
        await temp_user.disconnect()
        user_states.pop(user_id, None)
        await status_msg.edit_text(f"❌ **এরর:** `{str(e)}`\n\nপুনরায় চেষ্টা করতে আবার ফোন নম্বর পাঠান।")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server active on port {PORT}", flush=True)

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
            contact_btn = ReplyKeyboardMarkup(
                [[KeyboardButton("📱 শেয়ার করুন ফোন নম্বর", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            text = (
                "🚀 **Clipwell Downloader Bot**\n\n"
                "⚠️ **আপনি এখনও লগইন করেননি!**\n"
                "প্রাইভেট চ্যানেল থেকে ভিডিও ডাউনলোড করতে আপনার অ্যাকাউন্ট যুক্ত করুন।\n\n"
                "👉 **নিচের বাটনে চাপ দিয়ে নম্বর শেয়ার করুন অথবা সরাসরি 017XXXXXXXX নম্বরটি মেসেজে লিখে পাঠান:**"
            )
            await message.reply_text(text, reply_markup=contact_btn)
        else:
            text = (
                "✨ **আপনার অ্যাকাউন্ট কানেক্ট রয়েছে!** ✨\n\n"
                "📥 যেকোনো পাবলিক বা প্রাইভেট চ্যানেলের ভিডিও বা অ্যালবামের লিংক পাঠান।\n\n"
                "📌 **কমান্ডস:**\n"
                "• `/logout` - আপনার অ্যাকাউন্ট রিমুভ করতে\n"
                "• ⏳ **প্রাইভেসি রক্ষায় ১০ মিনিট পর অটো-ডিলিট হবে।**"
            )
            await message.reply_text(text, reply_markup=ReplyKeyboardRemove())

    # Command: /logout
    @bot.on_message(filters.command(["logout", "Logout"]) & filters.private)
    async def logout_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        delete_session(user_id)
        user_states.pop(user_id, None)
        await message.reply_text("🚪 **আপনার অ্যাকাউন্ট সফলভাবে রিমুভ করা হয়েছে!**\nপুনরায় যুক্ত করতে /start চাপুন।", reply_markup=ReplyKeyboardRemove())

    # Handle Contact Button Click
    @bot.on_message(filters.contact & filters.private)
    async def contact_handler(client: Client, message: Message):
        phone_number = message.contact.phone_number
        await request_otp_code(client, message, phone_number)

    # Text Handler (Handles Phone numbers, OTP, Passwords & Download Links)
    @bot.on_message(filters.text & filters.private)
    async def text_handler(client: Client, message: Message):
        text_str = message.text.strip()
        if text_str.startswith("/"):
            return

        user_id = message.from_user.id
        state_data = user_states.get(user_id)

        # 1. Handling OTP Submission
        if state_data and state_data.get("state") == "WAITING_OTP":
            otp = re.sub(r"\D", "", text_str)
            if not otp:
                await message.reply_text("❌ **অনুগ্রহ করে সঠিক সংখ্যা ওটিপি কোডটি পাঠান (যেমন: 12345):**")
                return

            temp_user = state_data["client"]
            status_msg = await message.reply_text("⏳ **ওটিপি যাচাই করা হচ্ছে...**")

            try:
                try:
                    await temp_user.sign_in(state_data["phone"], state_data["hash"], otp)
                except SessionPasswordNeeded:
                    user_states[user_id]["state"] = "WAITING_PASSWORD"
                    await status_msg.edit_text("🔐 **টু-স্টেপ ভেরিফিকেশন অন করা আছে!**\n\n👇 আপনার পাসওয়ার্ডটি মেসেজে লিখে পাঠান:")
                    return
                except Exception as login_err:
                    if await temp_user.get_me():
                        pass
                    else:
                        raise login_err

                session_str = await temp_user.export_session_string()
                save_session(user_id, session_str)

                await temp_user.disconnect()
                user_states.pop(user_id, None)

                await status_msg.edit_text(
                    "🎉 **অ্যালকাউন্ট সফলভাবে কানেক্ট হয়েছে!**\n\n"
                    "📥 এখন যেকোনো পাবলিক বা প্রাইভেট ভিডিও লিংক পাঠালে তা ডাউনলোড হয়ে যাবে।"
                )

            except Exception as e:
                if temp_user.is_connected:
                    await temp_user.disconnect()
                user_states.pop(user_id, None)
                await status_msg.edit_text(f"❌ **ভুল ওটিপি বা এরর:** `{str(e)}`\n\nপুনরায় চেষ্টা করতে আপনার নম্বরটি আবার লিখে পাঠান।")
            return

        # 2. Handling 2FA Password Submission
        if state_data and state_data.get("state") == "WAITING_PASSWORD":
            password = text_str
            temp_user = state_data["client"]
            status_msg = await message.reply_text("⏳ **পাসওয়ার্ড যাচাই করা হচ্ছে...**")

            try:
                await temp_user.check_password(password)
                session_str = await temp_user.export_session_string()
                save_session(user_id, session_str)

                await temp_user.disconnect()
                user_states.pop(user_id, None)

                await status_msg.edit_text("🎉 **অ্যালকাউন্ট সফলভাবে কানেক্ট হয়েছে!**")
            except Exception as e:
                await status_msg.edit_text(f"❌ **ভুল পাসওয়ার্ড:** `{str(e)}`\n\nপাসওয়ার্ডটি আবার চেষ্টা করুন:")
            return

        # 3. Check if User is Logged In
        user_session_str = get_session(user_id)

        # 4. If Not Logged In -> Check if text is a phone number
        if not user_session_str:
            clean_text = re.sub(r"[^\d+]", "", text_str)
            if clean_text.startswith("01") or clean_text.startswith("+8801") or clean_text.startswith("8801"):
                await request_otp_code(client, message, clean_text)
            else:
                contact_btn = ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 শেয়ার করুন ফোন নম্বর", request_contact=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
                await message.reply_text(
                    "⚠️ **আপনি লগইন করেননি!**\n\n"
                    "ভিডিও ডাউনলোড করতে নিচের বাটনে চাপ দিন অথবা আপনার ফোন নম্বরটি লিখে পাঠান (যেমন: `017XXXXXXXX`):",
                    reply_markup=contact_btn
                )
            return

        # 5. Download Video / Album Link Processor (If Logged In)
        private_pattern = r"t\.me/c/(\d+)/(\d+)"
        public_pattern = r"t\.me/([^/]+)/(\d+)"

        private_match = re.search(private_pattern, text_str)
        public_match = re.search(public_pattern, text_str)

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

            # Case: Album
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
                                f"🔗 **মূল লিংক:** {text_str}\n"
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

            # Case: Single Video/Media
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
                    f"🔗 **মূল লিংক:** {text_str}\n"
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

    # Start main bot
    await bot.start()
    print(">>> SMART MULTI-USER BOT IS ONLINE AND LISTENING <<<", flush=True)

    await idle()

    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())