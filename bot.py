import os
import re
import time
import json
import shutil
import asyncio
import logging
import html
import hmac
import hashlib
import aiohttp
from urllib.parse import quote, parse_qsl
from aiohttp import web
from hydrogram import Client, filters
from hydrogram.enums import ParseMode, ChatMemberStatus
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
from hydrogram.errors import (
    SessionPasswordNeeded,
    FloodWait,
    UserIsBlocked,
    InputUserDeactivated,
    UserNotParticipant
)

try:
    from hydrogram import StopTransmission
except ImportError:
    class StopTransmission(Exception):
        pass

# =====================================================================
# 🎨 CENTRALIZED UI CUSTOM EMOJIS CONFIGURATION
# 👉 এখান থেকে বা Admin Panel > Emoji Manager থেকে বদলানো যাবে
# =====================================================================
UI_EMOJIS = {
    # 📌 General & System Status
    "spark": "6053061091456457277",
    "rocket": "6314344267267450186",
    "online": "6311876323224592776",
    "offline": "6314179022695702286",
    "refresh": "5370715282044100355",
    "cross": "6052869226677410910",
    "check": "6053202116707622090",
    "warning": "6183514473895042082",
    "back": "6314565157435480578",

    # 🎛️ Admin Panel & Roles
    "panel": "6053275839821257175",
    "crown": "6338946058982267602",
    "shield": "6052869252447215120",
    "user": "5337255927735163754",
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

    # 🆕 নতুন ইমোজি — ফাঁকা থাকলে অটো সাধারণ ইমোজি দেখাবে, কোনো এরর হবে না
    "hourglass": "5371058888017715839",   # ⏳ Dispatching payload
    "chart": "5451882707875276247",       # 📊 Progress / Disk Usage
    "box": "5193085063998224234",         # 📦 Used Space
    "cabinet": "5352721946054268944",     # 🗂️ Total Capacity
    "antenna": "5424972470023104089",     # 📡 Database
    "chat": "5253742260054409879",        # 💬 Send Text Message
    "mic": "5224736245665511429",         # 🎙️ Send Voice / Audio
    "bulb": "5422439311196834318",        # 💡 Tip
    "info": "5352597830089347330",        # ℹ️ How To Operate
    "wave": "5199885118214255386",        # 👋 Welcome
    "note": "5395444784611480792",        # 📝 Text Content
    "gear": "5341715473882955310",        # ⚙️ Permissions

    # 🎁 Referral & 🎨 Colors — চাইলে নিজের Custom Emoji ID বসান (ফাঁকা = সাধারণ ইমোজি)
    "gift": "",
    "palette": "",

    # 🆕 Maintenance / Promo / Leaderboard / Help (ফাঁকা = সাধারণ ইমোজি)
    "tool": "",       # 🛠️ Maintenance
    "trophy": "",     # 🏆 Leaderboard
    "ticket": "",     # 🎟️ Promo / Redeem
    "book": "",       # 📖 Help Guide
    "next": "",       # ▶️ Next page
    "prev": "",       # ◀️ Previous page
    "emoji": "",      # 😀 Emoji Manager
    "bell": "",       # 🔔 Download Alerts
}

FALLBACK_SYMBOLS = {
    "spark": "⚡", "rocket": "🚀", "online": "🟢", "offline": "🔴",
    "refresh": "🔄", "cross": "❌", "check": "✅", "warning": "⚠️",
    "back": "🔙", "panel": "🎛️", "crown": "👑", "shield": "🛡️",
    "user": "👤", "users": "👥", "lock": "🔐", "phone": "📱",
    "disk": "💾", "broom": "🧹", "folder": "📂", "trash": "🗑️",
    "broadcast": "📢", "timer": "⏱️", "target": "🎯", "media": "🖼️",
    "pencil": "✏️", "list": "📋", "download": "📥", "upload": "📤",
    "bar_fill": "▰", "bar_empty": "▱",
    "hourglass": "⏳", "chart": "📊", "box": "📦", "cabinet": "🗂️", "antenna": "📡",
    "chat": "💬", "mic": "🎙️", "bulb": "💡", "info": "ℹ️", "wave": "👋",
    "note": "📝", "gear": "⚙️",
    "gift": "🎁", "palette": "🎨",
    "tool": "🛠️", "trophy": "🏆", "ticket": "🎟️", "book": "📖",
    "next": "▶️", "prev": "◀️", "emoji": "😀", "reaction": "🫡", "bell": "🔔",
}

# রিসেটের জন্য ডিফল্ট কপি (Emoji Manager > Use Default)
DEFAULT_UI_EMOJIS = dict(UI_EMOJIS)

def ce(name: str) -> str:
    """Renders Telegram Custom Animated Emoji in HTML ParseMode."""
    emoji_id = UI_EMOJIS.get(name, "")
    fallback = FALLBACK_SYMBOLS.get(name, "✨")
    if emoji_id:
        return f'<emoji id="{emoji_id}">{fallback}</emoji>'
    return fallback

def show_emoji(emoji_id: str, fallback: str) -> str:
    """যেকোনো ID + fallback থেকে প্রিভিউ"""
    if emoji_id:
        return f'<emoji id="{emoji_id}">{fallback}</emoji>'
    return fallback

# =====================================================================
# ⌨️ BOTTOM (REPLY) KEYBOARD BUTTONS CONFIG
# 👉 "style" হবে: "primary" (নীল) | "success" (সবুজ) | "danger" (লাল) | "" (ডিফল্ট)
# 👉 Admin Panel > Button Colors / Emoji Manager থেকে বদলালে সেটাই প্রাধান্য পাবে
# =====================================================================
KB_BUTTONS = {
    "admin":     {"text": "Admin Panel",     "emoji_id": "6314576556278685829", "style": "primary", "legacy": FALLBACK_SYMBOLS["panel"]},
    "broadcast": {"text": "Broadcast",       "emoji_id": "6311947409228307310", "style": "primary", "legacy": FALLBACK_SYMBOLS["broadcast"]},
    "storage":   {"text": "Storage",         "emoji_id": "6314438073648158907", "style": "success", "legacy": FALLBACK_SYMBOLS["disk"]},
    "clean":     {"text": "Clean Cache",     "emoji_id": "6314192620562161224", "style": "danger",  "legacy": FALLBACK_SYMBOLS["broom"]},
    "connect":   {"text": "Connect Account", "emoji_id": "6314537472076291328", "style": "success", "legacy": FALLBACK_SYMBOLS["phone"]},
    "latency":   {"text": "Latency",         "emoji_id": "6052973985224728368", "style": "primary", "legacy": FALLBACK_SYMBOLS["rocket"]},
    "howto":     {"text": "How To Use",      "emoji_id": "6314162714704878982", "style": "primary", "legacy": "ℹ️"},
    "profile":   {"text": "Profile",         "emoji_id": UI_EMOJIS["user"],     "style": "primary", "legacy": FALLBACK_SYMBOLS["user"]},
    "redeem":    {"text": "Redeem Code",     "emoji_id": "",                    "style": "success", "legacy": FALLBACK_SYMBOLS["ticket"]},
}

# রিসেটের জন্য ডিফল্ট কিবোর্ড ইমোজি
DEFAULT_KB_EMOJIS = {k: v.get("emoji_id", "") for k, v in KB_BUTTONS.items()}

# ---------------------------------------------------------------------
# 📐 বাটনের পজিশন (লেআউট) — প্রতিটা [ ] = একটা সারি (row)
# ---------------------------------------------------------------------
KB_LAYOUT_ADMIN = [
    ["admin", "broadcast"],
    ["storage", "clean"],
    ["connect", "latency"],
    ["profile", "redeem"],
    ["howto"],
]
KB_LAYOUT_USER = [
    ["connect", "latency"],
    ["profile", "howto"],
    ["redeem"],
]

# True হলে Admin/Owner একাধিক একাউন্ট কানেক্ট করতে পারবে (সাধারণ ইউজার শুধু ১টা)
ADMINS_CAN_ADD_MULTIPLE = True
# True হলে Admin/Owner এর ক্রেডিট লাগবে না
ADMINS_UNLIMITED = True

def is_btn(text: str, key: str) -> bool:
    """নতুন (ইমোজি ছাড়া) এবং পুরনো (ইমোজি সহ) দুই ধরনের বাটন টেক্সটই ম্যাচ করবে"""
    b = KB_BUTTONS[key]
    return text in (b["text"], f"{b['legacy']} {b['text']}")

# =====================================================================
# 🛠️ BOT API BRIDGE
# Hydrogram (MTProto) বাটনের icon_custom_emoji_id / style পাঠাতে পারে না,
# তাই বাটন-সহ মেসেজগুলো সরাসরি Telegram Bot API দিয়ে যাবে।
# =====================================================================
class RawMarkup:
    """Bot API-র জন্য কাঁচা JSON reply_markup রাখার ক্লাস"""
    def __init__(self, data: dict):
        self.data = data

_EMOJI_TAG_RE = re.compile(r'<emoji id="(\d+)">(.*?)</emoji>', re.DOTALL)

def to_bot_api_html(text: str) -> str:
    """Hydrogram-এর <emoji id=".."> কে Bot API-র <tg-emoji emoji-id=".."> এ রূপান্তর"""
    return _EMOJI_TAG_RE.sub(r'<tg-emoji emoji-id="\1">\2</tg-emoji>', text or "")

async def bot_api_call(method: str, payload: dict):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload) as r:
                data = await r.json()
                if not data.get("ok"):
                    logger.error(f"BotAPI {method} failed: {data.get('description')}")
                return data
    except Exception as e:
        logger.error(f"BotAPI {method} error: {e}")
        return {"ok": False}

def create_custom_inline_markup(rows: list) -> RawMarkup:
    """Raw Dictionary থেকে Bot API Inline Keyboard বানানোর ফাংশন (icon_custom_emoji_id, style, url সহ)"""
    kb_rows = []
    for row in rows:
        kb_row = []
        for btn in row:
            b = {"text": btn["text"]}
            if btn.get("callback_data"):
                b["callback_data"] = btn["callback_data"]
            if btn.get("url"):
                b["url"] = btn["url"]
            if btn.get("web_app"):
                b["web_app"] = {"url": btn["web_app"]}
            if btn.get("icon_custom_emoji_id"):
                b["icon_custom_emoji_id"] = str(btn["icon_custom_emoji_id"])
            if btn.get("style"):
                b["style"] = str(btn["style"])
            kb_row.append(b)
        kb_rows.append(kb_row)
    return RawMarkup({"inline_keyboard": kb_rows})

# ---- Hydrogram মেথড প্যাচ: RawMarkup পেলে Bot API, নাহলে আগের মতোই Hydrogram ----
_orig_reply_text = Message.reply_text
_orig_edit_text = Message.edit_text
_orig_edit_reply_markup = Message.edit_reply_markup
_orig_send_message = Client.send_message

async def _patched_reply_text(self, text, *args, reply_markup=None, **kwargs):
    if isinstance(reply_markup, RawMarkup):
        return await bot_api_call("sendMessage", {
            "chat_id": self.chat.id,
            "text": to_bot_api_html(text),
            "parse_mode": "HTML",
            "reply_markup": reply_markup.data
        })
    return await _orig_reply_text(self, text, *args, reply_markup=reply_markup, **kwargs)

async def _patched_edit_text(self, text, *args, reply_markup=None, **kwargs):
    if isinstance(reply_markup, RawMarkup):
        return await bot_api_call("editMessageText", {
            "chat_id": self.chat.id,
            "message_id": self.id,
            "text": to_bot_api_html(text),
            "parse_mode": "HTML",
            "reply_markup": reply_markup.data
        })
    return await _orig_edit_text(self, text, *args, reply_markup=reply_markup, **kwargs)

async def _patched_edit_reply_markup(self, reply_markup=None, *args, **kwargs):
    if isinstance(reply_markup, RawMarkup):
        return await bot_api_call("editMessageReplyMarkup", {
            "chat_id": self.chat.id,
            "message_id": self.id,
            "reply_markup": reply_markup.data
        })
    return await _orig_edit_reply_markup(self, reply_markup, *args, **kwargs)

async def _patched_send_message(self, chat_id, text, *args, reply_markup=None, **kwargs):
    if isinstance(reply_markup, RawMarkup):
        return await bot_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": to_bot_api_html(text),
            "parse_mode": "HTML",
            "reply_markup": reply_markup.data
        })
    return await _orig_send_message(self, chat_id, text, *args, reply_markup=reply_markup, **kwargs)

Message.reply_text = _patched_reply_text
Message.edit_text = _patched_edit_text
Message.edit_reply_markup = _patched_edit_reply_markup
Client.send_message = _patched_send_message

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

# =====================================================================
# 🔐 Environment Configuration (সব সিক্রেট এখন শুধু env থেকে পড়া হবে)
# =====================================================================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"].strip()
BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()

OWNER_ID = 8271633124
PORT = int(os.environ.get("PORT", "8080"))
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "120"))  # Seconds

# 🌐 Web Dashboard সিকিউরিটি: DASH_TOKEN না দিলে ড্যাশবোর্ডের ডাটা/API বন্ধ থাকবে
DASH_TOKEN = os.environ.get("DASH_TOKEN", "").strip()
DASH_URL = os.environ.get("DASH_URL", "").strip().rstrip("/")

DAILY_CREDITS = int(os.environ.get("DAILY_CREDITS", "30"))
BOT_START = time.time()
BOT_USERNAME = ""  # স্টার্টআপে অটো সেট হবে (রেফার লিংকের জন্য)

# Force Subscribe: @username অথবা -100xxxxxxxxxx (বট ঐ চ্যানেলে Admin হতে হবে)
_fs_raw = os.environ.get("FORCE_SUB_CHANNEL", "").strip()
FORCE_SUB_CHANNEL = int(_fs_raw) if _fs_raw.lstrip("-").isdigit() else _fs_raw
FORCE_SUB_LINK = os.environ.get("FORCE_SUB_LINK", "").strip() or (
    f"https://t.me/{_fs_raw.lstrip('@')}" if _fs_raw and not _fs_raw.lstrip("-").isdigit() else ""
)

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

# =====================================================================
# ⚙️ DYNAMIC SETTINGS (Admin Panel থেকে বদলানো যায়, Firebase এ সেভ হয়)
#   ref_reward  → প্রতিটা সফল রেফারে রেফারার কত ক্রেডিট পাবে (0 = বন্ধ)
#   btn_styles  → নিচের কিবোর্ডের বাটনের কালার ওভাররাইড
#   maintenance → True হলে সাধারণ ইউজার বট ব্যবহার করতে পারবে না
#   maint_msg   → মেইনটেনেন্স মেসেজ (ফাঁকা = ডিফল্ট)
#   emojis      → UI/Inline ইমোজি ওভাররাইড  {name: id}
#   kb_emojis   → নিচের কিবোর্ড বাটনের ইমোজি ওভাররাইড {key: id}
#   ads         → Adsgram Mini App ad-গেট সেটিংস
# =====================================================================
SETTINGS = {
    "ref_reward": 10,
    "btn_styles": {},
    "maintenance": False,
    "maint_msg": "",
    "emojis": {},
    "kb_emojis": {},
    "notify": {
        "on": True,
        "title": "New Download",
        "show": {"user": True, "username": True, "id": True, "type": True,
                 "size": True, "credits": True, "link": True, "time": True},
    },
    "ads": {
        "on": False,
        "provider": "test",        # "test" (ডেমো) | "adsgram"
        "block_id": "",
        "every_n": 8,              # প্রতি কয়টা ডাউনলোড পর পর ad
        "first_time": True,        # প্রথম ডাউনলোডেই ad
        "skip_admins": True,       # Admin/Owner কে ad দেখাবে না
        "fail_open": True,         # ad না এলে (no-fill) ইউজারকে ছেড়ে দেবে
        "min_seconds": 5,          # এর কমে ad শেষ হলে ফেক ধরা হবে
        "title": "Watch a short ad",
        "text": "Watch one short ad to unlock your download. It only takes a few seconds!",
    },
}
NOTIFY_FIELDS = [
    ("user", "Name"), ("username", "Username"), ("id", "User ID"), ("type", "Media Type"),
    ("size", "File Size"), ("credits", "Credits Left"), ("link", "Post Link"), ("time", "Time"),
]
STYLE_CYCLE = ["primary", "success", "danger", ""]
STYLE_DOT = {"primary": "🔵", "success": "🟢", "danger": "🔴", "": "⚪"}

def apply_emoji_overrides():
    """সেভ করা ইমোজি ওভাররাইড UI_EMOJIS এ বসায় (বটের সব জায়গায় সাথে সাথে কাজ করে)"""
    for k, v in DEFAULT_UI_EMOJIS.items():
        UI_EMOJIS[k] = SETTINGS["emojis"].get(k, v)

def sync_load_settings():
    if not db:
        return
    try:
        d = db.collection("bot_config").document("settings").get()
        if d.exists:
            data = d.to_dict()
            SETTINGS["ref_reward"] = int(data.get("ref_reward", 10))
            SETTINGS["btn_styles"] = dict(data.get("btn_styles", {}))
            SETTINGS["maintenance"] = bool(data.get("maintenance", False))
            SETTINGS["maint_msg"] = str(data.get("maint_msg", ""))
            SETTINGS["emojis"] = dict(data.get("emojis", {}))
            SETTINGS["kb_emojis"] = dict(data.get("kb_emojis", {}))
            n = data.get("notify")
            if isinstance(n, dict):
                SETTINGS["notify"]["on"] = bool(n.get("on", True))
                SETTINGS["notify"]["title"] = str(n.get("title", "New Download")) or "New Download"
                SETTINGS["notify"]["show"].update(dict(n.get("show", {})))
            ad = data.get("ads")
            if isinstance(ad, dict):
                SETTINGS["ads"].update(ad)
            apply_emoji_overrides()
    except Exception as e:
        logger.error(f"Load settings error: {e}")

def sync_save_settings():
    if not db:
        return
    try:
        db.collection("bot_config").document("settings").set(SETTINGS)
    except Exception as e:
        logger.error(f"Save settings error: {e}")

bot = Client(
    "tg_vault_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=16,
    parse_mode=ParseMode.HTML
)

user_states = {}
pending_ads = {}           # uid -> যে মেসেজের ডাউনলোড ad এর জন্য থেমে আছে
ad_started = {}            # uid -> কখন ad পেজ খুলেছে (ফেক চেকের জন্য)
login_clients = {}
loaded_user_clients = []
loaded_admins = {}
mem_bot_users = set()

active_tasks = {}
user_cache = {}
pending_target = {}        # admin -> যে user/emoji কে ম্যানেজ করছে
cancel_requests = set()    # ক্যান্সেল করা টাস্ক
READ_CACHE = {}            # token -> (doc_id, chat_id, msg_id) — Read Message ফিচারের জন্য
PROMOS = {}                # code -> {"credits": int, "max_uses": int, "used_by": [ids]}

# ----------------- BOTTOM KEYBOARD BUILDER ----------------- #

def _kb_btn(key: str) -> dict:
    b = KB_BUTTONS[key]
    out = {"text": b["text"]}
    eid = SETTINGS["kb_emojis"].get(key, b.get("emoji_id", ""))
    if eid:
        out["icon_custom_emoji_id"] = str(eid)
    style = SETTINGS["btn_styles"].get(key, b.get("style", ""))
    if style:
        out["style"] = style
    return out

def get_bottom_keyboard(user_id: int):
    """কিবোর্ডের বাটন সেটআপ (Bot API: icon_custom_emoji_id + style সহ)"""
    layout = KB_LAYOUT_ADMIN if is_any_admin(user_id) else KB_LAYOUT_USER
    rows = [[_kb_btn(k) for k in row if k in KB_BUTTONS] for row in layout]
    rows = [r for r in rows if r]
    return RawMarkup({"keyboard": rows, "resize_keyboard": True})

def colors_markup() -> RawMarkup:
    """Admin Panel > Button Colors: প্রতিটা বাটন চাপলে কালার ঘুরবে (🔵→🟢→🔴→⚪)"""
    rows = []
    for k, b in KB_BUTTONS.items():
        cur = SETTINGS["btn_styles"].get(k, b.get("style", ""))
        rows.append([{
            "text": f"{b['text']}: {STYLE_DOT.get(cur, '⚪')}",
            "callback_data": f"colcyc_{k}",
            "style": cur
        }])
    rows.append([
        {"text": "Apply to my keyboard", "callback_data": "btn_col_apply", "style": "success"},
        {"text": "Reset", "callback_data": "btn_col_reset", "style": "danger"},
    ])
    rows.append([{"text": "Back", "callback_data": "btn_refresh_panel",
                  "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
    return create_custom_inline_markup(rows)

# ----------------- 🎨 EMOJI MANAGER HELPERS ----------------- #

EM_PER_PAGE = 10

def emoji_current(scope: str, name: str) -> str:
    if scope == "k":
        return SETTINGS["kb_emojis"].get(name, KB_BUTTONS[name].get("emoji_id", ""))
    return UI_EMOJIS.get(name, "")

def emoji_fallback(scope: str, name: str) -> str:
    if scope == "k":
        return KB_BUTTONS[name]["legacy"]
    return FALLBACK_SYMBOLS.get(name, "✨")

def emoji_valid(scope: str, name: str) -> bool:
    return (scope == "k" and name in KB_BUTTONS) or (scope == "u" and name in UI_EMOJIS)

def emoji_set(scope: str, name: str, value):
    """value: str (ID, '' = ফাঁকা) অথবা None (= ডিফল্টে ফেরত)"""
    if scope == "k":
        if value is None:
            SETTINGS["kb_emojis"].pop(name, None)
        else:
            SETTINGS["kb_emojis"][name] = value
    else:
        if value is None:
            SETTINGS["emojis"].pop(name, None)
        else:
            SETTINGS["emojis"][name] = value
        apply_emoji_overrides()
    sync_save_settings()

def emoji_menu_text() -> str:
    return (
        f"┌── {ce('emoji')} <b>EMOJI MANAGER</b>\n"
        f"│\n"
        f"├ Change the emoji of <b>any button</b> or bot message icon.\n"
        f"├ <b>1.</b> Pick a category\n"
        f"├ <b>2.</b> Tap the item you want to change\n"
        f"├ <b>3.</b> Send a <b>custom emoji</b> (or its numeric ID)\n"
        f"│\n"
        f"└─ {ce('bulb')} <i>🟢 = custom emoji set · ⚪ = plain emoji / blank</i>"
    )

def emoji_main_markup() -> RawMarkup:
    return create_custom_inline_markup([
        [{"text": "Bottom Keyboard Buttons", "callback_data": "emcat_k_0",
          "icon_custom_emoji_id": UI_EMOJIS["panel"], "style": "primary"}],
        [{"text": "Inline Buttons & Message Icons", "callback_data": "emcat_u_0",
          "icon_custom_emoji_id": UI_EMOJIS["list"], "style": "primary"}],
        [{"text": "Reset All Emojis", "callback_data": "emresetall",
          "icon_custom_emoji_id": UI_EMOJIS["refresh"], "style": "danger"}],
        [{"text": "Back", "callback_data": "btn_refresh_panel",
          "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}],
    ])

def emoji_list_markup(scope: str, page: int) -> RawMarkup:
    names = list(KB_BUTTONS.keys()) if scope == "k" else list(DEFAULT_UI_EMOJIS.keys())
    total_pages = max(1, (len(names) + EM_PER_PAGE - 1) // EM_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = names[page * EM_PER_PAGE:(page + 1) * EM_PER_PAGE]
    rows, row = [], []
    for n in chunk:
        dot = "🟢" if emoji_current(scope, n) else "⚪"
        label = KB_BUTTONS[n]["text"] if scope == "k" else n
        row.append({"text": f"{dot} {emoji_fallback(scope, n)} {label}"[:30],
                    "callback_data": f"emsel_{scope}_{n}", "style": "primary"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append({"text": "Prev", "callback_data": f"emcat_{scope}_{page - 1}",
                    "icon_custom_emoji_id": UI_EMOJIS["prev"]})
    if page < total_pages - 1:
        nav.append({"text": "Next", "callback_data": f"emcat_{scope}_{page + 1}",
                    "icon_custom_emoji_id": UI_EMOJIS["next"]})
    if nav:
        rows.append(nav)
    rows.append([{"text": "Back", "callback_data": "btn_emoji_menu",
                  "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
    return create_custom_inline_markup(rows)

def emoji_entry_view(scope: str, name: str, waiting: bool = True):
    cur = emoji_current(scope, name)
    fb = emoji_fallback(scope, name)
    label = KB_BUTTONS[name]["text"] if scope == "k" else name
    where = "Bottom keyboard button" if scope == "k" else "Inline button / message icon"
    text = (
        f"┌── {ce('gear')} <b>EDIT EMOJI</b>\n"
        f"│\n"
        f"├ <b>Item:</b> <code>{html.escape(label)}</code> ({where})\n"
        f"├ <b>Current:</b> {show_emoji(cur, fb)}\n"
        f"├ <b>ID:</b> <code>{cur or 'blank'}</code>\n"
        f"│\n"
        + (f"└─ {ce('pencil')} <b>Now send a custom emoji</b> (or its numeric ID) to replace it."
           if waiting else f"└─ {ce('check')} <i>Tap another item from the list to continue.</i>")
    )
    rows = [[
        {"text": "Use Default", "callback_data": f"emdef_{scope}_{name}",
         "icon_custom_emoji_id": UI_EMOJIS["refresh"], "style": "primary"},
        {"text": "Make Blank", "callback_data": f"emclr_{scope}_{name}",
         "icon_custom_emoji_id": UI_EMOJIS["trash"], "style": "danger"},
    ]]
    if scope == "k":
        rows.append([{"text": "Apply to my keyboard", "callback_data": "btn_col_apply", "style": "success"}])
    rows.append([{"text": "Back to List", "callback_data": f"emcat_{scope}_0",
                  "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
    return text, create_custom_inline_markup(rows)

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

def cancel_markup(key: str) -> RawMarkup:
    return create_custom_inline_markup([[{
        "text": "Cancel",
        "callback_data": f"cancel_{key}",
        "icon_custom_emoji_id": UI_EMOJIS["cross"],
        "style": "danger"
    }]])

class ProgressTracker:
    def __init__(self, message: Message, action_text: str, task_id: str = None,
                 task_type: str = "download", cancel_key: str = None):
        self.message = message
        self.action_text = action_text
        self.task_id = task_id
        self.task_type = task_type
        self.cancel_key = cancel_key
        self.markup = cancel_markup(cancel_key) if cancel_key else None
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
        if self.cancel_key and self.cancel_key in cancel_requests:
            raise StopTransmission

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
            f"┌─ {ce('spark')} <b>{self.action_text}</b>\n"
            f"├ ⌠ {bar} ⌡ <code>{percent:.1f}%</code>\n"
            f"└ {icon} <code>{human_bytes(current)}</code> / <code>{human_bytes(total)}</code>"
        )
        try:
            await self.message.edit_text(text, reply_markup=self.markup)
        except Exception:
            pass

    def finish(self):
        if self.task_id:
            active_tasks.pop(self.task_id, None)

class AlbumProgressTracker:
    def __init__(self, message: Message, total_files: int, action_text: str = "Downloading album",
                 task_id: str = None, cancel_key: str = None):
        self.message = message
        self.total_files = total_files
        self.action_text = action_text
        self.task_id = task_id
        self.cancel_key = cancel_key
        self.markup = cancel_markup(cancel_key) if cancel_key else None
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

    def is_cancelled(self) -> bool:
        return bool(self.cancel_key and self.cancel_key in cancel_requests)

    async def progress(self, current: int, total: int, idx: int):
        if self.is_cancelled():
            raise StopTransmission

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
                f"┌─ {ce('spark')} <b>{self.action_text} ({completed_files}/{self.total_files})</b>\n"
                f"├ ⌠ {bar} ⌡ <code>{percent:.1f}%</code>\n"
                f"└ {ce('download')} <code>{human_bytes(total_downloaded)}</code> / <code>{human_bytes(total_size)}</code>"
            )
            try:
                await self.message.edit_text(text, reply_markup=self.markup)
            except Exception:
                pass

    def finish(self):
        if self.task_id:
            active_tasks.pop(self.task_id, None)

# ----------------- DATABASE HELPERS ----------------- #

def today_str() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())

def has_unlimited(user_id: int) -> bool:
    return ADMINS_UNLIMITED and is_any_admin(user_id)

def sync_save_user(user_id: int):
    u = user_cache.get(user_id)
    if not u or not db:
        return
    try:
        db.collection("registered_users").document(str(user_id)).set({"user_id": user_id, **u}, merge=True)
    except Exception as e:
        logger.error(f"Save user error: {e}")

def sync_get_user(user_id: int) -> dict:
    """ইউজারের ডাটা (credits, banned...) ক্যাশ/ডাটাবেস থেকে আনে; দিন বদলালে ক্রেডিট রিসেট করে"""
    u = user_cache.get(user_id)
    needs_save = False
    if u is None:
        u = {"credits": DAILY_CREDITS, "last_reset": today_str(), "banned": False,
             "joined": time.time(), "downloads": 0}
        found = False
        if db:
            try:
                d = db.collection("registered_users").document(str(user_id)).get()
                if d.exists:
                    found = True
                    data = d.to_dict()
                    for k in ("credits", "last_reset", "banned", "joined", "downloads",
                              "referred_by", "referrals", "fresh",
                              "name", "username", "last_active",
                              "since_ad", "ads_seen", "ad_pass"):
                        if k in data:
                            u[k] = data[k]
            except Exception:
                pass
        if not found:
            u["fresh"] = True  # একদম নতুন ইউজার (রেফার রিওয়ার্ডের জন্য)
        user_cache[user_id] = u
        needs_save = not found
    if u["last_reset"] != today_str():
        u["credits"] = max(u["credits"], DAILY_CREDITS)  # Admin এর দেওয়া বাড়তি ক্রেডিট নষ্ট হবে না
        u["last_reset"] = today_str()
        needs_save = True
    if needs_save:
        sync_save_user(user_id)
    return u

def sync_charge(user_id: int):
    """সফল ডাউনলোডের পর ১ ক্রেডিট কাটে"""
    u = sync_get_user(user_id)
    u["downloads"] = u.get("downloads", 0) + 1
    u["since_ad"] = u.get("since_ad", 0) + 1
    if not has_unlimited(user_id):
        u["credits"] = max(0, u["credits"] - 1)
    sync_save_user(user_id)

def sync_register_user(user_id: int, name: str = "", username: str = ""):
    """ইউজার রেজিস্টার + নাম/ইউজারনেম/last_active সেভ (সার্চের জন্য)"""
    mem_bot_users.add(user_id)
    u = sync_get_user(user_id)
    if name:
        u["name"] = name
    u["username"] = (username or "").lower()
    u["last_active"] = time.time()
    sync_save_user(user_id)

def sync_search_users(q: str, limit: int = 50) -> list:
    """ID / নাম / @username দিয়ে সার্চ। খালি q = সবাই (সর্বশেষ একটিভ আগে)"""
    q = (q or "").strip().lower().lstrip("@")
    rows = {}
    if db:
        try:
            for d in db.collection("registered_users").stream():
                if d.id.isdigit():
                    x = d.to_dict()
                    x["user_id"] = int(d.id)
                    rows[int(d.id)] = x
        except Exception as e:
            logger.error(f"Search users error: {e}")
    for uid, x in user_cache.items():
        rows[uid] = {**rows.get(uid, {}), **x, "user_id": uid}
    out = []
    for uid, x in rows.items():
        hay = f"{uid} {x.get('name', '')} {x.get('username', '')}".lower()
        if not q or q in hay:
            out.append(x)
    out.sort(key=lambda x: x.get("last_active", x.get("joined", 0)), reverse=True)
    return out[:limit]

# ----------------- REFERRAL SYSTEM ----------------- #

def sync_reward_referrer(ref_id: int, reward: int):
    """রেফারারকে ক্রেডিট দেয়। রেফারার না থাকলে বা ব্যান থাকলে None রিটার্ন করে"""
    exists = ref_id in user_cache
    if not exists and db:
        try:
            exists = db.collection("registered_users").document(str(ref_id)).get().exists
        except Exception:
            pass
    if not exists:
        return None
    r = sync_get_user(ref_id)
    if r.get("banned"):
        return None
    r["credits"] += reward
    r["referrals"] = r.get("referrals", 0) + 1
    sync_save_user(ref_id)
    return r

async def process_referral(message: Message, user_id: int):
    """/start ref_<id> পেলে, শুধু একদম নতুন ইউজারের ক্ষেত্রে রেফারারকে ক্রেডিট দেয়"""
    u = await asyncio.to_thread(sync_get_user, user_id)
    if not u.get("fresh"):
        return
    u["fresh"] = False
    payload = message.command[1] if len(message.command) > 1 else ""
    ref_id = int(payload[4:]) if payload.startswith("ref_") and payload[4:].isdigit() else 0
    reward = SETTINGS["ref_reward"]
    if ref_id and ref_id != user_id and reward > 0 and not is_any_admin(user_id):
        r = await asyncio.to_thread(sync_reward_referrer, ref_id, reward)
        if r:
            u["referred_by"] = ref_id
            try:
                await bot.send_message(
                    ref_id,
                    f"{ce('gift')} <b>New referral!</b> +<code>{reward}</code> credits. "
                    f"Now: <code>{r['credits']}</code>"
                )
            except Exception:
                pass
    await asyncio.to_thread(sync_save_user, user_id)

def sync_top_referrers(limit: int = 10) -> list:
    """সবচেয়ে বেশি রেফার করা ইউজারদের লিস্ট"""
    allu = sync_search_users("", 5000)
    top = [x for x in allu if x.get("referrals", 0) > 0]
    top.sort(key=lambda x: x.get("referrals", 0), reverse=True)
    return top[:limit]

# ----------------- PROMO / REDEEM CODES ----------------- #

def _load_promo(code: str):
    p = PROMOS.get(code)
    if p is None and db:
        try:
            d = db.collection("promo_codes").document(code).get()
            if d.exists:
                p = d.to_dict()
                PROMOS[code] = p
        except Exception as e:
            logger.error(f"Load promo error: {e}")
    return p

def _save_promo(code: str):
    if not db or code not in PROMOS:
        return
    try:
        db.collection("promo_codes").document(code).set(PROMOS[code])
    except Exception as e:
        logger.error(f"Save promo error: {e}")

def sync_create_promo(code: str, credits: int, max_uses: int):
    PROMOS[code] = {"credits": credits, "max_uses": max_uses, "used_by": [], "created_at": time.time()}
    _save_promo(code)

def sync_delete_promo(code: str) -> bool:
    existed = code in PROMOS
    PROMOS.pop(code, None)
    if db:
        try:
            ref = db.collection("promo_codes").document(code)
            if ref.get().exists:
                existed = True
                ref.delete()
        except Exception as e:
            logger.error(f"Delete promo error: {e}")
    return existed

def sync_list_promos() -> dict:
    out = dict(PROMOS)
    if db:
        try:
            for d in db.collection("promo_codes").stream():
                out.setdefault(d.id, d.to_dict())
        except Exception as e:
            logger.error(f"List promos error: {e}")
    return out

def sync_redeem(user_id: int, code: str):
    """রিটার্ন: (True, credits) অথবা (False, 'invalid' | 'used' | 'full')"""
    code = (code or "").strip().upper()
    p = _load_promo(code)
    if not p:
        return False, "invalid"
    used = p.setdefault("used_by", [])
    if user_id in used:
        return False, "used"
    if p.get("max_uses", 0) and len(used) >= p["max_uses"]:
        return False, "full"
    used.append(user_id)
    amount = int(p.get("credits", 0))
    u = sync_get_user(user_id)
    u["credits"] += amount
    sync_save_user(user_id)
    _save_promo(code)
    return True, amount

def sync_get_stats() -> dict:
    users = []
    if db:
        try:
            users = [d.to_dict() for d in db.collection("registered_users").stream()]
        except Exception:
            users = []
    if not users:
        users = list(user_cache.values())
    now = time.time()
    return {
        "total": len(users),
        "banned": sum(1 for u in users if u.get("banned")),
        "active24": sum(1 for u in users if now - u.get("last_active", 0) < 86400),
        "new24": sum(1 for u in users if now - u.get("joined", 0) < 86400),
        "downloads": sum(u.get("downloads", 0) for u in users),
    }

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
                    "broadcast": data.get("broadcast", True),
                    "users": data.get("users", False)
                }
    except Exception as e:
        logger.error(f"Get Admins Error: {e}")
    return admins

def sync_add_admin(user_id: int):
    default_perms = {"sessions": False, "broadcast": True, "users": False}
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

def sync_get_user_session(user_id: int):
    """এই ইউজার আগে কোনো একাউন্ট কানেক্ট করেছে কিনা চেক"""
    for s in sync_get_all_sessions():
        if s.get("added_by") == user_id:
            return s
    return None

def parse_tme_link(text: str):
    """t.me লিংক বা 'chat_id:msg_id' / 'username:msg_id' ফরম্যাট পার্স করে (chat_id, msg_id) রিটার্ন করে"""
    text = (text or "").strip()
    p_match = re.search(r"t\.me/c/(\d+)/(\d+)", text)
    if p_match:
        return int("-100" + p_match.group(1)), int(p_match.group(2))
    pub_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text)
    if pub_match:
        return pub_match.group(1), int(pub_match.group(2))
    if ":" in text:
        a, b = text.rsplit(":", 1)
        a = a.strip()
        if b.strip().isdigit() and a:
            if a.lstrip("-").isdigit():
                return int(a), int(b.strip())
            return a.lstrip("@"), int(b.strip())
    return None

def get_client_by_doc(doc_id: str):
    """doc_id দিয়ে userbot pool থেকে client খুঁজে বের করে"""
    for d, name, cl in loaded_user_clients:
        if d == doc_id:
            return cl, name
    return None, None

def session_pick_markup(action_prefix: str) -> RawMarkup:
    """সেশন পুল থেকে কোনটা দিয়ে কাজ করবে বেছে নেওয়ার মার্কআপ"""
    rows = [[{"text": "🔀 Auto (try all sessions)", "callback_data": f"{action_prefix}_auto", "style": "primary"}]]
    for doc_id, name, _ in loaded_user_clients:
        rows.append([{"text": name, "callback_data": f"{action_prefix}_{doc_id}", "style": "success"}])
    rows.append([{"text": "Back", "callback_data": "btn_refresh_panel",
                  "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
    return create_custom_inline_markup(rows)

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

# ----------------- DIRECT USER BROADCAST ENGINE ----------------- #

async def broadcast_to_all_users(source_message: Message, admin_id: int):
    all_users = await asyncio.to_thread(sync_get_all_bot_users)
    if not all_users:
        return await bot.send_message(
            admin_id,
            f"{ce('cross')} <b>No registered users found.</b>",
            reply_markup=get_bottom_keyboard(admin_id)
        )

    status_msg = await bot.send_message(
        admin_id,
        f"{ce('broadcast')} <b>Broadcasting started...</b>\n"
        f"{ce('target')} <b>Target Audience:</b> <code>{len(all_users)}</code> users\n"
        f"{ce('hourglass')} <b>Dispatching payload...</b>"
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
                    f"┌── {ce('broadcast')} <b>BROADCAST DISPATCHER</b>\n"
                    f"├ {ce('chart')} <b>Progress:</b> <code>{idx}/{len(all_users)}</code>\n"
                    f"├ {ce('check')} <b>Delivered:</b> <code>{success}</code>\n"
                    f"├ {ce('offline')} <b>Blocked:</b> <code>{blocked}</code>\n"
                    f"└ {ce('cross')} <b>Failed:</b> <code>{failed}</code>"
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"┌── {ce('check')} <b>BROADCAST COMPLETED</b>\n"
        f"├ {ce('target')} <b>Total Users:</b> <code>{len(all_users)}</code>\n"
        f"├ {ce('online')} <b>Delivered:</b> <code>{success}</code>\n"
        f"├ {ce('offline')} <b>Blocked:</b> <code>{blocked}</code>\n"
        f"└ {ce('cross')} <b>Failed:</b> <code>{failed}</code>"
    )

# ----------------- 🔔 DOWNLOAD ALERTS (ADMIN NOTIFICATION) ----------------- #

def describe_msg(m):
    """মেসেজ থেকে (টাইপ, সাইজ) বের করে"""
    if isinstance(m, (list, tuple)):
        m = m[0] if m else None
    if not m:
        return "Unknown", 0
    for attr, label in (("video", "Video"), ("photo", "Photo"), ("audio", "Audio"),
                        ("voice", "Voice"), ("animation", "GIF"), ("document", "Document"),
                        ("sticker", "Sticker")):
        o = getattr(m, attr, None)
        if o:
            return label, (getattr(o, "file_size", 0) or 0)
    if getattr(m, "text", None):
        return "Text", 0
    return "Other", 0

def build_notify_text(name: str, username: str, uid: int, credits, link: str, kind: str, size: int) -> str:
    cfg = SETTINGS["notify"]
    show = cfg["show"]
    title = html.escape((cfg.get("title") or "New Download")[:60])
    lines = []
    if show.get("user"):
        lines.append(f"{ce('user')} <b>User:</b> {html.escape(name or 'User')}")
    if show.get("username"):
        lines.append(f"{ce('pencil')} <b>Username:</b> {'@' + html.escape(username) if username else '—'}")
    if show.get("id"):
        lines.append(f"{ce('lock')} <b>ID:</b> <code>{uid}</code>")
    if show.get("type"):
        lines.append(f"{ce('media')} <b>Type:</b> <code>{html.escape(kind)}</code>")
    if show.get("size") and size:
        lines.append(f"{ce('box')} <b>Size:</b> <code>{human_bytes(size)}</code>")
    if show.get("credits"):
        lines.append(f"{ce('spark')} <b>Credits Left:</b> <code>{credits}</code>")
    if show.get("link"):
        lines.append(f"{ce('download')} <b>Link:</b> <code>{html.escape(link)}</code>")
    if show.get("time"):
        lines.append(f"{ce('timer')} <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC</code>")
    out = f"┌── {ce('bell')} <b>{title}</b>"
    if lines:
        out += "\n│\n" + "\n".join(("├ " if i < len(lines) - 1 else "└ ") + ln for i, ln in enumerate(lines))
    return out

def notify_markup(uid: int, link: str) -> RawMarkup:
    rows = []
    if SETTINGS["notify"]["show"].get("link") and link.startswith("https://"):
        rows.append([{"text": "Open Post", "url": link,
                      "icon_custom_emoji_id": UI_EMOJIS["download"], "style": "primary"}])
    rows.append([{"text": "Manage User", "callback_data": f"uv_{uid}",
                  "icon_custom_emoji_id": UI_EMOJIS["user"], "style": "success"}])
    return create_custom_inline_markup(rows)

async def notify_admins(uid: int, name: str, username: str, link: str, kind: str, size: int):
    """সফল ডাউনলোডের পর অ্যাডমিনদের (Manage Users পারমিশন আছে এমন) নোটিফিকেশন পাঠায়"""
    if not SETTINGS["notify"].get("on"):
        return
    try:
        u = await asyncio.to_thread(sync_get_user, uid)
        credits = "∞" if has_unlimited(uid) else u.get("credits", 0)
        text = build_notify_text(name, username, uid, credits, link, kind, size)
        markup = notify_markup(uid, link)
        recipients = {OWNER_ID} | {a for a in list(loaded_admins) if has_perm(a, "users")}
        for r in recipients:
            if r == uid:
                continue
            try:
                await bot.send_message(r, text, reply_markup=markup)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"notify_admins error: {e}")

def notify_menu_text() -> str:
    cfg = SETTINGS["notify"]
    state = f"{ce('online')} ON" if cfg.get("on") else f"{ce('offline')} OFF"
    return (
        f"┌── {ce('bell')} <b>DOWNLOAD ALERTS</b>\n"
        f"│\n"
        f"├ <b>Status:</b> {state}\n"
        f"├ <b>Title:</b> <code>{html.escape(cfg.get('title') or 'New Download')}</code>\n"
        f"│\n"
        f"└─ {ce('bulb')} <i>Choose which details appear in the alert. 🟢 = shown · ⚪ = hidden</i>"
    )

def notify_menu_markup() -> RawMarkup:
    cfg = SETTINGS["notify"]
    on = bool(cfg.get("on"))
    rows = [[{"text": "Alerts: ON" if on else "Alerts: OFF", "callback_data": "ntf_toggle",
              "icon_custom_emoji_id": UI_EMOJIS["bell"], "style": "success" if on else "danger"}]]
    row = []
    for key, label in NOTIFY_FIELDS:
        dot = "🟢" if cfg["show"].get(key) else "⚪"
        row.append({"text": f"{dot} {label}", "callback_data": f"ntf_f_{key}", "style": "primary"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "Edit Title", "callback_data": "ntf_title",
         "icon_custom_emoji_id": UI_EMOJIS["pencil"], "style": "primary"},
        {"text": "Send Test", "callback_data": "ntf_test",
         "icon_custom_emoji_id": UI_EMOJIS["rocket"], "style": "success"},
    ])
    rows.append([{"text": "Back", "callback_data": "btn_refresh_panel",
                  "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
    return create_custom_inline_markup(rows)

def ads_menu_text() -> str:
    a = SETTINGS["ads"]
    state = f"{ce('online')} ON" if a["on"] else f"{ce('offline')} OFF"
    blk = html.escape(str(a["block_id"])) if a["block_id"] else "—"
    ready = "" if DASH_URL else f"\n├ {ce('warning')} <b>DASH_URL env is not set!</b>"
    return (
        f"┌── {ce('rocket')} <b>ADS CONTROL</b>\n"
        f"│\n"
        f"├ <b>Status:</b> {state}\n"
        f"├ <b>Provider:</b> <code>{a['provider']}</code>\n"
        f"├ <b>Block ID:</b> <code>{blk}</code>\n"
        f"├ <b>Show every:</b> <code>{a['every_n']}</code> downloads\n"
        f"├ <b>First download ad:</b> {'🟢' if a['first_time'] else '⚪'}\n"
        f"├ <b>Skip admins:</b> {'🟢' if a['skip_admins'] else '⚪'}\n"
        f"├ <b>No-ad fallback:</b> {'🟢' if a['fail_open'] else '⚪'}\n"
        f"├ <b>Title:</b> {html.escape(a['title'])}"
        f"{ready}\n"
        f"│\n"
        f"└─ {ce('bulb')} <i>Use provider <b>test</b> for a demo ad, <b>adsgram</b> for real ads.</i>"
    )

def ads_menu_markup() -> RawMarkup:
    a = SETTINGS["ads"]
    on = bool(a["on"])
    def tog(label, key):
        return {"text": f"{'🟢' if a[key] else '⚪'} {label}", "callback_data": f"ad_t_{key}", "style": "primary"}
    rows = [
        [{"text": "Ads: ON" if on else "Ads: OFF", "callback_data": "ad_t_on",
          "icon_custom_emoji_id": UI_EMOJIS["rocket"], "style": "success" if on else "danger"}],
        [{"text": f"Provider: {a['provider']}", "callback_data": "ad_prov", "style": "primary"},
         {"text": "Block ID", "callback_data": "ad_block", "style": "primary"}],
        [{"text": f"Every {a['every_n']} downloads", "callback_data": "ad_every", "style": "primary"},
         {"text": "Edit Message", "callback_data": "ad_text",
          "icon_custom_emoji_id": UI_EMOJIS["pencil"], "style": "primary"}],
        [tog("First download", "first_time"), tog("Skip admins", "skip_admins")],
        [tog("No-ad fallback", "fail_open")],
        [{"text": "Preview Ad", "callback_data": "ad_preview",
          "icon_custom_emoji_id": UI_EMOJIS["check"], "style": "success"}],
        [{"text": "Back", "callback_data": "btn_refresh_panel",
          "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}],
    ]
    return create_custom_inline_markup(rows)

# ----------------- MAINTENANCE + FORCE SUB + BAN GATE ----------------- #

def maintenance_text() -> str:
    custom = (SETTINGS.get("maint_msg") or "").strip()
    body = html.escape(custom) if custom else "We are upgrading the bot right now. Please try again in a little while."
    return (
        f"┌── {ce('tool')} <b>UNDER MAINTENANCE</b>\n"
        f"│\n"
        f"└─ {body}"
    )

async def check_subscribed(user_id: int) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    try:
        m = await bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return m.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.error(f"ForceSub check error (বট চ্যানেলে Admin আছে কিনা দেখুন): {e}")
        return True  # এরর হলে ইউজারকে আটকাবে না

def force_sub_markup() -> RawMarkup:
    rows = []
    if FORCE_SUB_LINK:
        rows.append([{"text": "Join Channel", "url": FORCE_SUB_LINK,
                      "icon_custom_emoji_id": UI_EMOJIS["broadcast"], "style": "primary"}])
    rows.append([{"text": "I've Joined", "callback_data": "btn_check_sub",
                  "icon_custom_emoji_id": UI_EMOJIS["check"], "style": "success"}])
    return create_custom_inline_markup(rows)

@bot.on_message(filters.private & filters.incoming, group=-1)
async def global_gate(_, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if is_any_admin(uid):
        return
    u = await asyncio.to_thread(sync_get_user, uid)
    if u["banned"]:
        await message.reply_text(f"{ce('cross')} <b>You are banned from using this bot.</b>")
        message.stop_propagation()
    if SETTINGS.get("maintenance"):
        await message.reply_text(maintenance_text())
        message.stop_propagation()
    if not await check_subscribed(uid):
        await message.reply_text(
            f"┌── {ce('lock')} <b>JOIN REQUIRED</b>\n"
            f"│\n"
            f"└─ Please join our channel first, then tap <b>I've Joined</b>.",
            reply_markup=force_sub_markup()
        )
        message.stop_propagation()

@bot.on_callback_query(group=-1)
async def callback_gate(_, query: CallbackQuery):
    uid = query.from_user.id
    if is_any_admin(uid) or query.data == "btn_check_sub":
        return
    u = await asyncio.to_thread(sync_get_user, uid)
    if u["banned"]:
        await query.answer("You are banned.", show_alert=True)
        query.stop_propagation()
    if SETTINGS.get("maintenance"):
        await query.answer("Bot is under maintenance. Please try again later.", show_alert=True)
        query.stop_propagation()
    if not await check_subscribed(uid):
        await query.answer("Join the channel first!", show_alert=True)
        query.stop_propagation()

# ----------------- 📖 USER GUIDE (HOW TO USE) ----------------- #

HELP_TOTAL = 4

def help_page(n: int):
    n = max(1, min(HELP_TOTAL, n))
    pages = {
        1: (
            f"┌── {ce('book')} <b>HOW TO USE · 1/{HELP_TOTAL}</b>\n"
            f"│\n"
            f"├ {ce('download')} <b>Download any Telegram post</b>\n"
            f"│\n"
            f"├ <b>Step 1:</b> Open a channel post you want.\n"
            f"├ <b>Step 2:</b> Tap <b>Share → Copy Link</b>.\n"
            f"├ <b>Step 3:</b> Paste the link here and send.\n"
            f"│\n"
            f"└─ {ce('spark')} <i>Example:</i> <code>https://t.me/channel/123</code>"
        ),
        2: (
            f"┌── {ce('book')} <b>HOW TO USE · 2/{HELP_TOTAL}</b>\n"
            f"│\n"
            f"├ {ce('lock')} <b>Private / restricted channels</b>\n"
            f"│\n"
            f"├ <b>Step 1:</b> Tap <b>Connect Account</b> below.\n"
            f"├ <b>Step 2:</b> Send your phone number (with country code).\n"
            f"├ <b>Step 3:</b> Enter the OTP (and 2FA password if you have one).\n"
            f"├ <b>Step 4:</b> Now send the private post link as usual.\n"
            f"│\n"
            f"└─ {ce('bulb')} <i>Your account must be a member of that channel.</i>"
        ),
        3: (
            f"┌── {ce('book')} <b>HOW TO USE · 3/{HELP_TOTAL}</b>\n"
            f"│\n"
            f"├ {ce('spark')} <b>Credits</b>\n"
            f"│\n"
            f"├ Every successful download costs <b>1 credit</b>.\n"
            f"├ You get <code>{DAILY_CREDITS}</code> free credits every day (reset 00:00 UTC).\n"
            f"├ Check your balance anytime in <b>Profile</b>.\n"
            f"├ Got a promo code? Tap <b>Redeem Code</b> for bonus credits.\n"
            f"│\n"
            f"└─ {ce('timer')} <i>Delivered files auto-delete after {AUTO_DELETE_TIME}s — save them quickly!</i>"
        ),
        4: (
            f"┌── {ce('book')} <b>HOW TO USE · 4/{HELP_TOTAL}</b>\n"
            f"│\n"
            f"├ {ce('gift')} <b>Earn free credits</b>\n"
            f"│\n"
            f"├ Open <b>Profile → Refer & Earn</b> and share your link.\n"
            f"├ You get <code>{SETTINGS['ref_reward']}</code> credits for every new user who joins.\n"
            f"├ Top referrers appear on the leaderboard.\n"
            f"│\n"
            f"├ <b>Commands:</b> /start · /help · /profile · /login · /redeem · /ping\n"
            f"└─ {ce('check')} <i>That's all — send a link and enjoy!</i>"
        ),
    }
    nav = []
    if n > 1:
        nav.append({"text": "Prev", "callback_data": f"help_{n - 1}",
                    "icon_custom_emoji_id": UI_EMOJIS["prev"], "style": "primary"})
    if n < HELP_TOTAL:
        nav.append({"text": "Next", "callback_data": f"help_{n + 1}",
                    "icon_custom_emoji_id": UI_EMOJIS["next"], "style": "primary"})
    rows = [nav] if nav else []
    if n == 2:
        rows.append([{"text": "Connect Account", "callback_data": "btn_connect_start",
                      "icon_custom_emoji_id": UI_EMOJIS["phone"], "style": "success"}])
    if n == 4:
        rows.append([{"text": "Refer & Earn", "callback_data": "btn_refer",
                      "icon_custom_emoji_id": UI_EMOJIS["gift"], "style": "success"}])
    rows.append([{"text": "Close", "callback_data": "help_close",
                  "icon_custom_emoji_id": UI_EMOJIS["cross"], "style": "danger"}])
    return pages[n], create_custom_inline_markup(rows)

def help_hint_markup() -> RawMarkup:
    return create_custom_inline_markup([[{
        "text": "How To Use", "callback_data": "help_1",
        "icon_custom_emoji_id": UI_EMOJIS["info"], "style": "primary"
    }]])

@bot.on_message(filters.command("help") & filters.private & filters.incoming)
async def help_handler(_, message: Message):
    text, markup = help_page(1)
    await message.reply_text(text, reply_markup=markup)

# ----------------- 🎟️ REDEEM ----------------- #

async def ask_redeem(message: Message):
    user_states[message.from_user.id] = "WAIT_REDEEM_CODE"
    await message.reply_text(
        f"{ce('ticket')} <b>Send your promo code:</b>",
        reply_markup=ForceReply(selective=True)
    )

async def do_redeem(message: Message, code: str):
    uid = message.from_user.id
    ok, res = await asyncio.to_thread(sync_redeem, uid, code)
    if ok:
        u = await asyncio.to_thread(sync_get_user, uid)
        return await message.reply_text(
            f"┌── {ce('check')} <b>CODE REDEEMED!</b>\n"
            f"│\n"
            f"├ {ce('spark')} <b>Added:</b> <code>+{res}</code> credits\n"
            f"└─ {ce('user')} <b>Balance:</b> <code>{u['credits']}</code>",
            reply_markup=get_bottom_keyboard(uid)
        )
    msg = {
        "invalid": "Invalid code. Please check and try again.",
        "used": "You have already redeemed this code.",
        "full": "This code has been fully claimed.",
    }.get(res, "Could not redeem the code.")
    await message.reply_text(f"{ce('cross')} <b>{msg}</b>", reply_markup=get_bottom_keyboard(uid))

@bot.on_message(filters.command("redeem") & filters.private & filters.incoming)
async def redeem_cmd(_, message: Message):
    await asyncio.to_thread(sync_register_user, message.from_user.id,
                            message.from_user.first_name, message.from_user.username)
    if message.command and len(message.command) >= 2:
        return await do_redeem(message, message.command[1])
    await ask_redeem(message)

# ----------------- 📺 ADS GATE ----------------- #

def verify_init_data(init_data: str):
    """Telegram Mini App initData যাচাই করে user id রিটার্ন করে (ফেক হলে None)"""
    try:
        d = dict(parse_qsl(init_data or "", keep_blank_values=True))
        h = d.pop("hash", "")
        if not h:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, h):
            return None
        if time.time() - int(d.get("auth_date", 0)) > 86400:
            return None
        return int(json.loads(d["user"])["id"])
    except Exception:
        return None

def ad_button_markup() -> RawMarkup:
    return create_custom_inline_markup([[{
        "text": "Watch Ad", "style": "success",
        "icon_custom_emoji_id": UI_EMOJIS["rocket"],
        "web_app": f"{DASH_URL}/ad"
    }]])

async def ad_gate(message: Message, user_id: int) -> bool:
    """True = ad দেখাতে হবে (ডাউনলোড আপাতত থামবে). False = এগিয়ে যাও"""
    a = SETTINGS["ads"]
    if not a.get("on") or not DASH_URL:
        return False
    if a.get("skip_admins") and is_any_admin(user_id):
        return False
    u = await asyncio.to_thread(sync_get_user, user_id)

    # ad দেখা শেষ (১০ মিনিটের মধ্যে) → একবার ছেড়ে দাও
    if time.time() - u.get("ad_pass", 0) < 600:
        u["ad_pass"] = 0
        await asyncio.to_thread(sync_save_user, user_id)
        return False

    first = a.get("first_time") and not u.get("ads_seen") and u.get("downloads", 0) == 0
    due = u.get("since_ad", 0) >= int(a.get("every_n", 8))
    if not (first or due):
        return False

    pending_ads[user_id] = message
    await message.reply_text(
        f"┌── {ce('spark')} <b>{html.escape(a.get('title') or 'Watch a short ad')}</b>\n"
        f"│\n"
        f"└─ {html.escape(a.get('text') or '')}",
        reply_markup=ad_button_markup()
    )
    return True

async def resume_pending(uid: int):
    """Ad শেষ হলে আগের লিংকের ডাউনলোড নিজে থেকেই চালু করে"""
    m = pending_ads.pop(uid, None)
    try:
        if not m:
            return await bot.send_message(uid, f"{ce('check')} <b>Ad completed!</b> Now send your link again.")
        await bot.send_message(uid, f"{ce('check')} <b>Ad completed!</b> Starting your download...")
        await message_input_router(bot, m)
    except Exception as e:
        logger.error(f"resume_pending error: {e}")

# ----------------- USER PROFILE ----------------- #

def build_profile(user_id: int, first_name: str):
    u = sync_get_user(user_id)
    sess = sync_get_user_session(user_id)
    acc = html.escape(str(sess.get("account_name", "Unknown"))) if sess else "Not connected"
    role = (f"{ce('crown')} Owner" if user_id == OWNER_ID
            else f"{ce('shield')} Admin" if is_any_admin(user_id) else f"{ce('user')} User")
    credits = "∞ Unlimited" if has_unlimited(user_id) else f"{u['credits']} / {DAILY_CREDITS}"

    now = time.gmtime()
    left = 86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)

    # চলমান ট্রান্সফার (Cancel বাটনের জন্য)
    bases = []
    for k in list(active_tasks.keys()):
        if f"task_{user_id}_" in k:
            base = k[k.index("task_"):]
            if base not in bases:
                bases.append(base)

    text = (
        f"┌── {ce('user')} <b>MY PROFILE</b>\n"
        f"│\n"
        f"├ <b>Name:</b> {html.escape(first_name or 'User')}\n"
        f"├ <b>ID:</b> <code>{user_id}</code>\n"
        f"├ <b>Role:</b> {role}\n"
        f"├ {ce('spark')} <b>Credits Today:</b> <code>{credits}</code>\n"
        f"├ {ce('timer')} <b>Reset In:</b> <code>{left // 3600}h {(left % 3600) // 60}m</code>\n"
        f"├ {ce('download')} <b>Total Downloads:</b> <code>{u.get('downloads', 0)}</code>\n"
        f"├ {ce('gift')} <b>Referrals:</b> <code>{u.get('referrals', 0)}</code>\n"
        f"├ {ce('phone')} <b>Account:</b> <code>{acc}</code>\n"
        f"└ {ce('refresh')} <b>Active Transfers:</b> <code>{len(bases)}</code>"
    )
    rows = [[{"text": f"Cancel Download #{i}", "callback_data": f"cancel_{b}",
              "icon_custom_emoji_id": UI_EMOJIS["cross"], "style": "danger"}]
            for i, b in enumerate(bases, 1)]
    rows.append([{"text": "Refer & Earn", "callback_data": "btn_refer",
                  "icon_custom_emoji_id": UI_EMOJIS["gift"], "style": "primary"}])
    rows.append([{"text": "How To Use", "callback_data": "help_1",
                  "icon_custom_emoji_id": UI_EMOJIS["info"], "style": "primary"}])
    rows.append([{"text": "Refresh", "callback_data": "btn_profile_refresh",
                  "icon_custom_emoji_id": UI_EMOJIS["refresh"], "style": "success"}])
    return text, create_custom_inline_markup(rows)

@bot.on_message(filters.command("profile") & filters.private & filters.incoming)
async def profile_handler(_, message: Message):
    uid = message.from_user.id
    text, markup = await asyncio.to_thread(build_profile, uid, message.from_user.first_name)
    await message.reply_text(text, reply_markup=markup)

# ----------------- USER MANAGER (Ban / Credits) ----------------- #

def user_card_text(uid: int, u: dict) -> str:
    status = f"{ce('offline')} Banned" if u["banned"] else f"{ce('online')} Active"
    uname = f"@{html.escape(u['username'])}" if u.get("username") else "—"
    return (
        f"┌── {ce('user')} <b>USER MANAGER</b>\n"
        f"│\n"
        f"├ <b>Name:</b> {html.escape(u.get('name') or '—')}\n"
        f"├ <b>Username:</b> {uname}\n"
        f"├ <b>ID:</b> <code>{uid}</code>\n"
        f"├ <b>Status:</b> {status}\n"
        f"├ {ce('spark')} <b>Credits:</b> <code>{u['credits']}</code>\n"
        f"├ {ce('gift')} <b>Referrals:</b> <code>{u.get('referrals', 0)}</code>\n"
        f"└ {ce('download')} <b>Downloads:</b> <code>{u.get('downloads', 0)}</code>"
    )

def user_card_markup(uid: int, u: dict) -> RawMarkup:
    if u["banned"]:
        ban_btn = {"text": "Unban User", "callback_data": f"ub_unban_{uid}",
                   "icon_custom_emoji_id": UI_EMOJIS["check"], "style": "success"}
    else:
        ban_btn = {"text": "Ban User", "callback_data": f"ub_ban_{uid}",
                   "icon_custom_emoji_id": UI_EMOJIS["cross"], "style": "danger"}
    return create_custom_inline_markup([
        [ban_btn],
        [{"text": "Give / Remove Credits", "callback_data": f"uc_add_{uid}",
          "icon_custom_emoji_id": UI_EMOJIS["spark"], "style": "primary"}],
        [{"text": "All Users", "callback_data": "btn_users_0",
          "icon_custom_emoji_id": UI_EMOJIS["users"], "style": "primary"}],
        [{"text": "Back", "callback_data": "btn_refresh_panel",
          "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]
    ])

USERS_PER_PAGE = 8

def user_label(x: dict) -> str:
    dot = "🔴" if x.get("banned") else "🟢"
    nm = (x.get("name") or str(x.get("user_id")))[:16]
    un = f" @{x['username']}" if x.get("username") else ""
    return f"{dot} {nm}{un}"

def users_list_markup(rows: list, page: int = None, has_next: bool = False) -> RawMarkup:
    kb = [[{"text": user_label(x), "callback_data": f"uv_{x['user_id']}", "style": "primary"}] for x in rows]
    if page is not None:
        nav = []
        if page > 0:
            nav.append({"text": "Prev", "callback_data": f"btn_users_{page - 1}"})
        if has_next:
            nav.append({"text": "Next", "callback_data": f"btn_users_{page + 1}"})
        if nav:
            kb.append(nav)
    kb.append([{"text": "Back", "callback_data": "btn_refresh_panel",
                "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
    return create_custom_inline_markup(kb)

@bot.on_message(filters.command(["ban", "unban"]) & filters.private & filters.incoming)
async def ban_cmd(_, message: Message):
    uid = message.from_user.id
    if not has_perm(uid, "users"):
        return await message.reply_text(f"{ce('cross')} <b>Permission Denied.</b>")
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await message.reply_text("Usage: <code>/ban 123456</code> or <code>/unban 123456</code>")
    target = int(message.command[1])
    if is_any_admin(target):
        return await message.reply_text(f"{ce('cross')} <b>You can't ban an admin.</b>")
    ban = message.command[0].lower() == "ban"
    u = await asyncio.to_thread(sync_get_user, target)
    u["banned"] = ban
    await asyncio.to_thread(sync_save_user, target)
    await message.reply_text(user_card_text(target, u), reply_markup=user_card_markup(target, u))

@bot.on_message(filters.command("addcredit") & filters.private & filters.incoming)
async def addcredit_cmd(_, message: Message):
    uid = message.from_user.id
    if not has_perm(uid, "users"):
        return await message.reply_text(f"{ce('cross')} <b>Permission Denied.</b>")
    try:
        target, amt = int(message.command[1]), int(message.command[2])
    except Exception:
        return await message.reply_text("Usage: <code>/addcredit 123456 50</code>")
    u = await asyncio.to_thread(sync_get_user, target)
    u["credits"] = max(0, u["credits"] + amt)
    await asyncio.to_thread(sync_save_user, target)
    await message.reply_text(user_card_text(target, u), reply_markup=user_card_markup(target, u))
    try:
        await bot.send_message(target, f"{ce('spark')} <b>Admin added <code>{amt}</code> credits.</b> Now: <code>{u['credits']}</code>")
    except Exception:
        pass

# ----------------- ADMIN DASHBOARD MARKUPS ----------------- #

def get_role_based_markup(user_id: int, session_count: int, total_users: int):
    """icon_custom_emoji_id এবং style প্যারামিটার ব্যবহার করে বাটন তৈরি"""
    maint_on = bool(SETTINGS.get("maintenance"))
    raw_buttons = [
        [
            {
                "text": f"Broadcast to Users ({total_users})",
                "callback_data": "btn_start_broadcast",
                "icon_custom_emoji_id": UI_EMOJIS["broadcast"],
                "style": "primary"
            }
        ],
        [
            {
                "text": f"Sessions ({session_count})",
                "callback_data": "btn_list_sessions",
                "icon_custom_emoji_id": UI_EMOJIS["folder"],
                "style": "primary"
            },
            {
                "text": "Remove Session",
                "callback_data": "btn_del_menu",
                "icon_custom_emoji_id": UI_EMOJIS["trash"],
                "style": "danger"
            }
        ],
        [
            {
                "text": "Storage Info",
                "callback_data": "btn_storage_info",
                "icon_custom_emoji_id": UI_EMOJIS["disk"],
                "style": "success"
            },
            {
                "text": "Clean Cache",
                "callback_data": "btn_clean_storage",
                "icon_custom_emoji_id": UI_EMOJIS["broom"],
                "style": "danger"
            }
        ],
        [
            {
                "text": "Stats",
                "callback_data": "btn_stats",
                "icon_custom_emoji_id": UI_EMOJIS["target"],
                "style": "primary"
            },
            {
                "text": "Manage User",
                "callback_data": "btn_user_manager",
                "icon_custom_emoji_id": UI_EMOJIS["user"],
                "style": "primary"
            }
        ],
        [
            {
                "text": "All Users",
                "callback_data": "btn_users_0",
                "icon_custom_emoji_id": UI_EMOJIS["users"],
                "style": "primary"
            }
        ],
        [
            {
                "text": "Read Message (Session)",
                "callback_data": "btn_read_msg",
                "icon_custom_emoji_id": UI_EMOJIS["chat"],
                "style": "success"
            }
        ],
        [
            {
                "text": f"Referral Reward ({SETTINGS['ref_reward']})",
                "callback_data": "btn_ref_reward",
                "icon_custom_emoji_id": UI_EMOJIS["gift"],
                "style": "primary"
            },
            {
                "text": "Button Colors",
                "callback_data": "btn_colors",
                "icon_custom_emoji_id": UI_EMOJIS["palette"],
                "style": "primary"
            }
        ],
        [
            {
                "text": "Emoji Manager",
                "callback_data": "btn_emoji_menu",
                "icon_custom_emoji_id": UI_EMOJIS["emoji"],
                "style": "primary"
            },
            {
                "text": "Promo Codes",
                "callback_data": "btn_promo",
                "icon_custom_emoji_id": UI_EMOJIS["ticket"],
                "style": "success"
            }
        ],
        [
            {
                "text": "Download Alerts: ON" if SETTINGS["notify"].get("on") else "Download Alerts: OFF",
                "callback_data": "btn_notify",
                "icon_custom_emoji_id": UI_EMOJIS["bell"],
                "style": "success" if SETTINGS["notify"].get("on") else "danger"
            }
        ],
        [
            {
                "text": "Ads: ON" if SETTINGS["ads"]["on"] else "Ads: OFF",
                "callback_data": "btn_ads",
                "icon_custom_emoji_id": UI_EMOJIS["rocket"],
                "style": "success" if SETTINGS["ads"]["on"] else "danger"
            }
        ],
        [
            {
                "text": "Maintenance: ON" if maint_on else "Maintenance: OFF",
                "callback_data": "btn_toggle_maint",
                "icon_custom_emoji_id": UI_EMOJIS["tool"],
                "style": "danger" if maint_on else "success"
            },
            {
                "text": "Maint. Message",
                "callback_data": "btn_maint_msg",
                "icon_custom_emoji_id": UI_EMOJIS["pencil"],
                "style": "primary"
            }
        ]
    ]

    if user_id == OWNER_ID:
        raw_buttons.append([
            {
                "text": "Manage Sub-Admins",
                "callback_data": "btn_manage_admins",
                "icon_custom_emoji_id": UI_EMOJIS["users"],
                "style": "primary"
            }
        ])

    # 🌐 Web Dashboard (শুধু অ্যাডমিন প্যানেলে দেখা যায়; টোকেন লিংকে থাকে)
    if DASH_URL and DASH_TOKEN and has_perm(user_id, "users"):
        raw_buttons.append([
            {
                "text": "Open Web Dashboard",
                "url": f"{DASH_URL}/?key={quote(DASH_TOKEN)}",
                "style": "success"
            }
        ])

    raw_buttons.append([
        {
            "text": "Refresh Dashboard",
            "callback_data": "btn_refresh_panel",
            "icon_custom_emoji_id": UI_EMOJIS["refresh"],
            "style": "success"
        }
    ])

    return create_custom_inline_markup(raw_buttons)

async def build_panel(user_id: int, note: str = "Manage operations via the buttons below:"):
    sessions = await asyncio.to_thread(sync_get_all_sessions)
    all_users = await asyncio.to_thread(sync_get_all_bot_users)

    role_str = f"{ce('crown')} Master Owner" if user_id == OWNER_ID else f"{ce('shield')} Sub-Admin"
    db_str = f"Connected {ce('online')}" if db else f"Disconnected {ce('offline')}"
    maint_str = f"ON {ce('offline')}" if SETTINGS.get("maintenance") else f"OFF {ce('online')}"

    text = (
        f"┌── {ce('panel')} <b>ADMIN CONTROL SYSTEM</b>\n"
        f"│\n"
        f"├ {ce('user')} <b>Account Role:</b> {role_str}\n"
        f"├ {ce('antenna')} <b>Database:</b> {db_str}\n"
        f"├ {ce('users')} <b>Total Registered:</b> <code>{len(all_users)} Users</code>\n"
        f"├ {ce('folder')} <b>Userbot Session Pool:</b> <code>{len(sessions)} Active</code>\n"
        f"├ {ce('timer')} <b>Auto-Delete Delay:</b> <code>{AUTO_DELETE_TIME}s</code>\n"
        f"├ {ce('gift')} <b>Referral Reward:</b> <code>{SETTINGS['ref_reward']} credits</code>\n"
        f"├ {ce('tool')} <b>Maintenance:</b> {maint_str}\n"
        f"├ {ce('bell')} <b>Download Alerts:</b> {'ON ' + ce('online') if SETTINGS['notify'].get('on') else 'OFF ' + ce('offline')}\n"
        f"│\n"
        f"└─ {ce('spark')} <i>{note}</i>"
    )
    return text, get_role_based_markup(user_id, len(sessions), len(all_users))

def perm_markup(target_uid: int) -> RawMarkup:
    perms = loaded_admins.get(target_uid, {})
    s_tag = FALLBACK_SYMBOLS['online'] if perms.get("sessions") else FALLBACK_SYMBOLS['offline']
    b_tag = FALLBACK_SYMBOLS['online'] if perms.get("broadcast") else FALLBACK_SYMBOLS['offline']
    u_tag = FALLBACK_SYMBOLS['online'] if perms.get("users") else FALLBACK_SYMBOLS['offline']
    return create_custom_inline_markup([
        [{"text": f"Manage Sessions: {s_tag}", "callback_data": f"togperm_{target_uid}_sessions", "style": "primary"}],
        [{"text": f"Send Broadcast: {b_tag}", "callback_data": f"togperm_{target_uid}_broadcast", "style": "primary"}],
        [{"text": f"Manage Users: {u_tag}", "callback_data": f"togperm_{target_uid}_users", "style": "primary"}],
        [{"text": "Remove Admin", "callback_data": f"deladmin_{target_uid}", "icon_custom_emoji_id": UI_EMOJIS["trash"], "style": "danger"}],
        [{"text": "Back", "callback_data": "btn_manage_admins", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "danger"}]
    ])

@bot.on_message(filters.command(["admin", "panel"]) & filters.private & filters.incoming)
async def admin_panel_handler(_, message: Message):
    user_id = message.from_user.id
    if not is_any_admin(user_id):
        return await message.reply_text(
            f"{ce('cross')} <b>Access Denied: You do not have admin permissions.</b>",
            reply_markup=get_bottom_keyboard(user_id)
        )
    text, markup = await build_panel(user_id)
    await message.reply_text(text, reply_markup=markup)

# ----------------- LOGIN FLOW STARTER ----------------- #

async def begin_login(user_id: int):
    """/login, Connect Account বাটন এবং Help গাইড — তিন জায়গা থেকেই এটা চলবে"""
    # আগে একাউন্ট কানেক্ট করা থাকলে আবার লগইন করতে দেবে না
    if not (ADMINS_CAN_ADD_MULTIPLE and is_any_admin(user_id)):
        existing = await asyncio.to_thread(sync_get_user_session, user_id)
        if existing:
            user_states.pop(user_id, None)
            acc_name = html.escape(str(existing.get("account_name", "Unknown")))
            return await bot.send_message(
                user_id,
                f"┌── {ce('warning')} <b>ACCOUNT ALREADY CONNECTED</b>\n"
                f"│\n"
                f"├ {ce('user')} <b>Connected:</b> <code>{acc_name}</code>\n"
                f"└─ {ce('check')} <i>You have already connected an account. No need to connect again.</i>",
                reply_markup=get_bottom_keyboard(user_id)
            )

    user_states[user_id] = "WAIT_PHONE"
    markup = create_custom_inline_markup([
        [{"text": "Cancel", "callback_data": "btn_cancel_login", "icon_custom_emoji_id": UI_EMOJIS["cross"], "style": "danger"}]
    ])
    await bot.send_message(
        user_id,
        f"┌── {ce('phone')} <b>CONNECT YOUR TELEGRAM ACCOUNT</b>\n"
        f"│\n"
        f"├ Send your phone number with country code:\n"
        f"├ <i>(Example: <code>+8801700000000</code>)</i>\n"
        f"│\n"
        f"└─ <i>Connecting lets the bot access restricted channel files.</i>",
        reply_markup=markup
    )

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
        await query.message.edit_text(f"{ce('cross')} <b>Login process cancelled.</b>")
        return await query.answer("Cancelled")

    if data == "btn_retry_2fa":
        user_states[user_id] = "WAIT_2FA"
        await query.message.reply_text(f"{ce('lock')} <b>Please send your 2FA password:</b>", reply_markup=ForceReply(selective=True))
        return await query.answer()

    if data == "btn_check_sub":
        if await check_subscribed(user_id):
            await query.message.edit_text(f"{ce('check')} <b>Verified! Send /start to begin.</b>")
            return await query.answer("Verified")
        return await query.answer("You haven't joined yet!", show_alert=True)

    # ---------- 📖 Help Guide ---------- #
    if data.startswith("help_"):
        arg = data[len("help_"):]
        if arg == "close":
            try:
                await query.message.delete()
            except Exception:
                pass
            return await query.answer()
        page = int(arg) if arg.isdigit() else 1
        text, markup = help_page(page)
        await query.message.edit_text(text, reply_markup=markup)
        return await query.answer()

    if data == "btn_connect_start":
        await begin_login(user_id)
        return await query.answer()

    if data == "btn_profile_refresh":
        text, markup = await asyncio.to_thread(build_profile, user_id, query.from_user.first_name)
        await query.message.edit_text(text, reply_markup=markup)
        return await query.answer("Refreshed")

    if data == "btn_refer":
        u = await asyncio.to_thread(sync_get_user, user_id)
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        text = (
            f"┌── {ce('gift')} <b>REFER & EARN</b>\n"
            f"│\n"
            f"├ Earn <code>{SETTINGS['ref_reward']}</code> credits for every new user who joins with your link.\n"
            f"├ <b>Your referrals:</b> <code>{u.get('referrals', 0)}</code>\n"
            f"│\n"
            f"└─ <b>Your link:</b>\n<code>{link}</code>"
        )
        markup = create_custom_inline_markup([
            [{"text": "Share Link", "style": "success",
              "url": f"https://t.me/share/url?url={quote(link)}&text={quote('Download Telegram media with this bot!')}"}],
            [{"text": "Top Referrers", "callback_data": "btn_leaderboard",
              "icon_custom_emoji_id": UI_EMOJIS["trophy"], "style": "primary"}],
            [{"text": "Back", "callback_data": "btn_profile_refresh",
              "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]
        ])
        await query.message.edit_text(text, reply_markup=markup)
        return await query.answer()

    if data == "btn_leaderboard":
        top = await asyncio.to_thread(sync_top_referrers, 10)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, x in enumerate(top):
            mark = medals[i] if i < 3 else f"{i + 1}."
            nm = html.escape((x.get("name") or "User")[:12])
            lines.append(f"├ {mark} <b>{nm}</b> — <code>{x.get('referrals', 0)}</code> referrals")
        body = "\n".join(lines) if lines else "├ <i>No referrals yet. Be the first!</i>"
        text = (
            f"┌── {ce('trophy')} <b>TOP REFERRERS</b>\n"
            f"│\n"
            f"{body}\n"
            f"│\n"
            f"└─ {ce('gift')} <i>Invite friends and climb the board!</i>"
        )
        markup = create_custom_inline_markup([[{"text": "Back", "callback_data": "btn_refer",
                                                "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]])
        await query.message.edit_text(text, reply_markup=markup)
        return await query.answer()

    if data.startswith("cancel_"):
        key = data[len("cancel_"):]  # task_<uid>_<time>
        parts = key.split("_")
        owner_part = parts[1] if len(parts) >= 3 else ""
        if owner_part != str(user_id) and not is_any_admin(user_id):
            return await query.answer("Not your task", show_alert=True)
        cancel_requests.add(key)
        return await query.answer("Cancelling...")

    if not is_any_admin(user_id):
        return await query.answer("Access Denied", show_alert=True)

    if data == "btn_refresh_panel":
        user_states.pop(user_id, None)
        text, markup = await build_panel(user_id, "Dashboard Refreshed.")
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer("Dashboard Refreshed")

    elif data == "btn_start_broadcast":
        if not has_perm(user_id, "broadcast"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_BROADCAST_PAYLOAD"
        await query.message.reply_text(
            f"┌── {ce('broadcast')} <b>BROADCAST INTAKE</b>\n"
            f"│\n"
            f"├ {ce('chat')} Send any Text Message\n"
            f"├ {ce('mic')} Send Voice or Audio\n"
            f"├ {ce('media')} Send Photo or Video with Caption\n"
            f"│\n"
            f"└─ <i>Send <code>/cancel</code> to abort anytime.</i>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_stats":
        s = await asyncio.to_thread(sync_get_stats)
        up = int(time.time() - BOT_START)
        text = (
            f"┌── {ce('chart')} <b>BOT STATISTICS</b>\n"
            f"│\n"
            f"├ {ce('users')} <b>Total Users:</b> <code>{s['total']}</code>\n"
            f"├ {ce('online')} <b>Active (24h):</b> <code>{s['active24']}</code>\n"
            f"├ {ce('spark')} <b>New (24h):</b> <code>{s['new24']}</code>\n"
            f"├ {ce('offline')} <b>Banned:</b> <code>{s['banned']}</code>\n"
            f"├ {ce('download')} <b>Total Downloads:</b> <code>{s['downloads']}</code>\n"
            f"├ {ce('folder')} <b>Userbot Sessions:</b> <code>{len(loaded_user_clients)}</code>\n"
            f"├ {ce('refresh')} <b>Active Transfers:</b> <code>{len(active_tasks)}</code>\n"
            f"└ {ce('timer')} <b>Uptime:</b> <code>{up // 3600}h {(up % 3600) // 60}m</code>"
        )
        markup = create_custom_inline_markup([
            [{"text": "Refresh", "callback_data": "btn_stats", "icon_custom_emoji_id": UI_EMOJIS["refresh"], "style": "success"}],
            [{"text": "Back", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]
        ])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_user_manager":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_USER_LOOKUP"
        await query.message.reply_text(
            f"{ce('user')} <b>Send User ID, name or @username:</b>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    # ---------- 👥 Users List / View ---------- #
    elif data.startswith("btn_users_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        page = int(data.rsplit("_", 1)[1])
        allu = await asyncio.to_thread(sync_search_users, "", 2000)
        chunk = allu[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]
        has_next = len(allu) > (page + 1) * USERS_PER_PAGE
        await query.message.edit_text(
            f"┌── {ce('users')} <b>ALL USERS</b>\n"
            f"│\n"
            f"├ <b>Total:</b> <code>{len(allu)}</code>\n"
            f"└─ <b>Page:</b> <code>{page + 1}</code>\n\n"
            f"<i>Tap a user to manage. Use Manage User to search by name/@username.</i>",
            reply_markup=users_list_markup(chunk, page, has_next)
        )
        await query.answer()

    elif data.startswith("uv_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        target = int(data.rsplit("_", 1)[1])
        u = await asyncio.to_thread(sync_get_user, target)
        await query.message.edit_text(user_card_text(target, u), reply_markup=user_card_markup(target, u))
        await query.answer()

    elif data.startswith("ub_ban_") or data.startswith("ub_unban_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        ban = data.startswith("ub_ban_")
        target = int(data.rsplit("_", 1)[1])
        if is_any_admin(target):
            return await query.answer("You can't ban an admin", show_alert=True)
        u = await asyncio.to_thread(sync_get_user, target)
        u["banned"] = ban
        await asyncio.to_thread(sync_save_user, target)
        await query.answer("User banned" if ban else "User unbanned")
        await query.message.edit_text(user_card_text(target, u), reply_markup=user_card_markup(target, u))
        try:
            await bot.send_message(
                target,
                f"{ce('cross') if ban else ce('check')} <b>You have been {'banned' if ban else 'unbanned'} by admin.</b>"
            )
        except Exception:
            pass

    elif data.startswith("uc_add_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        pending_target[user_id] = int(data.rsplit("_", 1)[1])
        user_states[user_id] = "WAIT_CREDIT_AMOUNT"
        await query.message.reply_text(
            f"{ce('spark')} <b>Send credit amount</b> (e.g. <code>50</code>, remove: <code>-10</code>):",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "btn_storage_info":
        st = get_storage_stats()
        filled = int(st["percent"] / 10)
        bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
        text = (
            f"┌── {ce('disk')} <b>SERVER STORAGE OVERVIEW</b>\n"
            f"│\n"
            f"├ {ce('chart')} <b>Disk Usage:</b> ⌠ {bar} ⌡ <code>{st['percent']:.1f}%</code>\n"
            f"├ {ce('box')} <b>Used Space:</b> <code>{human_bytes(st['used'])}</code>\n"
            f"├ {ce('online')} <b>Free Space:</b> <code>{human_bytes(st['free'])}</code>\n"
            f"├ {ce('cabinet')} <b>Total Capacity:</b> <code>{human_bytes(st['total'])}</code>\n"
            f"│\n"
            f"└ {ce('folder')} <b>Temp Downloads:</b> <code>{human_bytes(st['dl_size'])}</code> ({st['dl_files']} files)"
        )
        markup = create_custom_inline_markup([
            [{"text": "Clean Temp Files", "callback_data": "btn_clean_storage", "icon_custom_emoji_id": UI_EMOJIS["broom"], "style": "danger"}],
            [{"text": "Back to Dashboard", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]
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
        await query.answer(f"Cleaned {human_bytes(freed_bytes)}", show_alert=True)
        st = get_storage_stats()
        filled = int(st["percent"] / 10)
        bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
        text = (
            f"┌── {ce('disk')} <b>SERVER STORAGE OVERVIEW</b>\n"
            f"│\n"
            f"├ {ce('chart')} <b>Disk Usage:</b> ⌠ {bar} ⌡ <code>{st['percent']:.1f}%</code>\n"
            f"├ {ce('box')} <b>Used Space:</b> <code>{human_bytes(st['used'])}</code>\n"
            f"├ {ce('online')} <b>Free Space:</b> <code>{human_bytes(st['free'])}</code>\n"
            f"├ {ce('cabinet')} <b>Total Capacity:</b> <code>{human_bytes(st['total'])}</code>\n"
            f"│\n"
            f"└ {ce('folder')} <b>Temp Downloads:</b> <code>{human_bytes(st['dl_size'])}</code> ({st['dl_files']} files)"
        )
        markup = create_custom_inline_markup([
            [{"text": "Clean Temp Files", "callback_data": "btn_clean_storage", "icon_custom_emoji_id": UI_EMOJIS["broom"], "style": "danger"}],
            [{"text": "Back to Dashboard", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]
        ])
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "btn_list_sessions":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        text = f"┌── {ce('folder')} <b>CONNECTED USERBOT SESSIONS</b>\n│\n"
        if not sessions:
            text += "└ <i>No userbot sessions currently linked.</i>"
        else:
            for idx, s in enumerate(sessions, 1):
                text += f"├ <b>{idx}.</b> <code>{html.escape(str(s.get('account_name', 'Unknown')))}</code>\n"
            text += "│\n└─ <i>Sessions handle private restricted downloads.</i>"
        markup = create_custom_inline_markup([[{"text": "Back", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]])
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data == "btn_del_menu":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            return await query.answer("No sessions to delete", show_alert=True)
        raw_buttons = [[{"text": s.get('account_name', 'Session'), "callback_data": f"del_{s['doc_id']}", "icon_custom_emoji_id": UI_EMOJIS["cross"], "style": "danger"}] for s in sessions]
        raw_buttons.append([{"text": "Back", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
        await query.message.edit_text("<b>Select a userbot session to disconnect:</b>", reply_markup=create_custom_inline_markup(raw_buttons))
        await query.answer()

    elif data.startswith("del_"):
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        doc_id = data.split("_", 1)[1]
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("Session removed successfully", show_alert=True)
        await query.message.delete()

    elif data == "btn_manage_admins":
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        raw_buttons = []
        for aid in loaded_admins:
            raw_buttons.append([{"text": f"Admin: {aid}", "callback_data": f"viewadmin_{aid}", "icon_custom_emoji_id": UI_EMOJIS["shield"], "style": "primary"}])
        raw_buttons.append([{"text": "Add Sub-Admin", "callback_data": "btn_add_new_admin", "icon_custom_emoji_id": UI_EMOJIS["user"], "style": "success"}])
        raw_buttons.append([{"text": "Back", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "danger"}])
        await query.message.edit_text("<b>Manage Sub-Admins and Delegated Rights:</b>", reply_markup=create_custom_inline_markup(raw_buttons))
        await query.answer()

    elif data == "btn_add_new_admin":
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        user_states[user_id] = "WAIT_ADMIN_ID"
        await query.message.reply_text(f"{ce('user')} <b>Send the Telegram User ID of the new admin:</b>", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data.startswith("viewadmin_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        target_uid = int(data.split("_")[1])
        await query.message.edit_text(f"{ce('gear')} <b>Permissions for Admin:</b> <code>{target_uid}</code>", reply_markup=perm_markup(target_uid))
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
        await query.message.edit_reply_markup(reply_markup=perm_markup(target_uid))

    elif data.startswith("deladmin_"):
        if user_id != OWNER_ID:
            return await query.answer("Owner Only", show_alert=True)
        target_uid = int(data.split("_")[1])
        await asyncio.to_thread(sync_delete_admin, target_uid)
        await query.answer(f"Admin {target_uid} removed", show_alert=True)
        raw_buttons = [[{"text": f"Admin: {aid}", "callback_data": f"viewadmin_{aid}", "icon_custom_emoji_id": UI_EMOJIS["shield"], "style": "primary"}] for aid in loaded_admins]
        raw_buttons.append([{"text": "Add Sub-Admin", "callback_data": "btn_add_new_admin", "icon_custom_emoji_id": UI_EMOJIS["user"], "style": "success"}])
        raw_buttons.append([{"text": "Back", "callback_data": "btn_refresh_panel", "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "danger"}])
        await query.message.edit_text("<b>Updated Admin List:</b>", reply_markup=create_custom_inline_markup(raw_buttons))

    # ---------- 📖 Read Message (via Session) ---------- #
    elif data == "btn_read_msg":
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        if not loaded_user_clients:
            return await query.answer("No active sessions. Connect one via /login first.", show_alert=True)
        await query.message.edit_text(
            f"{ce('folder')} <b>Select a session to read with:</b>\n"
            f"<i>Auto will try every connected session until one has access.</i>",
            reply_markup=session_pick_markup("rdsess")
        )
        await query.answer()

    elif data.startswith("rdsess_"):
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        doc_sel = data[len("rdsess_"):]
        pending_target[user_id] = doc_sel
        user_states[user_id] = "WAIT_READ_LINK"
        await query.message.reply_text(
            f"┌── {ce('chat')} <b>READ A MESSAGE</b>\n"
            f"│\n"
            f"├ Send a post link, e.g. <code>https://t.me/channel/123</code>\n"
            f"└─ Or <code>chat_id:msg_id</code>, e.g. <code>-1001234567890:55</code>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data.startswith("fwdread_"):
        if not has_perm(user_id, "sessions"):
            return await query.answer("Permission Denied", show_alert=True)
        token = data[len("fwdread_"):]
        info = READ_CACHE.pop(token, None)
        if not info:
            return await query.answer("Expired, please fetch the message again.", show_alert=True)
        doc_sel, chat_id, msg_id = info
        cl, _nm = get_client_by_doc(doc_sel)
        if not cl:
            return await query.answer("That session is no longer active.", show_alert=True)
        try:
            await cl.forward_messages(user_id, chat_id, msg_id)
            await query.answer("Forwarded to you!")
        except Exception as e:
            await query.answer(f"Failed: {e}"[:190], show_alert=True)

    # ---------- 🎨 Button Colors ---------- #
    elif data == "btn_colors":
        await query.message.edit_text(
            f"{ce('palette')} <b>BUTTON COLORS</b>\n\n"
            f"Tap a button to cycle its color:\n🔵 Blue → 🟢 Green → 🔴 Red → ⚪ Default\n\n"
            f"<i>Users see the new colors when they next receive the keyboard (e.g. after /start).</i>",
            reply_markup=colors_markup()
        )
        await query.answer()

    elif data.startswith("colcyc_"):
        key = data[len("colcyc_"):]
        if key in KB_BUTTONS:
            cur = SETTINGS["btn_styles"].get(key, KB_BUTTONS[key].get("style", ""))
            nxt = STYLE_CYCLE[(STYLE_CYCLE.index(cur) + 1) % len(STYLE_CYCLE)] if cur in STYLE_CYCLE else "primary"
            SETTINGS["btn_styles"][key] = nxt
            await asyncio.to_thread(sync_save_settings)
        await query.answer("Updated")
        await query.message.edit_reply_markup(reply_markup=colors_markup())

    elif data == "btn_col_reset":
        SETTINGS["btn_styles"] = {}
        await asyncio.to_thread(sync_save_settings)
        await query.answer("Reset to default")
        await query.message.edit_reply_markup(reply_markup=colors_markup())

    elif data == "btn_col_apply":
        await bot.send_message(
            user_id,
            f"{ce('check')} <b>Keyboard updated.</b>",
            reply_markup=get_bottom_keyboard(user_id)
        )
        await query.answer()

    # ---------- 🎁 Referral Reward ---------- #
    elif data == "btn_ref_reward":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_REF_REWARD"
        await query.message.reply_text(
            f"{ce('gift')} <b>Send new referral reward</b> "
            f"(current: <code>{SETTINGS['ref_reward']}</code>, <code>0</code> = off):",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    # ---------- 🛠️ Maintenance Mode ---------- #
    elif data == "btn_toggle_maint":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        SETTINGS["maintenance"] = not SETTINGS.get("maintenance", False)
        await asyncio.to_thread(sync_save_settings)
        on = SETTINGS["maintenance"]
        await query.answer("Maintenance ON — users are blocked" if on else "Maintenance OFF — bot is live", show_alert=True)
        text, markup = await build_panel(
            user_id,
            "Maintenance is ON. Only admins can use the bot." if on else "Maintenance is OFF. Bot is live for everyone."
        )
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "btn_maint_msg":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_MAINT_MSG"
        cur = (SETTINGS.get("maint_msg") or "").strip()
        await query.message.reply_text(
            f"┌── {ce('tool')} <b>MAINTENANCE MESSAGE</b>\n"
            f"│\n"
            f"├ <b>Current:</b> {html.escape(cur) if cur else '<i>default</i>'}\n"
            f"│\n"
            f"└─ Send the new message users will see.\n<i>Send <code>-</code> to reset to default.</i>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    # ---------- 😀 Emoji Manager ---------- #
    elif data == "btn_emoji_menu":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states.pop(user_id, None)
        pending_target.pop(user_id, None)
        await query.message.edit_text(emoji_menu_text(), reply_markup=emoji_main_markup())
        await query.answer()

    elif data.startswith("emcat_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states.pop(user_id, None)
        pending_target.pop(user_id, None)
        _, scope, page = data.split("_")
        title = "BOTTOM KEYBOARD BUTTONS" if scope == "k" else "INLINE BUTTONS & MESSAGE ICONS"
        await query.message.edit_text(
            f"┌── {ce('emoji')} <b>{title}</b>\n"
            f"│\n"
            f"└─ Tap an item to change its emoji.\n<i>🟢 = custom emoji set · ⚪ = plain / blank</i>",
            reply_markup=emoji_list_markup(scope, int(page))
        )
        await query.answer()

    elif data.startswith("emsel_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        _, scope, name = data.split("_", 2)
        if not emoji_valid(scope, name):
            return await query.answer("Unknown item", show_alert=True)
        pending_target[user_id] = (scope, name)
        user_states[user_id] = "WAIT_EMOJI_VALUE"
        text, markup = emoji_entry_view(scope, name, waiting=True)
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    elif data.startswith("emdef_") or data.startswith("emclr_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        _, scope, name = data.split("_", 2)
        if not emoji_valid(scope, name):
            return await query.answer("Unknown item", show_alert=True)
        to_default = data.startswith("emdef_")
        await asyncio.to_thread(emoji_set, scope, name, None if to_default else "")
        await query.answer("Reset to default" if to_default else "Emoji removed")
        pending_target[user_id] = (scope, name)
        user_states[user_id] = "WAIT_EMOJI_VALUE"
        text, markup = emoji_entry_view(scope, name, waiting=True)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "emresetall":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        SETTINGS["emojis"] = {}
        SETTINGS["kb_emojis"] = {}
        apply_emoji_overrides()
        await asyncio.to_thread(sync_save_settings)
        await query.answer("All emojis reset to default", show_alert=True)
        await query.message.edit_text(emoji_menu_text(), reply_markup=emoji_main_markup())

    # ---------- 🔔 Download Alerts ---------- #
    elif data == "btn_notify":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states.pop(user_id, None)
        await query.message.edit_text(notify_menu_text(), reply_markup=notify_menu_markup())
        await query.answer()

    elif data == "ntf_toggle":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        SETTINGS["notify"]["on"] = not SETTINGS["notify"].get("on", True)
        await asyncio.to_thread(sync_save_settings)
        await query.answer("Alerts ON" if SETTINGS["notify"]["on"] else "Alerts OFF")
        await query.message.edit_text(notify_menu_text(), reply_markup=notify_menu_markup())

    elif data.startswith("ntf_f_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        key = data[len("ntf_f_"):]
        if key in SETTINGS["notify"]["show"]:
            SETTINGS["notify"]["show"][key] = not SETTINGS["notify"]["show"][key]
            await asyncio.to_thread(sync_save_settings)
        await query.answer("Updated")
        await query.message.edit_text(notify_menu_text(), reply_markup=notify_menu_markup())

    elif data == "ntf_title":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_NOTIFY_TITLE"
        await query.message.reply_text(
            f"┌── {ce('bell')} <b>ALERT TITLE</b>\n"
            f"│\n"
            f"├ <b>Current:</b> <code>{html.escape(SETTINGS['notify'].get('title') or 'New Download')}</code>\n"
            f"└─ Send the new title (max 60 chars).\n<i>Send <code>-</code> to reset.</i>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

    elif data == "ntf_test":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        sample = "https://t.me/example_channel/123"
        text = build_notify_text(query.from_user.first_name, query.from_user.username or "",
                                 user_id, 29, sample, "Video", 25 * 1024 * 1024)
        await bot.send_message(user_id, text, reply_markup=notify_markup(user_id, sample))
        await query.answer("Test alert sent")

    # ---------- 📺 Ads Control ---------- #
    elif data == "btn_ads":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states.pop(user_id, None)
        await query.message.edit_text(ads_menu_text(), reply_markup=ads_menu_markup())
        await query.answer()

    elif data.startswith("ad_t_"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        key = data[len("ad_t_"):]
        if key in ("on", "first_time", "skip_admins", "fail_open"):
            SETTINGS["ads"][key] = not SETTINGS["ads"][key]
            await asyncio.to_thread(sync_save_settings)
        await query.answer("Updated")
        await query.message.edit_text(ads_menu_text(), reply_markup=ads_menu_markup())

    elif data == "ad_prov":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        a = SETTINGS["ads"]
        a["provider"] = "adsgram" if a["provider"] == "test" else "test"
        await asyncio.to_thread(sync_save_settings)
        await query.answer(f"Provider: {a['provider']}")
        await query.message.edit_text(ads_menu_text(), reply_markup=ads_menu_markup())

    elif data in ("ad_every", "ad_block", "ad_text"):
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = {"ad_every": "WAIT_AD_EVERY", "ad_block": "WAIT_AD_BLOCK", "ad_text": "WAIT_AD_TEXT"}[data]
        prompt = {
            "ad_every": "Send how many downloads between ads (e.g. <code>8</code>):",
            "ad_block": "Send your Adsgram <b>Block ID</b> (send <code>-</code> to clear):",
            "ad_text": "Send the ad message as <code>Title | Description</code>:",
        }[data]
        await query.message.reply_text(f"{ce('rocket')} {prompt}", reply_markup=ForceReply(selective=True))
        await query.answer()

    elif data == "ad_preview":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        if not DASH_URL:
            return await query.answer("Set the DASH_URL env first.", show_alert=True)
        a = SETTINGS["ads"]
        await bot.send_message(
            user_id,
            f"┌── {ce('spark')} <b>{html.escape(a['title'])}</b>\n│\n└─ {html.escape(a['text'])}",
            reply_markup=ad_button_markup()
        )
        await query.answer("Preview sent")

    # ---------- 🎟️ Promo Codes ---------- #
    elif data == "btn_promo":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        promos = await asyncio.to_thread(sync_list_promos)
        lines = []
        for code, p in list(promos.items())[:25]:
            mx = p.get("max_uses", 0)
            lines.append(f"├ <code>{html.escape(code)}</code> · +{p.get('credits', 0)} · "
                         f"{len(p.get('used_by', []))}/{mx if mx else '∞'} used")
        body = "\n".join(lines) if lines else "├ <i>No promo codes yet.</i>"
        markup = create_custom_inline_markup([
            [{"text": "Create / Delete Code", "callback_data": "btn_promo_new",
              "icon_custom_emoji_id": UI_EMOJIS["pencil"], "style": "success"}],
            [{"text": "Back", "callback_data": "btn_refresh_panel",
              "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}]
        ])
        await query.message.edit_text(
            f"┌── {ce('ticket')} <b>PROMO CODES</b>\n│\n{body}\n│\n"
            f"└─ <i>Users redeem with the Redeem Code button or /redeem CODE</i>",
            reply_markup=markup
        )
        await query.answer()

    elif data == "btn_promo_new":
        if not has_perm(user_id, "users"):
            return await query.answer("Permission Denied", show_alert=True)
        user_states[user_id] = "WAIT_PROMO_CREATE"
        await query.message.reply_text(
            f"┌── {ce('ticket')} <b>NEW PROMO CODE</b>\n"
            f"│\n"
            f"├ Format: <code>CODE credits max_uses</code>\n"
            f"├ Example: <code>WELCOME50 50 100</code> (<code>0</code> = unlimited uses)\n"
            f"└─ To remove: <code>delete WELCOME50</code>",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()

# ----------------- MESSAGE ROUTER & BROADCAST INTAKE ----------------- #

@bot.on_message(filters.private & filters.incoming & ~filters.command(["start", "help", "admin", "panel", "ping", "storage", "clean", "login", "cancel", "profile", "ban", "unban", "addcredit", "redeem"]))
async def message_input_router(_, message: Message):
    user_id = message.from_user.id
    await asyncio.to_thread(sync_register_user, user_id,
                            message.from_user.first_name, message.from_user.username)

    state = user_states.get(user_id)
    text_input = (message.text or message.caption or "").strip()

    # Reply Keyboard Router
    if is_btn(text_input, "connect"):
        return await public_login_cmd(_, message)
    elif is_btn(text_input, "latency"):
        return await ping_handler(_, message)
    elif is_btn(text_input, "admin"):
        return await admin_panel_handler(_, message)
    elif is_btn(text_input, "profile"):
        return await profile_handler(_, message)
    elif is_btn(text_input, "redeem"):
        return await ask_redeem(message)
    elif is_btn(text_input, "broadcast"):
        if not has_perm(user_id, "broadcast"):
            return await message.reply_text(f"{ce('cross')} <b>Permission Denied.</b>", reply_markup=get_bottom_keyboard(user_id))
        user_states[user_id] = "WAIT_BROADCAST_PAYLOAD"
        return await message.reply_text(
            f"┌── {ce('broadcast')} <b>BROADCAST INTAKE</b>\n"
            f"│\n"
            f"├ {ce('chat')} Send Text Message\n"
            f"├ {ce('mic')} Send Voice / Audio\n"
            f"├ {ce('media')} Send Photo / Video\n"
            f"│\n"
            f"└─ <i>Send <code>/cancel</code> to abort.</i>",
            reply_markup=ForceReply(selective=True)
        )
    elif is_btn(text_input, "storage"):
        return await storage_cmd_handler(_, message)
    elif is_btn(text_input, "clean"):
        return await clean_cmd_handler(_, message)
    elif is_btn(text_input, "howto"):
        return await help_handler(_, message)

    # Broadcast Payload Receiver
    if state == "WAIT_BROADCAST_PAYLOAD" and has_perm(user_id, "broadcast"):
        user_states.pop(user_id, None)
        asyncio.create_task(broadcast_to_all_users(message, user_id))
        return

    # Redeem Code
    if state == "WAIT_REDEEM_CODE":
        user_states.pop(user_id, None)
        if not text_input:
            return await message.reply_text(f"{ce('cross')} <b>Please send the code as text.</b>", reply_markup=get_bottom_keyboard(user_id))
        return await do_redeem(message, text_input.split()[0])

    # Referral Reward Amount (Admin Panel)
    if state == "WAIT_REF_REWARD" and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        if not text_input.isdigit():
            return await message.reply_text(f"{ce('cross')} <b>Send a valid number.</b>", reply_markup=get_bottom_keyboard(user_id))
        SETTINGS["ref_reward"] = int(text_input)
        await asyncio.to_thread(sync_save_settings)
        return await message.reply_text(
            f"{ce('check')} <b>Referral reward set to <code>{SETTINGS['ref_reward']}</code> credits.</b>",
            reply_markup=get_bottom_keyboard(user_id)
        )

    # Maintenance Message (Admin Panel)
    if state == "WAIT_MAINT_MSG" and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        if not text_input:
            return await message.reply_text(f"{ce('cross')} <b>Please send text.</b>", reply_markup=get_bottom_keyboard(user_id))
        SETTINGS["maint_msg"] = "" if text_input == "-" else text_input[:500]
        await asyncio.to_thread(sync_save_settings)
        return await message.reply_text(
            f"{ce('check')} <b>Maintenance message updated.</b>\n\n{maintenance_text()}",
            reply_markup=get_bottom_keyboard(user_id)
        )

    # Emoji Manager: নতুন কাস্টম ইমোজি / ID নেওয়া
    if state == "WAIT_EMOJI_VALUE" and has_perm(user_id, "users"):
        tgt = pending_target.get(user_id)
        if not tgt:
            user_states.pop(user_id, None)
            return
        ents = message.entities or message.caption_entities or []
        ids = [str(e.custom_emoji_id) for e in ents if getattr(e, "custom_emoji_id", None)]
        if not ids and text_input.isdigit():
            ids = [text_input]
        if not ids:
            return await message.reply_text(
                f"{ce('cross')} <b>Send a custom emoji</b> (or its numeric ID). "
                f"Send <code>/cancel</code> to stop."
            )
        scope, name = tgt
        await asyncio.to_thread(emoji_set, scope, name, ids[0])
        user_states.pop(user_id, None)
        pending_target.pop(user_id, None)
        text, markup = emoji_entry_view(scope, name, waiting=False)
        return await message.reply_text(f"{ce('check')} <b>Saved!</b>\n\n{text}", reply_markup=markup)

    # Download Alert Title
    if state == "WAIT_NOTIFY_TITLE" and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        if not text_input:
            return await message.reply_text(f"{ce('cross')} <b>Please send text.</b>", reply_markup=get_bottom_keyboard(user_id))
        SETTINGS["notify"]["title"] = "New Download" if text_input == "-" else text_input[:60]
        await asyncio.to_thread(sync_save_settings)
        return await message.reply_text(
            f"{ce('check')} <b>Alert title updated.</b>",
            reply_markup=notify_menu_markup()
        )

    # Ads settings input
    if state in ("WAIT_AD_EVERY", "WAIT_AD_BLOCK", "WAIT_AD_TEXT") and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        a = SETTINGS["ads"]
        if state == "WAIT_AD_EVERY":
            if not text_input.isdigit() or int(text_input) < 1:
                return await message.reply_text(f"{ce('cross')} <b>Send a number (1 or more).</b>")
            a["every_n"] = int(text_input)
        elif state == "WAIT_AD_BLOCK":
            a["block_id"] = "" if text_input == "-" else text_input.strip()[:40]
        else:
            if not text_input:
                return await message.reply_text(f"{ce('cross')} <b>Please send text.</b>")
            if "|" in text_input:
                t, b = text_input.split("|", 1)
                a["title"], a["text"] = t.strip()[:60], b.strip()[:300]
            else:
                a["title"] = text_input.strip()[:60]
        await asyncio.to_thread(sync_save_settings)
        return await message.reply_text(f"{ce('check')} <b>Saved.</b>", reply_markup=ads_menu_markup())

    # Promo Codes: তৈরি / ডিলিট
    if state == "WAIT_PROMO_CREATE" and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        parts = text_input.split()
        if len(parts) == 2 and parts[0].lower() == "delete":
            ok = await asyncio.to_thread(sync_delete_promo, parts[1].upper())
            return await message.reply_text(
                f"{ce('check') if ok else ce('cross')} <b>{'Code deleted.' if ok else 'Code not found.'}</b>",
                reply_markup=get_bottom_keyboard(user_id)
            )
        code = parts[0].upper() if parts else ""
        if (len(parts) < 2 or not parts[1].isdigit() or not re.fullmatch(r"[A-Z0-9_-]{3,20}", code)
                or (len(parts) > 2 and not parts[2].isdigit())):
            return await message.reply_text(
                f"{ce('cross')} <b>Invalid format.</b>\n"
                f"<i>Example: <code>WELCOME50 50 100</code> (code: 3-20 letters/numbers)</i>",
                reply_markup=get_bottom_keyboard(user_id)
            )
        credits = int(parts[1])
        max_uses = int(parts[2]) if len(parts) > 2 else 0
        await asyncio.to_thread(sync_create_promo, code, credits, max_uses)
        return await message.reply_text(
            f"┌── {ce('check')} <b>PROMO CODE CREATED</b>\n"
            f"│\n"
            f"├ <b>Code:</b> <code>{code}</code>\n"
            f"├ <b>Credits:</b> <code>+{credits}</code>\n"
            f"└─ <b>Max uses:</b> <code>{max_uses if max_uses else 'unlimited'}</code>",
            reply_markup=get_bottom_keyboard(user_id)
        )

    # Add Admin Prompt
    if state == "WAIT_ADMIN_ID" and user_id == OWNER_ID:
        user_states.pop(user_id, None)
        if not text_input.isdigit():
            return await message.reply_text(f"{ce('cross')} <b>User ID must contain only digits.</b>", reply_markup=get_bottom_keyboard(user_id))
        t_id = int(text_input)
        await asyncio.to_thread(sync_add_admin, t_id)
        return await message.reply_text(f"{ce('check')} <b>Admin <code>{t_id}</code> successfully added!</b>", reply_markup=get_bottom_keyboard(user_id))

    # User Manager: ID / নাম / @username দিয়ে সার্চ
    if state == "WAIT_USER_LOOKUP" and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        if text_input.isdigit():
            t_id = int(text_input)
            u = await asyncio.to_thread(sync_get_user, t_id)
            return await message.reply_text(user_card_text(t_id, u), reply_markup=user_card_markup(t_id, u))
        res = await asyncio.to_thread(sync_search_users, text_input, 10)
        if not res:
            return await message.reply_text(f"{ce('cross')} <b>No user found.</b>", reply_markup=get_bottom_keyboard(user_id))
        return await message.reply_text(
            f"{ce('users')} <b>Found {len(res)} user(s):</b>",
            reply_markup=users_list_markup(res)
        )

    # User Manager: ক্রেডিট পরিমাণ নেওয়া
    if state == "WAIT_CREDIT_AMOUNT" and has_perm(user_id, "users"):
        user_states.pop(user_id, None)
        t_id = pending_target.pop(user_id, None)
        try:
            amt = int(text_input)
        except ValueError:
            return await message.reply_text(f"{ce('cross')} <b>Send a valid number.</b>", reply_markup=get_bottom_keyboard(user_id))
        if not t_id:
            return
        u = await asyncio.to_thread(sync_get_user, t_id)
        u["credits"] = max(0, u["credits"] + amt)
        await asyncio.to_thread(sync_save_user, t_id)
        await message.reply_text(user_card_text(t_id, u), reply_markup=user_card_markup(t_id, u))
        try:
            await bot.send_message(t_id, f"{ce('spark')} <b>Admin added <code>{amt}</code> credits.</b> Now: <code>{u['credits']}</code>")
        except Exception:
            pass
        return

    # Read Message (via Session): লিংক বা chat_id:msg_id নেওয়া
    if state == "WAIT_READ_LINK" and has_perm(user_id, "sessions"):
        user_states.pop(user_id, None)
        doc_sel = pending_target.pop(user_id, None)
        parsed = parse_tme_link(text_input)
        if not parsed:
            return await message.reply_text(
                f"{ce('cross')} <b>Invalid format.</b>\n<i>Example: <code>https://t.me/channel/123</code> or <code>-1001234567890:55</code></i>",
                reply_markup=get_bottom_keyboard(user_id)
            )
        chat_id, msg_id = parsed

        clients_to_try = []
        if doc_sel and doc_sel != "auto":
            cl, nm = get_client_by_doc(doc_sel)
            if cl:
                clients_to_try = [(doc_sel, nm, cl)]
        if not clients_to_try:
            clients_to_try = loaded_user_clients

        if not clients_to_try:
            return await message.reply_text(f"{ce('cross')} <b>No active sessions.</b>", reply_markup=get_bottom_keyboard(user_id))

        status = await message.reply_text(f"{ce('spark')} <i>Fetching message...</i>")
        target_msg, used_name, used_doc = None, None, None
        for d, nm, cl in clients_to_try:
            try:
                msg = await asyncio.wait_for(cl.get_messages(chat_id, msg_id), timeout=12)
                if msg and not msg.empty:
                    target_msg, used_name, used_doc = msg, nm, d
                    break
            except Exception:
                continue

        if not target_msg or target_msg.empty:
            return await status.edit_text(f"{ce('cross')} <b>Message not found, or no connected session has access to that chat.</b>")

        media_type = ("Video" if target_msg.video else "Photo" if target_msg.photo else
                      "Audio" if target_msg.audio else "Voice" if target_msg.voice else
                      "Document" if target_msg.document else "Text" if target_msg.text else "Other / Service")
        sender = target_msg.from_user
        sender_str = f"{html.escape(sender.first_name or '')} (<code>{sender.id}</code>)" if sender else "Channel / Unknown"
        body = target_msg.text or target_msg.caption or ""
        body_preview = (html.escape(body[:600]) + ("…" if len(body) > 600 else "")) if body else "<i>No text content.</i>"
        date_str = target_msg.date.strftime("%Y-%m-%d %H:%M UTC") if getattr(target_msg, "date", None) else "—"

        text_out = (
            f"┌── {ce('chat')} <b>MESSAGE PREVIEW</b>\n"
            f"│\n"
            f"├ <b>Session Used:</b> <code>{html.escape(str(used_name))}</code>\n"
            f"├ <b>Chat:</b> <code>{html.escape(str(chat_id))}</code>\n"
            f"├ <b>Msg ID:</b> <code>{msg_id}</code>\n"
            f"├ <b>Type:</b> <code>{media_type}</code>\n"
            f"├ <b>From:</b> {sender_str}\n"
            f"├ <b>Date:</b> <code>{date_str}</code>\n"
            f"│\n"
            f"└─ <b>Content:</b>\n{body_preview}"
        )

        markup_rows = []
        if media_type != "Text" or body:
            token = f"{user_id}_{int(time.time() * 1000) % 1000000}"
            READ_CACHE[token] = (used_doc, chat_id, msg_id)
            markup_rows.append([{"text": "Forward To Me", "callback_data": f"fwdread_{token}",
                                  "icon_custom_emoji_id": UI_EMOJIS["check"], "style": "success"}])
        markup_rows.append([{"text": "Back", "callback_data": "btn_refresh_panel",
                              "icon_custom_emoji_id": UI_EMOJIS["back"], "style": "primary"}])
        await status.edit_text(text_out, reply_markup=create_custom_inline_markup(markup_rows))
        return

    # Multi-step Interactive Login Flow
    if state == "WAIT_PHONE":
        user_states.pop(user_id, None)
        phone = text_input.replace(" ", "")
        cl = Client(f"log_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await cl.connect()
            c = await cl.send_code(phone)
            login_clients[user_id] = {"cl": cl, "phone": phone, "hash": c.phone_code_hash}
            user_states[user_id] = "WAIT_OTP"
            await message.reply_text(
                f"┌── {ce('phone')} <b>TELEGRAM VERIFICATION</b>\n"
                f"│\n"
                f"├ Enter the OTP received in Telegram.\n"
                f"└─ {ce('bulb')} <i>Tip: Add spaces between digits (e.g. <code>1 2 3 4 5</code>)</i>",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            await message.reply_text(f"{ce('cross')} <b>Failed to send code:</b> <code>{e}</code>", reply_markup=get_bottom_keyboard(user_id))
        return

    elif state == "WAIT_OTP":
        otp = text_input.replace(" ", "").replace("-", "")
        data = login_clients.get(user_id)
        if not data:
            user_states.pop(user_id, None)
            return await message.reply_text(f"{ce('warning')} Session expired. Please run <code>/login</code> again.", reply_markup=get_bottom_keyboard(user_id))
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
                f"┌── {ce('check')} <b>ACCOUNT LINKED SUCCESSFULLY!</b>\n"
                f"│\n"
                f"├ {ce('user')} <b>Connected:</b> <code>{html.escape(acc)}</code>\n"
                f"└─ {ce('spark')} Your session is now active in the userbot pool.",
                reply_markup=get_bottom_keyboard(user_id)
            )
        except SessionPasswordNeeded:
            user_states[user_id] = "WAIT_2FA"
            await message.reply_text(
                f"┌── {ce('lock')} <b>TWO-STEP VERIFICATION REQUIRED</b>\n"
                f"│\n"
                f"└─ Please enter your cloud account password:",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            await cl.disconnect()
            login_clients.pop(user_id, None)
            user_states.pop(user_id, None)
            await message.reply_text(f"{ce('cross')} <b>Login failed:</b> <code>{e}</code>", reply_markup=get_bottom_keyboard(user_id))
        return

    elif state == "WAIT_2FA":
        data = login_clients.get(user_id)
        if not data:
            user_states.pop(user_id, None)
            return await message.reply_text(f"{ce('warning')} Session expired. Run <code>/login</code>.", reply_markup=get_bottom_keyboard(user_id))
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
                f"┌── {ce('check')} <b>ACCOUNT LINKED SUCCESSFULLY!</b>\n"
                f"│\n"
                f"├ {ce('user')} <b>Connected:</b> <code>{html.escape(acc)}</code>\n"
                f"└─ {ce('spark')} Your session is now ready.",
                reply_markup=get_bottom_keyboard(user_id)
            )
        except Exception as e:
            markup = create_custom_inline_markup([
                [{"text": "Retry", "callback_data": "btn_retry_2fa", "icon_custom_emoji_id": UI_EMOJIS["refresh"], "style": "primary"},
                 {"text": "Cancel", "callback_data": "btn_cancel_login", "icon_custom_emoji_id": UI_EMOJIS["cross"], "style": "danger"}]
            ])
            await message.reply_text(f"{ce('cross')} <b>Incorrect 2FA Password:</b> <code>{e}</code>", reply_markup=markup)
        return

    # ----------------- TELEGRAM POST DOWNLOAD ROUTINE ----------------- #
    p_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_input)
    pub_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_input)

    if not (p_match or pub_match):
        if len(text_input) > 2 and ("t.me/" in text_input or "telegram.me/" in text_input):
            return await message.reply_text(
                f"{ce('warning')} <b>Invalid post link format.</b>\n<i>Example: <code>https://t.me/channel/123</code></i>",
                reply_markup=help_hint_markup()
            )
        # লিংক ছাড়া অন্য কিছু পাঠালে ইউজারকে গাইড দেখাবে
        return await message.reply_text(
            f"┌── {ce('info')} <b>SEND A POST LINK</b>\n"
            f"│\n"
            f"├ Paste a Telegram post link to download it.\n"
            f"└─ {ce('spark')} <i>Example:</i> <code>https://t.me/channel/123</code>",
            reply_markup=help_hint_markup()
        )

    # ক্রেডিট চেক
    if not has_unlimited(user_id):
        _u = await asyncio.to_thread(sync_get_user, user_id)
        if _u["credits"] <= 0:
            return await message.reply_text(
                f"┌── {ce('warning')} <b>NO CREDITS LEFT</b>\n"
                f"│\n"
                f"├ Your daily <code>{DAILY_CREDITS}</code> credits are used up. They reset at 00:00 UTC.\n"
                f"├ {ce('gift')} <i>Refer friends from Profile → Refer & Earn.</i>\n"
                f"└─ {ce('ticket')} <i>Have a promo code? Tap Redeem Code.</i>",
                reply_markup=get_bottom_keyboard(user_id)
            )

    # 📺 Ad গেট (প্রথম ডাউনলোড / প্রতি N ডাউনলোড)
    if await ad_gate(message, user_id):
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

    post_link = (f"https://t.me/c/{p_match.group(1)}/{p_match.group(2)}" if p_match
                 else f"https://t.me/{pub_match.group(1)}/{pub_match.group(2)}")
    _fu = message.from_user

    # Fast Server Copy Bypass
    try:
        copied = await bot.copy_message(chat_id=message.chat.id, from_chat_id=chat_id, message_id=msg_id)
        if copied:
            await asyncio.to_thread(sync_charge, user_id)
            _k, _sz = describe_msg(copied)
            asyncio.create_task(notify_admins(_fu.id, _fu.first_name, _fu.username or "", post_link, _k, _sz))
            asyncio.create_task(silent_auto_delete(message.chat.id, [copied.id, message.id]))
            return
    except Exception:
        pass

    if not loaded_user_clients:
        await init_session_pool()

    if not loaded_user_clients:
        return await message.reply_text(
            f"{ce('cross')} <b>No active sessions found. Connect an account using <code>/login</code>.</b>",
            reply_markup=get_bottom_keyboard(user_id)
        )

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
        return await message.reply_text(
            f"{ce('cross')} <b>Post unavailable or channel not joined by any connected userbot.</b>",
            reply_markup=get_bottom_keyboard(user_id)
        )

    # Direct Forward Bypass
    try:
        fwd = await working_client.forward_messages(message.chat.id, chat_id, msg_id)
        if fwd:
            await asyncio.to_thread(sync_charge, user_id)
            _k, _sz = describe_msg(fwd)
            asyncio.create_task(notify_admins(_fu.id, _fu.first_name, _fu.username or "", post_link, _k, _sz))
            _fid = fwd[0].id if isinstance(fwd, (list, tuple)) else fwd.id
            asyncio.create_task(silent_auto_delete(message.chat.id, [_fid, message.id]))
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

    status = await message.reply_text(f"{ce('spark')} <b>Preparing transfer...</b>")
    task_id = f"task_{message.from_user.id}_{int(time.time())}"

    # Album Processing
    if len(media_group) > 1:
        alb_tracker = AlbumProgressTracker(status, len(media_group), "Downloading album...", task_id=task_id, cancel_key=task_id)
        for i, m in enumerate(media_group):
            m_obj = m.video or m.document or m.audio or m.photo
            sz = getattr(m_obj, "file_size", 0) or 0
            alb_tracker.file_progress[i] = (0, sz)

        tasks = [download_album_item(working_client, m, idx, message.from_user.id, alb_tracker) for idx, m in enumerate(media_group)]
        results = await asyncio.gather(*tasks)

        if task_id in cancel_requests:
            cancel_requests.discard(task_id)
            alb_tracker.finish()
            for r in results:
                for f in (r[0], r[3]):
                    if f and os.path.exists(f):
                        try:
                            os.remove(f)
                        except Exception:
                            pass
            return await status.edit_text(f"{ce('cross')} <b>Download cancelled.</b>")

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
            await asyncio.to_thread(sync_charge, user_id)
            _tot = sum((getattr(m.video or m.document or m.audio or m.photo, "file_size", 0) or 0) for m in media_group)
            asyncio.create_task(notify_admins(_fu.id, _fu.first_name, _fu.username or "", post_link,
                                              f"Album ({len(input_media)} files)", _tot))
            await status.delete()
            asyncio.create_task(silent_auto_delete(message.chat.id, [m.id for m in sent_msgs] + [message.id]))
        else:
            await status.edit_text(f"{ce('cross')} <b>Album processing failed.</b>")

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
            return await status.edit_text(f"{ce('note')} <b>Text Content:</b>\n\n{html.escape(target_msg.text)}")
        return await status.edit_text(f"{ce('warning')} <b>No media found in the post.</b>")

    ext = ".mp4" if target_msg.video else ".jpg" if target_msg.photo else ".mp3" if target_msg.audio else ""
    if not ext and target_msg.document:
        ext = os.path.splitext(target_msg.document.file_name or "")[1] or ".bin"

    single_dest = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_{int(time.time())}{ext}")
    dl_tracker = ProgressTracker(status, "Downloading media...", task_id=f"dl_{task_id}",
                                 task_type="download", cancel_key=task_id)
    file_path = None

    for attempt in range(2):
        if task_id in cancel_requests:
            break
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

    if task_id in cancel_requests:
        cancel_requests.discard(task_id)
        for f in (file_path, single_dest):
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        return await status.edit_text(f"{ce('cross')} <b>Download cancelled.</b>")

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

    up_tracker = ProgressTracker(status, "Uploading media...", task_id=f"up_{task_id}",
                                 task_type="upload", cancel_key=task_id)
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
        if task_id not in cancel_requests:
            await status.edit_text(f"{ce('cross')} <b>Upload failed:</b> <code>{e}</code>")
    finally:
        up_tracker.finish()

    cancelled = task_id in cancel_requests
    cancel_requests.discard(task_id)
    if cancelled:
        await status.edit_text(f"{ce('cross')} <b>Upload cancelled.</b>")
    elif sent:
        await status.delete()

    if sent and not cancelled:
        await asyncio.to_thread(sync_charge, user_id)
        _k, _sz = describe_msg(target_msg)
        asyncio.create_task(notify_admins(_fu.id, _fu.first_name, _fu.username or "", post_link, _k, _sz))
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
            if tracker and tracker.is_cancelled():
                break
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

@bot.on_message(filters.command("start") & filters.private & filters.incoming)
async def start_handler(_, message: Message):
    user_id = message.from_user.id
    await asyncio.to_thread(sync_register_user, user_id,
                            message.from_user.first_name, message.from_user.username)
    _u = await asyncio.to_thread(sync_get_user, user_id)
    is_new = bool(_u.get("fresh"))
    await process_referral(message, user_id)
    await message.reply_text(
        f"┌── {ce('wave')} <b>WELCOME, {html.escape(message.from_user.first_name.upper())}!</b>\n"
        f"│\n"
        f"├ {ce('spark')} Send any public or private Telegram link to download media.\n"
        f"├ {ce('lock')} Use <code>/login</code> or the button below to link your account.\n"
        f"├ {ce('user')} Daily free credits: <code>{DAILY_CREDITS}</code> (check <code>/profile</code>)\n"
        f"├ {ce('gift')} Invite friends from your profile to earn bonus credits.\n"
        f"├ {ce('book')} Need help? Tap <b>How To Use</b> or send <code>/help</code>.\n"
        f"│\n"
        f"└─ <i>All downloads will automatically self-delete after {AUTO_DELETE_TIME}s.</i>",
        reply_markup=get_bottom_keyboard(user_id)
    )
    # নতুন ইউজারকে সাথে সাথে স্টেপ-বাই-স্টেপ গাইড দেখানো
    if is_new:
        text, markup = help_page(1)
        await message.reply_text(text, reply_markup=markup)

@bot.on_message(filters.command("login") & filters.private & filters.incoming)
async def public_login_cmd(_, message: Message):
    await begin_login(message.from_user.id)

@bot.on_message(filters.command("cancel") & filters.private & filters.incoming)
async def cancel_handler(_, message: Message):
    user_id = message.from_user.id
    user_states.pop(user_id, None)
    login_clients.pop(user_id, None)
    pending_target.pop(user_id, None)
    await message.reply_text(f"{ce('check')} <b>Current operation cancelled.</b>", reply_markup=get_bottom_keyboard(user_id))

@bot.on_message(filters.command("ping") & filters.private & filters.incoming)
async def ping_handler(_, message: Message):
    t0 = time.time()
    msg = await message.reply_text(f"{ce('spark')} <i>Measuring round-trip latency...</i>")
    await msg.edit_text(f"{ce('rocket')} <b>Server Latency:</b> <code>{(time.time() - t0) * 1000:.1f}ms</code>")

@bot.on_message(filters.command("storage") & filters.private & filters.incoming)
async def storage_cmd_handler(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text(f"{ce('cross')} <b>Access Denied.</b>", reply_markup=get_bottom_keyboard(message.from_user.id))
    st = get_storage_stats()
    filled = int(st["percent"] / 10)
    bar = ce("bar_fill") * filled + ce("bar_empty") * (10 - filled)
    text = (
        f"┌── {ce('disk')} <b>SERVER STORAGE OVERVIEW</b>\n"
        f"│\n"
        f"├ {ce('chart')} <b>Disk Usage:</b> ⌠ {bar} ⌡ <code>{st['percent']:.1f}%</code>\n"
        f"├ {ce('box')} <b>Used Space:</b> <code>{human_bytes(st['used'])}</code>\n"
        f"├ {ce('online')} <b>Free Space:</b> <code>{human_bytes(st['free'])}</code>\n"
        f"├ {ce('cabinet')} <b>Total Capacity:</b> <code>{human_bytes(st['total'])}</code>\n"
        f"│\n"
        f"└ {ce('folder')} <b>Temp Downloads:</b> <code>{human_bytes(st['dl_size'])}</code> ({st['dl_files']} files)"
    )
    markup = create_custom_inline_markup([
        [{"text": "Clean Temp Files", "callback_data": "btn_clean_storage", "icon_custom_emoji_id": UI_EMOJIS["broom"], "style": "danger"}]
    ])
    await message.reply_text(text, reply_markup=markup)

@bot.on_message(filters.command("clean") & filters.private & filters.incoming)
async def clean_cmd_handler(_, message: Message):
    if not is_any_admin(message.from_user.id):
        return await message.reply_text(f"{ce('cross')} <b>Access Denied.</b>", reply_markup=get_bottom_keyboard(message.from_user.id))

    msg = await message.reply_text(f"{ce('broom')} <i>Purging temporary storage cache...</i>")
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
        f"┌── {ce('check')} <b>STORAGE CACHE PURGED</b>\n"
        f"│\n"
        f"├ {ce('trash')} <b>Files Removed:</b> <code>{deleted_files}</code>\n"
        f"└─ {ce('disk')} <b>Space Freed:</b> <code>{human_bytes(freed_bytes)}</code>"
    )

# ----------------- WEB DASHBOARD ----------------- #

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Clipwell Control</title>
<style>
:root{--bg:#0b1020;--card:#131a2e;--line:#232c47;--tx:#e8ecf8;--mut:#8b96b8;--acc:#6c8cff;--ok:#34d399;--bad:#f87171;--warn:#fbbf24}
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
body{background:radial-gradient(900px 500px at 10% -10%,#1c2750 0,transparent 60%),var(--bg);color:var(--tx);min-height:100vh;padding:16px}
.wrap{max-width:760px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:center;margin:6px 0 16px}
.brand{font-size:20px;font-weight:800;letter-spacing:.3px}
.badge{font-size:11px;font-weight:700;padding:4px 10px;border-radius:99px;background:#0d3b2e;color:var(--ok)}
.tabs{display:flex;gap:6px;background:var(--card);border:1px solid var(--line);padding:4px;border-radius:12px;margin-bottom:14px}
.tab{flex:1;text-align:center;padding:9px;border-radius:9px;font-size:13px;font-weight:700;color:var(--mut);cursor:pointer}
.tab.on{background:var(--acc);color:#fff}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}
.lbl{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px}
.big{font-size:26px;font-weight:800;margin:4px 0 10px}
.bar{height:10px;background:var(--line);border-radius:6px;overflow:hidden}
.fill{height:100%;width:0;background:var(--ok);transition:width .4s}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}
.cell{background:#0e1427;border:1px solid var(--line);border-radius:10px;padding:10px}
.cell b{display:block;font-size:14px;margin-top:2px}
.task{background:#0e1427;border:1px solid var(--line);border-radius:10px;padding:10px;margin-top:10px}
.task .r{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:6px}
.empty{color:var(--mut);font-size:13px;text-align:center;padding:16px 0}
.btn{width:100%;padding:12px;border:0;border-radius:10px;background:var(--bad);color:#fff;font-weight:700;cursor:pointer}
.search{width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--line);background:#0e1427;color:var(--tx);font-size:15px;outline:none;margin-bottom:12px}
.search:focus{border-color:var(--acc)}
.user{display:flex;gap:12px;align-items:center;padding:12px;border-bottom:1px solid var(--line)}
.user:last-child{border:0}
.av{width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,var(--acc),#a66cff);display:flex;align-items:center;justify-content:center;font-weight:800;flex:none}
.info{flex:1;min-width:0}
.nm{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sub{font-size:12px;color:var(--mut);margin-top:2px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.chip{font-size:11px;padding:2px 8px;border-radius:6px;background:#0e1427;border:1px solid var(--line);color:var(--mut)}
.st{font-size:11px;font-weight:700;padding:3px 9px;border-radius:99px}
.st.ok{background:#0d3b2e;color:var(--ok)}.st.no{background:#4a1d1d;color:var(--bad)}
.hide{display:none}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><div class="brand">⚡ Clipwell Control</div><div class="badge">● ONLINE</div></div>
  <div class="tabs">
    <div class="tab on" id="t1" onclick="show(1)">Overview</div>
    <div class="tab" id="t2" onclick="show(2)">Users</div>
  </div>

  <div id="p1">
    <div class="card">
      <div class="lbl">Disk usage</div>
      <div class="big" id="pct">--%</div>
      <div class="bar"><div class="fill" id="fill"></div></div>
      <div class="grid">
        <div class="cell"><span class="lbl">Used</span><b id="used">--</b></div>
        <div class="cell"><span class="lbl">Free</span><b id="free">--</b></div>
        <div class="cell"><span class="lbl">Total</span><b id="total">--</b></div>
        <div class="cell"><span class="lbl">Temp cache</span><b id="temp">--</b></div>
      </div>
    </div>
    <div class="card">
      <div class="lbl">Live transfers</div>
      <div id="tasks"><div class="empty">No active transfers.</div></div>
    </div>
    <button class="btn" onclick="clean()">🧹 Clean download cache</button>
  </div>

  <div id="p2" class="hide">
    <input class="search" id="q" placeholder="🔎 Search by ID, name or @username" oninput="debounce()">
    <div class="card" style="padding:4px 4px"><div id="users"><div class="empty">Loading...</div></div></div>
  </div>
</div>

<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
function api(p, o) {
  return fetch(p + (p.includes('?') ? '&' : '?') + 'key=' + encodeURIComponent(KEY), o)
    .then(function (r) { if (r.status === 401) throw new Error('auth'); return r.json(); });
}
function esc(s) { var d = document.createElement('div'); d.textContent = (s == null) ? '' : String(s); return d.innerHTML; }
function hb(b) { if (!b) return '0 B'; var k = 1024, s = ['B','KB','MB','GB','TB'], i = Math.floor(Math.log(b) / Math.log(k)); return parseFloat((b / Math.pow(k, i)).toFixed(2)) + ' ' + s[i]; }
function ago(t) { if (!t) return 'never'; var s = Date.now() / 1000 - t; if (s < 60) return 'just now'; if (s < 3600) return Math.floor(s / 60) + 'm ago'; if (s < 86400) return Math.floor(s / 3600) + 'h ago'; return Math.floor(s / 86400) + 'd ago'; }
function show(n) {
  document.getElementById('p1').classList.toggle('hide', n !== 1);
  document.getElementById('p2').classList.toggle('hide', n !== 2);
  document.getElementById('t1').classList.toggle('on', n === 1);
  document.getElementById('t2').classList.toggle('on', n === 2);
  if (n === 2) loadUsers();
}
async function stats() {
  try {
    var d = await api('/api/stats');
    document.getElementById('pct').innerText = d.percent.toFixed(1) + '% full';
    var f = document.getElementById('fill');
    f.style.width = Math.min(100, d.percent) + '%';
    f.style.background = d.percent > 85 ? '#f87171' : d.percent > 65 ? '#fbbf24' : '#34d399';
    document.getElementById('used').innerText = hb(d.used);
    document.getElementById('free').innerText = hb(d.free);
    document.getElementById('total').innerText = hb(d.total);
    document.getElementById('temp').innerText = hb(d.dl_size) + ' (' + d.dl_files + ')';
    var box = document.getElementById('tasks');
    if (!d.tasks.length) { box.innerHTML = '<div class="empty">No active transfers.</div>'; return; }
    box.innerHTML = d.tasks.map(function (t) {
      return '<div class="task"><b style="font-size:13px">' + esc(t.name) + '</b>' +
        '<div class="bar" style="margin-top:8px"><div class="fill" style="width:' + Math.min(100, t.percent || 0) + '%;background:#6c8cff"></div></div>' +
        '<div class="r"><span>' + (t.percent || 0) + '%</span><span>' + hb(t.current) + ' / ' + hb(t.total) + '</span><span>' + esc(t.speed) + '</span></div></div>';
    }).join('');
  } catch (e) {
    if (e.message === 'auth') document.getElementById('tasks').innerHTML = '<div class="empty">Unauthorized. Open with ?key=YOUR_TOKEN</div>';
  }
}
var timer;
function debounce() { clearTimeout(timer); timer = setTimeout(loadUsers, 300); }
async function loadUsers() {
  var q = document.getElementById('q').value;
  var box = document.getElementById('users');
  try {
    var d = await api('/api/users?q=' + encodeURIComponent(q));
    if (!d.users.length) { box.innerHTML = '<div class="empty">No users found.</div>'; return; }
    box.innerHTML = d.users.map(function (u) {
      var nm = u.name || ('User ' + u.id);
      return '<div class="user"><div class="av">' + esc(nm.charAt(0).toUpperCase()) + '</div>' +
        '<div class="info"><div class="nm">' + esc(nm) + (u.username ? ' <span style="color:#8b96b8;font-weight:400">@' + esc(u.username) + '</span>' : '') + '</div>' +
        '<div class="sub">ID ' + esc(u.id) + ' · active ' + ago(u.last_active) + '</div>' +
        '<div class="chips"><span class="chip">💳 ' + u.credits + '</span><span class="chip">📥 ' + u.downloads + '</span><span class="chip">🎁 ' + u.referrals + '</span></div></div>' +
        '<span class="st ' + (u.banned ? 'no' : 'ok') + '">' + (u.banned ? 'BANNED' : 'ACTIVE') + '</span></div>';
    }).join('');
  } catch (e) {
    box.innerHTML = '<div class="empty">Unauthorized. Open with ?key=YOUR_TOKEN</div>';
  }
}
async function clean() {
  if (!confirm('Delete all temporary download files?')) return;
  try { var d = await api('/api/clean', { method: 'POST' }); alert('Cleaned ' + hb(d.freed)); stats(); }
  catch (e) { alert('Failed'); }
}
stats(); setInterval(stats, 3000);
</script>
</body>
</html>
"""

# ----------------- 📺 ADS MINI APP PAGE ----------------- #

HTML_AD = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Watch Ad</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src="https://sad.adsgram.ai/js/sad.min.js"></script>
<style>
:root{--bg:#0b1020;--card:#131a2e;--line:#232c47;--tx:#e8ecf8;--mut:#8b96b8;--acc:#6c8cff;--ok:#34d399;--bad:#f87171}
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
body{background:radial-gradient(800px 480px at 50% -10%,#1f2c5e 0,transparent 60%),var(--bg);color:var(--tx);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:30px 22px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.45)}
.ico{width:88px;height:88px;margin:0 auto 18px;border-radius:50%;background:linear-gradient(135deg,var(--acc),#a66cff);display:flex;align-items:center;justify-content:center;font-size:40px;box-shadow:0 0 0 9px rgba(108,140,255,.12)}
h1{font-size:21px;font-weight:800;margin-bottom:8px}
p{font-size:14px;line-height:1.55;color:var(--mut);margin-bottom:22px}
.bar{height:8px;background:var(--line);border-radius:6px;overflow:hidden;margin-bottom:16px;display:none}
.fill{height:100%;width:0;background:linear-gradient(90deg,#4f7bff,#34d399);transition:width .3s}
.btn{width:100%;padding:15px;border:0;border-radius:14px;font-size:16px;font-weight:800;color:#fff;background:linear-gradient(135deg,#4f7bff,#8a5cff);cursor:pointer}
.btn:disabled{opacity:.55}
.btn.alt{background:#232c47;margin-top:10px}
.msg{font-size:13px;margin-top:14px;min-height:18px;color:var(--mut)}
.msg.ok{color:var(--ok)}.msg.bad{color:var(--bad)}
.hide{display:none}
</style>
</head>
<body>
<div class="card">
  <div class="ico" id="ico">🎬</div>
  <h1 id="title">Loading...</h1>
  <p id="text">Please wait a moment.</p>
  <div class="bar" id="bar"><div class="fill" id="fill"></div></div>
  <button class="btn" id="go" disabled>Watch Ad</button>
  <button class="btn alt hide" id="skip">Continue anyway</button>
  <div class="msg" id="msg"></div>
</div>
<script>
var tg = window.Telegram.WebApp; tg.ready(); tg.expand();
var $ = function (id) { return document.getElementById(id); };
var cfg = null, busy = false;

async function post(p, b) {
  var r = await fetch(p, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(Object.assign({initData: tg.initData}, b || {}))});
  var j = {}; try { j = await r.json(); } catch (e) {}
  j._ok = r.ok; return j;
}
function say(t, cls) { var m = $('msg'); m.textContent = t || ''; m.className = 'msg ' + (cls || ''); }

async function init() {
  var c = await post('/api/ad_config');
  if (!c._ok) { $('title').textContent = 'Oops'; $('text').textContent = 'Please open this page from the bot.'; return; }
  cfg = c;
  $('title').textContent = c.title;
  $('text').textContent = c.text;
  $('go').disabled = false;
}

async function finish(failed) {
  say('Unlocking your download...');
  var r = await post('/api/ad_done', {failed: !!failed});
  if (r.ok) {
    $('ico').textContent = '✅'; $('title').textContent = 'Done!';
    $('text').textContent = 'Your download is starting in the chat.';
    $('go').classList.add('hide'); $('skip').classList.add('hide');
    say('', 'ok'); setTimeout(function () { tg.close(); }, 1200);
  } else {
    busy = false; $('go').disabled = false;
    say(r.error === 'too_fast' ? 'Please watch the full ad.' : 'Something went wrong. Try again.', 'bad');
  }
}

function runTest() {
  var total = Math.max(5, (cfg.min_seconds || 5) + 1), left = total;
  $('bar').style.display = 'block'; $('go').textContent = 'Ad playing... ' + left + 's';
  var t = setInterval(function () {
    left--; $('fill').style.width = ((total - left) / total * 100) + '%';
    $('go').textContent = 'Ad playing... ' + Math.max(left, 0) + 's';
    if (left <= 0) { clearInterval(t); finish(false); }
  }, 1000);
}

async function watch() {
  if (busy || !cfg) return;
  busy = true; $('go').disabled = true; say('');
  if (cfg.provider !== 'adsgram') return runTest();
  try {
    if (!window.Adsgram || !cfg.block_id) throw {state: 'load', description: 'not configured'};
    var ctl = window.Adsgram.init({blockId: String(cfg.block_id)});
    var res = await ctl.show();
    if (res && res.done === false) throw res;
    await finish(false);
  } catch (e) {
    busy = false; $('go').disabled = false;
    var noFill = e && (e.state === 'load' || /no ?ad|not ?found|not ?configured/i.test(e.description || ''));
    if (noFill && cfg.fail_open) {
      say('No ad available right now.', 'bad'); $('skip').classList.remove('hide');
    } else {
      say('Please watch the full ad to continue.', 'bad');
    }
  }
}
$('go').onclick = watch;
$('skip').onclick = function () { finish(true); };
init();
</script>
</body>
</html>
"""

# ----------------- WEB SERVER HANDLERS ----------------- #

def clean_download_dir() -> int:
    freed = 0
    if os.path.exists(DOWNLOAD_DIR):
        for filename in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, filename)
            try:
                if os.path.isfile(fp) or os.path.islink(fp):
                    freed += os.path.getsize(fp)
                    os.unlink(fp)
                elif os.path.isdir(fp):
                    shutil.rmtree(fp)
            except Exception as e:
                logger.error(f"Failed to delete {fp}: {e}")
    return freed

def _authed(request) -> bool:
    return bool(DASH_TOKEN) and request.query.get("key", "") == DASH_TOKEN

def _unauth():
    return web.json_response({"error": "unauthorized"}, status=401)

async def web_index(request):
    return web.Response(text=HTML_DASHBOARD, content_type="text/html")

async def web_health(request):
    return web.Response(text="OK")

async def web_ad(request):
    return web.Response(text=HTML_AD, content_type="text/html")

async def api_ad_config(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    uid = verify_init_data(body.get("initData", ""))
    if not uid:
        return _unauth()
    ad_started[uid] = time.time()
    a = SETTINGS["ads"]
    return web.json_response({
        "provider": a["provider"], "block_id": a["block_id"],
        "title": a["title"], "text": a["text"],
        "min_seconds": a["min_seconds"], "fail_open": a["fail_open"],
    })

async def api_ad_done(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    uid = verify_init_data(body.get("initData", ""))
    if not uid:
        return _unauth()
    a = SETTINGS["ads"]
    started = ad_started.pop(uid, 0)
    if not started or time.time() - started < a["min_seconds"]:
        return web.json_response({"ok": False, "error": "too_fast"}, status=400)
    failed = bool(body.get("failed"))
    if failed and not a["fail_open"]:
        return web.json_response({"ok": False, "error": "no_fallback"}, status=400)
    u = await asyncio.to_thread(sync_get_user, uid)
    u["ad_pass"] = time.time()
    u["since_ad"] = 0
    if not failed:
        u["ads_seen"] = u.get("ads_seen", 0) + 1
    await asyncio.to_thread(sync_save_user, uid)
    asyncio.create_task(resume_pending(uid))
    return web.json_response({"ok": True})

async def api_stats(request):
    if not _authed(request):
        return _unauth()
    st = await asyncio.to_thread(get_storage_stats)
    tasks = []
    for _, t in list(active_tasks.items()):
        tasks.append({
            "name": t.get("name", ""),
            "type": t.get("type", "download"),
            "current": t.get("current", 0),
            "total": t.get("total", 0),
            "percent": t.get("percent", 0),
            "speed": t.get("speed", "--"),
        })
    st["tasks"] = tasks
    return web.json_response(st)

async def api_users(request):
    if not _authed(request):
        return _unauth()
    rows = await asyncio.to_thread(sync_search_users, request.query.get("q", ""), 100)
    return web.json_response({"users": [{
        "id": x.get("user_id"),
        "name": x.get("name", ""),
        "username": x.get("username", ""),
        "credits": x.get("credits", 0),
        "downloads": x.get("downloads", 0),
        "referrals": x.get("referrals", 0),
        "banned": bool(x.get("banned")),
        "last_active": x.get("last_active", 0),
        "joined": x.get("joined", 0),
    } for x in rows]})

async def api_clean(request):
    if not _authed(request):
        return _unauth()
    freed = await asyncio.to_thread(clean_download_dir)
    return web.json_response({"freed": freed})

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", web_index)
    app.router.add_get("/health", web_health)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/users", api_users)
    app.router.add_post("/api/clean", api_clean)
    app.router.add_get("/ad", web_ad)
    app.router.add_post("/api/ad_config", api_ad_config)
    app.router.add_post("/api/ad_done", api_ad_done)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web dashboard running on port {PORT}")

# ----------------- MAIN ----------------- #

async def main():
    global BOT_USERNAME

    await asyncio.to_thread(sync_load_settings)
    await start_web_server()

    await bot.start()
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    logger.info(f"Bot started as @{BOT_USERNAME}")

    try:
        await bot.set_bot_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("help", "How to use the bot"),
            BotCommand("login", "Connect your Telegram account"),
            BotCommand("profile", "My profile & credits"),
            BotCommand("redeem", "Redeem a promo code"),
            BotCommand("ping", "Check latency"),
            BotCommand("cancel", "Cancel current operation"),
        ])
    except Exception as e:
        logger.warning(f"set_bot_commands failed: {e}")

    await init_session_pool()

    from hydrogram import idle
    await idle()

    await bot.stop()

if __name__ == "__main__":
    bot.run(main())