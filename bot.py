import os
import re
import time
import json
import asyncio
import firebase_admin
from firebase_admin import credentials, firestore
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message, InputMediaVideo, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ForceReply
)
from pyrogram.errors import FloodWait
from aiohttp import web

# Credentials & Constants
API_ID = int(os.environ.get("API_ID", "35039821"))
API_HASH = os.environ.get("API_HASH", "77df805f1700eeefec861de6c93ee2ae").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
FIREBASE_KEY_RAW = os.environ.get("FIREBASE_KEY", "").strip()
OWNER_ID = 6142774415  # Admin Telegram ID
PORT = int(os.environ.get("PORT", "8080"))

# Initialize Firebase Database
db = None
if FIREBASE_KEY_RAW:
    try:
        cred_dict = json.loads(FIREBASE_KEY_RAW)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print(">>> 🟢 FIREBASE CONNECTED SUCCESSFULLY <<<", flush=True)
    except Exception as e:
        print(f"❌ Firebase Connection Error: {e}", flush=True)

# Firebase Helper Functions
def add_session_to_db(session_str: str, name: str):
    if not db:
        return False, "Firebase ডেটাবেজ কানেক্টেড নেই!"
    try:
        docs = db.collection("telegram_sessions").where("session_string", "==", session_str).stream()
        if any(docs):
            return False, "এই সেশনটি আগেই যোগ করা হয়েছে!"

        db.collection("telegram_sessions").add({
            "session_string": session_str,
            "account_name": name,
            "created_at": time.time()
        })
        return True, "Success"
    except Exception as e:
        return False, str(e)

def get_all_sessions():
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
                "account_name": data.get("account_name", "Unknown User")
            })
        return sessions
    except Exception as e:
        print(f"Error fetching sessions: {e}", flush=True)
        return []

def delete_session_from_db(doc_id: str):
    if not db:
        return False
    try:
        db.collection("telegram_sessions").document(doc_id).delete()
        return True
    except Exception as e:
        print(f"Error deleting session: {e}", flush=True)
        return False

# Analytics & User Tracking
def track_user_in_db(user_id: int, username: str, name: str):
    if not db:
        return
    try:
        doc_ref = db.collection("bot_users").document(str(user_id))
        doc_ref.set({
            "user_id": user_id,
            "username": username or "N/A",
            "name": name,
            "last_active": time.time()
        }, merge=True)
    except Exception as e:
        print(f"User track error: {e}", flush=True)

def increment_download_count():
    if not db:
        return
    try:
        doc_ref = db.collection("bot_stats").document("global_analytics")
        doc_ref.set({"total_downloads": firestore.Increment(1)}, merge=True)
    except Exception as e:
        print(f"Download stat error: {e}", flush=True)

def get_analytics_stats():
    if not db:
        return 0, 0
    try:
        users_docs = db.collection("bot_users").stream()
        total_users = sum(1 for _ in users_docs)

        stat_doc = db.collection("bot_stats").document("global_analytics").get()
        total_downloads = stat_doc.to_dict().get("total_downloads", 0) if stat_doc.exists else 0

        return total_users, total_downloads
    except Exception as e:
        print(f"Analytics fetch error: {e}", flush=True)
        return 0, 0

# UI Trackers & Rate Limits
admin_states = {}
progress_status = {}

# Time Formatter Helper
def format_time(seconds: int) -> str:
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

# Aesthetic Dynamic Progress Bar
async def progress_bar(current, total, status_msg, action_name, user_id):
    now = time.time()
    user_data = progress_status.get(user_id, {})
    last_update = user_data.get("last_time", 0)
    start_time = user_data.get("start_time", now)

    # 2.5s throttling to prevent flood error
    if (now - last_update < 2.5) and current < total:
        return

    progress_status[user_id] = {
        "last_time": now,
        "start_time": start_time
    }

    percentage = (current / total) * 100
    filled = int(percentage // 10)
    bar = "▰" * filled + "▱" * (10 - filled)

    curr_mb = current / (1024 * 1024)
    tot_mb = total / (1024 * 1024)

    # Speed & ETA calculation
    elapsed_time = max(now - start_time, 0.1)
    speed = current / elapsed_time  # Bytes/sec
    speed_mb = speed / (1024 * 1024)

    remaining_bytes = total - current
    eta = remaining_bytes / speed if speed > 0 else 0
    eta_str = format_time(eta)

    text = (
        f"╭──────── ⚡ **{action_name.upper()}** ⚡ ────────╮\n"
        f"│\n"
        f"├ 📊 **অগ্রগতি:** `[{bar}] {percentage:.1f}%`\n"
        f"├ 📦 **সাইজ:** `{curr_mb:.2f} MB / {tot_mb:.2f} MB`\n"
        f"├ 🚀 **স্পিড:** `{speed_mb:.2f} MB/s`\n"
        f"├ ⏳ **বাকি সময়:** `{eta_str}`\n"
        f"│\n"
        f"╰──────────────────────────────────╯"
    )
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass

# Health Check Endpoint
async def handle_ping(request):
    return web.Response(text="Clipwell High-Speed Engine is Running Smoothly!")

# Auto-delete helper
async def auto_delete_messages(bot_client, chat_id: int, message_ids: list, delay_seconds: int = 300):
    await asyncio.sleep(delay_seconds)
    try:
        await bot_client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception as e:
        print(f"Auto delete notice: {e}", flush=True)

def is_gif_message(msg):
    if not msg:
        return False
    if msg.animation:
        return True
    if msg.document and msg.document.mime_type and "gif" in msg.document.mime_type.lower():
        return True
    return False

def has_downloadable_media(msg):
    return bool(
        msg and (msg.video or msg.photo or msg.document or msg.animation or msg.media_group_id)
    )

def get_original_caption(original_caption: str) -> str:
    if original_caption and original_caption.strip():
        return original_caption.strip()
    return ""

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    bot = Client(
        "bot_instance",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )

    # Command: /start
    @bot.on_message(filters.command(["start"]) & filters.private)
    async def start_cmd(client: Client, message: Message):
        user = message.from_user
        track_user_in_db(user.id, user.username, user.first_name)

        buttons = [
            [
                InlineKeyboardButton("⚡ সার্ভার স্ট্যাটাস", callback_data="btn_ping"),
                InlineKeyboardButton("ℹ️ ব্যবহারের নিয়ম", callback_data="btn_help")
            ]
        ]
        if user.id == OWNER_ID:
            buttons.append([InlineKeyboardButton("⚙️ অ্যাডমিন প্যানেল", callback_data="btn_admin_shortcut")])

        text = (
            f"👋 **স্বাগতম, {user.first_name}!**\n\n"
            "╭─────── 📥 **CLIPWELL PRO ENGINE** ───────╮\n"
            "│\n"
            "├ 🔗 যেকোনো **পাবলিক** বা **প্রাইভেট** লিংক পাঠান\n"
            "├ 🎬 **সাপোর্ট:** Photo, Video, Document, GIF, Album\n"
            "├ ⚡ লাইভ স্পিড ট্র্যাকার ও আল্ট্রা-ফাস্ট আপলোড\n"
            "├ ⏳ **অটো-ডিলিট:** প্রাইভেসি রক্ষার্থে ৫ মিনিটে রিমুভ\n"
            "│\n"
            "╰──────────────────────────────────╯\n\n"
            "👉 *মিডিয়া পেতে নিচের ইনপুট বক্সে লিংক পেস্ট করুন।* "
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    # Command: /admin (Admin Control Panel)
    @bot.on_message(filters.command(["admin", "panel"]) & filters.private)
    async def admin_panel(client: Client, message: Message):
        if message.from_user.id != OWNER_ID:
            await message.reply_text("⛔ **অনুমতি নেই:** আপনি এই কমান্ডটি ব্যবহারের যোগ্য নন।")
            return

        all_sess = get_all_sessions()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 অ্যানালিটিক্স ও পরিসংখ্যান", callback_data="btn_stats")],
            [InlineKeyboardButton("➕ নতুন সেশন যুক্ত করুন", callback_data="btn_add_session")],
            [InlineKeyboardButton(f"📋 অ্যাক্টিভ সেশন লিস্ট ({len(all_sess)})", callback_data="btn_list_sessions")],
            [InlineKeyboardButton("🗑️ সেশন রিমুভ করুন", callback_data="btn_del_menu")]
        ])

        db_status = "🟢 সচল" if db else "🔴 সংযোগ বিচ্ছিন্ন"
        text = (
            "╭─────── ⚙️ **ADMIN CONTROL DASHBOARD** ───────╮\n"
            "│\n"
            f"├ 💾 **Firebase DB:** `{db_status}`\n"
            f"├ 🔑 **অ্যাক্টিভ ক্লায়েন্ট সেশন:** `{len(all_sess)} টি`\n"
            "├ নিচে থেকে কাঙ্ক্ষিত অপশন সিলেক্ট করুন।\n"
            "│\n"
            "╰──────────────────────────────────────────╯"
        )
        await message.reply_text(text, reply_markup=keyboard)

    # Callback Query Handler
    @bot.on_callback_query()
    async def callback_handler(client: Client, callback_query: CallbackQuery):
        data = callback_query.data
        user_id = callback_query.from_user.id

        # Public Callbacks
        if data == "btn_ping":
            start_ping = time.time()
            msg = await callback_query.message.edit_text("📡 *পিং চেক করা হচ্ছে...*")
            latency = (time.time() - start_ping) * 1000
            await msg.edit_text(
                f"╭─────── ⚡ **SERVER LATENCY** ───────╮\n"
                f"│\n"
                f"├ 🚀 **বটের রেসপন্স স্পিড:** `{latency:.2f} ms`\n"
                f"├ 🟢 **ইঞ্জিন স্ট্যাটাস:** `অনলাইন & অ্যাক্টিভ`\n"
                f"│\n"
                f"╰───────────────────────────────────╯",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="btn_back_home")]])
            )
            await callback_query.answer()
            return

        elif data == "btn_help":
            help_text = (
                "╭─────── 📖 **কীভাবে ব্যবহার করবেন?** ───────╮\n"
                "│\n"
                "├ **১.** যেকোনো টেলিগ্রাম পোস্টের লিংক কপি করুন।\n"
                "├ **২.** এই বটে লিংকটি মেসেজ আকারে সেন্ড করুন।\n"
                "├ **৩.** বট স্বয়ংক্রিয়ভাবে মিডিয়া ডাউনলোড করে দেবে।\n"
                "├ ⚠️ *প্রাইভেট চ্যানেলের ক্ষেত্রে সেশন অ্যাকাউন্টকে*\n"
                "│  *চ্যানেলটিতে যুক্ত থাকতে হবে।*\n"
                "│\n"
                "╰───────────────────────────────────╯"
            )
            await callback_query.message.edit_text(
                help_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="btn_back_home")]])
            )
            await callback_query.answer()
            return

        elif data == "btn_back_home":
            buttons = [
                [
                    InlineKeyboardButton("⚡ সার্ভার স্ট্যাটাস", callback_data="btn_ping"),
                    InlineKeyboardButton("ℹ️ ব্যবহারের নিয়ম", callback_data="btn_help")
                ]
            ]
            if user_id == OWNER_ID:
                buttons.append([InlineKeyboardButton("⚙️ অ্যাডমিন প্যানেল", callback_data="btn_admin_shortcut")])

            home_text = (
                f"👋 **স্বাগতম, {callback_query.from_user.first_name}!**\n\n"
                "╭─────── 📥 **CLIPWELL PRO ENGINE** ───────╮\n"
                "│\n"
                "├ 🔗 যেকোনো **পাবলিক** বা **প্রাইভেট** লিংক পাঠান\n"
                "├ 🎬 **সাপোর্ট:** Photo, Video, Document, GIF, Album\n"
                "├ ⚡ লাইভ স্পিড ট্র্যাকার ও আল্ট্রা-ফাস্ট আপলোড\n"
                "├ ⏳ **অটো-ডিলিট:** প্রাইভেসি রক্ষার্থে ৫ মিনিটে রিমুভ\n"
                "│\n"
                "╰──────────────────────────────────╯"
            )
            await callback_query.message.edit_text(home_text, reply_markup=InlineKeyboardMarkup(buttons))
            await callback_query.answer()
            return

        # Protected Admin Actions
        if user_id != OWNER_ID:
            await callback_query.answer("⛔ অননুমোদিত অ্যাক্সেস!", show_alert=True)
            return

        if data in ["btn_admin_shortcut", "btn_back_admin"]:
            all_sess = get_all_sessions()
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 অ্যানালিটিক্স ও পরিসংখ্যান", callback_data="btn_stats")],
                [InlineKeyboardButton("➕ নতুন সেশন যুক্ত করুন", callback_data="btn_add_session")],
                [InlineKeyboardButton(f"📋 অ্যাক্টিভ সেশন লিস্ট ({len(all_sess)})", callback_data="btn_list_sessions")],
                [InlineKeyboardButton("🗑️ সেশন রিমুভ করুন", callback_data="btn_del_menu")]
            ])
            db_status = "🟢 সচল" if db else "🔴 সংযোগ বিচ্ছিন্ন"
            text = (
                "╭─────── ⚙️ **ADMIN CONTROL DASHBOARD** ───────╮\n"
                "│\n"
                f"├ 💾 **Firebase DB:** `{db_status}`\n"
                f"├ 🔑 **অ্যাক্টিভ ক্লায়েন্ট সেশন:** `{len(all_sess)} টি`\n"
                "├ নিচে থেকে কাঙ্ক্ষিত অপশন সিলেক্ট করুন।\n"
                "│\n"
                "╰──────────────────────────────────────────╯"
            )
            await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_stats":
            total_users, total_downloads = get_analytics_stats()
            all_sess = get_all_sessions()

            text = (
                "╭─────── 📊 **লাইভ বট পরিসংখ্যান** ───────╮\n"
                "│\n"
                f"├ 👥 **মোট ইউজার:** `{total_users} জন`\n"
                f"├ 📥 **মোট ডাউনলোড সম্পন্ন:** `{total_downloads} টি`\n"
                f"├ 🔑 **কানেক্টেড সেশন:** `{len(all_sess)} টি`\n"
                f"├ 💾 **ক্লাউড ডেটাবেজ:** `সংযুক্ত (Firebase)`\n"
                "│\n"
                "╰───────────────────────────────────╯"
            )
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনুতে ফিরুন", callback_data="btn_back_admin")]])
            await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_add_session":
            admin_states[user_id] = "WAITING_SESSION"
            await callback_query.message.reply(
                "╭─────── ➕ **নতুন সেশন যুক্ত করুন** ───────╮\n"
                "│\n"
                "├ আপনার Pyrogram `SESSION_STRING` টি কপি করে\n"
                "├ **এই মেসেজে রিপ্লাই দিন:**\n"
                "│\n"
                "╰────────────────────────────────────────╯",
                reply_markup=ForceReply(selective=True)
            )
            await callback_query.answer()

        elif data == "btn_list_sessions":
            all_sess = get_all_sessions()
            if not all_sess:
                await callback_query.message.edit_text(
                    "⚠️ **কোনো সেশন যুক্ত করা নেই!**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনু", callback_data="btn_back_admin")]])
                )
            else:
                text = f"╭─────── 📋 **সংযুক্ত সেশন তালিকা ({len(all_sess)})** ───────╮\n│\n"
                for idx, s in enumerate(all_sess, 1):
                    text += f"├ **#{idx} অ্যাকাউন্ট:** `{s['account_name']}`\n"
                    text += f"│  🔑 **Doc ID:** `{s['doc_id']}`\n│\n"
                text += "╰──────────────────────────────────────╯"

                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনুতে ফিরুন", callback_data="btn_back_admin")]])
                await callback_query.message.edit_text(text, reply_markup=keyboard)
            await callback_query.answer()

        elif data == "btn_del_menu":
            all_sess = get_all_sessions()
            if not all_sess:
                await callback_query.answer("কোনো সেশন পাওয়া যায়নি!", show_alert=True)
                return

            buttons = []
            for s in all_sess:
                buttons.append([InlineKeyboardButton(f"❌ {s['account_name']}", callback_data=f"del_{s['doc_id']}")])
            buttons.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="btn_back_admin")])

            await callback_query.message.edit_text(
                "╭─────── 🗑️ **সেশন ডিলিট প্যানেল** ───────╮\n│\n├ যে অ্যাকাউন্টটি সরাতে চান তা সিলেক্ট করুন:\n╰─────────────────────────────────────╯",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()

        elif data.startswith("del_"):
            doc_id = data.split("del_")[1]
            success = delete_session_from_db(doc_id)
            if success:
                await callback_query.message.edit_text(
                    "✅ **সেশনটি ডেটাবেজ থেকে মুছে ফেলা হয়েছে!**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনু", callback_data="btn_back_admin")]])
                )
            else:
                await callback_query.message.edit_text(
                    "❌ **সেশনটি ডিলিট করা যায়নি।**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনু", callback_data="btn_back_admin")]])
                )
            await callback_query.answer()

    # Text Input & Link Handler
    @bot.on_message(filters.text & filters.private)
    async def text_handler(client: Client, message: Message):
        user_id = message.from_user.id
        text_str = message.text.strip()

        if text_str.startswith("/"):
            return

        track_user_in_db(user_id, message.from_user.username, message.from_user.first_name)

        # 1. Admin Session Submission
        if user_id == OWNER_ID and admin_states.get(user_id) == "WAITING_SESSION":
            admin_states.pop(user_id, None)
            status_msg = await message.reply_text("⏳ **টেলিগ্রাম সেশন যাচাই করা হচ্ছে...**")

            test_client = Client(f"test_{time.time()}", api_id=API_ID, api_hash=API_HASH, session_string=text_str, in_memory=True)
            try:
                await test_client.start()
                me = await test_client.get_me()
                acc_name = f"{me.first_name} (@{me.username})" if me.username else me.first_name
                await test_client.stop()

                success, err_msg = add_session_to_db(text_str, acc_name)
                if success:
                    await status_msg.edit_text(
                        f"🎉 **সেশন সফলভাবে যুক্ত হয়েছে!**\n"
                        f"👤 **অ্যাকাউন্ট নাম:** `{acc_name}`\n"
                        f"💾 **স্টোরেজ:** `Firebase Cloud`"
                    )
                else:
                    await status_msg.edit_text(f"⚠️ **ত্রুটি:** `{err_msg}`")
            except Exception as e:
                await status_msg.edit_text(f"❌ **ভুল বা অকার্যকর সেশন স্ট্রিং!**\nত্রুটি: `{str(e)}`")
            return

        # 2. Telegram Link Regex Matching
        private_pattern = r"t\.me/c/(\d+)/(\d+)"
        public_pattern = r"t\.me/([^/]+)/(\d+)"

        private_match = re.search(private_pattern, text_str)
        public_match = re.search(public_pattern, text_str)

        if not (private_match or public_match):
            await message.reply_text("⚠️ **ভুল লিংক:** অনুগ্রহ করে একটি সঠিক টেলিগ্রাম পোস্টের লিংক দিন।")
            return

        active_sessions = get_all_sessions()
        if not active_sessions:
            await message.reply_text("⚠️ **বট কনফিগারেশন ত্রুটি:** কোনো সেশন কানেক্ট করা নেই। অ্যাডমিনকে জানান।")
            return

        status = await message.reply_text("🔍 **পোস্টটি সেশন নেটওয়ার্কে খোঁজা হচ্ছে...**")

        if private_match:
            chat_id = int("-100" + private_match.group(1))
            msg_id = int(private_match.group(2))
        else:
            chat_id = public_match.group(1)
            msg_id = int(public_match.group(2))

        target_msg = None
        working_user_client = None

        for sess in active_sessions:
            temp_client = Client(
                f"sess_run_{time.time()}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=sess['session_string'],
                in_memory=True
            )
            try:
                await temp_client.start()
                try:
                    await temp_client.get_chat(chat_id)
                except Exception:
                    pass

                msg = await temp_client.get_messages(chat_id, msg_id)
                if has_downloadable_media(msg):
                    target_msg = msg
                    working_user_client = temp_client
                    break
                else:
                    await temp_client.stop()
            except Exception as e:
                print(f"Session notice for {sess['account_name']}: {e}", flush=True)
                if temp_client.is_connected:
                    await temp_client.stop()

        if not target_msg or not working_user_client:
            await status.edit_text(
                "❌ **পোস্টটি পাওয়া যায়নি!**\n\n"
                "📌 নিশ্চিত করুন আপনার সেশন অ্যাকাউন্টের অন্তত একটি সেই প্রাইভেট চ্যানেলে যুক্ত রয়েছে।"
            )
            return

        # Initialize progress tracker state for this user
        progress_status[user_id] = {
            "last_time": time.time(),
            "start_time": time.time()
        }

        try:
            # Case 1: Album (Media Group)
            if target_msg.media_group_id:
                group_messages = await working_user_client.get_media_group(chat_id, msg_id)
                downloaded_files = []
                media_list = []
                gif_files = []

                for idx, msg in enumerate(group_messages):
                    if msg.video or msg.photo or msg.document or msg.animation:
                        # Reset start time for accurate step calculation
                        progress_status[user_id]["start_time"] = time.time()
                        file_path = await working_user_client.download_media(
                            msg,
                            progress=progress_bar,
                            progress_args=(status, f"অ্যালবাম ডাউনলোড ({idx+1}/{len(group_messages)})", user_id)
                        )
                        downloaded_files.append(file_path)

                        if is_gif_message(msg):
                            gif_files.append((file_path, msg.caption))
                            continue

                        orig_cap = get_original_caption(msg.caption)

                        if msg.video or (msg.document and msg.document.mime_type and "video" in msg.document.mime_type):
                            media_list.append(InputMediaVideo(file_path, caption=orig_cap))
                        elif msg.photo or (msg.document and msg.document.mime_type and "image" in msg.document.mime_type):
                            media_list.append(InputMediaPhoto(file_path, caption=orig_cap))

                sent_msgs = []
                if media_list:
                    await status.edit_text("⬆️ **অ্যালবাম আপলোড করা হচ্ছে...**")
                    sent_msgs = await client.send_media_group(chat_id=message.chat.id, media=media_list)

                if gif_files:
                    await status.edit_text("⬆️ **অ্যানিমেশন/GIF আপলোড করা হচ্ছে...**")
                    for gpath, gcap_orig in gif_files:
                        gcap = get_original_caption(gcap_orig)
                        gif_msg = await client.send_animation(
                            chat_id=message.chat.id,
                            animation=gpath,
                            caption=gcap
                        )
                        sent_msgs.append(gif_msg)

                for path in downloaded_files:
                    if os.path.exists(path):
                        os.remove(path)

                if media_list or gif_files:
                    increment_download_count()
                    await status.delete()

                    # Schedule 5-minute Auto-Delete
                    delete_msg_ids = [message.id] + [m.id for m in sent_msgs]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=300))
                else:
                    await status.edit_text("❌ **অ্যালবামে কোনো ডাউনলোডযোগ্য ফাইল পাওয়া যায়নি।**")

            # Case 2: Single Media
            else:
                is_gif = is_gif_message(target_msg)
                final_caption = get_original_caption(target_msg.caption)

                if is_gif:
                    media_type_str = "GIF"
                elif target_msg.video:
                    media_type_str = "ভিডিও"
                elif target_msg.photo:
                    media_type_str = "ছবি"
                else:
                    media_type_str = "ডকুমেন্ট"

                progress_status[user_id]["start_time"] = time.time()
                file_path = await working_user_client.download_media(
                    target_msg,
                    progress=progress_bar,
                    progress_args=(status, f"{media_type_str} ডাউনলোড হচ্ছে", user_id)
                )

                # Reset tracker for upload
                progress_status[user_id]["start_time"] = time.time()

                sent_msg = None
                if is_gif:
                    sent_msg = await client.send_animation(
                        chat_id=message.chat.id,
                        animation=file_path,
                        caption=final_caption,
                        progress=progress_bar,
                        progress_args=(status, f"{media_type_str} আপলোড হচ্ছে", user_id)
                    )
                elif target_msg.video:
                    sent_msg = await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        caption=final_caption,
                        supports_streaming=True,
                        progress=progress_bar,
                        progress_args=(status, f"{media_type_str} আপলোড হচ্ছে", user_id)
                    )
                elif target_msg.photo:
                    sent_msg = await client.send_photo(
                        chat_id=message.chat.id,
                        photo=file_path,
                        caption=final_caption,
                        progress=progress_bar,
                        progress_args=(status, f"{media_type_str} আপলোড হচ্ছে", user_id)
                    )
                elif target_msg.document:
                    sent_msg = await client.send_document(
                        chat_id=message.chat.id,
                        document=file_path,
                        caption=final_caption,
                        progress=progress_bar,
                        progress_args=(status, f"{media_type_str} আপলোড হচ্ছে", user_id)
                    )

                if os.path.exists(file_path):
                    os.remove(file_path)

                increment_download_count()
                await status.delete()

                # Schedule 5-minute Auto-Delete
                if sent_msg:
                    delete_msg_ids = [message.id, sent_msg.id]
                    asyncio.create_task(auto_delete_messages(bot, message.chat.id, delete_msg_ids, delay_seconds=300))

        except Exception as e:
            await status.edit_text(f"❌ **প্রসেসিং ত্রুটি:** `{str(e)}`")
        finally:
            if working_user_client and working_user_client.is_connected:
                await working_user_client.stop()

    # Start Main Bot
    try:
        await bot.start()
        print(">>> 🚀 CLIPWELL ULTRA FAST BOT STARTED SUCCESSFULLY <<<", flush=True)
    except FloodWait as e:
        print(f"⚠️ FloodWait: Waiting for {e.value} seconds...", flush=True)
        await asyncio.sleep(e.value + 5)
        await bot.start()

    await idle()

    if bot.is_connected:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())