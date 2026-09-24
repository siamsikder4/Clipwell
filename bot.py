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

# Local disk storage
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

admin_states = {}
login_clients = {}
loaded_user_clients = []
loaded_admins = {}

# Broadcast In-Memory State
mem_broadcast_settings = {
    "is_enabled": False,
    "message": "🔥 Join our channel for high-speed updates!\n[Join Channel|https://t.me/telegram]",
    "media_url": "",
    "interval_minutes": 2
}
mem_broadcast_chats = {}

# ----------------- STORAGE & PROGRESS HELPERS ----------------- #

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
    
    # Download folder size
    dl_size = 0
    dl_files = 0
    if os.path.exists(DOWNLOAD_DIR):
        for path, dirs, files in os.walk(DOWNLOAD_DIR):
            for f in files:
                fp = os.path.join(path, f)
                dl_size += os.path.getsize(fp)
                dl_files += 1

    return {
        "total": total,
        "used": used,
        "free": free,
        "percent": percent,
        "dl_size": dl_size,
        "dl_files": dl_files
    }

class ProgressTracker:
    def __init__(self, message: Message, action_text: str):
        self.message = message
        self.action_text = action_text
        self.last_update_time = time.time()

    async def progress(self, current: int, total: int):
        now = time.time()
        if now - self.last_update_time < 4 and current != total:
            return
        self.last_update_time = now

        percent = (current / total) * 100 if total > 0 else 0
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

class AlbumProgressTracker:
    def __init__(self, message: Message, total_files: int, action_text: str = "Downloading album"):
        self.message = message
        self.total_files = total_files
        self.action_text = action_text
        self.last_update_time = time.time()
        self.file_progress = {}
        self.lock = asyncio.Lock()

    async def progress(self, current: int, total: int, idx: int):
        self.file_progress[idx] = (current, total)
        now = time.time()
        if now - self.last_update_time < 3 and current != total:
            return

        async with self.lock:
            if time.time() - self.last_update_time < 3 and current != total:
                return
            self.last_update_time = time.time()

            total_downloaded = sum(c for c, _ in self.file_progress.values())
            total_size = sum(t for _, t in self.file_progress.values())
            completed_files = sum(1 for c, t in self.file_progress.values() if t > 0 and c >= t)

            percent = (total_downloaded / total_size * 100) if total_size > 0 else 0
            filled = min(10, int(percent / 10))
            bar = "▓" * filled + "░" * (10 - filled)

            text = (
                f"⚡ **{self.action_text} ({completed_files}/{self.total_files})**\n\n"
                f"[{bar}] `{percent:.1f}%`\n"
                f"📥 `{human_bytes(total_downloaded)}` / `{human_bytes(total_size)}`"
            )
            try:
                await self.message.edit_text(text)
            except Exception:
                pass

# ----------------- DATABASE & PERMISSIONS ----------------- #

def sync_get_all_admins():
    admins = {}
    if not db:
        return admins
    try:
        for d in db.collection("bot_admins").stream():
            if d.id.isdigit():
                data = d.to_dict()
                admins[int(d.id)] = {
                    "sessions": data.get("sessions", False),
                    "broadcast": data.get("broadcast", False),
                    "chats": data.get("chats", False)
                }
    except Exception as e:
        logger.error(f"Get Admins Error: {e}")
    return admins

def sync_add_admin(user_id: int):
    default_perms = {"sessions": False, "broadcast": False, "chats": True}
    loaded_admins[user_id] = default_perms
    if not db:
        return True
    try:
        db.collection("bot_admins").document(str(user_id)).set(default_perms)
        return True
    except Exception:
        return False

def sync_update_admin_perm(user_id: int, perm_key: str, value: bool):
    if user_id in loaded_admins:
        loaded_admins[user_id][perm_key] = value
    if not db:
        return
    try:
        db.collection("bot_admins").document(str(user_id)).set({perm_key: value}, merge=True)
    except Exception:
        pass

def sync_delete_admin(user_id: int):
    loaded_admins.pop(user_id, None)
    if not db:
        return True
    try:
        db.collection("bot_admins").document(str(user_id)).delete()
        return True
    except Exception:
        return False

def has_perm(user_id: int, perm_key: str) -> bool:
    if user_id == OWNER_ID:
        return True
    return loaded_admins.get(user_id, {}).get(perm_key, False)

def is_any_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in loaded_admins

def sync_get_all_sessions():
    if not db:
        return []
    try:
        return [{"doc_id": d.id, **d.to_dict()} for d in db.collection("telegram_sessions").stream()]
    except Exception:
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
    global mem_broadcast_settings
    if not db:
        return mem_broadcast_settings
    try:
        doc = db.collection("bot_settings").document("broadcast_config").get()
        if doc.exists:
            data = doc.to_dict()
            mem_broadcast_settings.update(data)
            return mem_broadcast_settings
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
    except Exception:
        pass

def sync_get_all_broadcast_chats():
    if not db:
        return dict(mem_broadcast_chats)
    try:
        chats = {}
        for d in db.collection("broadcast_chats").stream():
            data = d.to_dict()
            chats[int(d.id)] = {
                "title": data.get("title", "Chat"),
                "last_msg_id": data.get("last_msg_id", 0),
                "is_active": data.get("is_active", True)
            }
        return chats
    except Exception:
        return dict(mem_broadcast_chats)

def sync_add_broadcast_chat(chat_id: int, title: str):
    if chat_id not in mem_broadcast_chats:
        mem_broadcast_chats[chat_id] = {"title": title or "Chat", "last_msg_id": 0, "is_active": True}
    if not db:
        return
    try:
        db.collection("broadcast_chats").document(str(chat_id)).set({
            "chat_id": chat_id,
            "title": title or "Chat",
            "is_active": True,
            "updated_at": time.time()
        }, merge=True)
    except Exception:
        pass

def sync_toggle_chat_active(chat_id: int):
    all_chats = sync_get_all_broadcast_chats()
    chat_data = all_chats.get(chat_id, {"title": "Chat", "last_msg_id": 0, "is_active": True})
    new_state = not chat_data.get("is_active", True)
    chat_data["is_active"] = new_state
    mem_broadcast_chats[chat_id] = chat_data

    if db:
        try:
            db.collection("broadcast_chats").document(str(chat_id)).set({"is_active": new_state}, merge=True)
        except Exception:
            pass
    return new_state

def sync_update_chat_last_msg(chat_id: int, last_msg_id: int):
    if chat_id in mem_broadcast_chats:
        mem_broadcast_chats[chat_id]["last_msg_id"] = last_msg_id
    if not db:
        return
    try:
        db.collection("broadcast_chats").document(str(chat_id)).update({
            "last_msg_id": last_msg_id,
            "last_sent": time.time()
        })
    except Exception:
        pass

def sync_remove_broadcast_chat(chat_id: int):
    mem_broadcast_chats.pop(chat_id, None)
    if not db:
        return
    try:
        db.collection("broadcast_chats").document(str(chat_id)).delete()
    except Exception:
        pass

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
                f"pool_{s['doc_id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=s['session_string'],
                in_memory=True
            )
            await cl.start()
            new_pool.append((s['doc_id'], s.get('account_name', 'Userbot'), cl))
        except Exception as e:
            logger.warning(f"Failed session {s['doc_id']}: {e}")
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

# ----------------- POWERFUL BROADCAST ENGINE ----------------- #

def parse_broadcast_content(raw_text: str):
    buttons = []
    clean_lines = []
    button_pattern = re.compile(r"\[(.*?)\|(https?://\S+)\]")

    for line in raw_text.splitlines():
        found_btns = button_pattern.findall(line)
        if found_btns:
            row = [InlineKeyboardButton(text=btn[0].strip(), url=btn[1].strip()) for btn in found_btns]
            buttons.append(row)
        else:
            clean_lines.append(line)

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    return "\n".join(clean_lines).strip(), markup

async def auto_broadcast_worker():
    await asyncio.sleep(5)
    logger.info("📢 Responsive Broadcast Worker Active.")
    while True:
        try:
            config = await asyncio.to_thread(sync_get_broadcast_settings)
            is_enabled = config.get("is_enabled", False)
            raw_msg = config.get("message", "").strip()
            media_url = config.get("media_url", "").strip()
            interval_mins = max(1, int(config.get("interval_minutes", 2)))

            if is_enabled and raw_msg:
                text_content, markup = parse_broadcast_content(raw_msg)
                target_chats = await asyncio.to_thread(sync_get_all_broadcast_chats)

                for chat_id, c_data in list(target_chats.items()):
                    fresh_conf = await asyncio.to_thread(sync_get_broadcast_settings)
                    if not fresh_conf.get("is_enabled", False):
                        logger.info("Auto-broadcast was turned off. Halting transmission.")
                        break

                    if not c_data.get("is_active", True):
                        continue

                    old_msg_id = c_data.get("last_msg_id", 0)
                    if old_msg_id:
                        try:
                            await bot.delete_messages(chat_id=chat_id, message_ids=old_msg_id)
                        except Exception:
                            pass

                    sent = None
                    try:
                        if media_url:
                            sent = await bot.send_photo(
                                chat_id=chat_id,
                                photo=media_url,
                                caption=text_content,
                                reply_markup=markup
                            )
                        else:
                            sent = await bot.send_message(
                                chat_id=chat_id,
                                text=text_content,
                                reply_markup=markup,
                                disable_web_page_preview=False
                            )

                        if sent:
                            await asyncio.to_thread(sync_update_chat_last_msg, chat_id, sent.id)

                        await asyncio.sleep(1.5)

                    except FloodWait as f:
                        await asyncio.sleep(f.value + 1)
                    except Exception as err:
                        err_str = str(err)
                        logger.warning(f"Broadcast failed for {chat_id}: {err_str}")
                        if any(x in err_str for x in ["CHAT_WRITE_FORBIDDEN", "USER_BANNED_IN_CHANNEL", "PEER_ID_INVALID", "CHAT_NOT_MODIFIED"]):
                            await asyncio.to_thread(sync_remove_broadcast_chat, chat_id)

            total_sleep_sec = interval_mins * 60
            for _ in range(total_sleep_sec // 5):
                await asyncio.sleep(5)
                check = await asyncio.to_thread(sync_get_broadcast_settings)
                if not check.get("is_enabled", False):
                    break

        except Exception as e:
            logger.error(f"Broadcast Loop Error: {e}")
            await asyncio.sleep(10)

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
                logger.info(f"Registered Chat: {chat_title} ({update.chat.id})")
            elif status in ["left", "banned"]:
                await asyncio.to_thread(sync_remove_broadcast_chat, update.chat.id)
                logger.info(f"Removed Chat: {chat_title} ({update.chat.id})")
    except Exception as e:
        logger.error(f"Chat Update Error: {e}")

@bot.on_message((filters.group | filters.channel) & ~filters.service)
async def auto_track_channel_messages(_, message: Message):
    try:
        await asyncio.to_thread(sync_add_broadcast_chat, message.chat.id, message.chat.title or "Chat")
    except Exception:
        pass

@bot.on_message(filters.command("addchat") & filters.private)
async def manual_add_chat(_, message: Message):
    if not has_perm(message.from_user.id, "chats"):
        return await message.reply_text("⛔ You do not have permission to manage broadcast chats.")
    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text("❌ Usage: `/addchat -100xxxxxxxxxx`")
    try:
        target_id = int(args[1].strip())
        await asyncio.to_thread(sync_add_broadcast_chat, target_id, "Manual Chat")
        await message.reply_text(f"✅ Chat `{target_id}` added to broadcast database successfully!")
    except Exception as e:
        await message.reply_text(f"❌ Error: `{e}`")

# ----------------- ADMIN DASHBOARDS & ACTIONS ----------------- #

def get_role_based_markup(user_id: int, session_count: int, is_broadcast_active: bool):
    buttons = []
    if has_perm(user_id, "sessions"):
        buttons.append([
            InlineKeyboardButton("➕ Add Session String", callback_data="btn_add_str"),
            InlineKeyboardButton("📱 Phone Login (OTP)", callback_data="btn_login_phone")
        ])
        buttons.append([
            InlineKeyboardButton(f"📁 Active Sessions ({session_count})", callback_data="btn_list_sessions"),
            InlineKeyboardButton("🗑️ Delete Session", callback_data="btn_del_menu")
        ])

    if has_perm(user_id, "broadcast"):
        b_tag = "🟢 Active" if is_broadcast_active else "🔴 Disabled"
        buttons.append([
            InlineKeyboardButton(f"📢 Auto-Broadcast [{b_tag}]", callback_data="btn_broadcast_panel")
        ])

    buttons.append([
        InlineKeyboardButton("💾 Disk Usage", callback_data="btn_storage_info"),
        InlineKeyboardButton("🧹 Clean Storage", callback_data="btn_clean_storage")
    ])

    if user_id == OWNER_ID:
        buttons.append([
            InlineKeyboardButton("👥 Manage Admins & Permissions", callback_data="btn_manage_admins")
        ])

    buttons.append([InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="btn_refresh_panel")])
    return InlineKeyboardMarkup(buttons)

def get_admin_perm_markup(target_id: int):
    perms = loaded_admins.get(target_id, {})
    s_tag = "🟢 Enabled" if perms.get("sessions") else "🔴 Disabled"
    b_tag = "🟢 Enabled" if perms.get("broadcast") else "🔴 Disabled"
    c_tag = "🟢 Enabled" if perms.get("chats") else "🔴 Disabled"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Sessions: {s_tag}", callback_data=f"togperm_{target_id}_sessions")],
        [InlineKeyboardButton(f"Broadcast: {b_tag}", callback_data=f"togperm_{target_id}_broadcast")],
        [InlineKeyboardButton(f"Add Chats (/addchat): {c_tag}", callback_data=f"togperm_{target_id}_chats")],
        [InlineKeyboardButton("🗑️ Remove Admin", callback_data=f"deladmin_{target_id}")],
        [InlineKeyboardButton("🔙 Back to Admin List", callback_data="btn_manage_admins")]
    ])

@bot.on_message(filters.command(["admin", "panel"]) & filters.private)
async def admin_panel_handler(_, message: Message):
    user_id = message.from_user.id
    if not is_any_admin(user_id):
        return await message.reply_text("⛔ **Access Denied.**")

    sessions = await asyncio.to_thread(sync_get_all_sessions)
    config = await asyncio.to_thread(sync_get_broadcast_settings)

    text = (
        "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
        f"  👤 **Role:** `{'👑 Owner' if user_id == OWNER_ID else '🛡️ Sub-Admin'}`\n"
        f"  📊 **Database:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
        f"  👥 **Active Userbots:** `{len(sessions)}`\n"
        f"  ⏱️ **Auto-Delete Timer:** `{AUTO_DELETE_TIME}s`\n"
        f"  📢 **Auto-Broadcast:** `{'Enabled 🟢' if config.get('is_enabled') else 'Disabled 🔴'}`\n\n"
        "  _Select an action below:_\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    await message.reply_text(text, reply_markup=get_role_based_markup(user_id, len(sessions), config.get("is_enabled", False)))

@bot.on_callback_query()
async def admin_callback_handler(_, query: CallbackQuery):
    user_id = query.from_user.id
    if not is_any_admin(user_id):
        return await query.answer("⛔ Access Denied", show_alert=True)

    data = query.data

    if data == "btn_refresh_panel":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        text = (
            "┏━━━━━━ 🎛️ **ADMIN CONTROL PANEL** ━━━━━━┓\n\n"
            f"  👤 **Role:** `{'👑 Owner' if user_id == OWNER_ID else '🛡️ Sub-Admin'}`\n"
            f"  📊 **Database:** `{'Online 🟢' if db else 'Offline 🔴'}`\n"
            f"  👥 **Active Userbots:** `{len(sessions)}`\n"
            f"  ⏱️ **Auto-Delete Timer:** `{AUTO_DELETE_TIME}s`\n"
            f"  📢 **Auto-Broadcast:** `{'Enabled 🟢' if config.get('is_enabled') else 'Disabled 🔴'}`\n\n"
            "  _Select an action below:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_role_based_markup(user_id, len(sessions), config.get("is_enabled", False)))
        await query.answer("Refreshed 🔄")

    elif data == "btn_storage_info":
        st = get_storage_stats()
        filled = int(st["percent"] / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        text = (
            "┏━━━━━━ 💾 **DISK STORAGE INFO** ━━━━━━┓\n\n"
            f"  📊 **Disk Full:** `[{bar}] {st['percent']:.1f}%`\n"
            f"  📦 **Used Space:** `{human_bytes(st['used'])}`\n"
            f"  🟢 **Free Space:** `{human_bytes(st['free'])}`\n"
            f"  🗂️ **Total Disk:** `{human_bytes(st['total'])}`\n\n"
            f"  📂 **Downloads Temp:** `{human_bytes(st['dl_size'])}` ({st['dl_files']} files)\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧹 Clean Temp Files", callback_data="btn_clean_storage")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="btn_refresh_panel")]
        ])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_clean_storage":
        deleted_files = 0
        freed_bytes = 0
        if os.path.exists(DOWNLOAD_DIR):
            for filename in os.listdir(DOWNLOAD_DIR):
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        freed_bytes += os.path.getsize(file_path)
                        os.unlink(file_path)
                        deleted_files += 1
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                        deleted_files += 1
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
        await query.answer(f"Cleaned {deleted_files} files! Freed {human_bytes(freed_bytes)}", show_alert=True)
        # Refresh info
        st = get_storage_stats()
        filled = int(st["percent"] / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        text = (
            "┏━━━━━━ 💾 **DISK STORAGE INFO** ━━━━━━┓\n\n"
            f"  📊 **Disk Full:** `[{bar}] {st['percent']:.1f}%`\n"
            f"  📦 **Used Space:** `{human_bytes(st['used'])}`\n"
            f"  🟢 **Free Space:** `{human_bytes(st['free'])}`\n"
            f"  🗂️ **Total Disk:** `{human_bytes(st['total'])}`\n\n"
            f"  📂 **Downloads Temp:** `{human_bytes(st['dl_size'])}` ({st['dl_files']} files)\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧹 Clean Temp Files", callback_data="btn_clean_storage")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="btn_refresh_panel")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "btn_broadcast_panel":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        status_text = "🟢 ACTIVE" if config.get("is_enabled") else "🔴 DISABLED"
        toggle_text = "🔴 Turn OFF (Global)" if config.get("is_enabled") else "🟢 Turn ON (Global)"

        text = (
            "┏━━━━━━ 📢 **AUTO-BROADCAST CONFIG** ━━━━━━┓\n\n"
            f"  ⚡ **Global Status:** `{status_text}`\n"
            f"  ⏱️ **Interval:** Every `{config.get('interval_minutes', 2)} minutes`\n"
            f"  🎯 **Total Registered Chats:** `{len(chats)}`\n"
            f"  🖼️ **Media URL:** `{config.get('media_url') or 'None'}`\n\n"
            f"  📝 **Message Template:**\n  `{config.get('message', 'None')}`\n\n"
            "  _Select an action below:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_text, callback_data="btn_toggle_broadcast")],
            [InlineKeyboardButton(f"📋 Manage Chats List ({len(chats)})", callback_data="btn_manage_chats")],
            [
                InlineKeyboardButton("✏️ Edit Message", callback_data="btn_edit_broadcast_msg"),
                InlineKeyboardButton("🖼️ Set Media URL", callback_data="btn_set_media_url")
            ],
            [InlineKeyboardButton("⏱️ Set Interval", callback_data="btn_set_broadcast_interval")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="btn_refresh_panel")]
        ])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_toggle_broadcast":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        new_state = not config.get("is_enabled", False)
        config["is_enabled"] = new_state
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        await query.answer(f"Global Broadcast {'Enabled' if new_state else 'Disabled'}")

        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        status_text = "🟢 ACTIVE" if new_state else "🔴 DISABLED"
        toggle_text = "🔴 Turn OFF (Global)" if new_state else "🟢 Turn ON (Global)"
        text = (
            "┏━━━━━━ 📢 **AUTO-BROADCAST CONFIG** ━━━━━━┓\n\n"
            f"  ⚡ **Global Status:** `{status_text}`\n"
            f"  ⏱️ **Interval:** Every `{config.get('interval_minutes', 2)} minutes`\n"
            f"  🎯 **Total Registered Chats:** `{len(chats)}`\n"
            f"  🖼️ **Media URL:** `{config.get('media_url') or 'None'}`\n\n"
            f"  📝 **Message Template:**\n  `{config.get('message', 'None')}`\n\n"
            "  _Select an action below:_\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_text, callback_data="btn_toggle_broadcast")],
            [InlineKeyboardButton(f"📋 Manage Chats List ({len(chats)})", callback_data="btn_manage_chats")],
            [
                InlineKeyboardButton("✏️ Edit Message", callback_data="btn_edit_broadcast_msg"),
                InlineKeyboardButton("🖼️ Set Media URL", callback_data="btn_set_media_url")
            ],
            [InlineKeyboardButton("⏱️ Set Interval", callback_data="btn_set_broadcast_interval")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="btn_refresh_panel")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "btn_set_media_url":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        admin_states[user_id] = "WAIT_MEDIA_URL"
        await query.message.reply_text("🖼️ Send direct image link (e.g. `https://example.com/banner.jpg`) or send `none` to remove:", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data == "btn_manage_chats":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        if not chats:
            await query.answer("No chats registered yet!", show_alert=True)
            return

        buttons = []
        for cid, cinfo in chats.items():
            st = "🟢" if cinfo.get("is_active", True) else "🔴"
            title = cinfo.get("title", f"Chat {cid}")[:22]
            buttons.append([InlineKeyboardButton(f"{st} {title}", callback_data=f"viewchat_{cid}")])

        buttons.append([InlineKeyboardButton("🔙 Back to Broadcast", callback_data="btn_broadcast_panel")])
        await query.message.edit_text(
            "📋 **Chat Auto-Broadcast Settings**\n\n"
            "🟢 = Auto-message active\n"
            "🔴 = Auto-message disabled\n\n"
            "Select any chat to toggle:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await query.answer()

    elif data.startswith("viewchat_"):
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        cid = int(data.split("_")[1])
        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        cinfo = chats.get(cid, {"title": "Chat", "is_active": True})

        is_active = cinfo.get("is_active", True)
        tog_btn = "🔴 Disable Auto-Message" if is_active else "🟢 Enable Auto-Message"

        text = (
            f"📌 **Chat Details:** `{cinfo.get('title')}`\n"
            f"🆔 **Chat ID:** `{cid}`\n"
            f"⚡ **Status:** `{'🟢 Active' if is_active else '🔴 Disabled'}`\n\n"
            "Select an action:"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(tog_btn, callback_data=f"togchat_{cid}")],
            [InlineKeyboardButton("🗑️ Remove from Broadcast", callback_data=f"delchat_{cid}")],
            [InlineKeyboardButton("🔙 Back to Chat List", callback_data="btn_manage_chats")]
        ])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data.startswith("togchat_"):
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        cid = int(data.split("_")[1])
        new_state = await asyncio.to_thread(sync_toggle_chat_active, cid)
        await query.answer(f"Auto-message {'Enabled' if new_state else 'Disabled'} for this chat!")

        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        cinfo = chats.get(cid, {"title": "Chat", "is_active": new_state})
        tog_btn = "🔴 Disable Auto-Message" if new_state else "🟢 Enable Auto-Message"
        text = (
            f"📌 **Chat Details:** `{cinfo.get('title')}`\n"
            f"🆔 **Chat ID:** `{cid}`\n"
            f"⚡ **Status:** `{'🟢 Active' if new_state else '🔴 Disabled'}`\n\n"
            "Select an action:"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(tog_btn, callback_data=f"togchat_{cid}")],
            [InlineKeyboardButton("🗑️ Remove from Broadcast", callback_data=f"delchat_{cid}")],
            [InlineKeyboardButton("🔙 Back to Chat List", callback_data="btn_manage_chats")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data.startswith("delchat_"):
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        cid = int(data.split("_")[1])
        await asyncio.to_thread(sync_remove_broadcast_chat, cid)
        await query.answer("Chat removed completely!", show_alert=True)

        chats = await asyncio.to_thread(sync_get_all_broadcast_chats)
        buttons = []
        for c, cinfo in chats.items():
            st = "🟢" if cinfo.get("is_active", True) else "🔴"
            title = cinfo.get("title", f"Chat {c}")[:22]
            buttons.append([InlineKeyboardButton(f"{st} {title}", callback_data=f"viewchat_{c}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Broadcast", callback_data="btn_broadcast_panel")])
        await query.message.edit_text("Chat list updated:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "btn_edit_broadcast_msg":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        admin_states[user_id] = "WAIT_BROADCAST_MSG"
        await query.message.reply_text(
            "📝 Send new broadcast text.\n\n"
            "💡 **Button Example:**\n"
            "Hello World!\n"
            "`[Join Channel|https://t.me/channel]`\n"
            "`[Website|https://google.com]`",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_set_broadcast_interval":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        admin_states[user_id] = "WAIT_BROADCAST_INTERVAL"
        await query.message.reply_text("⏱️ Enter interval in minutes (e.g. 2, 5, 30):", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data == "btn_add_str":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        admin_states[user_id] = "WAIT_STRING"
        await query.message.reply_text("📝 Send Hydrogram / Pyrogram Session String:", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data == "btn_login_phone":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        admin_states[user_id] = "WAIT_PHONE"
        await query.message.reply_text("📱 Send phone number (+880...):", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data == "btn_list_sessions":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        text = "┏━━━━━━ 📁 **ACTIVE SESSIONS** ━━━━━━┓\n\n"
        for idx, s in enumerate(sessions, 1):
            text += f"  **{idx}.** `{s.get('account_name', 'Unknown')}`\n"
        text += "\n┗━━━━━━━━━━━━━━━━━━━━━━━━┛"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_panel")]]))
        await query.answer()

    elif data == "btn_del_menu":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        buttons = [[InlineKeyboardButton(f"❌ {s.get('account_name', 'Session')}", callback_data=f"del_{s['doc_id']}")] for s in sessions]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_panel")])
        await query.message.edit_text("Select session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()

    elif data.startswith("del_"):
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        doc_id = data.split("_", 1)[1]
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("Session deleted", show_alert=True)
        await query.message.delete()

    elif data == "btn_manage_admins":
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        buttons = []
        for aid in loaded_admins:
            buttons.append([InlineKeyboardButton(f"🛡️ Admin `{aid}`", callback_data=f"viewadmin_{aid}")])
        buttons.append([InlineKeyboardButton("➕ Add New Admin", callback_data="btn_add_new_admin")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_panel")])
        await query.message.edit_text("Select an admin to customize permissions:", reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()

    elif data == "btn_add_new_admin":
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        admin_states[user_id] = "WAIT_ADMIN_ID"
        await query.message.reply_text("🆔 Send Telegram User ID of new admin:", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data.startswith("viewadmin_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        target_uid = int(data.split("_")[1])
        await query.message.edit_text(
            f"⚙️ **Permission Settings for Admin:** `{target_uid}`\n\n"
            "Toggle specific abilities below:",
            reply_markup=get_admin_perm_markup(target_uid)
        )
        await query.answer()

    elif data.startswith("togperm_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        _, t_uid_str, p_key = data.split("_")
        target_uid = int(t_uid_str)
        curr = loaded_admins.get(target_uid, {}).get(p_key, False)
        await asyncio.to_thread(sync_update_admin_perm, target_uid, p_key, not curr)
        await query.message.edit_reply_markup(reply_markup=get_admin_perm_markup(target_uid))
        await query.answer("Permission Updated")

    elif data.startswith("deladmin_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        target_uid = int(data.split("_")[1])
        await asyncio.to_thread(sync_delete_admin, target_uid)
        await query.answer(f"Admin {target_uid} removed", show_alert=True)
        buttons = [[InlineKeyboardButton(f"🛡️ Admin `{aid}`", callback_data=f"viewadmin_{aid}")] for aid in loaded_admins]
        buttons.append([InlineKeyboardButton("➕ Add New Admin", callback_data="btn_add_new_admin")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_panel")])
        await query.message.edit_text("Admin list updated:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "btn_retry_2fa":
        admin_states[user_id] = "WAIT_2FA"
        await query.message.reply_text("🔐 Re-enter 2FA password:", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data == "btn_cancel_login":
        session_data = login_clients.pop(user_id, None)
        admin_states.pop(user_id, None)
        if session_data and session_data.get("cl") and session_data["cl"].is_connected:
            await session_data["cl"].disconnect()
        await query.message.edit_text("❌ Login cancelled.")
        await query.answer("Cancelled")

# ----------------- ADMIN INPUT PROMPTS ----------------- #

@bot.on_message(filters.reply & filters.private)
async def admin_reply_processor(_, message: Message):
    user_id = message.from_user.id
    if not is_any_admin(user_id):
        return

    state = admin_states.get(user_id)
    if not state:
        return

    text_input = (message.text or "").strip()

    if state == "WAIT_ADMIN_ID" and user_id == OWNER_ID:
        admin_states.pop(user_id, None)
        if not text_input.isdigit():
            return await message.reply_text("❌ User ID must be numeric.")
        t_id = int(text_input)
        await asyncio.to_thread(sync_add_admin, t_id)
        await message.reply_text(
            f"✅ Added Admin `{t_id}`.\nNow click `/admin` -> **Manage Admins** to select permissions."
        )

    elif state == "WAIT_BROADCAST_MSG" and has_perm(user_id, "broadcast"):
        admin_states.pop(user_id, None)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        config["message"] = text_input
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        await message.reply_text("✅ Broadcast template saved!")

    elif state == "WAIT_MEDIA_URL" and has_perm(user_id, "broadcast"):
        admin_states.pop(user_id, None)
        val = "" if text_input.lower() == "none" else text_input
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        config["media_url"] = val
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        await message.reply_text("✅ Media URL updated successfully!")

    elif state == "WAIT_BROADCAST_INTERVAL" and has_perm(user_id, "broadcast"):
        admin_states.pop(user_id, None)
        if not text_input.isdigit() or int(text_input) < 1:
            return await message.reply_text("❌ Enter a valid positive number.")
        mins = int(text_input)
        config = await asyncio.to_thread(sync_get_broadcast_settings)
        config["interval_minutes"] = mins
        await asyncio.to_thread(sync_save_broadcast_settings, config)
        await message.reply_text(f"✅ Interval set to {mins} minutes.")

    elif state == "WAIT_STRING" and has_perm(user_id, "sessions"):
        admin_states.pop(user_id, None)
        status = await message.reply_text("🔄 Verifying string...")
        cl = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_input, in_memory=True)
        try:
            await cl.start()
            me = await cl.get_me()
            acc = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await cl.stop()
            await asyncio.to_thread(sync_add_session, text_input, acc)
            asyncio.create_task(init_session_pool())
            await status.edit_text(f"✅ Connected: `{acc}`")
        except Exception as e:
            await status.edit_text(f"❌ Error: {e}")

    elif state == "WAIT_PHONE" and has_perm(user_id, "sessions"):
        admin_states.pop(user_id, None)
        phone = text_input.replace(" ", "")
        cl = Client(f"log_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await cl.connect()
            c = await cl.send_code(phone)
            login_clients[user_id] = {"cl": cl, "phone": phone, "hash": c.phone_code_hash}
            admin_states[user_id] = "WAIT_OTP"
            await message.reply_text("📩 Enter OTP (e.g. `1 2 3 4 5`):", reply_markup=ForceReply(selective=True))
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")

    elif state == "WAIT_OTP" and has_perm(user_id, "sessions"):
        otp = text_input.replace(" ", "").replace("-", "")
        data = login_clients.get(user_id)
        if not data:
            admin_states.pop(user_id, None)
            return
        cl = data["cl"]
        try:
            await cl.sign_in(data["phone"], data["hash"], otp)
            s_str = await cl.export_session_string()
            me = await cl.get_me()
            acc = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await cl.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await asyncio.to_thread(sync_add_session, s_str, acc)
            asyncio.create_task(init_session_pool())
            await message.reply_text(f"✅ Logged in: `{acc}`")
        except SessionPasswordNeeded:
            admin_states[user_id] = "WAIT_2FA"
            await message.reply_text("🔐 Enter 2FA Password:", reply_markup=ForceReply(selective=True))
        except Exception as e:
            await cl.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await message.reply_text(f"❌ Failed: {e}")

    elif state == "WAIT_2FA" and has_perm(user_id, "sessions"):
        data = login_clients.get(user_id)
        if not data:
            admin_states.pop(user_id, None)
            return
        cl = data["cl"]
        try:
            await cl.check_password(text_input)
            s_str = await cl.export_session_string()
            me = await cl.get_me()
            acc = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await cl.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await asyncio.to_thread(sync_add_session, s_str, acc)
            asyncio.create_task(init_session_pool())
            await message.reply_text(f"✅ Logged in: `{acc}`")
        except Exception as e:
            await message.reply_text(
                f"❌ Wrong 2FA: `{e}`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="btn_retry_2fa"),
                     InlineKeyboardButton("❌ Cancel", callback_data="btn_cancel_login")]
                ])
            )

# ----------------- ROBUST TELEGRAM DOWNLOADER ----------------- #

sem = asyncio.Semaphore(2)

async def download_album_item(client, media_msg, idx, user_id, tracker: AlbumProgressTracker = None):
    async with sem:
        ext = ".mp4" if media_msg.video else ".jpg" if media_msg.photo else ".mp3" if media_msg.audio else ""
        if not ext and media_msg.document:
            ext = os.path.splitext(media_msg.document.file_name or "")[1] or ".bin"
        dest = os.path.join(DOWNLOAD_DIR, f"alb_{user_id}_{int(time.time())}_{idx}{ext}")

        async def _item_progress(current, total):
            if tracker:
                await tracker.progress(current, total, idx)

        path = None
        for attempt in range(2):
            try:
                path = await client.download_media(
                    media_msg,
                    file_name=dest,
                    progress=_item_progress if tracker else None
                )
                if path and os.path.exists(path) and os.path.getsize(path) > 0:
                    break
            except Exception as e:
                logger.warning(f"Album item {idx} attempt {attempt+1} failed: {e}")
                await asyncio.sleep(2)

        thumb_path = None
        if path and media_msg.video and media_msg.video.thumbs:
            try:
                thumb_dest = os.path.join(DOWNLOAD_DIR, f"alb_thumb_{user_id}_{int(time.time())}_{idx}.jpg")
                thumb_path = await client.download_media(media_msg.video.thumbs[0].file_id, file_name=thumb_dest)
            except Exception:
                pass

        return path, media_msg, idx, thumb_path

@bot.on_message(filters.private & ~filters.command(["start", "help", "admin", "panel", "ping", "addchat", "storage", "clean"]))
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

    # Fast Server-Side Copy
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
        return await message.reply_text("❌ No active sessions found. Add via `/admin`.")

    target_msg, working_client = None, None
    for _, _, u_client in loaded_user_clients:
        try:
            msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=12)
            if msg and not msg.empty:
                target_msg, working_client = msg, u_client
                break
        except Exception:
            continue

    if not target_msg or target_msg.empty:
        return await message.reply_text("❌ Post unavailable or channel not joined.")

    # Fast Direct Forward Bypass
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

    status = await message.reply_text("⚡ **Preparing download...**")

    # ----------------- ALBUM PROCESSING ----------------- #
    if len(media_group) > 1:
        alb_tracker = AlbumProgressTracker(status, len(media_group), "Downloading album...")
        for i, m in enumerate(media_group):
            m_obj = m.video or m.document or m.audio or m.photo
            sz = getattr(m_obj, "file_size", 0) or 0
            alb_tracker.file_progress[i] = (0, sz)

        tasks = [download_album_item(working_client, m, idx, message.from_user.id, alb_tracker) for idx, m in enumerate(media_group)]
        results = await asyncio.gather(*tasks)

        files_to_cleanup, thumbs_to_cleanup, input_media = [], [], []
        for path, m, idx, thumb_path in sorted([r for r in results if r[0]], key=lambda x: x[2]):
            files_to_cleanup.append(path)
            if thumb_path:
                thumbs_to_cleanup.append(thumb_path)

            cap = m.caption or (target_msg.caption if idx == 0 else "")
            if m.video:
                input_media.append(InputMediaVideo(
                    path,
                    thumb=thumb_path,
                    duration=m.video.duration or 0,
                    width=m.video.width or 0,
                    height=m.video.height or 0,
                    caption=cap,
                    supports_streaming=True
                ))
            elif m.photo:
                input_media.append(InputMediaPhoto(path, caption=cap))
            elif m.audio:
                input_media.append(InputMediaAudio(path, caption=cap, duration=m.audio.duration or 0))
            else:
                input_media.append(InputMediaDocument(path, caption=cap))

        if len(input_media) >= 2:
            await status.edit_text("⚡ **Uploading album to Telegram...**")
            sent_msgs = []
            for i in range(0, len(input_media), 10):
                res = await bot.send_media_group(message.chat.id, input_media[i:i + 10])
                sent_msgs.extend(res)
            await status.delete()
            asyncio.create_task(silent_auto_delete(message.chat.id, [m.id for m in sent_msgs] + [message.id]))
        else:
            await status.edit_text("❌ Album processing failed. Some files were unavailable.")

        for f in files_to_cleanup + thumbs_to_cleanup:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        return

    # ----------------- SINGLE MEDIA PROCESSING ----------------- #
    if not (target_msg.media or target_msg.video or target_msg.photo or target_msg.document or target_msg.audio or target_msg.voice):
        if target_msg.text:
            return await status.edit_text(f"📝 **Text:**\n\n{target_msg.text}")
        return await status.edit_text("⚠️ No media found.")

    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ""
    if not ext and target_msg.document:
        ext = os.path.splitext(target_msg.document.file_name or "")[1] or ".bin"

    single_dest = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}{ext}")
    dl_tracker = ProgressTracker(status, "Downloading media...")
    file_path = None

    for attempt in range(2):
        try:
            file_path = await working_client.download_media(
                target_msg,
                file_name=single_dest,
                progress=dl_tracker.progress
            )
            if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                break
        except Exception as e:
            logger.warning(f"Download attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await status.edit_text("⚠️ Retrying download connection...")
                await asyncio.sleep(2)

    if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return await status.edit_text("❌ **Download failed after retry.** File might be restricted or connection timed out.")

    thumb_path = None
    duration = 0
    width = 0
    height = 0

    if target_msg.video:
        duration = target_msg.video.duration or 0
        width = target_msg.video.width or 0
        height = target_msg.video.height or 0
        if target_msg.video.thumbs:
            try:
                thumb_dest = os.path.join(DOWNLOAD_DIR, f"thumb_{message.from_user.id}_{int(time.time())}.jpg")
                thumb_path = await working_client.download_media(target_msg.video.thumbs[0].file_id, file_name=thumb_dest)
            except Exception as err:
                logger.warning(f"Thumbnail download failed: {err}")

    up_tracker = ProgressTracker(status, "Uploading media...")
    cap = target_msg.caption or ""
    sent = None

    try:
        if target_msg.video:
            sent = await bot.send_video(
                message.chat.id,
                file_path,
                caption=cap,
                duration=duration,
                width=width,
                height=height,
                thumb=thumb_path,
                supports_streaming=True,
                progress=up_tracker.progress
            )
        elif target_msg.photo:
            sent = await bot.send_photo(
                message.chat.id,
                file_path,
                caption=cap,
                progress=up_tracker.progress
            )
        elif target_msg.audio:
            sent = await bot.send_audio(
                message.chat.id,
                file_path,
                caption=cap,
                duration=target_msg.audio.duration or 0,
                progress=up_tracker.progress
            )
        else:
            sent = await bot.send_document(
                message.chat.id,
                file_path,
                caption=cap,
                progress=up_tracker.progress
            )
    except Exception as e:
        await status.edit_text(f"❌ **Upload failed:** `{e}`")

    await status.delete()
    if sent:
        asyncio.create_task(silent_auto_delete(message.chat.id, [sent.id, message.id]))

    for f in [file_path, thumb_path]:
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

# ----------------- BASE COMMANDS ----------------- #

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    await message.reply_text(
        f"👋 **Welcome, {message.from_user.first_name}!**\n\n"
        "⚡ Send any public or private Telegram post / album link to download."
    )

@bot.on_message(filters.command("ping") & filters.private)
async def ping_handler(_, message: Message):
    t0 = time.time()
    msg = await message.reply_text("⚡ Ping...")
    await msg.edit_text(f"🚀 **Latency:** `{(time.time() - t0) * 1000:.1f}ms`")

@bot.on_message(filters.command("storage") & filters.private)
async def storage_cmd_handler(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text("⛔ Access Denied.")
    st = get_storage_stats()
    filled = int(st["percent"] / 10)
    bar = "▓" * filled + "░" * (10 - filled)
    text = (
        "┏━━━━━━ 💾 **DISK STORAGE INFO** ━━━━━━┓\n\n"
        f"  📊 **Disk Full:** `[{bar}] {st['percent']:.1f}%`\n"
        f"  📦 **Used Space:** `{human_bytes(st['used'])}`\n"
        f"  🟢 **Free Space:** `{human_bytes(st['free'])}`\n"
        f"  🗂️ **Total Disk:** `{human_bytes(st['total'])}`\n\n"
        f"  📂 **Downloads Temp:** `{human_bytes(st['dl_size'])}` ({st['dl_files']} files)\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Clean Temp Files", callback_data="btn_clean_storage")]
    ])
    await message.reply_text(text, reply_markup=markup)

@bot.on_message(filters.command("clean") & filters.private)
async def clean_cmd_handler(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text("⛔ Access Denied.")

    msg = await message.reply_text("🧹 Cleaning temp files...")
    deleted_files = 0
    freed_bytes = 0

    if os.path.exists(DOWNLOAD_DIR):
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    freed_bytes += os.path.getsize(file_path)
                    os.unlink(file_path)
                    deleted_files += 1
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                    deleted_files += 1
            except Exception as e:
                logger.error(f"Failed to delete {file_path}: {e}")

    await msg.edit_text(
        f"✅ **Storage Cleaned Successfully!**\n\n"
        f"🗑️ Files Removed: `{deleted_files}`\n"
        f"💾 Space Freed: `{human_bytes(freed_bytes)}`"
    )

# ----------------- WEB DASHBOARD (LIVE UI) ----------------- #

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clipwell Bot Server Status</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 24px; width: 100%; max-width: 440px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
        .title { font-size: 20px; font-weight: 700; color: #38bdf8; display: flex; align-items: center; gap: 8px; }
        .status-badge { background: #064e3b; color: #34d399; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 9999px; display: inline-flex; align-items: center; gap: 6px; }
        .status-dot { width: 8px; height: 8px; background: #10b981; border-radius: 50%; animation: pulse 2s infinite; }
        .storage-box { background: #0f172a; border-radius: 12px; padding: 16px; margin-bottom: 20px; border: 1px solid #1e293b; }
        .storage-title { font-size: 13px; color: #94a3b8; margin-bottom: 8px; display: flex; justify-content: space-between; }
        .storage-val { font-size: 26px; font-weight: 800; color: #f8fafc; margin-bottom: 12px; }
        .progress-bar-bg { width: 100%; height: 12px; background: #334155; border-radius: 6px; overflow: hidden; }
        .progress-bar-fill { height: 100%; border-radius: 6px; transition: width 0.5s ease; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
        .stat-item { background: #1e293b; border: 1px solid #334155; padding: 12px; border-radius: 10px; }
        .stat-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-num { font-size: 15px; font-weight: 600; color: #f1f5f9; margin-top: 4px; }
        .btn-clean { display: block; width: 100%; margin-top: 16px; padding: 12px; background: #ef4444; color: white; border: none; border-radius: 10px; font-weight: 600; cursor: pointer; text-align: center; text-decoration: none; transition: 0.2s; }
        .btn-clean:hover { background: #dc2626; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="title">⚡ Clipwell Bot</div>
            <div class="status-badge"><div class="status-dot"></div> ONLINE</div>
        </div>

        <div class="storage-box">
            <div class="storage-title">
                <span>DISK USAGE</span>
                <span id="pct-text">--%</span>
            </div>
            <div class="storage-val" id="pct-big">0% Full</div>
            <div class="progress-bar-bg">
                <div id="prog-fill" class="progress-bar-fill" style="width: 0%; background: #10b981;"></div>
            </div>

            <div class="stat-grid">
                <div class="stat-item">
                    <div class="stat-label">Used Space</div>
                    <div class="stat-num" id="stat-used">--</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Free Space</div>
                    <div class="stat-num" id="stat-free">--</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Total Disk</div>
                    <div class="stat-num" id="stat-total">--</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Temp Files</div>
                    <div class="stat-num" id="stat-temp">--</div>
                </div>
            </div>
        </div>

        <button class="btn-clean" onclick="cleanStorage()">🧹 Clean Download Cache</button>
    </div>

    <script>
        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const d = await res.json();
                document.getElementById('pct-text').innerText = d.percent.toFixed(1) + '%';
                document.getElementById('pct-big').innerText = d.percent.toFixed(1) + '% Full';
                
                const fill = document.getElementById('prog-fill');
                fill.style.width = Math.min(100, d.percent) + '%';
                if(d.percent > 85) fill.style.background = '#ef4444';
                else if(d.percent > 65) fill.style.background = '#f59e0b';
                else fill.style.background = '#10b981';

                document.getElementById('stat-used').innerText = d.used;
                document.getElementById('stat-free').innerText = d.free;
                document.getElementById('stat-total').innerText = d.total;
                document.getElementById('stat-temp').innerText = d.dl_size + ' (' + d.dl_files + ')';
            } catch (e) {
                console.error(e);
            }
        }

        async function cleanStorage() {
            if(!confirm("Are you sure you want to delete all cached download files?")) return;
            try {
                const res = await fetch('/api/clean', { method: 'POST' });
                const d = await res.json();
                alert(d.message);
                fetchStats();
            } catch(e) {
                alert("Failed to clean storage.");
            }
        }

        fetchStats();
        setInterval(fetchStats, 5000);
    </script>
</body>
</html>
"""

async def handle_root(_):
    return web.Response(text=HTML_DASHBOARD, content_type="text/html")

async def handle_stats(_):
    st = get_storage_stats()
    return web.json_response({
        "percent": st["percent"],
        "used": human_bytes(st["used"]),
        "free": human_bytes(st["free"]),
        "total": human_bytes(st["total"]),
        "dl_size": human_bytes(st["dl_size"]),
        "dl_files": st["dl_files"]
    })

async def handle_web_clean(_):
    deleted = 0
    freed = 0
    if os.path.exists(DOWNLOAD_DIR):
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    freed += os.path.getsize(file_path)
                    os.unlink(file_path)
                    deleted += 1
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                    deleted += 1
            except Exception:
                pass
    return web.json_response({
        "status": "ok",
        "message": f"Cleaned {deleted} files! Freed {human_bytes(freed)}"
    })

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_post("/api/clean", handle_web_clean)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Storage Monitor Web UI Running on Port {PORT}")

# ----------------- STARTUP ----------------- #

async def main():
    await bot.start()
    try:
        await bot.set_bot_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("admin", "Admin Control Panel"),
            BotCommand("storage", "Check Disk Usage %"),
            BotCommand("clean", "Clean Temp Download Files"),
            BotCommand("ping", "Check latency"),
            BotCommand("addchat", "Add channel for broadcast")
        ])
    except Exception:
        pass
    await init_session_pool()
    await web_server()
    asyncio.create_task(auto_broadcast_worker())
    logger.info("Bot Online with Full Responsive Broadcast Engine!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())