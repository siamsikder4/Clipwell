import os
import re
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from aiohttp import web

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
PORT = int(os.environ.get("PORT", "8080"))

bot = Client("bot_instance", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_instance", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

async def handle_ping(request):
    return web.Response(text="Bot is online!")

@bot.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text("👋 প্রাইভেট বা পাবলিক চ্যানেলের ভিডিও লিংক দিন, ডাউনলোড করে দিচ্ছি।")

@bot.on_message(filters.text & filters.private)
async def process_link(client: Client, message: Message):
    link = message.text.strip()
    
    private_pattern = r"t\.me/c/(\d+)/(\d+)"
    public_pattern = r"t\.me/([^/]+)/(\d+)"

    private_match = re.search(private_pattern, link)
    public_match = re.search(public_pattern, link)

    if not (private_match or public_match):
        await message.reply_text("⚠️ সঠিক টেলিগ্রাম মেসেজ লিংক দিন।")
        return

    status = await message.reply_text("🔄 লিংক চেক করা হচ্ছে...")

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
            await status.edit_text("❌ লিংকে কোনো ভিডিও নেই।")
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

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await user.start()
    await bot.start()
    print("Bot is running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
