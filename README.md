<div align="center">

  # ⚡ CLIPWELL DOWNLOADER BOT ⚡
  *A high-speed multi-platform social media and restricted Telegram media downloader powered by Pyrogram, yt-dlp, and Firebase.*

  [![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
  [![Pyrogram](https://img.shields.io/badge/Pyrogram-v2.0-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.pyrogram.org)
  [![yt-dlp](https://img.shields.io/badge/yt--dlp-Video_Engine-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)
  [![Firebase](https://img.shields.io/badge/Firebase-Firestore-FFCA28?style=for-the-badge&logo=firebase&logoColor=black)](https://firebase.google.com)
  [![Railway](https://img.shields.io/badge/Deploy-Railway%20%2F%20Render-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](https://railway.app)
  [![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

</div>

---

## 📖 Overview

**Clipwell** is an all-in-one Telegram downloader bot built for speed, privacy, and flexibility. It seamlessly downloads videos from major platforms (**YouTube, TikTok, Instagram, Facebook**) and retrieves restricted photos, videos, albums, and documents from **Public and Private Telegram channels** through connected userbot sessions.

All user profiles, authorization levels, and platform download metrics are permanently synchronized using **Google Firebase Firestore**.

---

## ✨ Key Features

* **Universal Social Downloader:** Fast extraction and download from YouTube, TikTok (including shortlinks like `vt.tiktok.com`), Instagram Reels, and Facebook videos via `yt-dlp`.
* **Private Channel Media Retrieval:** Downloads restricted content from private Telegram channels using stored Pyrogram user sessions.
* **Dual-Tier User System:** Open access for social media downloads; restricted Telegram content downloads are gated to authorized session accounts.
* **Firebase Firestore Sync:** Cloud storage for active sessions, user tiers, and platform download analytics.
* **Detailed Analytics Breakdown:** Tracks Total Users, Regular Users (No Session), Session Users, and platform counts (Telegram, YouTube, TikTok, Instagram, Facebook).
* **Minimalist Progress Bar:** Live tracking displaying transfer percentage, current/total size, transfer rate (MB/s), and calculated ETA.
* **5-Minute Auto-Delete:** Automatically purges delivered media and requests after 5 minutes for user privacy and server space efficiency.
* **24/7 Hosting Ready:** Integrated lightweight `aiohttp` web server (Port `8080`) for keep-alive monitoring on platforms like Railway or Render.

---

## 🎨 Complete In-Bot UI Flow

### 1. Start Interface (`/start`)

**Regular User View:**
```text
**Hello Siam,**
Account Status: `Regular User`

Send any supported video link to download:
• **Supported:** YouTube, TikTok, Instagram, Facebook
• **Telegram Links:** Requires session ID
• Sent media auto-deletes in 5 minutes.

[ ⚡ Ping ]  [ ℹ️ Help ]