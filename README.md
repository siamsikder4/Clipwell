# 🚀 Clipwell Telegram Downloader Bot

An advanced, multi-session Telegram media downloader bot built with **Pyrogram**, **Firebase Firestore**, and **Aiohttp**. It allows users to effortlessly download media from both public and private Telegram channels/groups without requiring them to log in.

---

## ✨ Features

- **Multi-Session Support:** Seamlessly handles multiple user session strings stored securely in Firebase.
- **Firebase Persistence:** Zero data loss during re-deployments or server restarts.
- **Admin Control Panel:** Interactive inline button interface to add, view, or remove sessions directly from the Telegram chat.
- **High-Speed Processing:** Optimized media downloading and uploading equipped with real-time aesthetic progress bars.
- **Media Group (Album) Support:** Accurately downloads and forwards entire photo/video albums simultaneously.
- **Auto-Delete Security:** Automatically deletes user links and downloaded media after 5 minutes to maintain privacy and clean chat history.
- **Analytics Dashboard:** Real-time tracking of total bot users and total download counts.
- **FloodWait Protection:** Automatically catches and handles Telegram rate limits to prevent bot crashes.
- **Render Ready:** Includes a lightweight built-in HTTP server (`aiohttp`) for seamless health checks and 24/7 deployment on Render.

---

## 🛠️ Prerequisites & Environment Variables

To run this bot successfully, configure the following environment variables on your hosting provider (e.g., Render):

| Variable Key | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Your Telegram API ID | `35039821` |
| `API_HASH` | Your Telegram API Hash | `77df805f1700eeefec...` |
| `BOT_TOKEN` | Your Telegram Bot Token from `@BotFather` | `123456:ABC-DEF12341234...` |
| `FIREBASE_KEY` | Entire JSON content of your Firebase Service Account | `{"type": "service_account", ...}` |

---

## 📦 Dependencies (`requirements.txt`)

Ensure your project contains a `requirements.txt` file with the following packages:

```text
pyrogram
tgcrypto
aiohttp
firebase-admin
