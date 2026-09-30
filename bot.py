import os
import re
import time
import json
import shutil
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
    InputMediaDocument,
    ChatMemberUpdated
)
from hydrogram.errors import SessionPasswordNeeded, FloodWait, PhoneNumberInvalid, PhoneCodeInvalid, PasswordHashInvalid

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment Configuration
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8952918726:AAGnKZm-S8hmBaWzltPfrdWRcyVHGVx44d0").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()

OWNER_ID = 6142774415
PORT = int(os.environ.get("PORT", "8080"))
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "120"))

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Firebase Setup
db = None
if FIREBASE_KEY_RAW:
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        cred = credentials.Certificate(json.loads(FIREBASE_KEY_RAW))
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase: Connected successfully.")
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

# States & Pools
user_states = {}
login_clients = {}
loaded_user_clients = []
loaded_admins = {}
active_tasks = {}

mem_broadcast_settings = {
    "is_enabled": False,
    "message": "🔥 Join our channel for updates!\n[Join Channel|https://t.me/telegram]",
    "media_url": "",
    "interval_minutes": 2
}
mem_broadcast_chats = {}

# ----------------- HELPERS ----------------- #

def human_bytes(size: int) -> str:
    if not size:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def get_storage_stats():
    total, used, free = shutil.disk_usage(".")
    percent = (used / total) * 100 if total > 0 else 0
    dl_size = 0
    dl_files = 0
    if os.path.exists(DOWNLOAD_DIR):
        for path, _, files in os.walk(DOWNLOAD_DIR):
            for f in files:
                fp = os.path.join(path, f)
                dl_size += os.path.getsize(fp)
                dl_files += 1
    return {
        "total": total, "used": used, "free": free,
        "percent": percent, "dl_size": dl_size, "dl_files": dl_files
    }

class ProgressTracker:
    def __init__(self, message: Message, action_text: str, task_id: str = None, task_type: str = "download"):
        self.message = message
        self.action_text = action_text
        self.task_id = task_id
        self.task_type = task_type
        self.last_update_time = time.time()

        if self.task_id:
            active_tasks[self.task_id] = {
                "name": action_text, "type": task_type,
                "current": 0, "total": 0, "percent": 0.0,
                "speed": "0 B/s", "updated_at": time.time()
            }

    async def progress(self, current: int, total: int):
        now = time.time()
        percent = (current / total) * 100 if total > 0 else 0

        if self.task_id and self.task_id in active_tasks:
            time_diff = max(0.1, now - self.last_update_time)
            bytes_diff = current - active_tasks[self.task_id]["current"]
            speed = f"{human_bytes(int(bytes_diff / time_diff))}/s" if bytes_diff > 0 else "0 B/s"
            active_tasks[self.task_id].update({
                "current": current, "total": total,
                "percent": round(percent, 1), "speed": speed, "updated_at": now
            })

        if now - self.last_update_time < 3 and current != total:
            return
        self.last_update_time = now

        filled = int(percent / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        text = (
            f"⚡ **{self.action_text}**\n\n"
            f"[{bar}] `{percent:.1f}%`\n"
            f"📥 `{human_bytes(current)}` / `{human_bytes(total)}`"
        )
        try:
            await self.message.edit_text(text)
        except Exception:
            pass

    def finish(self):
        if self.task_id:
            active_tasks.pop(self.task_id, None)

# ----------------- DATABASE HELPERS ----------------- #

def sync_get_all_sessions():
    if not db:
        return []
    try:
        return [{"doc_id": d.id, **d.to_dict()} for d in db.collection("telegram_sessions").stream()]
    except Exception:
        return []

def sync_add_session(session_str: str, name: str, user_id: int):
    if not db:
        return False, "Database offline"
    try:
        db.collection("telegram_sessions").document(str(user_id)).set({
            "session_string": session_str,
            "account_name": name,
            "user_id": user_id,
            "updated_at": time.time()
        })
        return True, "Success"
    except Exception as e:
        return False, str(e)

def sync_delete_session(doc_id: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").document(str(doc_id)).delete()
        return True
    except Exception:
        return False

def sync_get_user_session(user_id: int):
    if not db:
        return None
    try:
        doc = db.collection("telegram_sessions").document(str(user_id)).get()
        return doc.to_dict() if doc.exists else None
    except Exception:
        return None

def sync_get_all_admins():
    admins = {}
    if not db:
        return admins
    try:
        for d in db.collection("bot_admins").stream():
            if d.id.isdigit():
                admins[int(d.id)] = d.to_dict()
    except Exception as e:
        logger.error(f"Get Admins Error: {e}")
    return admins

def is_any_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in loaded_admins

async def init_session_pool():
    global loaded_user_clients, loaded_admins
    loaded_admins = await asyncio.to_thread(sync_get_all_admins)
    if not db:
        return
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    new_pool = []
    for s in sessions:
        try:
            cl = Client(
                f"pool_{s.get('user_id', s['doc_id'])}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=s['session_string'],
                in_memory=True
            )
            await cl.start()
            new_pool.append((str(s.get('user_id', s['doc_id'])), s.get('account_name', 'Userbot'), cl))
        except Exception as e:
            logger.warning(f"Failed session {s['doc_id']}: {e}")
    for _, _, old_cl in loaded_user_clients:
        try:
            await old_cl.stop()
        except Exception:
            pass
    loaded_user_clients = new_pool
    logger.info(f"Pool refreshed: {len(loaded_user_clients)} accounts active.")

# ----------------- USER SELF LOGIN & PROFILE UI ----------------- #

def get_login_ui(is_logged_in: bool = False, acc_name: str = ""):
    if is_logged_in:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"👤 Account: {acc_name[:15]}", callback_data="btn_noop")],
            [InlineKeyboardButton("🚪 Log Out Session", callback_data="btn_user_logout")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Login With Telegram", callback_data="btn_start_login")]
    ])

@bot.on_message(filters.command("login") & filters.private)
async def login_command_handler(_, message: Message):
    user_id = message.from_user.id
    user_sess = await asyncio.to_thread(sync_get_user_session, user_id)
    if user_sess:
        text = (
            "✨ **Your Telegram Session is Active!**\n\n"
            f"👤 **Account:** `{user_sess.get('account_name', 'Connected')}`\n"
            "🔒 You can download private channels/posts freely."
        )
        return await message.reply_text(text, reply_markup=get_login_ui(True, user_sess.get('account_name', '')))

    text = (
        "🔐 **Telegram Self-Login Portal**\n\n"
        "Connect your Telegram account securely via OTP to bypass private media restrictions.\n\n"
        "• _Directly authenticates with official Telegram servers._\n"
        "• _Click below to begin._"
    )
    await message.reply_text(text, reply_markup=get_login_ui(False))

@bot.on_callback_query(filters.regex(r"^btn_start_login$"))
async def callback_start_login(_, query: CallbackQuery):
    user_id = query.from_user.id
    user_states[user_id] = "WAIT_PHONE"
    text = (
        "📱 **Enter your Telegram Phone Number**\n\n"
        "Please send your full international number with country code:\n"
        "👉 Example: `+8801700000000`"
    )
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="btn_cancel_login")]
    ]))
    await query.answer()

@bot.on_callback_query(filters.regex(r"^btn_user_logout$"))
async def callback_user_logout(_, query: CallbackQuery):
    user_id = query.from_user.id
    await asyncio.to_thread(sync_delete_session, str(user_id))
    asyncio.create_task(init_session_pool())
    await query.message.edit_text(
        "👋 **Logged Out Successfully!**\n\nYour session was wiped from our memory.",
        reply_markup=get_login_ui(False)
    )
    await query.answer("Logged out.")

@bot.on_callback_query(filters.regex(r"^btn_noop$"))
async def callback_noop(_, query: CallbackQuery):
    await query.answer()

@bot.on_callback_query(filters.regex(r"^btn_cancel_login$"))
async def callback_cancel_login(_, query: CallbackQuery):
    user_id = query.from_user.id
    session_data = login_clients.pop(user_id, None)
    user_states.pop(user_id, None)
    if session_data and session_data.get("cl") and session_data["cl"].is_connected:
        await session_data["cl"].disconnect()
    await query.message.edit_text("❌ Login cancelled.", reply_markup=get_login_ui(False))
    await query.answer()

# ----------------- LOGIN INPUT HANDLER ----------------- #

@bot.on_message(filters.private & ~filters.command(["start", "help", "admin", "login", "ping", "storage", "clean"]))
async def user_input_router(_, message: Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    text_input = (message.text or "").strip()

    if not state:
        # Pass to Telegram Link Downloader if not in interactive flow
        return await telegram_media_fetcher(_, message)

    if state == "WAIT_PHONE":
        phone = text_input.replace(" ", "")
        status = await message.reply_text("⏳ Generating login request...")
        cl = Client(f"usr_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await cl.connect()
            c = await cl.send_code(phone)
            login_clients[user_id] = {"cl": cl, "phone": phone, "hash": c.phone_code_hash}
            user_states[user_id] = "WAIT_OTP"
            await status.edit_text(
                f"📩 **Telegram sent an OTP to `{phone}`!**\n\n"
                "Please enter the code in format: `1 2 3 4 5` (separated with space):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="btn_cancel_login")]])
            )
        except PhoneNumberInvalid:
            await status.edit_text("❌ **Invalid phone number.** Please check country code and try again.")
            user_states.pop(user_id, None)
        except Exception as e:
            await status.edit_text(f"❌ Error: `{e}`")
            user_states.pop(user_id, None)

    elif state == "WAIT_OTP":
        otp = text_input.replace(" ", "").replace("-", "")
        data = login_clients.get(user_id)
        if not data:
            user_states.pop(user_id, None)
            return await message.reply_text("Session expired. Please `/login` again.")

        cl = data["cl"]
        status = await message.reply_text("⚡ Verifying OTP...")
        try:
            await cl.sign_in(data["phone"], data["hash"], otp)
            s_str = await cl.export_session_string()
            me = await cl.get_me()
            acc = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await cl.disconnect()

            login_clients.pop(user_id, None)
            user_states.pop(user_id, None)

            await asyncio.to_thread(sync_add_session, s_str, acc, user_id)
            asyncio.create_task(init_session_pool())

            await status.edit_text(
                f"✅ **Login Successful!**\n\n👤 **Account:** `{acc}`\nYou can now send restricted links to download.",
                reply_markup=get_login_ui(True, acc)
            )
        except SessionPasswordNeeded:
            user_states[user_id] = "WAIT_2FA"
            await status.edit_text(
                "🔐 **Two-Step Verification Enabled!**\n\nPlease enter your 2FA cloud password:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="btn_cancel_login")]])
            )
        except PhoneCodeInvalid:
            await status.edit_text("❌ Invalid OTP code. Try typing it with spaces (e.g. `1 2 3 4 5`).")
        except Exception as e:
            await cl.disconnect()
            login_clients.pop(user_id, None)
            user_states.pop(user_id, None)
            await status.edit_text(f"❌ Login failed: `{e}`")

    elif state == "WAIT_2FA":
        data = login_clients.get(user_id)
        if not data:
            user_states.pop(user_id, None)
            return await message.reply_text("Session expired. Try `/login` again.")

        cl = data["cl"]
        status = await message.reply_text("🔐 Verifying 2FA password...")
        try:
            await cl.check_password(text_input)
            s_str = await cl.export_session_string()
            me = await cl.get_me()
            acc = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await cl.disconnect()

            login_clients.pop(user_id, None)
            user_states.pop(user_id, None)

            await asyncio.to_thread(sync_add_session, s_str, acc, user_id)
            asyncio.create_task(init_session_pool())

            await status.edit_text(
                f"✅ **Authentication Successful!**\n\n👤 **Account:** `{acc}`\nReady to fetch media.",
                reply_markup=get_login_ui(True, acc)
            )
        except PasswordHashInvalid:
            await status.edit_text("❌ **Incorrect password.** Please re-enter your 2FA password:")
        except Exception as e:
            await status.edit_text(f"❌ Verification failed: `{e}`")

# ----------------- COMPACT ADMIN PANEL ----------------- #

def get_compact_admin_markup(session_count: int, is_broadcast: bool):
    b_tag = "🟢 ON" if is_broadcast else "🔴 OFF"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"👥 Sessions ({session_count})", callback_data="adm_sessions"),
            InlineKeyboardButton(f"📢 Broadcast: {b_tag}", callback_data="adm_toggle_bc")
        ],
        [
            InlineKeyboardButton("💾 Disk Stats", callback_data="adm_storage"),
            InlineKeyboardButton("🧹 Flush Temp", callback_data="adm_clean")
        ],
        [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="adm_refresh")]
    ])

@bot.on_message(filters.command(["admin", "panel"]) & filters.private)
async def compact_admin_panel(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text("⛔ **Access Denied.**")

    sessions = await asyncio.to_thread(sync_get_all_sessions)
    st = get_storage_stats()
    text = (
        "⚡ **CONTROL PANEL**\n"
        "──────────────────────\n"
        f"• **Active Userbots:** `{len(sessions)}`\n"
        f"• **Disk Usage:** `{st['percent']:.1f}%` ({human_bytes(st['used'])})\n"
        f"• **Auto-Delete:** `{AUTO_DELETE_TIME}s`\n"
        "──────────────────────"
    )
    await message.reply_text(text, reply_markup=get_compact_admin_markup(len(sessions), mem_broadcast_settings["is_enabled"]))

@bot.on_callback_query(filters.regex(r"^adm_"))
async def compact_admin_callbacks(_, query: CallbackQuery):
    user_id = query.from_user.id
    if not is_any_admin(user_id):
        return await query.answer("Access Denied", show_alert=True)

    data = query.data

    if data == "adm_refresh":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        st = get_storage_stats()
        text = (
            "⚡ **CONTROL PANEL**\n"
            "──────────────────────\n"
            f"• **Active Userbots:** `{len(sessions)}`\n"
            f"• **Disk Usage:** `{st['percent']:.1f}%` ({human_bytes(st['used'])})\n"
            f"• **Auto-Delete:** `{AUTO_DELETE_TIME}s`\n"
            "──────────────────────"
        )
        await query.message.edit_text(text, reply_markup=get_compact_admin_markup(len(sessions), mem_broadcast_settings["is_enabled"]))
        await query.answer("Refreshed")

    elif data == "adm_sessions":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        buttons = []
        for s in sessions:
            name = s.get('account_name', 'User')[:18]
            buttons.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"del_sess_{s['doc_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="adm_refresh")])
        await query.message.edit_text("📁 **Registered Accounts:**\n_Click any to disconnect_", reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()

    elif data.startswith("del_sess_"):
        doc_id = data.replace("del_sess_", "")
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("Session removed!", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        await query.message.edit_text(f"Updated. Active: {len(sessions)}", reply_markup=get_compact_admin_markup(len(sessions), mem_broadcast_settings["is_enabled"]))

    elif data == "adm_clean":
        freed = 0
        if os.path.exists(DOWNLOAD_DIR):
            for f in os.listdir(DOWNLOAD_DIR):
                fp = os.path.join(DOWNLOAD_DIR, f)
                try:
                    freed += os.path.getsize(fp)
                    os.unlink(fp) if os.path.isfile(fp) else shutil.rmtree(fp)
                except Exception:
                    pass
        await query.answer(f"Freed {human_bytes(freed)}", show_alert=True)

    elif data == "adm_toggle_bc":
        mem_broadcast_settings["is_enabled"] = not mem_broadcast_settings["is_enabled"]
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        await query.message.edit_reply_markup(reply_markup=get_compact_admin_markup(len(sessions), mem_broadcast_settings["is_enabled"]))
        await query.answer(f"Broadcast: {'Enabled' if mem_broadcast_settings['is_enabled'] else 'Disabled'}")

    elif data == "adm_storage":
        st = get_storage_stats()
        await query.answer(f"Free: {human_bytes(st['free'])} | Temp: {human_bytes(st['dl_size'])}", show_alert=True)

# ----------------- DOWNLOAD & FORWARD CORE ----------------- #

async def telegram_media_fetcher(_, message: Message):
    text_str = (message.text or message.caption or "").strip()
    p_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    pub_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if not (p_match or pub_match):
        return

    chat_id = int("-100" + p_match.group(1)) if p_match else pub_match.group(1)
    msg_id = int(p_match.group(2)) if p_match else int(pub_match.group(2))

    # Fast Copy
    try:
        copied = await bot.copy_message(message.chat.id, chat_id, msg_id)
        if copied:
            asyncio.create_task(silent_auto_delete(message.chat.id, [copied.id, message.id]))
            return
    except Exception:
        pass

    if not loaded_user_clients:
        await init_session_pool()

    if not loaded_user_clients:
        return await message.reply_text("❌ No user session active. Connect your account via `/login`.")

    target_msg, working_client = None, None
    for _, _, u_client in loaded_user_clients:
        try:
            msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=10)
            if msg and not msg.empty:
                target_msg, working_client = msg, u_client
                break
        except Exception:
            continue

    if not target_msg or target_msg.empty:
        return await message.reply_text("❌ Content is restricted or userbots haven't joined this channel.")

    status = await message.reply_text("⚡ **Downloading media...**")
    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ""
    if not ext and target_msg.document:
        ext = os.path.splitext(target_msg.document.file_name or "")[1] or ".bin"

    file_path = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}{ext}")
    tracker = ProgressTracker(status, "Downloading...", task_id=f"dl_{message.id}")

    try:
        dl_res = await working_client.download_media(target_msg, file_name=file_path, progress=tracker.progress)
        tracker.finish()
        if not dl_res or not os.path.exists(file_path):
            return await status.edit_text("❌ Download failed.")

        up_tracker = ProgressTracker(status, "Uploading...", task_id=f"up_{message.id}", task_type="upload")
        sent = None
        cap = target_msg.caption or ""

        if target_msg.video:
            sent = await bot.send_video(message.chat.id, file_path, caption=cap, progress=up_tracker.progress)
        elif target_msg.photo:
            sent = await bot.send_photo(message.chat.id, file_path, caption=cap, progress=up_tracker.progress)
        elif target_msg.audio:
            sent = await bot.send_audio(message.chat.id, file_path, caption=cap, progress=up_tracker.progress)
        else:
            sent = await bot.send_document(message.chat.id, file_path, caption=cap, progress=up_tracker.progress)

        up_tracker.finish()
        await status.delete()
        if sent:
            asyncio.create_task(silent_auto_delete(message.chat.id, [sent.id, message.id]))
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def silent_auto_delete(chat_id: int, message_ids: list, delay: int = AUTO_DELETE_TIME):
    await asyncio.sleep(delay)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception:
        pass

# ----------------- BASE & WEB ----------------- #

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    text = (
        f"👋 **Hey {message.from_user.first_name}!**\n\n"
        "⚡ Send any public or private Telegram post/media link to download.\n"
        "🔐 Use `/login` to link your Telegram account for restricted channels."
    )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Login Account", callback_data="btn_start_login")]
    ]))

async def handle_root(_):
    return web.Response(text="<h1>Clipwell Engine Running</h1>", content_type="text/html")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def main():
    await bot.start()
    try:
        await bot.set_bot_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("login", "Telegram Self-Login"),
            BotCommand("admin", "Slim Control Panel")
        ])
    except Exception:
        pass
    await init_session_pool()
    await web_server()
    logger.info("Bot Online with User Self-Login Enabled!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())