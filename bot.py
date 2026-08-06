import os
import re
import asyncio
import sqlite3
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded
from aiohttp import web

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

# ডাটাবেজ সেটআপ (Session Storage)
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

# ইনিশিয়ালাইজেশন
init_db()
bot = Client("bot_instance", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
login_state = {}

async def handle_ping(request):
    return web.Response(text="Multi-user Bot is online!")

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    saved_session = get_session(user_id)
    
    if not saved_session:
        await message.reply_text(
            "👋 **টেলিগ্রাম প্রাইভেট ভিডিও ডাউনলোডার বট**\n\n"
            "⚠️ আপনি এখনো লগইন করেননি। আপনার অ্যাকাউন্ট কানেক্ট করতে লিখুন:\n"
            "`/login +8801XXXXXXXXX`"
        )
    else:
        await message.reply_text(
            "👋 **বট তৈরি আছে!**\n\n"
            "আপনি সফলভাবে কানেক্টেড আছেন। প্রাইভেট বা পাবলিক চ্যানেলের ভিডিও লিংক পাঠান।"
        )

# ১. লগইন শুরু (নম্বর দেওয়া)
@bot.on_message(filters.command("login") & filters.private)
async def login_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    
    if len(args) < 2:
        await message.reply_text("❌ ফোন নম্বর দিন।\nউদাহরণ: `/login +8801700000000`")
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
        await message.reply_text("📩 আপনার টেলিগ্রামে ওটিপি (OTP) পাঠানো হয়েছে।\nকোড পাঠাতে লিখুন: `/otp 12345`")
    except Exception as e:
        await message.reply_text(f"❌ এরর: `{str(e)}`")

# ২. ওটিপি ইনপুট দেওয়া
@bot.on_message(filters.command("otp") & filters.private)
async def otp_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    state = login_state.get(user_id)
    
    if not state:
        await message.reply_text("⚠️ আগে `/login +8801...` দিয়ে নম্বর পাঠান।")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ OTP দিন।\nউদাহরণ: `/otp 12345`")
        return

    otp = args[1]
    temp_user = state["client"]

    try:
        await temp_user.sign_in(state["phone"], state["hash"], otp)
        session_str = await temp_user.export_session_string()
        
        save_session(user_id, session_str)
        await temp_user.disconnect()
        login_state.pop(user_id, None)

        await message.reply_text("🎉 **আপনার আইডি সফলভাবে লগইন হয়েছে!**\nএখন প্রাইভেট চ্যানেলের ভিডিও লিংক পাঠালে ডাউনলোড করে দেওয়া হবে।")
    except SessionPasswordNeeded:
        await message.reply_text("🔐 টু-স্টেপ ভেরিফিকেশন পাসওয়ার্ড দিন:\n`/password আপনার_পাসওয়ার্ড`")
    except Exception as e:
        await message.reply_text(f"❌ এরর: `{str(e)}`")

# ৩. পাসওয়ার্ড ইনপুট দেওয়া (যদি টু-স্টেপ থাকে)
@bot.on_message(filters.command("password") & filters.private)
async def password_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    state = login_state.get(user_id)
    
    if not state:
        await message.reply_text("⚠️ লগইন প্রসেস চালু নেই।")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("❌ পাসওয়ার্ড দিন।\nউদাহরণ: `/password mypass123`")
        return

    password = args[1]
    temp_user = state["client"]

    try:
        await temp_user.check_password(password)
        session_str = await temp_user.export_session_string()
        
        save_session(user_id, session_str)
        await temp_user.disconnect()
        login_state.pop(user_id, None)

        await message.reply_text("🎉 **আপনার আইডি সফলভাবে লগইন হয়েছে!**")
    except Exception as e:
        await message.reply_text(f"❌ এরর: `{str(e)}`")

# ভিডিও ডাউনলোড প্রসেসিং
@bot.on_message(filters.text & filters.private)
async def process_link(client: Client, message: Message):
    if message.text.startswith("/"):
        return

    user_id = message.from_user.id
    user_session_str = get_session(user_id)

    if not user_session_str:
        await message.reply_text("⚠️ প্রাইভেট ভিডিও ডাউনলোড করতে আপনার নিজের টেলিগ্রাম অ্যাকাউন্ট লগইন করতে হবে।\nলগইন করতে লিখুন: `/login +8801XXXXXXXXX`")
        return

    link = message.text.strip()
    private_pattern = r"t\.me/c/(\d+)/(\d+)"
    public_pattern = r"t\.me/([^/]+)/(\d+)"

    private_match = re.search(private_pattern, link)
    public_match = re.search(public_pattern, link)

    if not (private_match or public_match):
        await message.reply_text("⚠️ সঠিক টেলিগ্রাম মেসেজ লিংক দিন।")
        return

    status = await message.reply_text("🔄 লিংক চেক করা হচ্ছে...")

    # ইউজারের সেশন দিয়ে ক্লায়েন্ট চালু করা
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

        if not target_msg or not (target_msg.video or target_msg.document or target_msg.animation):
            await status.edit_text("❌ লিংকে কোনো ভিডিও পাওয়া যায়নি (অথবা এই চ্যানেল বা পোস্টে আপনার অ্যাকাউন্টের অ্যাক্সেস নেই)।")
            await user_client.stop()
            return

        await status.edit_text("⬇️ ডাউনলোড হচ্ছে...")
        file_path = await fetch_client.download_media(target_msg)

        await status.edit_text("⬆️ চ্যাটে আপলোড করা হচ্ছে...")
        caption = target_msg.caption or ""

        await client.send_video(
            chat_id=message.chat.id,
            video=file_path,
            caption=caption,
            supports_streaming=True
        )

        if os.path.exists(file_path):
            os.remove(file_path)

        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ এরর: `{str(e)}`")
    finally:
        if user_client.is_connected:
            await user_client.stop()

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await bot.start()
    print("Multi-user Bot running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())