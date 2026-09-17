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
    InputMediaDocument,
    ChatMemberUpdated
)
from hydrogram.errors import SessionPasswordNeeded, FloodWait

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
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "120"))  # Seconds

# High-speed RAM disk if available, fallback to regular disk
DOWNLOAD_DIR = "/dev/shm" if os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK) else "downloads"
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

admin_states = {}
login_clients = {}
loaded_user_clients = []

# Default broadcast settings with 2-minute interval
mem_broadcast_settings = {
    "is_enabled": True,
    "message": "🔥 Join our channel for high-speed updates!",
    "interval_minutes": 2
}
mem_broadcast_chats = set()

# ----------------- DATABASE HELPERS ----------------- #

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

def sync_get_broadcast_settings():
    if not db:
        return mem_broadcast_settings
    try:
        doc = db.collection("bot_settings").document("broadcast_config").get()
        if doc.exists:
            return doc.to_dict()
        return mem_broadcast_settings
    except Exception:
        return mem_broadcast_settings

def sync_save_broadcast_settings(data: dict):
    global mem_broadcast_settings
    mem_broadcast_settings.update(data)
    if not db:
        return
    try:
        db.collection("bot_settings").document("broadcast_config").set(data, merge=True)
    except Exception as e:
        logger.error(f"Save Broadcast Settings Error: {e}")

def sync_add_broadcast_chat(chat_id: int, title: str):
    mem_broadcast_chats.add(chat_id)
    if not db:
        return
    try:
        db.collection("broadcast_chats").document(str(chat_id)).set({
            "chat_id": chat_id,
            "title": title or "Chat",
            "updated_at": time.time()
        }, merge=True)
    except Exception:
        pass

def sync_remove_broadcast_chat(chat_id: int):
    mem_broadcast_chats.discard(chat_id)
    if not db:
        return
    try:
        db.collection("broadcast_chats").document(str(chat_id)).delete()
    except Exception:
        pass

def sync_get_all_broadcast_chats():
    if not db:
        return list(mem_broadcast_chats)
    try:
        return [int(d.id) for d in db.collection("broadcast_chats").stream()]
    except Exception:
        return list(mem_broadcast_chats)

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

# ----------------- BACKGROUND AUTO-BROADCAST WORKER ----------------- #

async def auto_broadcast_worker():
    await asyncio.sleep(10)  # Startup delay
    while True:
        try:
            config = await asyncio.to_thread(sync_get_broadcast_settings)
            is_enabled = config.get("is_enabled", False)
            msg_text = config.get("message", "").strip()
            interval_mins = max(1, config.get("interval_minutes", 2))

            if is_enabled and msg_text:
                target_chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
                for chat_id in target_chats:
                    try:
                        await bot.send_message(chat_id, msg_text)
                        await asyncio.sleep(0.5)  # Telegram API flood protection
                    except FloodWait as f:
                        await asyncio.sleep(f.value)
                    except Exception as err:
                        err_str = str(err)
                        if any(x in err_str for x in ["CHAT_WRITE_FORBIDDEN", "USER_BANNED_IN_CHANNEL", "PEER_ID_INVALID"]):
                            await asyncio.to_thread(sync_remove_broadcast_chat, chat_id)
                        logger.warning(f"Broadcast failed to {chat_id}: {err}")

            await asyncio.sleep(interval_mins * 60)
        except Exception as e:
            logger.error(f"Broadcast Loop Error: {e}")
            await asyncio.sleep(30)

# ----------------- CHAT TRACKING EVENTS ----------------- #

@bot.on_chat_member_updated()
async def bot_join_leave_detector(_, update: ChatMemberUpdated):
    if not update.new_chat_member:
        return
    try:
        me = await bot.get_me()
        if update.new_chat_member.user.id == me.id:
            status = update.new_chat_member.status
            chat_title = update.chat.title or "Telegram Chat"
            if status in ["administrator", "member"]:
                await asyncio.to_thread(sync_add_broadcast_chat, update.chat.id, chat_title)
                logger.info(f"Registered for Broadcast: {chat_title} ({update.chat.id})")
            elif status in ["left", "banned"]:
                await asyncio.to_thread(sync_remove_broadcast_chat, update.chat.id)
                logger.info(f"Removed from Broadcast: {chat_title} ({update.chat.id})")
    except Exception as e:
        logger.error(f"Chat Member Update Error: {e}")

@bot.on_message((filters.group | filters.channel) & ~filters.service)
async def auto_track_channel_messages(_, message: Message):
    try:
        await asyncio.to_thread(sync_add_broadcast_chat, message.chat.id, message.chat.title or "Chat")
    except Exception:
        pass

# ----------------- ADMIN DASHBOARD & MENUS ----------------- #

def get_admin_markup(session_count: int, is_broadcast_active: bool):
    b_tag = "🟢 Active" if is_broadcast_active else "🔴 Disabled"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Session String", callback_data="btn_add_str"),
            InlineKeyboardButton("📱 Phone Login (OTP)", callback_data="btn_login_phone")
        ],
        [
            InlineKeyboardButton(f"📁 Active Sessions ({session_count})", callback_data="btn_list_sessions"),
            InlineKeyboardButton("🗑️ Delete Session", callback_data="btn_del_menu")
        ],
        [
            InlineKeyboardButton(f"📢 Auto-Broadcast [{b_tag}]", callback_data="btn_broadcast_panel")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="btn_refresh_panel")
        ]
    ])

def get_broadcast_markup(is_enabled: bool):
    toggle_text = "🔴 Turn OFF" if is_enabled else "🟢 Turn ON"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(toggle_text, callback_data="btn_toggle_broadcast")
        ],
        [
            InlineKeyboardButton("✏️ Edit Message", callback_data="btn_edit_broadcast_msg"),
            InlineKeyboardButton("⏱️ Set Interval", callback_data="btn_set_broadcast_interval")
        ],
        [
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="btn_refresh_panel")
        ]
    ])

@bot.on_message(filters.command(["admin", "panel"]) & filters.user(OWNER_ID) & filters.private)
async def admin_panel_handler(_, message: Message):
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    config = await asyncio.to_thread(sync_get_broadcast_settings)
    
    text = (
        "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
        f"  📊 **Database Status:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
        f"  👥 **Active Userbots:** `{len(sessions)} Connected`\n"
        f"  ⏱️ **Auto-Delete Timer:** `{AUTO_DELETE_TIME}s (Silent)`\n"
        f"  📢 **Auto-Broadcast:** `{'Enabled 🟢' if config.get('is_enabled') else 'Disabled 🔴'}`\n\n"
        "  _Select an action below to manage features:_\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    await message.reply_text(text, reply_markup=get_admin_markup(len(sessions), config.get("is_enabled", False)))

@bot.on_callback_query()
async def admin_callback_handler(_, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id != OWNER_ID:
        await query.answer("⛔ Access Denied", show_alert=True)
        return

    data = query.data

    if data == "btn_refresh_panel":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        text = (
            "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
            f"  📊 **Database Status:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
            f"  👥 **Active Userbots:** `{len(sessions)} Connected`\n"
            f"  ⏱️ **Auto-Delete Timer:** `{AUTO_DELETE_TIME}s (Silent)`\n"
            f"  📢 **Auto-Broadcast:** `{'Enabled 🟢' if config.get('is_enabled') else 'Disabled 🔴'}`\n\n"
            "  _Select an action below to manage features:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_admin_markup(len(sessions), config.get("is_enabled", False)))
        await query.answer("Dashboard Refreshed 🔄")

    elif data == "btn_broadcast_panel":
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        status_text = "🟢 ACTIVE" if config.get("is_enabled") else "🔴 DISABLED"
        
        text = (
            "┏━━━━━━ 📢 **AUTO-BROADCAST CONFIG** ━━━━━━┓\n\n"
            f"  ⚡ **Status:** `{status_text}`\n"
            f"  ⏱️ **Interval:** Every `{config.get('interval_minutes', 2)} minutes`\n"
            f"  🎯 **Connected Chats:** `{len(chats)} Groups & Channels`\n\n"
            f"  📝 **Current Message:**\n"
            f"  `{config.get('message', 'None')}`\n\n"
            "  _Tap below to edit message or toggle status:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_broadcast_markup(config.get("is_enabled", False)))
        await query.answer()

    elif data == "btn_toggle_broadcast":
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        new_state = not config.get("is_enabled", False)
        config["is_enabled"] = new_state
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        
        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        status_text = "🟢 ACTIVE" if new_state else "🔴 DISABLED"
        text = (
            "┏━━━━━━ 📢 **AUTO-BROADCAST CONFIG** ━━━━━━┓\n\n"
            f"  ⚡ **Status:** `{status_text}`\n"
            f"  ⏱️ **Interval:** Every `{config.get('interval_minutes', 2)} minutes`\n"
            f"  🎯 **Connected Chats:** `{len(chats)} Groups & Channels`\n\n"
            f"  📝 **Current Message:**\n"
            f"  `{config.get('message', 'None')}`\n\n"
            "  _Tap below to edit message or toggle status:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_broadcast_markup(new_state))
        await query.answer(f"Broadcast is now {'Active' if new_state else 'Disabled'}")

    elif data == "btn_edit_broadcast_msg":
        admin_states[user_id] = "WAIT_BROADCAST_MSG"
        await query.message.reply_text(
            "📝 **Send the new broadcast message text:**\n\n"
            "_(Formatting and links are fully supported)_",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_set_broadcast_interval":
        admin_states[user_id] = "WAIT_BROADCAST_INTERVAL"
        await query.message.reply_text(
            "⏱️ **Enter interval time in minutes** (e.g. `2`, `5`, `30`):",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_add_str":
        admin_states[user_id] = "WAIT_STRING"
        await query.message.reply_text(
            "📝 **Send your Hydrogram / Pyrogram Session String:**",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_login_phone":
        admin_states[user_id] = "WAIT_PHONE"
        await query.message.reply_text(
            "📱 **Send your phone number with country code** (e.g. `+8801XXXXXXXXX`):",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_retry_2fa":
        admin_states[user_id] = "WAIT_2FA"
        await query.message.reply_text(
            "🔐 **Please re-enter your 2FA password:**",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_cancel_login":
        session_data = login_clients.pop(user_id, None)
        admin_states.pop(user_id, None)
        if session_data and session_data.get("client"):
            try:
                if session_data["client"].is_connected:
                    await session_data["client"].disconnect()
            except Exception:
                pass
        await query.message.edit_text("❌ **Login cancelled.**")
        await query.answer("Cancelled", show_alert=True)

    elif data == "btn_list_sessions":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("No active sessions found!", show_alert=True)
            return
        text = "┏━━━━━━ 📁 **ACTIVE SESSIONS** ━━━━━━┓\n\n"
        for idx, s in enumerate(sessions, 1):
            text += f"  **{idx}.** `{s.get('account_name', 'Unknown')}`\n      ID: `{s['doc_id'][:10]}`\n"
        text += "\n┗━━━━━━━━━━━━━━━━━━━━━━━━┛"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_panel")]])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_del_menu":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("No sessions available to delete!", show_alert=True)
            return
        buttons = []
        for s in sessions:
            buttons.append([InlineKeyboardButton(f"❌ {s.get('account_name', 'Session')}", callback_data=f"del_{s['doc_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_panel")])
        await query.message.edit_text("Select a session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()

    elif data.startswith("del_"):
        doc_id = data.split("_", 1)[1]
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("Session removed!", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        text = (
            "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
            f"  📊 **Database Status:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
            f"  👥 **Active Userbots:** `{len(sessions)} Connected`\n"
            f"  ⏱️ **Auto-Delete Timer:** `{AUTO_DELETE_TIME}s (Silent)`\n"
            f"  📢 **Auto-Broadcast:** `{'Enabled 🟢' if config.get('is_enabled') else 'Disabled 🔴'}`\n\n"
            "  _Select an action below to manage features:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_admin_markup(len(sessions), config.get("is_enabled", False)))

# ----------------- ADMIN INPUT PROMPT HANDLER ----------------- #

@bot.on_message(filters.reply & filters.user(OWNER_ID) & filters.private)
async def admin_reply_processor(_, message: Message):
    state = admin_states.get(message.from_user.id)
    if not state:
        return

    text_input = (message.text or message.caption or "").strip()

    if state == "WAIT_BROADCAST_MSG":
        admin_states.pop(message.from_user.id, None)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        config["message"] = text_input
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        await message.reply_text("✅ **Broadcast message updated successfully!**\n\nOpen `/admin` to verify.")

    elif state == "WAIT_BROADCAST_INTERVAL":
        admin_states.pop(message.from_user.id, None)
        if not text_input.isdigit() or int(text_input) < 1:
            await message.reply_text("❌ **Invalid interval!** Please provide a positive number.")
            return
        mins = int(text_input)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        config["interval_minutes"] = mins
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        await message.reply_text(f"✅ **Broadcast interval set to {mins} minutes!**")

    elif state == "WAIT_STRING":
        admin_states.pop(message.from_user.id, None)
        status_msg = await message.reply_text("🔄 **Verifying session string...**")

        test_cl = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_input, in_memory=True)
        try:
            await test_cl.start()
            me = await test_cl.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await test_cl.stop()

            success, err = await asyncio.to_thread(sync_add_session, text_input, acc_name)
            if success:
                asyncio.create_task(init_session_pool())
                await status_msg.edit_text(f"✅ **Account Connected!**\n\n👤 `{acc_name}`")
            else:
                await status_msg.edit_text(f"⚠️ **Database Error:** `{err}`")
        except Exception as e:
            await status_msg.edit_text(f"❌ **Invalid Session String:** `{e}`")

    elif state == "WAIT_PHONE":
        admin_states.pop(message.from_user.id, None)
        phone = text_input.replace(" ", "").strip()
        status_msg = await message.reply_text("⏳ **Requesting verification code...**")

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
                "📩 **Enter the OTP code received:**\n\n"
                "💡 *Tip:* Separate digits with spaces (e.g. `1 2 3 4 5`)",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_cl.is_connected:
                await temp_cl.disconnect()
            await status_msg.edit_text(f"❌ **Failed to send OTP:** `{e}`")

    elif state == "WAIT_OTP":
        otp = text_input.replace(" ", "").replace("-", "").strip()
        data = login_clients.get(message.from_user.id)
        if not data:
            admin_states.pop(message.from_user.id, None)
            await message.reply_text("⚠️ Session expired. Please try again.")
            return

        temp_cl = data["client"]
        status_msg = await message.reply_text("⏳ **Verifying code...**")

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
            await status_msg.edit_text(f"✅ **Login Successful!**\n\n👤 **Account:** `{acc_name}`")
        except SessionPasswordNeeded:
            admin_states[message.from_user.id] = "WAIT_2FA"
            await status_msg.edit_text(
                "🔐 **Two-Step Verification Enabled!**\n\nPlease reply with your 2FA password:",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_cl.is_connected:
                await temp_cl.disconnect()
            login_clients.pop(message.from_user.id, None)
            admin_states.pop(message.from_user.id, None)
            await status_msg.edit_text(f"❌ **Login Failed:** `{e}`")

    elif state == "WAIT_2FA":
        password = text_input.strip()
        data = login_clients.get(message.from_user.id)
        if not data:
            admin_states.pop(message.from_user.id, None)
            await message.reply_text("⚠️ Session expired. Please try again.")
            return

        temp_cl = data["client"]
        status_msg = await message.reply_text("⏳ **Checking 2FA password...**")

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
            await status_msg.edit_text(f"✅ **Login Successful!**\n\n👤 **Account:** `{acc_name}`")
        except Exception as e:
            admin_states[message.from_user.id] = "WAIT_2FA"
            retry_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 Try Again", callback_data="btn_retry_2fa"),
                    InlineKeyboardButton("❌ Cancel Login", callback_data="btn_cancel_login")
                ]
            ])
            await status_msg.edit_text(
                f"❌ **Wrong 2FA Password!**\n\nError: `{e}`\n\nPlease choose an option below:",
                reply_markup=retry_markup
            )

# ----------------- ULTRA-FAST TELEGRAM DOWNLOADER ----------------- #

sem = asyncio.Semaphore(3)

async def download_album_item(client, media_msg, idx, user_id):
    async with sem:
        try:
            ext = ".mp4" if media_msg.video else ".jpg" if media_msg.photo else ".mp3" if media_msg.audio else ""
            if not ext and media_msg.document:
                ext = os.path.splitext(media_msg.document.file_name or "")[1] or ".bin"

            dest = os.path.join(DOWNLOAD_DIR, f"alb_{user_id}_{int(time.time())}_{idx}{ext}")
            path = await client.download_media(media_msg, file_name=dest)
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                return path, media_msg, idx
        except Exception as e:
            logger.error(f"Download item {idx} error: {e}")
        return None, media_msg, idx

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

    # Instant Server-Copy Bypass
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
        await message.reply_text("❌ **No active sessions found.** Please add one via `/admin`.")
        return

    target_msg = None
    working_client = None

    for _, _, u_client in loaded_user_clients:
        try:
            msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=8)
            if msg and not msg.empty:
                target_msg = msg
                working_client = u_client
                break
        except Exception:
            continue

    if not target_msg or target_msg.empty:
        await message.reply_text("❌ **Post unavailable or account lacks channel access.**")
        return

    # Direct Userbot Forward Bypass
    try:
        fwd = await working_client.forward_messages(message.chat.id, chat_id, msg_id)
        if fwd:
            asyncio.create_task(silent_auto_delete(message.chat.id, [fwd.id, message.id]))
            return
    except Exception:
        pass

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

    status = await message.reply_text("⚡ **Fetching media...**")

    # Album Processing
    if len(media_group) > 1:
        tasks = [download_album_item(working_client, m, idx, message.from_user.id) for idx, m in enumerate(media_group)]
        results = await asyncio.gather(*tasks)

        files, input_media = [], []
        for path, m, idx in sorted([r for r in results if r[0]], key=lambda x: x[2]):
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

        if len(input_media) >= 2:
            sent_msgs = []
            for i in range(0, len(input_media), 10):
                res = await bot.send_media_group(message.chat.id, input_media[i:i + 10])
                sent_msgs.extend(res)
            await status.delete()
            asyncio.create_task(silent_auto_delete(message.chat.id, [m.id for m in sent_msgs] + [message.id]))
        else:
            await status.edit_text("❌ **Failed to process album items.**")

        for f in files:
            try:
                os.remove(f)
            except Exception:
                pass
        return

    # Single Media Processing
    if not (target_msg.media or target_msg.video or target_msg.photo or target_msg.document or target_msg.audio or target_msg.voice):
        if target_msg.text:
            await status.edit_text(f"📝 **Text Content:**\n\n{target_msg.text}")
        else:
            await status.edit_text("⚠️ **No downloadable media found.**")
        return

    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ""
    if not ext and target_msg.document:
        ext = os.path.splitext(target_msg.document.file_name or "")[1] or ".bin"

    single_dest = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}{ext}")
    file_path = await working_client.download_media(target_msg, file_name=single_dest)

    if not file_path or not os.path.exists(file_path):
        await status.edit_text("❌ **Download failed.**")
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
        f"👋 **Welcome, {message.from_user.first_name}!**\n\n"
        "⚡ **Ultra-Fast Telegram Vault is Ready.**\n"
        "Send any public or private post / album link to download."
    )

@bot.on_message(filters.command("ping") & filters.private)
async def ping_handler(_, message: Message):
    t0 = time.time()
    msg = await message.reply_text("⚡ Ping...")
    await msg.edit_text(f"🚀 **Latency:** `{(time.time() - t0) * 1000:.1f}ms`")

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
            BotCommand("start", "Start the bot"),
            BotCommand("admin", "Admin control panel"),
            BotCommand("ping", "Check server latency")
        ])
    except Exception:
        pass
    await init_session_pool()
    await web_server()
    asyncio.create_task(auto_broadcast_worker())
    logger.info("Bot Online with Auto-Broadcast worker active!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())