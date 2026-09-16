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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
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
        logger.info("Firebase: Connected successfully")
    except Exception as e:
        logger.error(f"Firebase Init Error: {e}")

bot = Client(
    "tg_vault_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    workers=16
)

admin_states = {}
login_clients = {}
loaded_user_clients = []

# ----------------- SESSION POOL & DB ----------------- #

def sync_get_all_sessions():
    if not db:
        return []
    try:
        return [{"doc_id": d.id, **d.to_dict()} for d in db.collection("telegram_sessions").stream()]
    except Exception as e:
        logger.error(f"Get Sessions Error: {e}")
        return []

def sync_add_session(session_str: str, name: str):
    if not db:
        return False, "Database disconnected"
    try:
        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "created_at": time.time()
        })
        return True, "Success"
    except Exception as e:
        return False, str(e)

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
            cl = Client(
                f"pool_{s['doc_id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=s['session_string'],
                in_memory=True
            )
            await cl.start()
            new_pool.append((s['doc_id'], s.get('account_name', 'Userbot'), cl))
        except Exception as e:
            logger.warning(f"Failed to activate session {s['doc_id']}: {e}")
    
    for _, _, old_cl in loaded_user_clients:
        try:
            await old_cl.stop()
        except Exception:
            pass
    loaded_user_clients = new_pool
    logger.info(f"Session Pool Ready: {len(loaded_user_clients)} active userbots.")

# ----------------- SILENT AUTO-DELETE ----------------- #

async def silent_auto_delete(chat_id: int, message_ids: list, delay: int = AUTO_DELETE_TIME):
    valid_ids = [m for m in message_ids if m]
    if not valid_ids or delay <= 0:
        return
    await asyncio.sleep(delay)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=valid_ids)
    except Exception:
        pass

# ----------------- ADMIN DASHBOARD ----------------- #

def get_admin_markup(session_count: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ সেশন স্ট্রিং যোগ", callback_data="btn_add_str"),
            InlineKeyboardButton("📱 ফোন দিয়ে লগইন (OTP)", callback_data="btn_login_phone")
        ],
        [
            InlineKeyboardButton(f"📁 সেশন তালিকা ({session_count})", callback_data="btn_list_sessions"),
            InlineKeyboardButton("🗑️ সেশন ডিলিট মেনু", callback_data="btn_del_menu")
        ],
        [
            InlineKeyboardButton("🔄 রিফ্রেশ ড্যাশবোর্ড", callback_data="btn_refresh_panel")
        ]
    ])

@bot.on_message(filters.command(["admin", "panel"]) & filters.user(OWNER_ID) & filters.private)
async def admin_panel_handler(_, message: Message):
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    text = (
        "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
        f"  📊 **ডাটাবেস স্ট্যাটাস:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
        f"  👥 **সক্রিয় ইউজারবট সেশন:** `{len(sessions)} Connected`\n"
        f"  ⏱️ **অটো-ডিলিট টাইমার:** `{AUTO_DELETE_TIME}s (Silent)`\n\n"
        "  _নিচের বাটন চেপে নতুন সেশন যোগ বা নিয়ন্ত্রণ করুন:_\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    await message.reply_text(text, reply_markup=get_admin_markup(len(sessions)))

@bot.on_callback_query()
async def admin_callback_handler(_, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id != OWNER_ID:
        await query.answer("⛔ Access Denied", show_alert=True)
        return

    data = query.data

    if data == "btn_refresh_panel":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        text = (
            "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
            f"  📊 **ডাটাবেস স্ট্যাটাস:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
            f"  👥 **সক্রিয় ইউজারবট সেশন:** `{len(sessions)} Connected`\n"
            f"  ⏱️ **অটো-ডিলিট টাইমার:** `{AUTO_DELETE_TIME}s (Silent)`\n\n"
            "  _নিচের বাটন চেপে নতুন সেশন যোগ বা নিয়ন্ত্রণ করুন:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_admin_markup(len(sessions)))
        await query.answer("রিলোড সম্পন্ন 🔄")

    elif data == "btn_add_str":
        admin_states[user_id] = "WAIT_STRING"
        await query.message.reply_text(
            "📝 **Hydrogram / Pyrogram Session String পাঠান:**\n\n"
            "_(সরাসরি স্ট্রিং কপি করে এখানে সেন্ড করুন)_",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_login_phone":
        admin_states[user_id] = "WAIT_PHONE"
        await query.message.reply_text(
            "📱 **কান্ট্রি কোড সহ ফোন নম্বর পাঠান:**\n\n"
            "উদাহরণ: `+8801XXXXXXXXX`",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_list_sessions":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("কোনো সক্রিয় সেশন নেই!", show_alert=True)
            return
        text = "┏━━━━━━ 📁 **ACTIVE SESSIONS** ━━━━━━┓\n\n"
        for idx, s in enumerate(sessions, 1):
            text += f"  **{idx}.** `{s.get('account_name', 'Unknown')}`\n      ID: `{s['doc_id'][:10]}`\n"
        text += "\n┗━━━━━━━━━━━━━━━━━━━━━━━━┛"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ব্যাক", callback_data="btn_refresh_panel")]])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_del_menu":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("ডিলিট করার মতো কোনো সেশন নেই!", show_alert=True)
            return
        buttons = []
        for s in sessions:
            buttons.append([InlineKeyboardButton(f"❌ {s.get('account_name', 'Session')}", callback_data=f"del_{s['doc_id']}")])
        buttons.append([InlineKeyboardButton("🔙 ব্যাক", callback_data="btn_refresh_panel")])
        await query.message.edit_text("যে সেশনটি রিমুভ করতে চান সেটি সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()

    elif data.startswith("del_"):
        doc_id = data.split("_", 1)[1]
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("সেশন মুছে ফেলা হয়েছে!", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        text = (
            "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
            f"  📊 **ডাটাবেস স্ট্যাটাস:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
            f"  👥 **সক্রিয় ইউজারবট সেশন:** `{len(sessions)} Connected`\n"
            f"  ⏱️ **অটো-ডিলিট টাইমার:** `{AUTO_DELETE_TIME}s (Silent)`\n\n"
            "  _নিচের বাটন চেপে নতুন সেশন যোগ বা নিয়ন্ত্রণ করুন:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_admin_markup(len(sessions)))

# ----------------- ADMIN INPUT PROMPT HANDLER ----------------- #

@bot.on_message(filters.reply & filters.user(OWNER_ID) & filters.private)
async def admin_reply_processor(_, message: Message):
    state = admin_states.get(message.from_user.id)
    if not state:
        return

    text_input = (message.text or "").strip()

    if state == "WAIT_STRING":
        admin_states.pop(message.from_user.id, None)
        status_msg = await message.reply_text("🔄 **সেশন স্ট্রিং যাচাই করা হচ্ছে...**")

        test_cl = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_input, in_memory=True)
        try:
            await test_cl.start()
            me = await test_cl.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await test_cl.stop()

            success, err = await asyncio.to_thread(sync_add_session, text_input, acc_name)
            if success:
                asyncio.create_task(init_session_pool())
                await status_msg.edit_text(f"✅ **অ্যাকাউন্ট যুক্ত হয়েছে!**\n\n👤 `{acc_name}`")
            else:
                await status_msg.edit_text(f"⚠️ **ডাটাবেস ত্রুটি:** `{err}`")
        except Exception as e:
            await status_msg.edit_text(f"❌ **ভুল সেশন স্ট্রিং:** `{e}`")

    elif state == "WAIT_PHONE":
        admin_states.pop(message.from_user.id, None)
        phone = text_input.replace(" ", "").strip()
        status_msg = await message.reply_text("⏳ **টেলিগ্রাম ভেরিফিকেশন কোড পাঠানো হচ্ছে...**")

        temp_cl = Client(f"login_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await temp_cl.connect()
            code_info = await temp_cl.send_code(phone)
            login_clients[message.from_user.id] = {
                "client": temp_cl,
                "phone": phone,
                "phone_code_hash": code_info.phone_code_hash
            }
            admin_states[message.from_user.id] = "WAIT_OTP"
            await status_msg.edit_text(
                "📩 **প্রাপ্ত ওটিপি (OTP) কোডটি পাঠান:**\n\n"
                "💡 *টিপস:* ডিজিটের মাঝে স্পেস দিয়ে লিখুন (যেমন: `1 2 3 4 5`)",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_cl.is_connected:
                await temp_cl.disconnect()
            await status_msg.edit_text(f"❌ **ওটিপি পাঠানো যায়নি:** `{e}`")

    elif state == "WAIT_OTP":
        otp = text_input.replace(" ", "").replace("-", "").strip()
        data = login_clients.get(message.from_user.id)
        if not data:
            admin_states.pop(message.from_user.id, None)
            await message.reply_text("⚠️ সেশনের মেয়াদ শেষ। আবার চেষ্টা করুন।")
            return

        temp_cl = data["client"]
        status_msg = await message.reply_text("⏳ **কোড যাচাই করা হচ্ছে...**")

        try:
            await temp_cl.sign_in(data["phone"], data["phone_code_hash"], otp)
            sess_str = await temp_cl.export_session_string()
            me = await temp_cl.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_cl.disconnect()
            
            login_clients.pop(message.from_user.id, None)
            admin_states.pop(message.from_user.id, None)

            await asyncio.to_thread(sync_add_session, sess_str, acc_name)
            asyncio.create_task(init_session_pool())
            await status_msg.edit_text(f"✅ **লগইন সফল হয়েছে!**\n\n👤 **অ্যাকাউন্ট:** `{acc_name}`")
        except SessionPasswordNeeded:
            admin_states[message.from_user.id] = "WAIT_2FA"
            await status_msg.edit_text(
                "🔐 **টু-স্টেপ ভেরিফিকেশন অন করা আছে!**\n\nদয়া করে আপনার 2FA পাসওয়ার্ডটি পাঠান:",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_cl.is_connected:
                await temp_cl.disconnect()
            login_clients.pop(message.from_user.id, None)
            admin_states.pop(message.from_user.id, None)
            await status_msg.edit_text(f"❌ **লগইন ব্যর্থ:** `{e}`")

    elif state == "WAIT_2FA":
        password = text_input.strip()
        data = login_clients.get(message.from_user.id)
        if not data:
            admin_states.pop(message.from_user.id, None)
            await message.reply_text("⚠️ সেশনের মেয়াদ শেষ। আবার চেষ্টা করুন।")
            return

        temp_cl = data["client"]
        status_msg = await message.reply_text("⏳ **পাসওয়ার্ড চেক করা হচ্ছে...**")

        try:
            await temp_cl.check_password(password)
            sess_str = await temp_cl.export_session_string()
            me = await temp_cl.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_cl.disconnect()

            login_clients.pop(message.from_user.id, None)
            admin_states.pop(message.from_user.id, None)

            await asyncio.to_thread(sync_add_session, sess_str, acc_name)
            asyncio.create_task(init_session_pool())
            await status_msg.edit_text(f"✅ **লগইন সফল হয়েছে!**\n\n👤 **অ্যাকাউন্ট:** `{acc_name}`")
        except Exception as e:
            if temp_cl.is_connected:
                await temp_cl.disconnect()
            login_clients.pop(message.from_user.id, None)
            admin_states.pop(message.from_user.id, None)
            await status_msg.edit_text(f"❌ **ভুল 2FA পাসওয়ার্ড:** `{e}`")

# ----------------- FAST TELEGRAM DOWNLOADER ----------------- #

@bot.on_message(filters.private & ~filters.command(["start", "help", "admin", "panel", "ping"]))
async def telegram_media_fetcher(_, message: Message):
    text_str = (message.text or message.caption or "").strip()

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

    # ১. দ্রুততম সার্ভার-কপি (পাবলিক এবং ওপেন পোস্টের জন্য মিলি-সেকেন্ড বাইপাস)
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

    target_msg = None
    working_client = None

    # ইউজারবট ক্লায়েন্ট যাচাই
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
        await message.reply_text("❌ পোস্ট পাওয়া যায়নি অথবা অ্যাকাউন্টে চ্যানেলটির অ্যাক্সেস নেই।")
        return

    # ২. ইউজারবট ডিরেক্ট ফরোয়ার্ড বাইপাস
    try:
        fwd = await working_client.forward_messages(message.chat.id, chat_id, msg_id)
        if fwd:
            asyncio.create_task(silent_auto_delete(message.chat.id, [fwd.id, message.id]))
            return
    except Exception:
        pass

    # রেস্ট্রিক্টেড পোস্ট হলে অ্যালবাম চেক
    media_group = [target_msg]
    if target_msg.media_group_id:
        try:
            scan_ids = list(range(max(1, msg_id - 9), msg_id + 10))
            nearby = await working_client.get_messages(chat_id, scan_ids)
            filtered = [m for m in nearby if m and not m.empty and m.media_group_id == target_msg.media_group_id]
            if len(filtered) > 1:
                media_group = sorted(filtered, key=lambda x: x.id)
        except Exception:
            pass

    status = await message.reply_text("⚡ **ডাউনলোড হচ্ছে...**")

    # অ্যালবাম ডেলিভারি
    if len(media_group) > 1:
        files, input_media = [], []
        for idx, m in enumerate(media_group):
            try:
                ext = ".mp4" if m.video else ".jpg" if m.photo else ".mp3" if m.audio else ".bin"
                dest = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}_{idx}{ext}")
                path = await working_client.download_media(m, file_name=dest)
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
            await status.edit_text("❌ অ্যালবাম সংগ্রহ করা সম্ভব হয়নি।")

        for f in files:
            try:
                os.remove(f)
            except Exception:
                pass
        return

    # সিঙ্গেল মিডিয়া ডেলিভারি
    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ".bin"
    single_dest = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}{ext}")
    file_path = await working_client.download_media(target_msg, file_name=single_dest)

    if not file_path or not os.path.exists(file_path):
        await status.edit_text("❌ মিডিয়া ফাইল ডাউনলোড ব্যর্থ হয়েছে।")
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

# ----------------- BASE COMMANDS ----------------- #

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    await message.reply_text(
        f"👋 **স্বাগতম, {message.from_user.first_name}!**\n\n"
        "⚡ **আল্ট্রা-ফাস্ট টেলিগ্রাম ফাইল সেভার রেডি।**\n"
        "যেকোনো টেলিগ্রাম পোস্ট বা অ্যালবামের লিংক পাঠান।"
    )

@bot.on_message(filters.command("ping") & filters.private)
async def ping_handler(_, message: Message):
    t0 = time.time()
    msg = await message.reply_text("⚡ Ping...")
    await msg.edit_text(f"🚀 **ল্যাটেন্সি:** `{(time.time() - t0) * 1000:.1f}ms`")

# ----------------- WEB SERVER & RUNNER ----------------- #

async def web_server():
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def main():
    await bot.start()
    try:
        await bot.set_bot_commands([
            BotCommand("start", "বট শুরু করুন"),
            BotCommand("admin", "অ্যাডমিন প্যানেল"),
            BotCommand("ping", "সার্ভার স্পিড চেক")
        ])
    except Exception:
        pass
    await init_session_pool()
    await web_server()
    logger.info("Bot Online!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())