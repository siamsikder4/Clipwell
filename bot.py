import os
import re
import time
import json
import asyncio
import logging
import hashlib
import shutil
from aiohttp import web
import yt_dlp
from hydrogram import Client, filters
from hydrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ForceReply,
    BotCommand
)
from hydrogram.errors import SessionPasswordNeeded

# Auto Setup FFmpeg
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8952918726:AAGnKZm-S8hmBaWzltPfrdWRcyVHGVx44d0").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()

OWNER_ID = 6142774415
PORT = int(os.environ.get("PORT", "8080"))

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Database Initialization
db = None
if FIREBASE_KEY_RAW:
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        cred_dict = json.loads(FIREBASE_KEY_RAW)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase: Connected successfully")
    except Exception as e:
        logger.error(f"Firebase Init Error: {e}")

# Bot Client
bot = Client(
    "bot_instance",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    workers=16
)

# Global States
admin_states = {}
login_clients = {}
progress_status = {}
loaded_user_clients = []
video_cache = {}
search_cache = {}

# ----------------- TOP SONGS DATA ----------------- #
TOP_SONGS = {
    "gb": [
        "Dolly Parton - Jolene",
        "Ella Langley - Choosin' Texas",
        "Dolly Parton - 9 to 5",
        "Drake - Slime You Out",
        "Dolly Parton - Islands In the Stream",
        "Morgan Wallen - Last Night",
        "KAROL G - Si Antes Te Hubiera Conocido",
        "Dolly Parton - I Will Always Love You",
        "Lil Baby - California Breeze",
        "Ella Langley - That's Why We Fight"
    ],
    "us": [
        "Post Malone - I Had Some Help",
        "Shaboozey - A Bar Song (Tipsy)",
        "Kendrick Lamar - Not Like Us",
        "Sabrina Carpenter - Espresso",
        "Tommy Richman - MILLION DOLLAR BABY",
        "Billie Eilish - BIRDS OF A FEATHER",
        "Chappell Roan - Good Luck, Babe!",
        "Teddy Swims - Lose Control",
        "Hozier - Too Sweet",
        "Benson Boone - Beautiful Things"
    ],
    "uz": [
        "Yulduz Usmonova - Muhabbat",
        "Shohruhxon - Aldamadim",
        "Jaloliddin Ahmadaliyev - Janonim",
        "Doston Ergashev - Qora ko'z",
        "Hamdam Sobirov - Yig'lama qiz",
        "Ozoda Nursaidova - Alam",
        "Rayhon - Yurak",
        "Munisa Rizayeva - Yovvoyi",
        "Janob Rasul - Tamanno",
        "Sardor Tairov - Malikam"
    ],
    "ru": [
        "Miyagi & Andy Panda - Minor",
        "Anna Asti - Царица",
        "MACAN - Спой",
        "Jakone, A-Sen - По весне",
        "Xcho - Ты и Я",
        "GAYAZOV$ BROTHER$ - Малиновая лада",
        "HammAli & Navai - Прятки",
        "JONY - Комета",
        "Люся Чеботина - Солнце Монако",
        "Rauf & Faik - Детство"
    ],
    "kz": [
        "Jah Khalib - Медина",
        "Raim - Самая вышка",
        "De Lacure - Келші жаным",
        "Kalifarniya - Puertorika",
        "Sadraddin - Ant",
        "Mirani - Ainalaiyn",
        "Qanay - Boztorgai",
        "Alashuly - Tulpar",
        "Kairat Nurtas - Sen meni tusinbedin",
        "Erke Esmahan - Kaida"
    ],
    "tr": [
        "Semicenk - Canın Sağ Olsun",
        "Mert Demir - Ateşe Düştüm",
        "Dedublüman - Belki",
        "Mabel Matiz - Antidepresan",
        "KÖFN - Bir Tek Sana Müptelayım",
        "Reynmen - Renklensin",
        "Uzi - Arasan Da",
        "Lvbel C5 - Dacia",
        "BLOK3 - AFFETMEM",
        "Simge - Aşkın Olayım"
    ]
}

# ----------------- SESSION POOL MANAGER ----------------- #

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
            new_pool.append((s['doc_id'], s['account_name'], cl))
        except Exception as e:
            logger.warning(f"Failed to start pool client {s['doc_id']}: {e}")
    
    for _, _, old_cl in loaded_user_clients:
        try:
            if old_cl.is_connected:
                await old_cl.stop()
        except Exception:
            pass
    loaded_user_clients = new_pool
    logger.info(f"Session Pool Ready: {len(loaded_user_clients)} active userbots.")

# ----------------- DATABASE HELPERS ----------------- #

def sync_add_session(session_str: str, name: str):
    if not db:
        return False, "Database offline"
    try:
        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "created_at": time.time()
        })
        return True, "Success"
    except Exception as e:
        return False, str(e)

def sync_get_all_sessions():
    if not db:
        return []
    try:
        docs = db.collection("telegram_sessions").stream()
        sessions = []
        for doc in docs:
            data = doc.to_dict()
            sessions.append({
                "doc_id": doc.id,
                "session_string": data.get("session_string"),
                "account_name": data.get("account_name", "Unknown")
            })
        return sessions
    except Exception as e:
        logger.error(f"Get Sessions Error: {e}")
        return []

def sync_delete_session(doc_id: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").document(doc_id).delete()
        return True
    except Exception as e:
        return False

def sync_track_user(user_id: int, username: str, name: str) -> bool:
    if not db:
        return False
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        doc_ref.set({
            "user_id": int(user_id),
            "username": username or "N/A",
            "name": name or "N/A",
            "last_active": time.time()
        }, merge=True)
        return True
    except Exception:
        return False

def sync_log_url(user_id: int, user_name: str, url: str, platform: str):
    if not db:
        return
    try:
        db.collection("submitted_urls").add({
            "user_id": user_id,
            "user_name": user_name or "Unknown",
            "url": url,
            "platform": platform,
            "timestamp": time.time()
        })
    except Exception:
        pass

def sync_get_active_urls(limit: int = 10):
    if not db:
        return []
    try:
        from firebase_admin import firestore
        docs = db.collection("submitted_urls").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [doc.to_dict() for doc in docs]
    except Exception:
        return []

def sync_increment_downloads(platform: str, count: int = 1, photos: int = 0, videos: int = 0, audios: int = 0):
    if not db or count <= 0:
        return
    try:
        from firebase_admin import firestore
        update_payload = {
            "total_downloads": firestore.Increment(count),
            f"count_{platform.lower()}": firestore.Increment(count)
        }
        if platform.lower() == "telegram":
            if photos > 0:
                update_payload["tg_photos"] = firestore.Increment(photos)
            if videos > 0:
                update_payload["tg_videos"] = firestore.Increment(videos)
            if audios > 0:
                update_payload["tg_audios"] = firestore.Increment(audios)
        db.collection("bot_stats").document("global_analytics").set(update_payload, merge=True)
    except Exception:
        pass

def sync_increment_user_downloads(user_id: int, platform: str, count: int = 1, photos: int = 0, videos: int = 0, audios: int = 0):
    if not db or count <= 0:
        return
    try:
        from firebase_admin import firestore
        update_data = {
            "total_downloads": firestore.Increment(count),
            "last_active": time.time()
        }
        if platform.lower() == "telegram":
            update_data["telegram_downloads"] = firestore.Increment(count)
            if photos > 0:
                update_data["tg_photos"] = firestore.Increment(photos)
            if videos > 0:
                update_data["tg_videos"] = firestore.Increment(videos)
            if audios > 0:
                update_data["tg_audios"] = firestore.Increment(audios)
        else:
            update_data["social_downloads"] = firestore.Increment(count)
        db.collection("bot_users").document(str(user_id)).set(update_data, merge=True)
    except Exception:
        pass

def sync_get_stats():
    if not db:
        return 0, {}, 0, 0, 0
    try:
        stat_doc = db.collection("bot_stats").document("global_analytics").get()
        total_dl = 0
        tg_photos, tg_videos, tg_audios = 0, 0, 0
        platform_counts = {"Telegram": 0, "YouTube": 0, "TikTok": 0, "Instagram": 0, "Facebook": 0, "Others": 0}

        if stat_doc.exists:
            data = stat_doc.to_dict()
            total_dl = data.get("total_downloads", 0)
            tg_photos = data.get("tg_photos", 0)
            tg_videos = data.get("tg_videos", 0)
            tg_audios = data.get("tg_audios", 0)
            platform_counts = {
                "Telegram": data.get("count_telegram", 0),
                "YouTube": data.get("count_youtube", 0),
                "TikTok": data.get("count_tiktok", 0),
                "Instagram": data.get("count_instagram", 0),
                "Facebook": data.get("count_facebook", 0),
                "Others": data.get("count_others", 0)
            }
        return total_dl, platform_counts, tg_photos, tg_videos, tg_audios
    except Exception:
        return 0, {}, 0, 0, 0

async def generate_admin_dashboard():
    total_dl, platforms, tg_photos, tg_videos, tg_audios = await asyncio.to_thread(sync_get_stats)
    all_sess = await asyncio.to_thread(sync_get_all_sessions)
    db_status = "Online 🟢" if db else "Offline 🔴"

    text = (
        "⚙️ **Admin Dashboard**\n"
        "────────────────────\n"
        f"• **Database:** `{db_status}`\n"
        f"• **Active Sessions:** `{len(all_sess)}`\n"
        f"• **Total Downloads:** `{total_dl:,}`\n\n"
        "📊 **Platform Insights**\n"
        f"• **Telegram:** `{platforms.get('Telegram', 0):,}`\n"
        f"  └ 🖼️ `{tg_photos:,}` • 🎥 `{tg_videos:,}` • 🎵 `{tg_audios:,}`\n"
        f"• **YouTube:** `{platforms.get('YouTube', 0):,}`\n"
        f"• **TikTok:** `{platforms.get('TikTok', 0):,}`\n"
        f"• **Instagram:** `{platforms.get('Instagram', 0):,}`\n"
        f"• **Facebook:** `{platforms.get('Facebook', 0):,}`\n"
        f"• **Others:** `{platforms.get('Others', 0):,}`\n"
        "────────────────────"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Add via Phone (OTP)", callback_data="btn_login_account"),
            InlineKeyboardButton("➕ Paste String", callback_data="btn_add_session")
        ],
        [
            InlineKeyboardButton(f"📁 Sessions ({len(all_sess)})", callback_data="btn_list_sessions"),
            InlineKeyboardButton("🗑️ Remove Session", callback_data="btn_del_menu")
        ],
        [
            InlineKeyboardButton("🔗 Activity Log", callback_data="btn_view_urls"),
            InlineKeyboardButton("🔄 Refresh", callback_data="btn_refresh_admin")
        ]
    ])
    return text, keyboard

# ----------------- UTILITY & DOWNLOADER ----------------- #

def get_aria2_opts():
    if shutil.which("aria2c"):
        return {
            'external_downloader': {'default': 'aria2c'},
            'external_downloader_args': {
                'aria2c': [
                    '--min-split-size=1M',
                    '--max-connection-per-server=16',
                    '--split=16',
                    '--summary-interval=0',
                    '--allow-overwrite=true'
                ]
            }
        }
    return {}

def get_yt_cookies():
    cookie_path = "cookies.txt"
    if os.path.exists(cookie_path):
        return {'cookiefile': cookie_path}
    return {}

async def progress_bar(current, total, status_msg, action_name, user_id):
    if not total or total <= 0:
        return
    now = time.time()
    user_data = progress_status.get(user_id, {})
    last_update = user_data.get("last_time", 0)

    if (now - last_update < 3.0) and current < total:
        return

    progress_status[user_id] = {"last_time": now}
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "▰" * filled + "▱" * (10 - filled)

    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)

    text = (
        f"⏳ **{action_name}**\n\n"
        f"`{bar}` **{percentage:.1f}%**\n"
        f"⚡ **Size:** `{curr_mb:.1f} MB / {tot_mb:.1f} MB`"
    )
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass

async def auto_delete_messages(chat_id: int, message_ids: list, delay_seconds: int = 120):
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception:
        pass

def sanitize_youtube_url(url: str) -> str:
    match = re.search(r'(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})', url)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    return url.strip()

def search_youtube_videos(query: str, limit: int = 5):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = res.get('entries', [])
            results = []
            for e in entries:
                if not e:
                    continue
                dur = e.get('duration') or 0
                dur_str = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else ""
                results.append({
                    "id": e.get('id'),
                    "title": e.get('title'),
                    "url": f"https://www.youtube.com/watch?v={e.get('id')}",
                    "duration": dur_str
                })
            return results
    except Exception as e:
        logger.error(f"Search YouTube Error: {e}")
        return []

def extract_youtube_metadata(url: str):
    clean_url = sanitize_youtube_url(url)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'web']
            }
        }
    }
    ydl_opts.update(get_yt_cookies())

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
    except Exception as e:
        logger.warning(f"Metadata extract failed: {e}. Retrying fallback...")
        try:
            ydl_opts_fallback = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'skip_download': True
            }
            with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
                info = ydl.extract_info(clean_url, download=False)
        except Exception as e2:
            logger.error(f"Metadata fallback failed: {e2}")
            return None

    if not info:
        return None

    title = str(info.get('title') or "YouTube Video")
    thumbnail = info.get('thumbnail')
    formats = info.get('formats', [])

    available_heights = set()
    for f in formats:
        h = f.get('height')
        vcodec = f.get('vcodec')
        if h and vcodec and vcodec != 'none':
            available_heights.add(int(h))

    standard_resolutions = [1080, 720, 480, 360, 240, 144]
    available_qualities = [res for res in standard_resolutions if res in available_heights]

    if not available_qualities and available_heights:
        available_qualities = sorted(list(available_heights), reverse=True)[:4]

    if not available_qualities:
        available_qualities = [720, 360]

    return {
        "title": title,
        "thumbnail": thumbnail,
        "qualities": available_qualities
    }

def download_youtube_with_quality(url: str, user_id: int, height: int = None):
    clean_url = sanitize_youtube_url(url)
    timestamp = int(time.time())
    prefix = f"{user_id}_{timestamp}_yt_"
    out_template = os.path.join(DOWNLOAD_DIR, f"{prefix}%(id)s.%(ext)s")

    if height:
        fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
    else:
        fmt = "bestvideo+bestaudio/best"

    ydl_opts = {
        'format': fmt,
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'web']
            }
        },
        'postprocessor_args': {
            'Merger': ['-movflags', '+faststart']
        },
        'buffersize': 1024 * 1024 * 16,
        'max_filesize': 1950 * 1024 * 1024,
    }
    ydl_opts.update(get_yt_cookies())

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            title = str(info.get('title') or "Video") if info else "Video"
            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix):
                    return os.path.join(DOWNLOAD_DIR, fname), title
    except Exception as e:
        logger.warning(f"Download failed: {e}. Retrying fallback...")
        try:
            ydl_opts_fallback = {
                'format': fmt,
                'outtmpl': out_template,
                'merge_output_format': 'mp4',
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True
            }
            with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
                info = ydl.extract_info(clean_url, download=True)
                title = str(info.get('title') or "Video") if info else "Video"
                for fname in os.listdir(DOWNLOAD_DIR):
                    if fname.startswith(prefix):
                        return os.path.join(DOWNLOAD_DIR, fname), title
        except Exception as e2:
            logger.error(f"Fallback download failed: {e2}")

    return None, None

def download_direct_social_best(url: str, user_id: int):
    clean_url = url.strip()
    timestamp = int(time.time())
    prefix = f"{user_id}_{timestamp}_soc_"
    out_template = os.path.join(DOWNLOAD_DIR, f"{prefix}%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'concurrent_fragment_downloads': 4,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
        },
        'postprocessor_args': {
            'Merger': ['-movflags', '+faststart']
        },
        'buffersize': 1024 * 1024 * 16,
        'max_filesize': 1950 * 1024 * 1024,
    }
    
    ydl_opts.update(get_aria2_opts())

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            title = str(info.get('title') or "Social Video") if info else "Social Video"
            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix):
                    return os.path.join(DOWNLOAD_DIR, fname), title
    except Exception as e:
        logger.error(f"Social download error: {e}")

    return None, None

def extract_and_download_social_audio(url: str, user_id: int):
    clean_url = sanitize_youtube_url(url)
    timestamp = int(time.time())
    prefix = f"{user_id}_{timestamp}_aud_"
    out_template = os.path.join(DOWNLOAD_DIR, f"{prefix}%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'web']
            }
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'max_filesize': 500 * 1024 * 1024,
    }
    ydl_opts.update(get_yt_cookies())

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            title = str(info.get('title') or "Audio") if info else "Audio"

            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix) and fname.endswith(".mp3"):
                    return os.path.join(DOWNLOAD_DIR, fname), title

            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix):
                    return os.path.join(DOWNLOAD_DIR, fname), title
    except Exception as e:
        logger.warning(f"Audio extraction error: {e}")

    try:
        ydl_opts_fallback = {
            'format': 'bestaudio/best',
            'outtmpl': out_template,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True
        }
        with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            title = str(info.get('title') or "Audio") if info else "Audio"

            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(prefix):
                    return os.path.join(DOWNLOAD_DIR, fname), title
    except Exception as e:
        logger.error(f"Fallback audio error: {e}")

    return None, None

def generate_top_markup(country_code="gb"):
    flags = [
        ("🇺🇿", "uz"),
        ("🇷🇺", "ru"),
        ("🇬🇧", "gb"),
        ("🇺🇸", "us"),
        ("🇰🇿", "kz"),
        ("🇹🇷", "tr")
    ]
    flag_row = []
    for flag, code in flags:
        label = f"{flag} ✅" if code == country_code else flag
        flag_row.append(InlineKeyboardButton(label, callback_data=f"top_country_{code}"))

    songs = TOP_SONGS.get(country_code, TOP_SONGS["gb"])
    buttons = [flag_row]
    for s in songs:
        buttons.append([InlineKeyboardButton(s, callback_data=f"top_search_{hashlib.md5(s.encode()).hexdigest()[:8]}")])
        search_cache[hashlib.md5(s.encode()).hexdigest()[:8]] = s

    buttons.append([InlineKeyboardButton("➡️", callback_data="top_next")])
    return InlineKeyboardMarkup(buttons)

# ----------------- MESSAGE HANDLER ----------------- #

@bot.on_message(filters.private)
async def private_message_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    text_str = (message.text or message.caption or "").strip()

    asyncio.create_task(asyncio.to_thread(sync_track_user, user_id, user.username, user.first_name))

    # Admin Panel Command
    if text_str.startswith("/admin") or text_str.startswith("/panel"):
        if user_id != OWNER_ID:
            await message.reply_text("⛔ **Access restricted.** Admin only.")
            return
        dash_text, dash_markup = await generate_admin_dashboard()
        await message.reply_text(dash_text, reply_markup=dash_markup)
        return

    # Waiting Session String
    if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
        admin_states.pop(user_id, None)
        status_msg = await message.reply_text("🔄 Verifying session string...")

        test_client = Client(f"test_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
        try:
            await test_client.start()
            me = await test_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await test_client.stop()

            success, err_msg = await asyncio.to_thread(sync_add_session, text_str, acc_name)
            if success:
                asyncio.create_task(init_session_pool())
                await status_msg.edit_text(f"✅ **Session saved successfully!**\n\n👤 **Account:** `{acc_name}`")
            else:
                await status_msg.edit_text(f"⚠️ **Database Error:** `{err_msg}`")
        except Exception as e:
            await status_msg.edit_text(f"❌ **Invalid Session String:**\n`{str(e)}`")
        return

    # Login Phone
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_PHONE":
        admin_states.pop(user_id, None)
        phone_number = text_str.replace(" ", "").strip()
        status_msg = await message.reply_text("⏳ Requesting Telegram OTP code...")

        temp_client = Client(f"login_{int(time.time())}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await temp_client.connect()
            code_info = await temp_client.send_code(phone_number)
            login_clients[user_id] = {
                "client": temp_client,
                "phone": phone_number,
                "phone_code_hash": code_info.phone_code_hash
            }
            admin_states[user_id] = "LOGIN_OTP"
            await status_msg.edit_text(
                "📩 **Enter the OTP code received:**\n\n"
                "💡 *Tip:* Put a space between digits (e.g. `1 2 3 4 5`).",
                reply_markup=ForceReply(selective=True)
            )
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            await status_msg.edit_text(f"❌ **Failed to send OTP:**\n`{str(e)}`")
        return

    # Login OTP
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_OTP":
        otp_code = text_str.replace(" ", "").replace("-", "").strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("⚠️ **Session expired.** Please restart login.")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ Verifying code...")

        try:
            await temp_client.sign_in(session_data["phone"], session_data["phone_code_hash"], otp_code)
            session_string = await temp_client.export_session_string()
            me = await temp_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)

            await asyncio.to_thread(sync_add_session, session_string, acc_name)
            asyncio.create_task(init_session_pool())
            await status_msg.edit_text(f"✅ **Account Connected!**\n\n👤 **Name:** `{acc_name}`")
        except SessionPasswordNeeded:
            admin_states[user_id] = "LOGIN_2FA"
            await status_msg.edit_text("🔐 **Two-Step Verification Enabled**\n\nPlease reply with your 2FA password:", reply_markup=ForceReply(selective=True))
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ **Login Failed:**\n`{str(e)}`")
        return

    # Login 2FA
    if user_id == OWNER_ID and admin_states.get(user_id) == "LOGIN_2FA":
        password = text_str.strip()
        session_data = login_clients.get(user_id)

        if not session_data:
            admin_states.pop(user_id, None)
            await message.reply_text("⚠️ **Session expired.** Please restart login.")
            return

        temp_client = session_data["client"]
        status_msg = await message.reply_text("⏳ Checking password...")

        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            me = await temp_client.get_me()
            acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
            await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)

            await asyncio.to_thread(sync_add_session, session_string, acc_name)
            asyncio.create_task(init_session_pool())
            await status_msg.edit_text(f"✅ **Account Connected!**\n\n👤 **Name:** `{acc_name}`")
        except Exception as e:
            if temp_client.is_connected:
                await temp_client.disconnect()
            login_clients.pop(user_id, None)
            admin_states.pop(user_id, None)
            await status_msg.edit_text(f"❌ **2FA Verification Failed:**\n`{str(e)}`")
        return

    # Start Command (/start)
    if text_str.startswith("/start"):
        bot_info = await client.get_me()
        bot_username = bot_info.username or "bot"

        start_text = (
            "**Hello! 🐥**\n"
            f"I'm @{bot_username} – a bot for **downloading photos/videos** and searching for music.\n\n"
            "__Send me:__\n"
            "– Link from **TikTok / Instagram / YouTube / Pinterest / Likee / Threads / VK and others**\n"
            "– Telegram post links (**Public & Restricted**)\n"
            "– Song title or artist name\n"
            "– Voice message, video, circle\n"
            "– Lyrics from the song\n\n"
            "__And I will download for you: photo, video, sound, song or lyrics.__\n\n"
            "/help – help\n"
            "/lang – change language\n"
            "/top – top music\n"
            "/my – your playlist\n\n"
            "**Add me to the group**, send me a link to the video – and I'll upload it directly to the chat. 🚀"
        )

        buttons = [
            [InlineKeyboardButton("➕ Add to chat", url=f"https://t.me/{bot_username}?startgroup=true")]
        ]

        if user_id == OWNER_ID:
            buttons.append([InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="btn_admin_shortcut")])

        await message.reply_text(start_text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Menu: Popular Songs (/top)
    if text_str.startswith("/top"):
        try:
            await message.react("🫡")
        except Exception:
            pass
        markup = generate_top_markup("gb")
        await message.reply_text("🎧 **TOP Popular Songs**", reply_markup=markup)
        return

    # Menu: Your Playlist (/my)
    if text_str.startswith("/my"):
        try:
            await message.react("🫡")
        except Exception:
            pass
        my_text = (
            "🎵 **Your Playlist & Recent Downloads**\n"
            "────────────────────\n"
            "• You don't have any saved tracks yet.\n\n"
            "💡 *Tip:* Send any song name or music link to search and build your playlist!"
        )
        await message.reply_text(my_text)
        return

    # Menu: Change Language (/lang)
    if text_str.startswith("/lang"):
        try:
            await message.react("🫡")
        except Exception:
            pass
        lang_text = "🌐 **Select your preferred language:**"
        lang_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇺🇸 English", callback_data="set_lang_en"),
                InlineKeyboardButton("🇧🇩 বাংলা", callback_data="set_lang_bn")
            ],
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru"),
                InlineKeyboardButton("🇺🇿 O'zbek", callback_data="set_lang_uz")
            ]
        ])
        await message.reply_text(lang_text, reply_markup=lang_buttons)
        return

    # Menu: How to use (/help)
    if text_str.startswith("/help"):
        try:
            await message.react("🫡")
        except Exception:
            pass
        help_text = (
            "📖 **How to download photos/videos:**\n\n"
            "1. Log in to the **TikTok / YouTube / Instagram / Facebook** app.\n"
            "2. Select the video or music you need.\n"
            "3. Click on the 🔄 **Share** button and click **Copy link**.\n"
            "4. Send the link to this bot – it will process & download it directly!\n\n"
            "🔍 **Music Search:** Type any artist name or song title directly."
        )
        await message.reply_text(help_text)
        return

    # Ping Command (/ping)
    if text_str.startswith("/ping"):
        start_t = time.time()
        p_msg = await message.reply_text("⚡ Ping...")
        end_t = time.time()
        await p_msg.edit_text(f"⚡ **Pong!** `{(end_t - start_t) * 1000:.1f}ms`")
        return

    # ----------------- TELEGRAM POST DOWNLOADER ----------------- #
    private_match = re.search(r"t\.me/c/(\d+)/(\d+)", text_str)
    public_match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text_str)

    if private_match or public_match:
        try:
            await message.react("🫡")
        except Exception:
            pass

        tg_url = text_str.strip()
        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, tg_url, "telegram"))

        status = await message.reply_text("⚡ **Connecting to Telegram...**")

        try:
            if not loaded_user_clients:
                await init_session_pool()

            if not loaded_user_clients:
                await status.edit_text("❌ **No active sessions found.** Please add one in `/admin`.")
                return

            if private_match:
                chat_id = int("-100" + private_match.group(1))
                msg_id = int(private_match.group(2))
            else:
                chat_id = public_match.group(1)
                msg_id = int(public_match.group(2))

            target_msg = None
            working_client = None

            for _, _, u_client in loaded_user_clients:
                try:
                    msg = await asyncio.wait_for(u_client.get_messages(chat_id, msg_id), timeout=6)
                    if msg and not msg.empty:
                        target_msg = msg
                        working_client = u_client
                        break
                except Exception:
                    continue

            if not target_msg or target_msg.empty:
                await status.edit_text("❌ **Post unavailable.** Ensure the session account has access to this chat.")
                return

            if not (target_msg.media or target_msg.video or target_msg.photo or target_msg.document or target_msg.audio or target_msg.voice):
                if target_msg.text:
                    await status.edit_text(f"📝 **Text Content:**\n\n{target_msg.text}")
                else:
                    await status.edit_text("⚠️ **No downloadable media found.**")
                return

            await status.edit_text("📥 **Fetching media file...**")
            download_path = await working_client.download_media(
                target_msg,
                progress=progress_bar,
                progress_args=(status, "Downloading from Telegram", user_id)
            )

            if not download_path or not os.path.exists(download_path):
                await status.edit_text("❌ **Failed to download media.**")
                return

            await status.edit_text("📤 **Uploading file...**")

            delete_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Delete Now", callback_data="btn_delete_this")
            ]])

            caption_header = f"**{target_msg.caption}**\n\n" if target_msg.caption else ""
            caption = f"{caption_header}⏱️ *Auto-deletes in 2 minutes.*"
            sent_msg = None

            if target_msg.video:
                sent_msg = await message.reply_video(
                    video=download_path,
                    caption=caption,
                    supports_streaming=True,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Video", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 0, 1, 0))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, 0, 1, 0))
            elif target_msg.photo:
                sent_msg = await message.reply_photo(
                    photo=download_path,
                    caption=caption,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Photo", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 1, 0, 0))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, 1, 0, 0))
            elif target_msg.audio:
                sent_msg = await message.reply_audio(
                    audio=download_path,
                    caption=caption,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Audio", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 0, 0, 1))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, 0, 0, 1))
            elif target_msg.voice:
                sent_msg = await message.reply_voice(
                    voice=download_path,
                    caption=caption,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Voice", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 0, 0, 1))
                asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "telegram", 1, 0, 0, 1))
            else:
                sent_msg = await message.reply_document(
                    document=download_path,
                    caption=caption,
                    reply_markup=delete_markup,
                    progress=progress_bar,
                    progress_args=(status, "Uploading Document", user_id)
                )
                asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "telegram", 1, 0, 0, 0))

            if sent_msg:
                asyncio.create_task(auto_delete_messages(message.chat.id, [sent_msg.id, status.id, message.id], delay_seconds=120))
            else:
                await status.delete()

            if os.path.exists(download_path):
                os.remove(download_path)

        except Exception as e:
            logger.error(f"Telegram Download Error: {e}")
            await status.edit_text(f"❌ **Error:** `{str(e)}`")
        return

    # ----------------- SOCIAL & YOUTUBE / SEARCH HANDLER ----------------- #
    url_pattern = re.search(r'(https?://[^\s]+)', text_str)

    # 1. URL Handler
    if url_pattern:
        try:
            await message.react("🫡")
        except Exception:
            pass

        url = url_pattern.group(0).strip()
        is_youtube = ("youtube.com" in url or "youtu.be" in url)

        # YouTube URL
        if is_youtube:
            status = await message.reply_text("⚡ **Analyzing YouTube video...**")
            asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, url, "YouTube"))

            meta = await asyncio.to_thread(extract_youtube_metadata, url)
            if not meta:
                await status.edit_text("❌ **Failed to fetch video.** Link may be private, restricted, or unavailable.")
                return

            title = meta.get("title", "Video")
            thumbnail = meta.get("thumbnail")
            qualities = meta.get("qualities", [720, 360])

            url_hash = hashlib.md5(f"{url}_{user_id}_{time.time()}".encode()).hexdigest()[:12]
            video_cache[url_hash] = {
                "url": url,
                "title": title,
                "platform": "YouTube"
            }

            buttons = []
            row = []
            for q in qualities:
                row.append(InlineKeyboardButton(f"📹 {q}p", callback_data=f"dl_res_{url_hash}_{q}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            buttons.append([InlineKeyboardButton("🔊 Audio", callback_data=f"dl_aud_{url_hash}")])

            caption_text = (
                f"📹 **{title}** ➔\n"
                f"👤 **#YouTube** ➔\n\n"
                "**Formats to download ↓**"
            )

            try:
                await status.delete()
                if thumbnail:
                    await message.reply_photo(photo=thumbnail, caption=caption_text, reply_markup=InlineKeyboardMarkup(buttons))
                else:
                    await message.reply_text(caption_text, reply_markup=InlineKeyboardMarkup(buttons))
            except Exception:
                await message.reply_text(caption_text, reply_markup=InlineKeyboardMarkup(buttons))
            return

        # Direct Social Media (TikTok, IG, FB)
        platform = "TikTok" if "tiktok" in url else "Instagram" if "instagram" in url else "Facebook" if ("facebook" in url or "fb.watch" in url) else "Social"
        asyncio.create_task(asyncio.to_thread(sync_log_url, user_id, user.first_name, url, platform))

        status = await message.reply_text(f"⚡ **Downloading {platform} video in best quality...**")

        file_path, title = await asyncio.to_thread(download_direct_social_best, url, user_id)

        if not file_path or not os.path.exists(file_path):
            await status.edit_text("❌ **Download Failed.** Link might be private, deleted, or unsupported.")
            return

        try:
            await status.edit_text(f"📤 **Uploading {platform} video...**")
            caption = f"📹 **{title}** ➔\n👤 **#{platform}** ➔"

            url_hash = hashlib.md5(f"{url}_{user_id}_{time.time()}".encode()).hexdigest()[:12]
            video_cache[url_hash] = {"url": url, "title": title, "platform": platform}
            social_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔊 Extract Audio", callback_data=f"dl_aud_{url_hash}")]])

            await message.reply_video(
                video=file_path,
                caption=caption,
                supports_streaming=True,
                reply_markup=social_markup,
                progress=progress_bar,
                progress_args=(status, f"Uploading ({platform})", user_id)
            )

            await status.delete()
            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, platform, 1, 0, 1, 0))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, platform, 1, 0, 1, 0))
        except Exception as e:
            logger.error(f"Social Direct Upload Error: {e}")
            await status.edit_text(f"❌ **Upload Failed:** `{str(e)}`")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        return

    # 2. Text Search Handler (Music/songs by name)
    if text_str and not text_str.startswith("/"):
        try:
            await message.react("🫡")
        except Exception:
            pass

        search_msg = await message.reply_text(f"🔎 **Searching for:** `{text_str}`...")
        results = await asyncio.to_thread(search_youtube_videos, text_str, 5)

        if not results:
            await search_msg.edit_text("❌ **No results found.** Please try with another keyword.")
            return

        text_lines = [f"🔍 **Search results for:** `{text_str}`\n"]
        num_row = []

        for idx, item in enumerate(results, 1):
            dur_display = f" `{item['duration']}`" if item['duration'] else ""
            text_lines.append(f"**{idx}.** {item['title']}{dur_display}")
            
            s_hash = hashlib.md5(item['url'].encode()).hexdigest()[:8]
            search_cache[s_hash] = item['url']
            num_row.append(InlineKeyboardButton(str(idx), callback_data=f"pick_{s_hash}"))

        markup = InlineKeyboardMarkup([
            num_row
        ])

        await search_msg.edit_text("\n".join(text_lines), reply_markup=markup)
        return

# ----------------- CALLBACK QUERY HANDLER ----------------- #

@bot.on_callback_query()
async def callback_query_handler(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data == "btn_delete_this":
        try:
            await query.message.delete()
        except Exception:
            await query.answer("Couldn't delete message.", show_alert=False)
        return

    # Language Selector Callback
    if data.startswith("set_lang_"):
        lang_code = data.split("_")[2]
        lang_names = {"en": "English", "bn": "বাংলা", "ru": "Русский", "uz": "O'zbek"}
        await query.answer(f"Language set to {lang_names.get(lang_code, 'English')}", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # Country Selector in /top
    if data.startswith("top_country_"):
        country = data.split("_")[2]
        markup = generate_top_markup(country)
        await query.message.edit_reply_markup(reply_markup=markup)
        await query.answer()
        return

    # Top search query clicked
    if data.startswith("top_search_"):
        q_hash = data.split("_")[2]
        song_title = search_cache.get(q_hash)
        if not song_title:
            await query.answer("Query expired.", show_alert=True)
            return

        await query.answer(f"Searching: {song_title}...", show_alert=False)
        results = await asyncio.to_thread(search_youtube_videos, song_title, 5)
        if not results:
            await query.message.reply_text("❌ No track found for this item.")
            return

        text_lines = [f"🎧 **Results for:** `{song_title}`\n"]
        num_row = []
        for idx, item in enumerate(results, 1):
            dur_display = f" `{item['duration']}`" if item['duration'] else ""
            text_lines.append(f"**{idx}.** {item['title']}{dur_display}")
            s_hash = hashlib.md5(item['url'].encode()).hexdigest()[:8]
            search_cache[s_hash] = item['url']
            num_row.append(InlineKeyboardButton(str(idx), callback_data=f"pick_{s_hash}"))

        await query.message.reply_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup([num_row]))
        return

    # Pick item from search list
    if data.startswith("pick_"):
        s_hash = data.split("_")[1]
        target_url = search_cache.get(s_hash)
        if not target_url:
            await query.answer("Item expired. Search again.", show_alert=True)
            return

        await query.answer("Fetching formats...", show_alert=False)
        status = await query.message.reply_text("⚡ **Analyzing selected track...**")

        meta = await asyncio.to_thread(extract_youtube_metadata, target_url)
        if not meta:
            await status.edit_text("❌ Failed to fetch track stream.")
            return

        title = meta.get("title", "Track")
        thumbnail = meta.get("thumbnail")
        qualities = meta.get("qualities", [720, 360])

        url_hash = hashlib.md5(f"{target_url}_{user_id}_{time.time()}".encode()).hexdigest()[:12]
        video_cache[url_hash] = {
            "url": target_url,
            "title": title,
            "platform": "YouTube"
        }

        buttons = []
        row = []
        for q in qualities:
            row.append(InlineKeyboardButton(f"📹 {q}p", callback_data=f"dl_res_{url_hash}_{q}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton("🔊 Audio", callback_data=f"dl_aud_{url_hash}")])

        caption_text = (
            f"📹 **{title}** ➔\n"
            f"👤 **#YouTube** ➔\n\n"
            "**Formats to download ↓**"
        )

        try:
            await status.delete()
            if thumbnail:
                await query.message.reply_photo(photo=thumbnail, caption=caption_text, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await query.message.reply_text(caption_text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await query.message.reply_text(caption_text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Handle YouTube Video Quality Selection
    if data.startswith("dl_res_"):
        parts = data.split("_")
        url_hash = parts[2]
        res_tag = parts[3]

        item = video_cache.get(url_hash)
        if not item:
            await query.answer("⚠️ Link expired. Please send the URL again.", show_alert=True)
            return

        target_url = item["url"]
        title = item["title"]
        platform = item["platform"]
        height_val = int(res_tag) if res_tag.isdigit() else None

        await query.answer(f"⏳ Downloading in {res_tag}p...", show_alert=False)
        status = await query.message.reply_text(f"⚡ **Downloading video ({res_tag}p)...**")

        file_path, fetched_title = await asyncio.to_thread(download_youtube_with_quality, target_url, user_id, height_val)

        if not file_path or not os.path.exists(file_path):
            await status.edit_text("❌ **Download Failed.** Could not fetch stream for this quality.")
            return

        try:
            await status.edit_text("📤 **Uploading video...**")
            caption = f"📹 **{fetched_title or title}** ➔\n👤 **#{platform}** ➔"

            await query.message.reply_video(
                video=file_path,
                caption=caption,
                supports_streaming=True,
                progress=progress_bar,
                progress_args=(status, f"Uploading ({res_tag}p)", user_id)
            )
            await status.delete()
            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, platform, 1, 0, 1, 0))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, platform, 1, 0, 1, 0))
        except Exception as e:
            logger.error(f"Video Quality Upload Error: {e}")
            await status.edit_text(f"❌ **Upload Failed:** `{str(e)}`")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        return

    # Handle Audio Selection
    if data.startswith("dl_aud_"):
        url_hash = data.split("_", 2)[2]
        item = video_cache.get(url_hash)

        if not item:
            await query.answer("⚠️ Link expired. Please send the URL again.", show_alert=True)
            return

        target_url = item["url"]
        title = item["title"]
        platform = item.get("platform", "Audio")

        await query.answer("🔊 Extracting audio stream...", show_alert=False)
        status = await query.message.reply_text("⚡ **Extracting MP3 audio...**")

        audio_path, fetched_title = await asyncio.to_thread(extract_and_download_social_audio, target_url, user_id)

        if not audio_path or not os.path.exists(audio_path):
            await status.edit_text("❌ **Failed to extract audio.**")
            return

        try:
            await status.edit_text("📤 **Uploading audio file...**")
            caption = f"🔊 **{fetched_title or title}** ➔\n👤 **#{platform}** ➔"

            await query.message.reply_audio(
                audio=audio_path,
                caption=caption,
                title=fetched_title or title,
                progress=progress_bar,
                progress_args=(status, "Uploading Audio", user_id)
            )
            await status.delete()
            asyncio.create_task(asyncio.to_thread(sync_increment_downloads, "Others", 1, 0, 0, 1))
            asyncio.create_task(asyncio.to_thread(sync_increment_user_downloads, user_id, "Others", 1, 0, 0, 1))
        except Exception as e:
            logger.error(f"Audio Upload Error: {e}")
            await status.edit_text(f"❌ **Upload Failed:** `{str(e)}`")
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception:
                    pass
        return

    # Admin Panel Callbacks (Owner Only)
    if user_id != OWNER_ID:
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    if data in ["btn_admin_shortcut", "btn_refresh_admin"]:
        text, markup = await generate_admin_dashboard()
        await query.message.edit_text(text, reply_markup=markup)
        return

    if data == "btn_add_session":
        admin_states[user_id] = "WAITING_SESSION"
        await query.message.reply_text(
            "📝 **Send your Pyrogram/Hydrogram Session String:**",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()
        return

    if data == "btn_login_account":
        admin_states[user_id] = "LOGIN_PHONE"
        await query.message.reply_text(
            "📱 **Send phone number with country code** (e.g. `+8801XXXXXXXXX`):",
            reply_markup=ForceReply(selective=True)
        )
        await query.answer()
        return

    if data == "btn_list_sessions":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("No active sessions found.", show_alert=True)
            return
        text = "📁 **Active Sessions**\n────────────────────\n"
        for idx, s in enumerate(sessions, 1):
            text += f"`{idx}.` **{s['account_name']}**\n    ID: `{s['doc_id'][:8]}`\n"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_admin")]]))
        return

    if data == "btn_del_menu":
        sessions = await asyncio.to_thread(sync_get_all_sessions)
        if not sessions:
            await query.answer("No sessions available to delete.", show_alert=True)
            return
        buttons = []
        for s in sessions:
            buttons.append([InlineKeyboardButton(f"❌ {s['account_name']}", callback_data=f"del_{s['doc_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_admin")])
        await query.message.edit_text("Select an account session to remove:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("del_"):
        doc_id = data.split("_", 1)[1]
        await asyncio.to_thread(sync_delete_session, doc_id)
        asyncio.create_task(init_session_pool())
        await query.answer("Session removed successfully.", show_alert=True)
        text, markup = await generate_admin_dashboard()
        await query.message.edit_text(text, reply_markup=markup)
        return

    if data == "btn_view_urls":
        urls = await asyncio.to_thread(sync_get_active_urls, 8)
        if not urls:
            await query.answer("No recent requests logged.", show_alert=True)
            return
        text = "🔗 **Recent Activity**\n────────────────────\n"
        for u in urls:
            text += f"• **{u.get('platform', 'Link')}:** `{u.get('url')[:38]}...`\n  └ User: `{u.get('user_name')}`\n"
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="btn_refresh_admin")]]))
        return

# ----------------- AIOHTTP DUMMY WEB SERVER ----------------- #

async def web_server():
    async def handle_ping(request):
        return web.Response(text="Bot is running!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Aiohttp web server running on port {PORT}")

# ----------------- BOT STARTUP ----------------- #

async def main():
    await bot.start()
    
    # Set the Telegram Command Menu Buttons (Shown in screenshot)
    try:
        await bot.set_bot_commands([
            BotCommand("my", "your playlist"),
            BotCommand("top", "popular songs"),
            BotCommand("lang", "change language"),
            BotCommand("help", "how to use")
        ])
        logger.info("Bot Menu Commands registered successfully!")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")

    logger.info("Bot started successfully!")
    await init_session_pool()
    await web_server()
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())