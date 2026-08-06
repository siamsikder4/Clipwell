<div align="center">

  # ⚡ CLIPWELL DOWNLOADER BOT ⚡
  *A high-speed, multi-session Telegram media downloader powered by Pyrogram & Firebase.*

  [![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
  [![Pyrogram](https://img.shields.io/badge/Pyrogram-v2.0-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.pyrogram.org)
  [![Firebase](https://img.shields.io/badge/Firebase-Firestore-FFCA28?style=for-the-badge&logo=firebase&logoColor=black)](https://firebase.google.com)
  [![Render](https://img.shields.io/badge/Render-24%2F7%20Online-46E3B7?style=for-the-badge&logo=render&logoColor=black)](https://render.com)
  [![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

</div>

---

## 📖 Overview

**Clipwell** is a production-grade Telegram bot designed to fetch and download restricted photos, videos, and media albums from both **Public** and **Private** channels or groups without forcing end-users to log in.

With **Firebase Firestore integration**, your session keys and analytics are preserved permanently across server restarts, deployments, or cache purges.

---

## ✨ Key Features

| Feature | Description |
| :--- | :--- |
| 🔑 **Multi-Session Engine** | Connect multiple helper userbots to access restricted private channels. |
| 💾 **Firebase Persistence** | Session strings and stats are safely stored in Firebase Firestore DB. |
| ⚡ **TgCrypto Speedup** | Powered by C-based crypto extensions for ultra-fast download & upload speeds. |
| 📊 **Real-Time Analytics** | Track total bot users and download metrics directly from `/admin`. |
| ⏳ **5-Min Auto-Delete** | Links and downloaded media automatically self-destruct after 5 minutes. |
| 🎛️ **Interactive Admin UI** | Manage sessions seamlessly via Telegram inline buttons (Add, Delete, View). |
| 🛡️ **FloodWait Safe** | Catches Telegram rate limits automatically and waits without crashing. |
| 🌐 **24/7 Render Keep-Alive** | Built-in HTTP web server (`aiohttp`) for continuous uptime health checks. |

---

## 🎨 In-Bot UI Preview

```text
╭─ ⚙️ ADMIN CONTROL PANEL ⚙️
│
├ 💾 Firebase DB: 🟢 Connected
├ 🟢 Active Sessions: 2
├ Select an option below to manage.
╰──────────────────────────
[ 📊 Analytics & Stats ]
[ ➕ Add New Session ]
[ 📋 View All Sessions (2) ]
[ 🗑️ Remove Session ]
