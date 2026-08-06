import os
import re
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto
from aiohttp import web

# Environment Credentials
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()
PORT = int(os.environ.get("PORT", "8080"))

# Render Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell Bot is live and active!")

# Helper function for 10-minute auto-deletion
async def auto_delete_messages(bot_client, chat_id: int, message_ids: list, delay_seconds: int = 600):
    await asyncio.sleep(delay_seconds)
    try:
        await bot_client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception as e:
        print(f"Auto delete error: {e}", flush=True)

async def main():
    # 1. Start Web Server for Render
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Web server active on port {PORT}", flush=True)

    # 2. Instantiate Clients INSIDE active asyncio loop (Prevents Loop Mismatch)
    bot = Client(
        "bot_instance",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )

    user = Client(
        "user_instance",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING if SESSION_STRING else None,
        in_memory=True
    )

    # 3. Register Command Handlers
    @bot.on_message(filters.command(["start", "Start"]) & filters.private)
    async def start_cmd(client: Client, message: Message):
        text = (
            "✨ **Clipwell Downloader Bot is Active!** ✨\n\n"
            "📥 Send me any video or album link from a public or private channel.\n\n"
            "📌 **Features:**\n"
            "• Single Video & Media Album Support\n"
            "• Original link attached in caption\n"
            "• ⏳ **Auto-deletes messages after 10 minutes for privacy**"
        )
        await message.reply_text(text)

    @bot.on_message(filters.text & filters.private)
    async def process_link(client: Client, message: Message):
        if message.text.startswith("/"):
            return

        if not SESSION_STRING:
            await message.reply_text("⚠️ **Error:** `SESSION_STRING` is missing in Render Environment Variables!")
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

        try:
            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            target_msg = await user.get_messages(chat_id, msg_id)

            if not target_msg:
                await status.edit_text("❌ **Post not found or restricted! Make sure your account is a member of the private channel.**")
                return

            # Case 1: Album
            if target_msg.media_group_id:
                await status.edit_text("🖼️ **Album detected! Fetching media group...**")
                group_messages = await user.get_media_group(chat_id, msg_id)
                
                downloaded_files = []
                media_list = []
                
                await status.edit_text(f"⬇️ **Downloading {len(group_messages)} files from album...**")

                for idx, msg in enumerate(group_messages):
                    if msg.video or msg.photo or msg.document or msg.animation:
                        file_path = await user.download_media(msg)
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
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=600))
                else:
                    await status.edit_text("❌ **No supported video or photo found in the album.**")

            # Case 2: Single Video
            else:
                if not (target_msg.video or target_msg.photo or target_msg.document or target_msg.animation):
                    await status.edit_text("❌ **No downloadable video or media found at this link.**")
                    return

                await status.edit_text("⬇️ **Downloading media...**")
                file_path = await user.download_media(target_msg)

                await status.edit_text("⬆️ **Uploading media to chat...**")
                orig_caption = target_msg.caption or ""
                final_caption = (
                    (f"📄 {orig_caption}\n\n" if orig_caption else "") +
                    f"🔗 **Original Link:** {link}\n"
                    f"⏳ *This media will be automatically deleted in 10 minutes.*"
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
            await status.edit_text(f"❌ **Error:** `{str(e)}`")

    # 4. Start Clients Safely
    if SESSION_STRING:
        await user.start()
        print(">>> USERBOT STARTED SUCCESSFULLY <<<", flush=True)

    await bot.start()
    print(">>> BOT STARTED SUCCESSFULLY AND LISTENING <<<", flush=True)

    await idle()

    if user.is_connected:
        await user.stop()
    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())