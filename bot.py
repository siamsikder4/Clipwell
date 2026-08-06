import os
import re
import time
import sqlite3
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto
from aiohttp import web

# Environment Credentials
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # আপনার টেলিগ্রাম ইউজার আইডি (ঐচ্ছিক)
PORT = int(os.environ.get("PORT", "8080"))

# SQLite Database Setup
DB_PATH = "sessions.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_string TEXT UNIQUE NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def add_session_to_db(session_str: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO admin_sessions (session_string) VALUES (?)", (session_str,))
        conn.commit()
        res = True
    except sqlite3.IntegrityError:
        res = False
    conn.close()
    return res

def get_all_sessions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, session_string FROM admin_sessions")
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_session_from_db(session_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admin_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

# Initialize Database
init_db()

# Tracker for UI updates
last_update_time = {}

# Render Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell Multi-Session Engine Active!")

# Real-time Animated Progress Callback
async def progress_bar(current, total, status_msg, action_name, user_id):
    now = time.time()
    last_time = last_update_time.get(user_id, 0)
    
    if (now - last_time < 3.5) and current < total:
        return
        
    last_update_time[user_id] = now
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)
    
    text = (
        f"⚡ **{action_name}**\n\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"📊 `{curr_mb:.1f} MB / {tot_mb:.1f} MB`"
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
    @bot.on_message(filters.command(["start", "Start"]) & filters.private)
    async def start_cmd(client: Client, message: Message):
        text = (
            "✨ **Clipwell Downloader Bot** ✨\n\n"
            "📥 যেকোনো পাবলিক বা প্রাইভেট চ্যানেলের ভিডিও বা অ্যালবামের লিংক পাঠান।"
        )
        await message.reply_text(text)

    # Admin Command: /addsession <SESSION_STRING>
    @bot.on_message(filters.command(["addsession"]) & filters.private)
    async def add_session_cmd(client: Client, message: Message):
        if OWNER_ID != 0 and message.from_user.id != OWNER_ID:
            await message.reply_text("❌ আপনি এই কমান্ডটি ব্যবহারের অনুমতি পাননি।")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❌ **ব্যবহার পদ্ধতি:** `/addsession <SESSION_STRING>`")
            return

        session_str = args[1].strip()
        success = add_session_to_db(session_str)

        if success:
            await message.reply_text("✅ **সেশন সফলভাবে ডেটাবেজে যুক্ত করা হয়েছে!**")
        else:
            await message.reply_text("⚠️ এই সেশনটি ইতিমধ্যেই যুক্ত আছে।")

    # Admin Command: /sessions
    @bot.on_message(filters.command(["sessions"]) & filters.private)
    async def list_sessions_cmd(client: Client, message: Message):
        if OWNER_ID != 0 and message.from_user.id != OWNER_ID:
            return

        sessions = get_all_sessions()
        if not sessions:
            await message.reply_text("ℹ️ কোনো অ্যাক্টিভ সেশন পাওয়া যায়নি। `/addsession` দিয়ে সেশন যুক্ত করুন।")
            return

        text = "<b>📋 যুক্ত থাকা সেশনসমূহ:</b>\n\n"
        for s_id, _ in sessions:
            text += f"• **Session ID:** `{s_id}` (রিমুভ করতে: `/delsession {s_id}`)\n"
        await message.reply_text(text)

    # Admin Command: /delsession <ID>
    @bot.on_message(filters.command(["delsession"]) & filters.private)
    async def del_session_cmd(client: Client, message: Message):
        if OWNER_ID != 0 and message.from_user.id != OWNER_ID:
            return

        args = message.text.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.reply_text("❌ **ব্যবহার পদ্ধতি:** `/delsession <Session_ID>`")
            return

        s_id = int(args[1])
        delete_session_from_db(s_id)
        await message.reply_text(f"🗑️ Session ID `{s_id}` মুছে ফেলা হয়েছে।")

    # Link Processing for Everyone
    @bot.on_message(filters.text & filters.private)
    async def process_link(client: Client, message: Message):
        if message.text.startswith("/"):
            return

        link = message.text.strip()
        user_id = message.from_user.id
        private_pattern = r"t\.me/c/(\d+)/(\d+)"
        public_pattern = r"t\.me/([^/]+)/(\d+)"

        private_match = re.search(private_pattern, link)
        public_match = re.search(public_pattern, link)

        if not (private_match or public_match):
            await message.reply_text("⚠️ **অনুগ্রহ করে একটি বৈধ টেলিগ্রাম পোস্ট লিংক পাঠান।**")
            return

        saved_sessions = get_all_sessions()
        if not saved_sessions:
            await message.reply_text("⚠️ **কোনো সেশন যুক্ত করা নেই!**\nঅ্যাডমিনকে `/addsession` দিয়ে সেশন যুক্ত করতে বলুন।")
            return

        status = await message.reply_text("🔍 **মেসেজ চেক করা হচ্ছে...**")

        if private_match:
            chat_id = int("-100" + private_match.group(1))
            msg_id = int(private_match.group(2))
        else:
            chat_id = public_match.group(1)
            msg_id = int(public_match.group(2))

        target_msg = None
        working_user_client = None

        # Loop through saved sessions to find one with access to the channel
        for s_id, s_str in saved_sessions:
            temp_client = Client(f"user_session_{s_id}", api_id=API_ID, api_hash=API_HASH, session_string=s_str, in_memory=True)
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
            await status.edit_text("❌ **পোস্ট পাওয়া যায়নি! নিশ্চিত হন যুক্ত থাকা কোনো একাউন্ট ওই চ্যানেলে জয়েন আছে।**")
            return

        try:
            # Case 1: Album
            if target_msg.media_group_id:
                group_messages = await working_user_client.get_media_group(chat_id, msg_id)
                downloaded_files = []
                media_list = []
                
                for idx, msg in enumerate(group_messages):
                    if msg.video or msg.photo or msg.document or msg.animation:
                        file_path = await working_user_client.download_media(
                            msg,
                            progress=progress_bar,
                            progress_args=(status, f"ডাউনলোড হচ্ছে অ্যালবাম ({idx+1}/{len(group_messages)})", user_id)
                        )
                        downloaded_files.append(file_path)

                        if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                            media_list.append(InputMediaVideo(file_path))
                        elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                            media_list.append(InputMediaPhoto(file_path))

                if media_list:
                    await status.edit_text("⬆️ **অ্যালবাম আপলোড হচ্ছে...**")
                    await client.send_media_group(chat_id=message.chat.id, media=media_list)
                    
                    for path in downloaded_files:
                        if os.path.exists(path):
                            os.remove(path)
                    
                    await status.delete()
                else:
                    await status.edit_text("❌ **অ্যালবামে কোনো ফাইল পাওয়া যায়নি।**")

            # Case 2: Single Video / Media
            else:
                file_path = await working_user_client.download_media(
                    target_msg,
                    progress=progress_bar,
                    progress_args=(status, "ভিডিও ডাউনলোড হচ্ছে", user_id)
                )

                last_update_time[user_id] = 0

                if target_msg.video:
                    await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        supports_streaming=True,
                        progress=progress_bar,
                        progress_args=(status, "ভিডিও আপলোড হচ্ছে", user_id)
                    )
                elif target_msg.photo:
                    await client.send_photo(chat_id=message.chat.id, photo=file_path)
                elif target_msg.document:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=file_path,
                        progress=progress_bar,
                        progress_args=(status, "ফাইল আপলোড হচ্ছে", user_id)
                    )
                elif target_msg.animation:
                    await client.send_animation(chat_id=message.chat.id, animation=file_path)

                if os.path.exists(file_path):
                    os.remove(file_path)

                await status.delete()

        except Exception as e:
            await status.edit_text(f"❌ **এরর:** `{str(e)}`")
        finally:
            if working_user_client and working_user_client.is_connected:
                await working_user_client.stop()

    # Start Main Bot
    await bot.start()
    print(">>> MULTI-SESSION ADMIN BOT STARTED SUCCESSFULLY <<<", flush=True)

    await idle()

    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())