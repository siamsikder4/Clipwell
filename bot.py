import os
import re
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded
from aiohttp import web

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
PORT = int(os.environ.get("PORT", "8080"))

# বট ইনস্ট্যান্স
bot = Client("bot_instance", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ইউজার ক্লায়েন্ট
user = None
if SESSION_STRING:
    user = Client("user_instance", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# ইন-মেমোরি লগইন তথ্য রাখার ডিকশনারি
login_state = {}

async def handle_ping(request):
    return web.Response(text="Bot is online!")

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    if not user or not user.is_connected:
        await message.reply_text(
            "⚠️ **ইউজার অ্যাকাউন্ট লগইন করা নেই!**\n\n"
            "বট থেকেই সরাসরি লগইন করতে নিচের ফরম্যাটে আপনার ফোন নম্বর পাঠান:\n"
            "`/login +8801XXXXXXXXX`"
        )
    else:
        await message.reply_text("👋 বট তৈরি আছে! প্রাইভেট বা পাবলিক চ্যানেলের ভিডিও লিংক পাঠান।")

# ১. সরাসরি বট চ্যাটে ফোন নম্বর দেওয়া
@bot.on_message(filters.command("login") & filters.private)
async def login_cmd(client: Client, message: Message):
    global user
    if user and user.is_connected:
        await message.reply_text("✅ আপনার ইউজার সেশন ইতিমধ্যেই কানেক্টেড আছে!")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("❌ ফোন নম্বর দিন।\nউদাহরণ: `/login +8801700000000`")
        return

    phone_number = args[1]
    temp_user = Client("temp_user", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await temp_user.connect()

    try:
        code_info = await temp_user.send_code(phone_number)
        login_state[message.chat.id] = {
            "client": temp_user,
            "phone": phone_number,
            "hash": code_info.phone_code_hash
        }
        await message.reply_text("📩 আপনার টেলিগ্রামে ওটিপি (OTP) পাঠানো হয়েছে।\nকোড পাঠাতে লিখুন: `/otp 12345`")
    except Exception as e:
        await message.reply_text(f"❌ এরর: `{str(e)}`")

# ২. ওটিপি (OTP) ইনপুট দেওয়া
@bot.on_message(filters.command("otp") & filters.private)
async def otp_cmd(client: Client, message: Message):
    global user
    state = login_state.get(message.chat.id)
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
        
        user = temp_user
        login_state.pop(message.chat.id, None)

        await message.reply_text(
            "🎉 **সফলভাবে লগইন সম্পূর্ণ হয়েছে!**\n\n"
            "🔑 **আপনার জেনারেট হওয়া SESSION_STRING:**\n"
            f"`{session_str}`\n\n"
            "📌 **পরামর্শ:** Render-এ বট রিস্টার্ট হলেও যাতে বারবার লগইন করতে না হয়, তার জন্য Render-এর Environment Variables-এ `SESSION_STRING` বক্সে এই কোডটি কপি করে সেভ করে রাখুন।"
        )
    except SessionPasswordNeeded:
        await message.reply_text("🔐 টু-স্টেপ ভেরিফিকেশন পাসওয়ার্ড লাগবে।\nপাসওয়ার্ড দিতে লিখুন: `/password আপনার_পাসওয়ার্ড`")
    except Exception as e:
        await message.reply_text(f"❌ এরর: `{str(e)}`")

# ৩. টু-স্টেপ ভেরিফিকেশন পাসওয়ার্ড (যদি থাকে)
@bot.on_message(filters.command("password") & filters.private)
async def password_cmd(client: Client, message: Message):
    global user
    state = login_state.get(message.chat.id)
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
        
        user = temp_user
        login_state.pop(message.chat.id, None)

        await message.reply_text(
            "🎉 **সফলভাবে লগইন সম্পূর্ণ হয়েছে!**\n\n"
            "🔑 **আপনার জেনারেট হওয়া SESSION_STRING:**\n"
            f"`{session_str}`\n\n"
            "📌 এটি কপি করে Render Environment Variables-এ `SESSION_STRING` এ বসিয়ে দিন।"
        )
    except Exception as e:
        await message.reply_text(f"❌ এরর: `{str(e)}`")

# ভিডিও প্রসেসিং
@bot.on_message(filters.text & filters.private)
async def process_link(client: Client, message: Message):
    if message.text.startswith("/"):
        return

    if not user or not user.is_connected:
        await message.reply_text("⚠️ আগে `/login +8801...` দিয়ে লগইন সম্পন্ন করুন।")
        return

    link = message.text.strip()
    private_pattern = r"t\.me/c/(\d+)/(\d+)"
    public_pattern = r"t\.me/([^/]+)/(\d+)"

    private_match = re.search(private_pattern, link)
    public_match = re.search(public_pattern, link)

    if not (private_match or public_match):
        await message.reply_text("⚠️ সঠিক টেলিগ্রাম লিংক দিন।")
        return

    status = await message.reply_text("🔄 প্রসেস করা হচ্ছে...")

    try:
        if private_match:
            chat_id = int("-100" + private_match.group(1))
            msg_id = int(private_match.group(2))
            fetch_client = user
        else:
            chat_id = public_match.group(1)
            msg_id = int(public_match.group(2))
            fetch_client = bot

        target_msg = await fetch_client.get_messages(chat_id, msg_id)

        if not target_msg or not (target_msg.video or target_msg.document or target_msg.animation):
            await status.edit_text("❌ লিংকে কোনো ভিডিও পাওয়া যায়নি।")
            return

        await status.edit_text("⬇️ ডাউনলোড হচ্ছে...")
        file_path = await fetch_client.download_media(target_msg)

        await status.edit_text("⬆️ আপলোড হচ্ছে...")
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

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await bot.start()
    if user:
        await user.start()
    print("Bot is running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())