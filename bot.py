import os
import re
import time
import json
import shutil
import asyncio
import logging
from aiohttp import web
from hydrogram import Client, filters
from hydrogram.enums import ParseMode
from hydrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
    ForceReply,
    BotCommand,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaDocument
)
from hydrogram.errors import SessionPasswordNeeded, FloodWait, UserIsBlocked, InputUserDeactivated

# =====================================================================
# 🎨 CENTRALIZED UI CUSTOM EMOJIS CONFIGURATION
# আপনার প্রদত্ত হুবহু Telegram Custom Emoji IDs
# =====================================================================
UI_EMOJIS = {
    # 📌 General & System Status
    "spark": "6053061091456457277",
    "rocket": "6314344267267450186",
    "online": "6311876323224592776",
    "offline": "6314179022695702286",
    "refresh": "6053323501073341449",
    "cross": "6052869226677410910",
    "check": "6053202116707622090",
    "warning": "6183514473895042082",
    "back": "6314565157435480578",

    # 🎛️ Admin Panel & Roles
    "panel": "6053275839821257175",
    "crown": "6338946058982267602",
    "shield": "6052869252447215120",
    "user": "6053028307971085979",
    "users": "6311892601150643233",
    "lock": "6314447153209023665",
    "phone": "6314529384652872650",

    # 💾 Storage & Maintenance
    "disk": "6052914628776697272",
    "broom": "6314192620562161224",
    "folder": "6052921672523061549",
    "trash": "6314398126157340467",

    # 📢 Broadcast & Messaging
    "broadcast": "6053275839821257175",
    "timer": "6314097066129759042",
    "target": "6053120894581086446",
    "media": "6314426086394436542",
    "pencil": "6314293152861657827",
    "list": "6338935574967098253",

    # 📥 Download & Upload Progress
    "download": "6053120894581086446",
    "upload": "6314266682978211811",
    "bar_fill": "6053117952528493140",
    "bar_empty": "6338899694810307622",
    "reaction": "6314425974725288033",
}

FALLBACK_SYMBOLS = {
    "spark": "⚡", "rocket": "🚀", "online": "🟢", "offline": "🔴",
    "refresh": "🔄", "cross": "❌", "check": "✅", "warning": "⚠️",
    "back": "🔙", "panel": "🎛️", "crown": "👑", "shield": "🛡️",
    "user": "👤", "users": "👥", "lock": "🔐", "phone": "📱",
    "disk": "💾", "broom": "🧹", "folder": "📂", "trash": "🗑️",
    "broadcast": "📢", "timer": "⏱️", "target": "🎯", "media": "🖼️",
    "pencil": "✏️", "list": "📋", "download": "📥", "upload": "📤",
    "bar_fill": "▰", "bar_empty": "▱", "reaction": "🫡",
    "voice": "🎙️", "chat": "💬", "video": "📹", "info": "ℹ️",
    "hand": "👋", "chart": "📊", "pin": "📌", "bulb": "💡", "box": "📦"
}

def ce(name: str) -> str:
    """টেলিগ্রামের অফিশিয়াল tg-emoji ট্যাগ যাতে কোনো এরর না আসে"""
    eid = str(UI_EMOJIS.get(name, "")).strip()
    fallback = FALLBACK_SYMBOLS.get(name, "✨")
    if eid and eid.isdigit():
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment Configuration
raw_api_id = os.environ.get("API_ID", "35039821").strip().replace('"', '').replace("'", "")
API_ID = int(raw_api_id) if raw_api_id.isdigit() else 35039821

API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip().replace('"', '').replace("'", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8952918726:AAGnKZm-S8hmBaWzltPfrdWRcyVHGVx44d0").strip().replace('"', '').replace("'", "")
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()

OWNER_ID = 8271633124
PORT = int(os.environ.get("PORT", "8080"))
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "120"))

# Local Storage Directory
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Firebase Safe Init
db = None
if FIREBASE_KEY_RAW:
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        clean_key = FIREBASE_KEY_RAW.strip()
        if clean_key.startswith("'") and clean_key.endswith("'"):
            clean_key = clean_key[1:-1]
        elif clean_key.startswith('"') and clean_key.endswith('"'):
            clean_key = clean_key[1:-1]
        key_dict = json.loads(clean_key)
        cred = credentials.Certificate(key_dict)
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
    workers=16,
    parse_mode=ParseMode.HTML
)

user_states = {}
login_clients = {}
loaded_user_clients = []
loaded_admins = {}
mem_bot_users = set()

# Live Web UI Progress Tracking Pool
active_tasks = {}

# ----------------- ALWAYS PERSISTENT BOTTOM KEYBOARDS ----------------- #

def get_bottom_keyboard(user_id: int):
    """is_persistent=True প্যারামিটার ব্যবহার করে বাটন স্থায়ী রাখা হয়েছে"""
    if is_any_admin(user_id):
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton(f"{FALLBACK_SYMBOLS['broadcast']} Broadcast to Users")],
                [KeyboardButton(f"{FALLBACK_SYMBOLS['panel']} Admin Panel"), KeyboardButton(f"{FALLBACK_SYMBOLS['disk']} Storage")],
                [KeyboardButton(f"{FALLBACK_SYMBOLS['broom']} Clean Storage"), KeyboardButton(f"{FALLBACK_SYMBOLS['phone']} Connect Account")],
                [KeyboardButton(f"{FALLBACK_SYMBOLS['rocket']} Latency"), KeyboardButton(f"{FALLBACK_SYMBOLS['info']} How To Use")]
            ],
            resize_keyboard=True,
            is_persistent=True
        )
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(f"{FALLBACK_SYMBOLS['phone']} Connect Account")],
            [KeyboardButton(f"{FALLBACK_SYMBOLS['rocket']} Latency"), KeyboardButton(f"{FALLBACK_SYMBOLS['info']} How To Use")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

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
    def __init__(self, message: Message, action_text: str, task_id: str = None, task_type: str = "download"):
        self.message = message
        self.action_text = action_text
        self.task_id = task_id
        self.task_type = task_type
        self.last_update_time = time.time()

        if self.task_id:
            active_tasks[self.task_id] = {
                "name": action_text,
                "type": task_type,
                "current": 0,
                "total": 0,
                "percent": 0.0,
                "speed": "0 B/s",
                "updated_at": time.time()
            }

    async def progress(self, current: int, total: int):
        now = time.time()
        percent = (current / total) * 100 if total > 0 else 0

        if self.task_id and self.task_id in active_tasks:
            time_diff = max(0.1, now - self.last_update_time)
            bytes_diff = current - active_tasks[self.task_id]["current"]
            speed = f"{human_bytes(int(bytes_diff / time_diff))}/s" if bytes_diff > 0 else "0 B/s"
            
            active_tasks[self.task_id].update({
                "current": current,
                "total": total,
                "percent": round(percent, 1),
                "speed": speed,
                "updated_at": now
            })

        if now - self.last_update_time < 3 and current != total:
            return
        self.last_update_time = now

        filled = int(percent / 10)
        bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
        icon = ce("upload") if self.task_type == "upload" else ce("download")

        text = (
            f"{ce('spark')} <b>{self.action_text}</b>\n\n"
            f"⌠ {bar} ⌡ <code>{percent:.1f}%</code>\n"
            f"{icon} <code>{human_bytes(current)}</code> / <code>{human_bytes(total)}</code>"
        )
        try:
            await self.message.edit_text(text)
        except Exception:
            pass

    def finish(self):
        if self.task_id:
            active_tasks.pop(self.task_id, None)

class AlbumProgressTracker:
    def __init__(self, message: Message, total_files: int, action_text: str = "Downloading album", task_id: str = None):
        self.message = message
        self.total_files = total_files
        self.action_text = action_text
        self.task_id = task_id
        self.last_update_time = time.time()
        self.file_progress = {}
        self.lock = asyncio.Lock()

        if self.task_id:
            active_tasks[self.task_id] = {
                "name": f"{action_text} ({total_files} files)",
                "type": "album",
                "current": 0,
                "total": 0,
                "percent": 0.0,
                "speed": "--",
                "updated_at": time.time()
            }

    async def progress(self, current: int, total: int, idx: int):
        self.file_progress[idx] = (current, total)
        now = time.time()

        total_downloaded = sum(c for c, _ in self.file_progress.values())
        total_size = sum(t for _, t in self.file_progress.values())
        percent = (total_downloaded / total_size * 100) if total_size > 0 else 0

        if self.task_id and self.task_id in active_tasks:
            active_tasks[self.task_id].update({
                "current": total_downloaded,
                "total": total_size,
                "percent": round(percent, 1),
                "updated_at": now
            })

        if now - self.last_update_time < 3 and current != total:
            return

        async with self.lock:
            if time.time() - self.last_update_time < 3 and current != total:
                return
            self.last_update_time = time.time()

            completed_files = sum(1 for c, t in self.file_progress.values() if t > 0 and c >= t)
            filled = min(10, int(percent / 10))
            bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)

            text = (
                f"{ce('spark')} <b>{self.action_text} ({completed_files}/{self.total_files})</b>\n\n"
                f"⌠ {bar} ⌡ <code>{percent:.1f}%</code>\n"
                f"{ce('download')} <code>{human_bytes(total_downloaded)}</code> / <code>{human_bytes(total_size)}</code>"
            )
            try:
                await self.message.edit_text(text)
            except Exception:
                pass

    def finish(self):
        if self.task_id:
            active_tasks.pop(self.task_id, None)

# ----------------- DATABASE HELPERS ----------------- #

def sync_register_user(user_id: int):
    mem_bot_users.add(user_id)
    if not db:
        return
    try:
        db.collection("registered_users").document(str(user_id)).set({
            "user_id": user_id,
            "last_active": time.time()
        }, merge=True)
    except Exception:
        pass

def sync_get_all_bot_users():
    if not db:
        return list(mem_bot_users)
    try:
        users = []
        for d in db.collection("registered_users").stream():
            if d.id.isdigit():
                users.append(int(d.id))
        return list(set(users) | mem_bot_users)
    except Exception:
        return list(mem_bot_users)

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
                    "broadcast": data.get("broadcast", True)
                }
    except Exception as e:
        logger.error(f"Get Admins Error: {e}")
    return admins

def sync_add_admin(user_id: int):
    default_perms = {"sessions": False, "broadcast": True}
    loaded_admins[user_id] = default_perms
    if not db:
        return True
    try:
        db.collection("bot_admins").document(str(user_id)).set(default_perms)
        return True
    except Exception:
        return False

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

def sync_add_session(session_str: str, name: str, user_id: int = None):
    if not db:
        return False, "Database disconnected"
    try:
        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "added_by": user_id,
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

# ----------------- SAFER SESSION POOL INIT ----------------- #

async def init_session_pool():
    global loaded_user_clients, loaded_admins
    try:
        loaded_admins = await asyncio.to_thread(sync_get_all_admins)
    except Exception as e:
        logger.error(f"Admin load error: {e}")

    if not db:
        return

    sessions = await asyncio.to_thread(sync_get_all_sessions)
    new_pool = []
    for s in sessions:
        s_str = s.get("session_string")
        if not s_str:
            continue
        try:
            cl = Client(
                f"pool_{s['doc_id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=s_str,
                in_memory=True
            )
            await asyncio.wait_for(cl.start(), timeout=10)
            new_pool.append((s['doc_id'], s.get('account_name', 'Userbot'), cl))
        except Exception as e:
            logger.warning(f"Skipping broken session {s.get('doc_id')}: {e}")
            if any(err in str(e) for err in ["AUTH_KEY_DUPLICATED", "USER_DEACTIVATED", "SESSION_REVOKED"]):
                asyncio.create_task(asyncio.to_thread(sync_delete_session, s["doc_id"]))

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

# ----------------- DIRECT USER BROADCAST ENGINE ----------------- #

async def broadcast_to_all_users(source_message: Message, admin_id: int):
    all_users = await asyncio.to_thread(sync_get_all_bot_users)
    if not all_users:
        return await bot.send_message(admin_id, f"{ce('cross')} <b>No registered users found.</b>", reply_markup=get_bottom_keyboard(admin_id))

    status_msg = await bot.send_message(
        admin_id,
        f"{ce('broadcast')} <b>Broadcasting started...</b>\n"
        f"{ce('target')} Target Audience: <code>{len(all_users)}</code> users\n"
        f"{ce('timer')} Dispatching payload...",
        reply_markup=get_bottom_keyboard(admin_id)
    )

    success, blocked, failed = 0, 0, 0

    for idx, uid in enumerate(all_users, 1):
        try:
            await source_message.copy(chat_id=uid)
            success += 1
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try:
                await source_message.copy(chat_id=uid)
                success += 1
            except Exception:
                failed += 1
        except (UserIsBlocked, InputUserDeactivated):
            blocked += 1
        except Exception:
            failed += 1

        if idx % 20 == 0 or idx == len(all_users):
            try:
                await status_msg.edit_text(
                    f"{ce('broadcast')} <b>Broadcast in progress...</b>\n\n"
                    f"{ce('chart')} <b>Progress:</b> <code>{idx}/{len(all_users)}</code>\n"
                    f"{ce('check')} <b>Delivered:</b> <code>{success}</code>\n"
                    f"{ce('offline')} <b>Blocked:</b> <code>{blocked}</code>\n"
                    f"{ce('cross')} <b>Failed:</b> <code>{failed}</code>"
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"┏━━━━━━ {ce('check')} <b>BROADCAST REPORT</b> ━━━━━━┓\n\n"
        f"  {ce('target')} <b>Total Users:</b> <code>{len(all_users)}</code>\n"
        f"  {ce('online')} <b>Delivered:</b> <code>{success}</code>\n"
        f"  {ce('offline')} <b>Blocked:</b> <code>{blocked}</code>\n"
        f"  {ce('cross')} <b>Failed:</b> <code>{failed}</code>\n\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )

# ----------------- ADMIN DASHBOARD MARKUPS ----------------- #

def get_role_based_markup(user_id: int, session_count: int, total_users: int):
    buttons = [
        [
            InlineKeyboardButton(f"{FALLBACK_SYMBOLS['broadcast']} Broadcast to Users ({total_users})", callback_data="btn_start_broadcast")
        ],
        [
            InlineKeyboardButton(f"{FALLBACK_SYMBOLS['folder']} Sessions ({session_count})", callback_data="btn_list_sessions"),
            InlineKeyboardButton(f"{FALLBACK_SYMBOLS['trash']} Remove Session", callback_data="btn_del_menu")
        ],
        [
            InlineKeyboardButton(f"{FALLBACK_SYMBOLS['disk']} Storage Info", callback_data="btn_storage_info"),
            InlineKeyboardButton(f"{FALLBACK_SYMBOLS['broom']} Clean Cache", callback_data="btn_clean_storage")
        ]
    ]

    if user_id == OWNER_ID:
        buttons.append([
            InlineKeyboardButton(f"{FALLBACK_SYMBOLS['users']} Manage Sub-Admins", callback_data="btn_manage_admins")
        ])

    buttons.append([InlineKeyboardButton(f"{FALLBACK_SYMBOLS['refresh']} Refresh Dashboard", callback_data="btn_refresh_panel")])
    return InlineKeyboardMarkup(buttons)

@bot.on_message(filters.command(["admin", "panel"]) & filters.private)
async def admin_panel_handler(_, message: Message):
    user_id = message.from_user.id
    if not is_any_admin(user_id):
        return await message.reply_text(f"{ce('cross')} <b>Access Denied.</b>", reply_markup=get_bottom_keyboard(user_id))

    sessions = await asyncio.to_thread(sync_get_all_sessions)
    all_users = await asyncio.to_thread(sync_get_all_bot_users)

    role_str = f"{ce('crown')} Master Owner" if user_id == OWNER_ID else f"{ce('shield')} Sub-Admin"
    db_str = f"Online {ce('online')}" if db else f"Offline {ce('offline')}"

    text = (
        f"┏━━━━━━ {ce('panel')} <b>ADMIN CONTROL PANEL</b> ━━━━━━┓\n\n"
        f"  {ce('user')} <b>Account Role:</b> {role_str}\n"
        f"  {ce('chart')} <b>Database:</b> {db_str}\n"
        f"  {ce('users')} <b>Total Users:</b> <code>{len(all_users)}</code>\n"
        f"  {ce('folder')} <b>Active Userbots:</b> <code>{len(sessions)}</code>\n"
        f"  {ce('timer')} <b>Auto-Delete Delay:</b> <code>{AUTO_DELETE_TIME}s</code>\n\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    await message.reply_text(text, reply_markup=get_role_based_markup(user_id, len(sessions), len(all_users)))

# ----------------- CALLBACK BUTTONS ROUTING ----------------- #

@bot.on_callback_query()
async def callback_handler(_, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    if data == "btn_cancel_login":
        session_data = login_clients.pop(user_id, None)
        user_states.pop(user_id, None)
        if session_data and session_data.get("cl") and session_data["cl"].is_connected:
            await session_data["cl"].disconnect()
        await query.message.edit_text(f"{ce('cross')} Login process cancelled.")
        return await query.answer("Cancelled")

    if data == "btn_retry_2fa":
        user_states[user_id] = "WAIT_2FA"
        await query.message.reply_text(f"{ce('lock')} Please send your 2FA password:", reply_markup=ForceReply(selective=True))
        return await query.answer()

    if not is_any_admin(user_id):
        return await query.answer("Access Denied", show_alert=True)

    if data == "btn_refresh_panel":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        all_users = await asyncio.to_thread(sync_get_all_bot_users)
        role_str = f"{ce('crown')} Master Owner" if user_id == OWNER_ID else f"{ce('shield')} Sub-Admin"
        db_str = f"Online {ce('online')}" if db else f"Offline {ce('offline')}"

        text = (
            f"┏━━━━━━ {ce('panel')} <b>ADMIN CONTROL PANEL</b> ━━━━━━┓\n\n"
            f"  {ce('user')} <b>Account Role:</b> {role_str}\n"
            f"  {ce('chart')} <b>Database:</b> {db_str}\n"
            f"  {ce('users')} <b>Total Users:</b> <code>{len(all_users)}</code>\n"
            f"  {ce('folder')} <b>Active Userbots:</b> <code>{len(sessions)}</code>\n"
            f"  {ce('timer')} <b>Auto-Delete Delay:</b> <code>{AUTO_DELETE_TIME}s</code>\n\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        await query.message.edit_text(text, reply_markup=get_role_based_markup(user_id, len(sessions), len(all_users)))
        await query.answer("Dashboard Refreshed")

    elif data == "btn_start_broadcast":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_BROADCAST_PAYLOAD"
        await query.message.reply_text(
            f"{ce('broadcast')} <b>Send the message or media to broadcast:</b>\n\n"
            f"• {ce('chat')} Text Message\n"
            f"• {ce('voice')} Voice / Audio Note\n"
            f"• {ce('media')} Photo / {ce('video')} Video with Caption\n\n"
            f"<i>Send <code>/cancel</code> to abort.</i>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_storage_info":
        st = get_storage_stats()
        filled = int(st["percent"] / 10)
        bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
        text = (
            f"┏━━━━━━ {ce('disk')} <b>DISK STORAGE INFO</b> ━━━━━━┓\n\n"
            f"  {ce('chart')} <b>Disk Full:</b> ⌠{bar}⌡ <code>{st['percent']:.1f}%</code>\n"
            f"  {ce('box')} <b>Used Space:</b> <code>{human_bytes(st['used'])}</code>\n"
            f"  {ce('online')} <b>Free Space:</b> <code>{human_bytes(st['free'])}</code>\n"
            f"  {ce('disk')} <b>Total Space:</b> <code>{human_bytes(st['total'])}</code>\n\n"
            f"  {ce('folder')} <b>Temp Downloads:</b> <code>{human_bytes(st['dl_size'])}</code> ({st['dl_files']} files)\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['broom']} Clean Temp Files", callback_data="btn_clean_storage")],
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_refresh_panel")]
        ])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_clean_storage":
        freed_bytes = 0
        if os.path.exists(DOWNLOAD_DIR):
            for filename in os.listdir(DOWNLOAD_DIR):
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        freed_bytes += os.path.getsize(file_path)
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
        await query.answer(f"Freed {human_bytes(freed_bytes)}", show_alert=True)
        st = get_storage_stats()
        filled = int(st["percent"] / 10)
        bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
        text = (
            f"┏━━━━━━ {ce('disk')} <b>DISK STORAGE INFO</b> ━━━━━━┓\n\n"
            f"  {ce('chart')} <b>Disk Full:</b> ⌠{bar}⌡ <code>{st['percent']:.1f}%</code>\n"
            f"  {ce('box')} <b>Used Space:</b> <code>{human_bytes(st['used'])}</code>\n"
            f"  {ce('online')} <b>Free Space:</b> <code>{human_bytes(st['free'])}</code>\n"
            f"  {ce('disk')} <b>Total Space:</b> <code>{human_bytes(st['total'])}</code>\n\n"
            f"  {ce('folder')} <b>Temp Downloads:</b> <code>{human_bytes(st['dl_size'])}</code> ({st['dl_files']} files)\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['broom']} Clean Temp Files", callback_data="btn_clean_storage")],
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_refresh_panel")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "btn_list_sessions":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        text = f"┏━━━━━━ {ce('folder')} <b>ACTIVE SESSIONS</b> ━━━━━━┓\n\n"
        if not sessions:
            text += "  <i>No userbot sessions found.</i>\n"
        for idx, s in enumerate(sessions, 1):
            text += f"  <b>{idx}.</b> <code>{s.get('account_name', 'Unknown')}</code>\n"
        text += "\n┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_refresh_panel")]]))
        await query.answer()

    elif data == "btn_del_menu":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        buttons = [[InlineKeyboardButton(f"{FALLBACK_SYMBOLS['cross']} {s.get('account_name', 'Session')}", callback_data=f"del_{s['doc_id']}")] for s in sessions]
        buttons.append([InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_refresh_panel")])
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
            buttons.append([InlineKeyboardButton(f"{FALLBACK_SYMBOLS['shield']} Admin: {aid}", callback_data=f"viewadmin_{aid}")])
        buttons.append([InlineKeyboardButton("➕ Add Sub-Admin", callback_data="btn_add_new_admin")])
        buttons.append([InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_refresh_panel")])
        await query.message.edit_text("Manage sub-admins and rights:", reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()

    elif data == "btn_add_new_admin":
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        user_states[user_id] = "WAIT_ADMIN_ID"
        await query.message.reply_text(f"{ce('user')} Send Telegram User ID of new admin:", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data.startswith("viewadmin_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        target_uid = int(data.split("_")[1])
        perms = loaded_admins.get(target_uid, {})
        s_tag = FALLBACK_SYMBOLS['online'] if perms.get("sessions") else FALLBACK_SYMBOLS['offline']
        b_tag = FALLBACK_SYMBOLS['online'] if perms.get("broadcast") else FALLBACK_SYMBOLS['offline']

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Manage Sessions: {s_tag}", callback_data=f"togperm_{target_uid}_sessions")],
            [InlineKeyboardButton(f"Send Broadcast: {b_tag}", callback_data=f"togperm_{target_uid}_broadcast")],
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['trash']} Remove Admin", callback_data=f"deladmin_{target_uid}")],
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_manage_admins")]
        ])
        await query.message.edit_text(f"⚙️ <b>Permissions for Admin:</b> <code>{target_uid}</code>", reply_markup=markup)
        await query.answer()

    elif data.startswith("togperm_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        _, t_uid_str, p_key = data.split("_")
        target_uid = int(t_uid_str)
        curr = loaded_admins.get(target_uid, {}).get(p_key, False)
        loaded_admins.setdefault(target_uid, {})[p_key] = not curr
        if db:
            db.collection("bot_admins").document(str(target_uid)).set({p_key: not curr}, merge=True)
        await query.answer("Permission Updated")

        perms = loaded_admins.get(target_uid, {})
        s_tag = FALLBACK_SYMBOLS['online'] if perms.get("sessions") else FALLBACK_SYMBOLS['offline']
        b_tag = FALLBACK_SYMBOLS['online'] if perms.get("broadcast") else FALLBACK_SYMBOLS['offline']
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Manage Sessions: {s_tag}", callback_data=f"togperm_{target_uid}_sessions")],
            [InlineKeyboardButton(f"Send Broadcast: {b_tag}", callback_data=f"togperm_{target_uid}_broadcast")],
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['trash']} Remove Admin", callback_data=f"deladmin_{target_uid}")],
            [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_manage_admins")]
        ])
        await query.message.edit_reply_markup(reply_markup=markup)

    elif data.startswith("deladmin_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        target_uid = int(data.split("_")[1])
        await asyncio.to_thread(sync_delete_admin, target_uid)
        await query.answer(f"Admin {target_uid} removed", show_alert=True)
        buttons = [[InlineKeyboardButton(f"{FALLBACK_SYMBOLS['shield']} Admin: {aid}", callback_data=f"viewadmin_{aid}")] for aid in loaded_admins]
        buttons.append([InlineKeyboardButton("➕ Add Sub-Admin", callback_data="btn_add_new_admin")])
        buttons.append([InlineKeyboardButton(f"{FALLBACK_SYMBOLS['back']} Back", callback_data="btn_refresh_panel")])
        await query.message.edit_text("Admin list updated:", reply_markup=InlineKeyboardMarkup(buttons))

# ----------------- MESSAGE ROUTER & BROADCAST INTAKE ----------------- #

@bot.on_message(filters.private & ~filters.command(["start", "help", "admin", "panel", "ping", "storage", "clean", "login", "cancel"]))
async def message_input_router(_, message: Message):
    user_id = message.from_user.id
    asyncio.create_task(asyncio.to_thread(sync_register_user, user_id))

    state = user_states.get(user_id)
    text_input = (message.text or message.caption or "").strip()

    if "Connect Account" in text_input:
        return await public_login_cmd(_, message)

    elif "Check Latency" in text_input or "Latency" in text_input:
        return await ping_handler(_, message)

    elif "Admin Panel" in text_input:
        return await admin_panel_handler(_, message)

    elif "Broadcast to Users" in text_input or "Broadcast" in text_input:
        if not has_perm(user_id, "broadcast"):
            return await message.reply_text(f"{ce('cross')} <b>Permission Denied.</b>", reply_markup=get_bottom_keyboard(user_id))
        user_states[user_id] = "WAIT_BROADCAST_PAYLOAD"
        return await message.reply_text(
            f"{ce('broadcast')} <b>Forward or send your Broadcast payload now:</b>\n\n"
            f"• {ce('chat')} Text Message\n"
            f"• {ce('voice')} Voice Note / Audio\n"
            f"• {ce('media')} Photo / {ce('video')} Video with Caption\n\n"
            f"<i>Send <code>/cancel</code> to abort.</i>",
            reply_markup=ForceReply(selective=True)
        )

    elif "Disk Storage" in text_input or "Storage" in text_input:
        return await storage_cmd_handler(_, message)

    elif "Clean Storage" in text_input or "Clean Cache" in text_input:
        return await clean_cmd_handler(_, message)

    elif "How To Use" in text_input:
        return await message.reply_text(
            f"┏━━━━━━ {ce('info')} <b>HOW TO USE</b> ━━━━━━┓\n\n"
            f"  1. Send any public or private Telegram link to download media.\n"
            f"  2. Connect your account via <code>/login</code> to access restricted private channels.\n"
            f"  3. Delivered media automatically self-deletes in <code>{AUTO_DELETE_TIME}s</code>.\n\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=get_bottom_keyboard(user_id)
        )

    if state == "WAIT_BROADCAST_PAYLOAD" and has_perm(user_id, "broadcast"):
        user_states.pop(user_id, None)
        asyncio.create_task(broadcast_to_all_users(message, user_id))
        return

    if state == "WAIT_ADMIN_ID" and user_id == OWNER_ID:
        user_states.pop(user_id, None)
        if not text_input.isdigit():
            return await message.reply_text(f"{ce('cross')} <b>User ID must contain only digits.</b>", reply_markup=get_bottom_keyboard(user_id))
        t_id = int(text_input)
        await asyncio.to_thread(sync_add_admin, t_id)
        return await message.reply_text(f"{ce('check')} <b>Admin <code>{t_id}</code> added successfully!</b>", reply_markup=get_bottom_keyboard(user_id))

    if state == "WAIT_PHONE":
        user_states.pop(user_id, None)
        phone = re.sub(r"[^\d+]", "", text_input)
        cl = Client(f"log_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await cl.connect()
            c = await cl.send_code(phone)
            login_clients[user_id] = {"cl": cl, "phone": phone, "hash": c.phone_code_hash}
            user_states[user_id] = "WAIT_OTP"
            await message.reply_text(
                f"{ce('phone')} <b>Telegram Verification Code</b>\n\n"
                f"Enter the OTP received from Telegram.\n\n"
                f"{ce('warning')} <b>Important:</b> To prevent Telegram from auto-expiring the code, "
                f"separate digits with spaces or dashes (e.g. <code>1-2-3-4-5</code>):",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            await message.reply_text(f"{ce('cross')} Failed to send code: <code>{e}</code>", reply_markup=get_bottom_keyboard(user_id))
        return

    elif state == "WAIT_OTP":
        otp = "".join(re.findall(r"\d", text_input))
        data = login_clients.get(user_id)
        if not data:
            user_states.pop(user_id, None)
            return await message.reply_text(f"{ce('cross')} Session expired. Please send /login again.", reply_markup=get_bottom_keyboard(user_id))

        cl = data["cl"]
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
            await message.reply_text(
                f"{ce('check')} <b>Account Connected Successfully!</b>\n\n"
                f"Welcome, <code>{acc}</code>. Your session is active.",
                reply_markup=get_bottom_keyboard(user_id)
            )
        except SessionPasswordNeeded:
            user_states[user_id] = "WAIT_2FA"
            await message.reply_text(
                f"{ce('lock')} <b>Two-Step Verification Detected</b>\n\n"
                f"Please enter your cloud account password:",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            err = str(e)
            if "PHONE_CODE_EXPIRED" in err:
                err_msg = "The code expired or Telegram blocked it. Please request a new code via /login and format it with dashes (e.g. 1-2-3-4-5)."
            else:
                err_msg = err

            await cl.disconnect()
            login_clients.pop(user_id, None)
            user_states.pop(user_id, None)
            await message.reply_text(f"{ce('cross')} <b>Login failed:</b> <code>{err_msg}</code>", reply_markup=get_bottom_keyboard(user_id))
        return

    elif state == "WAIT_2FA":
        data = login_clients.get(user_id)
        if not data:
            user_states.pop(user_id, None)
            return
        cl = data["cl"]
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
            await message.reply_text(
                f"{ce('check')} <b>Account Connected Successfully!</b>\n\n"
                f"Welcome, <code>{acc}</code>.",
                reply_markup=get_bottom_keyboard(user_id)
            )
        except Exception as e:
            await message.reply_text(
                f"{ce('cross')} Incorrect 2FA Password: <code>{e}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['refresh']} Retry", callback_data="btn_retry_2fa"),
                     InlineKeyboardButton(f"{FALLBACK_SYMBOLS['cross']} Cancel", callback_data="btn_cancel_login")]
                ])
            )
        return

    # ----------------- TELEGRAM POST DOWNLOAD ROUTINE ----------------- #
    p_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_input)
    pub_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_input)

    if not (p_match or pub_match):
        return

    try:
        await message.react(FALLBACK_SYMBOLS["reaction"])
    except Exception:
        pass

    if p_match:
        chat_id = int("-100" + p_match.group(1))
        msg_id = int(p_match.group(2))
    else:
        chat_id = pub_match.group(1)
        msg_id = int(pub_match.group(2))

    # Fast Server Copy
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
        return await message.reply_text(f"{ce('cross')} No active sessions found. Connect an account using <code>/login</code>.", reply_markup=get_bottom_keyboard(user_id))

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
        return await message.reply_text(f"{ce('cross')} Post unavailable or channel not joined by any active account.", reply_markup=get_bottom_keyboard(user_id))

    # Direct Forward Bypass
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

    status = await message.reply_text(f"{ce('spark')} <b>Preparing transfer...</b>", reply_markup=get_bottom_keyboard(user_id))
    task_id = f"task_{message.from_user.id}_{int(time.time())}"

    # Album Processing
    if len(media_group) > 1:
        alb_tracker = AlbumProgressTracker(status, len(media_group), "Downloading album...", task_id=task_id)
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

        alb_tracker.finish()

        if len(input_media) >= 2:
            await status.edit_text(f"{ce('spark')} <b>Uploading album to Telegram...</b>")
            sent_msgs = []
            for i in range(0, len(input_media), 10):
                res = await bot.send_media_group(message.chat.id, input_media[i:i + 10])
                sent_msgs.extend(res)
            await status.delete()
            asyncio.create_task(silent_auto_delete(message.chat.id, [m.id for m in sent_msgs] + [message.id]))
        else:
            await status.edit_text(f"{ce('cross')} Album processing failed.")

        for f in files_to_cleanup + thumbs_to_cleanup:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        return

    # Single Media Processing
    if not (target_msg.media or target_msg.video or target_msg.photo or target_msg.document or target_msg.audio or target_msg.voice):
        if target_msg.text:
            return await status.edit_text(f"📝 <b>Text:</b>\n\n{target_msg.text}")
        return await status.edit_text(f"{ce('warning')} No media found.")

    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ""
    if not ext and target_msg.document:
        ext = os.path.splitext(target_msg.document.file_name or "")[1] or ".bin"

    single_dest = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}{ext}")
    dl_tracker = ProgressTracker(status, "Downloading media...", task_id=f"dl_{task_id}", task_type="download")
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
        except Exception:
            if attempt == 0:
                await asyncio.sleep(2)

    dl_tracker.finish()

    if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return await status.edit_text(f"{ce('cross')} <b>Download failed.</b>")

    thumb_path = None
    duration = target_msg.video.duration or 0 if target_msg.video else 0
    width = target_msg.video.width or 0 if target_msg.video else 0
    height = target_msg.video.height or 0 if target_msg.video else 0

    if target_msg.video and target_msg.video.thumbs:
        try:
            thumb_dest = os.path.join(DOWNLOAD_DIR, f"thumb_{message.from_user.id}_{int(time.time())}.jpg")
            thumb_path = await working_client.download_media(target_msg.video.thumbs[0].file_id, file_name=thumb_dest)
        except Exception:
            pass

    up_tracker = ProgressTracker(status, "Uploading media...", task_id=f"up_{task_id}", task_type="upload")
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
        await status.edit_text(f"{ce('cross')} <b>Upload failed:</b> <code>{e}</code>")
    finally:
        up_tracker.finish()

    await status.delete()
    if sent:
        asyncio.create_task(silent_auto_delete(message.chat.id, [sent.id, message.id]))

    for f in [file_path, thumb_path]:
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

# ----------------- ALBUM ITEM DOWNLOAD HELPER ----------------- #

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
            except Exception:
                await asyncio.sleep(2)

        thumb_path = None
        if path and media_msg.video and media_msg.video.thumbs:
            try:
                thumb_dest = os.path.join(DOWNLOAD_DIR, f"alb_thumb_{user_id}_{int(time.time())}_{idx}.jpg")
                thumb_path = await client.download_media(media_msg.video.thumbs[0].file_id, file_name=thumb_dest)
            except Exception:
                pass

        return path, media_msg, idx, thumb_path

# ----------------- BASE COMMANDS ----------------- #

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    user_id = message.from_user.id
    asyncio.create_task(asyncio.to_thread(sync_register_user, user_id))
    await message.reply_text(
        f"{ce('hand')} <b>Welcome, {message.from_user.first_name}!</b>\n\n"
        f"{ce('spark')} Send any public or private Telegram link to download media.\n\n"
        f"{ce('lock')} Use <code>/login</code> or the button below to link your account.",
        reply_markup=get_bottom_keyboard(user_id)
    )

@bot.on_message(filters.command("login") & filters.private)
async def public_login_cmd(_, message: Message):
    user_id = message.from_user.id
    user_states[user_id] = "WAIT_PHONE"
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"{FALLBACK_SYMBOLS['cross']} Cancel", callback_data="btn_cancel_login")]])
    await message.reply_text(
        f"{ce('phone')} <b>Connect Your Telegram Account</b>\n\n"
        "Send your phone number with country code (e.g. <code>+8801700000000</code>):\n\n"
        "<i>Your account helps the bot download restricted content.</i>",
        reply_markup=markup
    )

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message: Message):
    user_id = message.from_user.id
    user_states.pop(user_id, None)
    login_clients.pop(user_id, None)
    await message.reply_text(f"{ce('check')} <b>Action cancelled.</b>", reply_markup=get_bottom_keyboard(user_id))

@bot.on_message(filters.command("ping") & filters.private)
async def ping_handler(_, message: Message):
    t0 = time.time()
    msg = await message.reply_text(f"{ce('spark')} Testing Latency...")
    await msg.edit_text(f"{ce('rocket')} <b>Latency:</b> <code>{(time.time() - t0) * 1000:.1f}ms</code>")

@bot.on_message(filters.command("storage") & filters.private)
async def storage_cmd_handler(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text(f"{ce('cross')} Access Denied.", reply_markup=get_bottom_keyboard(message.from_user.id))
    st = get_storage_stats()
    filled = int(st["percent"] / 10)
    bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
    text = (
        f"┏━━━━━━ {ce('disk')} <b>DISK STORAGE INFO</b> ━━━━━━┓\n\n"
        f"  {ce('chart')} <b>Disk Full:</b> ⌠{bar}⌡ <code>{st['percent']:.1f}%</code>\n"
        f"  {ce('box')} <b>Used Space:</b> <code>{human_bytes(st['used'])}</code>\n"
        f"  {ce('online')} <b>Free Space:</b> <code>{human_bytes(st['free'])}</code>\n"
        f"  {ce('disk')} <b>Total Space:</b> <code>{human_bytes(st['total'])}</code>\n\n"
        f"  {ce('folder')} <b>Temp Downloads:</b> <code>{human_bytes(st['dl_size'])}</code> ({st['dl_files']} files)\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{FALLBACK_SYMBOLS['broom']} Clean Temp Files", callback_data="btn_clean_storage")]
    ])
    await message.reply_text(text, reply_markup=markup)

@bot.on_message(filters.command("clean") & filters.private)
async def clean_cmd_handler(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text(f"{ce('cross')} Access Denied.")

    msg = await message.reply_text(f"{ce('broom')} Cleaning temp files...")
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
        f"{ce('check')} <b>Storage Cleaned!</b>\n\n"
        f"{ce('trash')} Files Removed: <code>{deleted_files}</code>\n"
        f"{ce('disk')} Space Freed: <code>{human_bytes(freed_bytes)}</code>"
    )

# ----------------- WEB DASHBOARD ----------------- #

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
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 24px; width: 100%; max-width: 480px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
        .title { font-size: 20px; font-weight: 700; color: #38bdf8; display: flex; align-items: center; gap: 8px; }
        .status-badge { background: #064e3b; color: #34d399; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 9999px; display: inline-flex; align-items: center; gap: 6px; }
        .status-dot { width: 8px; height: 8px; background: #10b981; border-radius: 50%; animation: pulse 2s infinite; }
        .section-title { font-size: 13px; font-weight: 700; color: #94a3b8; text-transform: uppercase; margin-bottom: 12px; letter-spacing: 0.5px; }
        
        .storage-box { background: #0f172a; border-radius: 12px; padding: 16px; margin-bottom: 20px; border: 1px solid #1e293b; }
        .storage-title { font-size: 13px; color: #94a3b8; margin-bottom: 8px; display: flex; justify-content: space-between; }
        .storage-val { font-size: 24px; font-weight: 800; color: #f8fafc; margin-bottom: 10px; }
        .progress-bar-bg { width: 100%; height: 10px; background: #334155; border-radius: 6px; overflow: hidden; }
        .progress-bar-fill { height: 100%; border-radius: 6px; transition: width 0.4s ease; }
        
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
        .stat-item { background: #1e293b; border: 1px solid #334155; padding: 10px; border-radius: 8px; }
        .stat-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-num { font-size: 14px; font-weight: 600; color: #f1f5f9; margin-top: 2px; }

        .live-box { background: #0f172a; border-radius: 12px; padding: 16px; margin-bottom: 16px; border: 1px solid #1e293b; min-height: 110px; }
        .empty-tasks { color: #64748b; font-size: 13px; text-align: center; padding: 24px 0; }
        .task-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 12px; margin-bottom: 10px; }
        .task-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
        .task-name { font-size: 13px; font-weight: 600; color: #f8fafc; }
        .task-tag { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 6px; text-transform: uppercase; }
        .tag-download { background: #0284c7; color: #e0f2fe; }
        .tag-upload { background: #7c3aed; color: #ede9fe; }
        .tag-album { background: #d97706; color: #fef3c7; }
        .task-meta { display: flex; justify-content: space-between; font-size: 12px; color: #94a3b8; margin-top: 6px; }

        .btn-clean { display: block; width: 100%; padding: 12px; background: #ef4444; color: white; border: none; border-radius: 10px; font-weight: 600; cursor: pointer; text-align: center; text-decoration: none; transition: 0.2s; }
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
                <span>DISK STORAGE</span>
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
                    <div class="stat-label">Temp Cache</div>
                    <div class="stat-num" id="stat-temp">--</div>
                </div>
            </div>
        </div>

        <div class="section-title">⚡ Live Download & Upload Tasks</div>
        <div class="live-box" id="tasks-container">
            <div class="empty-tasks">No active transfers running.</div>
        </div>

        <button class="btn-clean" onclick="cleanStorage()">🧹 Clean Download Cache</button>
    </div>

    <script>
        function humanBytes(bytes) {
            if (!bytes || bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

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

        async function fetchTasks() {
            try {
                const res = await fetch('/api/tasks');
                const tasks = await res.json();
                const container = document.getElementById('tasks-container');

                const keys = Object.keys(tasks);
                if (keys.length === 0) {
                    container.innerHTML = '<div class="empty-tasks">No active transfers running.</div>';
                    return;
                }

                let html = '';
                keys.forEach(k => {
                    const t = tasks[k];
                    const tagClass = t.type === 'download' ? 'tag-download' : (t.type === 'upload' ? 'tag-upload' : 'tag-album');
                    const barColor = t.type === 'upload' ? '#8b5cf6' : '#38bdf8';
                    html += `
                        <div class="task-card">
                            <div class="task-header">
                                <span class="task-name">${t.name}</span>
                                <span class="task-tag ${tagClass}">${t.type}</span>
                            </div>
                            <div class="progress-bar-bg">
                                <div class="progress-bar-fill" style="width: ${t.percent}%; background: ${barColor};"></div>
                            </div>
                            <div class="task-meta">
                                <span>${humanBytes(t.current)} / ${humanBytes(t.total)} (${t.percent}%)</span>
                                <span>⚡ ${t.speed || '--'}</span>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
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
        fetchTasks();
        setInterval(fetchStats, 5000);
        setInterval(fetchTasks, 1500);
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

async def handle_tasks(_):
    now = time.time()
    stale_keys = [k for k, v in active_tasks.items() if now - v.get("updated_at", 0) > 45]
    for k in stale_keys:
        active_tasks.pop(k, None)
    return web.json_response(active_tasks)

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
    app.router.add_get("/api/tasks", handle_tasks)
    app.router.add_post("/api/clean", handle_web_clean)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Monitor Server active on port {PORT}")

# ----------------- STARTUP ----------------- #

async def main():
    await bot.start()
    try:
        await bot.set_bot_commands([
            BotCommand("start", "Start the bot")
        ])
    except Exception:
        pass
    await init_session_pool()
    await web_server()
    logger.info("Bot Online with Persistent Bottom Keyboard and /start menu!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())