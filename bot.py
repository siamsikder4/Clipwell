import os
import re
import time
import json
import asyncio
import logging
from aiohttp import web
from hydrogram import Client, filters
from hydrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ForceReply,
    BotCommand,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaDocument
)
from hydrogram.errors import SessionPasswordNeeded, FloodWait

# Logging Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

# Configuration
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8952918726:AAGnKZm-S8hmBaWzltPfrdWRcyVHGVx44d0").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()

OWNER_ID = 6142774415
PORT = int(os.environ.get("PORT", "8080"))
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "120"))  # Seconds

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Database Initialization
db = None
if FIREBASE_KEY_RAW:
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        cred = credentials.Certificate(json.loads(FIREBASE_KEY_RAW))
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase: Connected")
    except Exception as e:
        logger.error(f"Firebase Init Error: {e}")

bot = Client("tg_saver_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True, workers=16)

admin_states = {}
login_clients = {}
loaded_user_clients = []

# ----------------- SESSION POOL & DB ----------------- #

def sync_get_all_sessions():
    if not db:
        return []
    try:
        return [{"doc_id": d.id, **d.to_dict()} for d in db.collection("telegram_sessions").stream()]
    except Exception:
        return []

def sync_add_session(session_str: str, name: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").add({"session_string": session_str, "account_name": name, "created_at": time.time()})
        return True
    except Exception:
        return False

def sync_delete_session(doc_id: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").document(doc_id).delete()
        return True
    except Exception:
        return False

async def init_session_pool():
    global loaded_user_clients
    if not db:
        return
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    new_pool = []
    for s in sessions:
        try:
            cl = Client(f"pool_{s['doc_id']}", api_id=API_ID, api_hash=API_HASH, session_string=s['session_string'], in_memory=True)
            await cl.start()
            new_pool.append((s['doc_id'], s.get('account_name', 'Userbot'), cl))
        except Exception as e:
            logger.warning(f"Failed to start pool: {e}")
    for _, _, old_cl in loaded_user_clients:
        try:
            await old_cl.stop()
        except Exception:
            pass
    loaded_user_clients = new_pool
    logger.info(f"Userbots Active: {len(loaded_user_clients)}")

# ----------------- SILENT AUTO DELETE ----------------- #

async def silent_auto_delete(chat_id: int, message_ids: list, delay: int = AUTO_DELETE_TIME):
    """কোনো দৃশ্যমান মেসেজ ছাড়া নির্দিষ্ট সময় পর ফাইল স্বয়ংক্রিয়ভাবে মুছে দেবে"""
    valid_ids = [m for m in message_ids if m]
    if not valid_ids or delay <= 0:
        return
    await asyncio.sleep(delay)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=valid_ids)
    except Exception:
        pass

# ----------------- TELEGRAM HANDLER ----------------- #

@bot.on_message(filters.private & ~filters.command(["start", "help", "admin", "panel", "ping"]))
async def telegram_download_handler(client: Client, message: Message):
    text_str = (message.text or message.caption or "").strip()

    # টেলিগ্রাম পোস্ট লিংক ডিটেকশন
    p_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    pub_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if not (p_match or pub_match):
        return

    try:
        await message.react("🫡")
    except Exception:
        pass

    if p_match:
        chat_id = int("-100" + p_match.group(1))
        msg_id = int(p_match.group(2))
    else:
        chat_id = pub_match.group(1)
        msg_id = int(pub_match.group(2))

    # ১. আল্ট্রা-ফাস্ট বাইপাস (মিলি-সেকেন্ডে টেলিগ্রাম সার্ভার কপি)
    try:
        copied = await bot.copy_message(chat_id=message.chat.id, from_chat_id=chat_id, message_id=msg_id)
        if copied:
            asyncio.create_task(silent_auto_delete(message.chat.id, [copied.id, message.id]))
            return
    except Exception:
        pass

    if not loaded_user_clients:
        await init_session_pool()

    if not loaded_user_clients:
        await message.reply_text("❌ কোনো সক্রিয় ইউজারবট সেশন পাওয়া যায়নি। `/admin` দিয়ে সেশন অ্যাড করুন।")
        return

    # ইউজারবট দিয়ে প্রাইভেট/রেস্ট্রিক্টেড মেসেজ চেক
    target_msg = None
    working_client = None

    for _, _, u_client in loaded_user_clients:
        try:
            msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=4)
            if msg and not msg.empty:
                target_msg = msg
                working_client = u_client
                break
        except Exception:
            continue

    if not target_msg or target_msg.empty:
        await message.reply_text("❌ পোস্টটি পাওয়া যায়নি বা অ্যাক্সেস নেই।")
        return

    # ২. ইউজারবট ডিরেক্ট সার্ভার ফরওয়ার্ড (মিলি-সেকেন্ড বাইপাস)
    try:
        fwd = await working_client.forward_messages(message.chat.id, chat_id, msg_id)
        if fwd:
            asyncio.create_task(silent_auto_delete(message.chat.id, [fwd.id, message.id]))
            return
    except Exception:
        pass

    # রেস্ট্রিক্টেড পোস্ট হলে অ্যালবাম স্ক্যান
    media_group = [target_msg]
    if target_msg.media_group_id:
        try:
            scan_ids = list(range(max(1, msg_id - 8), msg_id + 9))
            nearby = await working_client.get_messages(chat_id, scan_ids)
            filtered = [m for m in nearby if m and not m.empty and m.media_group_id == target_msg.media_group_id]
            if len(filtered) > 1:
                media_group = sorted(filtered, key=lambda x: x.id)
        except Exception:
            pass

    status = await message.reply_text("⚡ **ডাউনলোড হচ্ছে...**")

    # অ্যালবাম প্রসেসিং
    if len(media_group) > 1:
        files, input_media = [], []
        for idx, m in enumerate(media_group):
            try:
                ext = ".mp4" if m.video else ".jpg" if m.photo else ".mp3" if m.audio else ".bin"
                path = await working_client.download_media(m, file_name=os.path.join(DOWNLOAD_DIR, f"{user_id}_{int(time.time())}_{idx}{ext}"))
                if path and os.path.exists(path):
                    files.append(path)
                    cap = m.caption or (target_msg.caption if idx == 0 else "")
                    if m.video:
                        input_media.append(InputMediaVideo(path, caption=cap, supports_streaming=True))
                    elif m.photo:
                        input_media.append(InputMediaPhoto(path, caption=cap))
                    elif m.audio:
                        input_media.append(InputMediaAudio(path, caption=cap))
                    else:
                        input_media.append(InputMediaDocument(path, caption=cap))
            except Exception:
                pass

        if len(input_media) >= 2:
            sent_msgs = []
            for i in range(0, len(input_media), 10):
                res = await bot.send_media_group(message.chat.id, input_media[i:i + 10])
                sent_msgs.extend(res)
            await status.delete()
            asyncio.create_task(silent_auto_delete(message.chat.id, [m.id for m in sent_msgs] + [message.id]))
        else:
            await status.edit_text("❌ অ্যালবাম প্রসেস করা সম্ভব হয়নি।")

        for f in files:
            try:
                os.remove(f)
            except Exception:
                pass
        return

    # সিঙ্গেল মিডিয়া প্রসেসিং
    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ".bin"
    file_path = await working_client.download_media(target_msg, file_name=os.path.join(DOWNLOAD_DIR, f"{user_id}_{int(time.time())}{ext}"))

    if not file_path or not os.path.exists(file_path):
        await status.edit_text("❌ ডাউনলোড ব্যর্থ হয়েছে।")
        return

    cap = target_msg.caption or ""
    sent = None

    if target_msg.video:
        sent = await bot.send_video(message.chat.id, file_path, caption=cap, supports_streaming=True)
    elif target_msg.photo:
        sent = await bot.send_photo(message.chat.id, file_path, caption=cap)
    elif target_msg.audio:
        sent = await bot.send_audio(message.chat.id, file_path, caption=cap)
    else:
        sent = await bot.send_document(message.chat.id, file_path, caption=cap)

    await status.delete()

    if sent:
        asyncio.create_task(silent_auto_delete(message.chat.id, [sent.id, message.id]))

    try:
        os.remove(file_path)
    except Exception:
        pass

# ----------------- BASIC COMMANDS ----------------- #

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m: Message):
    await m.reply_text(
        "⚡ **ফাস্ট টেলিগ্রাম ডাউনলোডার রেডি!**\n\n"
        "যেকোনো পাবলিক বা প্রাইভেট চ্যানেলের পোস্ট/অ্যালবাম লিংক পাঠান।"
    )

@bot.on_message(filters.command("ping") & filters.private)
async def ping_cmd(_, m: Message):
    t0 = time.time()
    msg = await m.reply_text("⚡...")
    await msg.edit_text(f"🚀 **ল্যাটেন্সি:** `{(time.time() - t0) * 1000:.1f}ms`")

# ----------------- ADMIN DASHBOARD ----------------- #

@bot.on_message(filters.command(["admin", "panel"]) & filters.user(OWNER_ID) & filters.private)
async def admin_panel(_, m: Message):
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    text = (
        "🎛️ **অ্যাডমিন কন্ট্রোল**\n\n"
        f"🟢 সক্রিয় ইউজারবট: `{len(sessions)}` টি\n"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ সেশন স্ট্রিং যোগ", callback_data="add_sess"),
         InlineKeyboardButton("📱 ফোন দিয়ে লগইন", callback_data="login_phone")],
        [InlineKeyboardButton(f"📁 সেশন তালিকা ({len(sessions)})", callback_data="list_sess")]
    ])
    await m.reply_text(text, reply_markup=markup)

@bot.on_callback_query()
async def cb_handler(_, q: CallbackQuery):
    if q.from_user.id != OWNER_ID:
        return await q.answer("অ্যাক্সেস নেই!", show_alert=True)

    if q.data == "add_sess":
        admin_states[q.from_user.id] = "WAIT_STRING"
        await q.message.reply_text("📝 Hydrogram/Pyrogram Session String দিন:", reply_markup=ForceReply(selective=True))
        await q.answer()

    elif q.data == "login_phone":
        admin_states[q.from_user.id] = "WAIT_PHONE"
        await q.message.reply_text("📱 কান্ট্রি কোড সহ নম্বর দিন (+880...):", reply_markup=ForceReply(selective=True))
        await q.answer()

    elif q.data == "list_sess":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        buttons = [[InlineKeyboardButton(f"❌ {s.get('account_name', 'User')}", callback_data=f"del_{s['doc_id']}")] for s in sessions]
        await q.message.edit_text("ডিলিট করতে যেকোনো অ্যাকাউন্টে চাপ দিন:", reply_markup=InlineKeyboardMarkup(buttons or [[InlineKeyboardButton("খালি", callback_data="none")]]))
        await q.answer()

    elif q.data.startswith("del_"):
        await asyncio.to_thread(sync_delete_session, q.data.split("_")[1])
        asyncio.create_task(init_session_pool())
        await q.answer("সেশন ডিলিট হয়েছে!", show_alert=True)
        await q.message.delete()

@bot.on_message(filters.reply & filters.user(OWNER_ID) & filters.private)
async def admin_input_handler(_, m: Message):
    state = admin_states.get(m.from_user.id)
    if not state:
        return

    txt = m.text.strip()

    if state == "WAIT_STRING":
        admin_states.pop(m.from_user.id, None)
        msg = await m.reply_text("🔄 যাচাই করা হচ্ছে...")
        try:
            cl = Client(f"verify_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=txt, in_memory=True)
            await cl.start()
            me = await cl.get_me()
            await cl.stop()
            await asyncio.to_thread(sync_add_session, txt, me.first_name)
            asyncio.create_task(init_session_pool())
            await msg.edit_text(f"✅ কানেক্টেড: `{me.first_name}`")
        except Exception as e:
            await msg.edit_text(f"❌ ত্রুটি: {e}")

    elif state == "WAIT_PHONE":
        admin_states.pop(m.from_user.id, None)
        phone = txt.replace(" ", "")
        cl = Client(f"tmp_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await cl.connect()
        code = await cl.send_code(phone)
        login_clients[m.from_user.id] = {"cl": cl, "phone": phone, "hash": code.phone_code_hash}
        admin_states[m.from_user.id] = "WAIT_OTP"
        await m.reply_text("📩 ওটিপি (OTP) কোড পাঠান (মাঝখানে স্পেস দিয়ে, যেমন: 1 2 3 4 5):", reply_markup=ForceReply(selective=True))

    elif state == "WAIT_OTP":
        otp = txt.replace(" ", "").replace("-", "")
        data = login_clients.pop(m.from_user.id, None)
        admin_states.pop(m.from_user.id, None)
        if not data:
            return
        cl = data["cl"]
        try:
            await cl.sign_in(data["phone"], data["hash"], otp)
            sess = await cl.export_session_string()
            me = await cl.get_me()
            await cl.disconnect()
            await asyncio.to_thread(sync_add_session, sess, me.first_name)
            asyncio.create_task(init_session_pool())
            await m.reply_text(f"✅ লগইন সম্পন্ন: `{me.first_name}`")
        except SessionPasswordNeeded:
            login_clients[m.from_user.id] = {"cl": cl}
            admin_states[m.from_user.id] = "WAIT_2FA"
            await m.reply_text("🔐 2-Step Verification পাসওয়ার্ড পাঠান:", reply_markup=ForceReply(selective=True))
        except Exception as e:
            await cl.disconnect()
            await m.reply_text(f"❌ লগইন ব্যর্থ: {e}")

    elif state == "WAIT_2FA":
        data = login_clients.pop(m.from_user.id, None)
        admin_states.pop(m.from_user.id, None)
        if not data:
            return
        cl = data["cl"]
        try:
            await cl.check_password(txt)
            sess = await cl.export_session_string()
            me = await cl.get_me()
            await cl.disconnect()
            await asyncio.to_thread(sync_add_session, sess, me.first_name)
            asyncio.create_task(init_session_pool())
            await m.reply_text(f"✅ লগইন সম্পন্ন: `{me.first_name}`")
        except Exception as e:
            await cl.disconnect()
            await m.reply_text(f"❌ পাসওয়ার্ড ভুল: {e}")

# ----------------- WEB SERVER & STARTUP ----------------- #

async def web_server():
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def main():
    await bot.start()
    await init_session_pool()
    await web_server()
    logger.info("Bot is active!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())