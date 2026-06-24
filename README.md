# 🤖 Upload Notifier Bot

A production-grade Telegram bot that monitors **1TamilMV** for new uploads and manages task assignments between the **owner** and **admins** — all via inline buttons.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎬 Auto Scraper | Checks 1TamilMV every 5 minutes for new uploads |
| 📬 Instant Notifications | Notifies owner + all admins on new content |
| 📋 Task Assignment | Owner assigns upload tasks to specific admins |
| ✅ Done Button | Admin marks task complete with a single button tap |
| 🔍 Verification | Owner approves or rejects completed tasks |
| 🔁 Rejection Loop | Rejected tasks auto-notify the admin to redo |
| 📢 Broadcast | Owner sends messages to all admins at once |
| 👥 Admin Management | Add/remove admins dynamically |

---

## 🚀 Quick Setup

### 1. Clone & Configure

```bash
git clone <your-repo>
cd upload-notifier-bot
cp .env.example .env
```

Edit `.env`:
```env
BOT_TOKEN=your_bot_token_here
OWNER_ID=your_telegram_user_id
```

> Get your bot token from [@BotFather](https://t.me/BotFather)  
> Get your user ID from [@userinfobot](https://t.me/userinfobot)

### 2. Install & Run

```bash
pip install -r requirements.txt
python src/bot.py
```

### 3. OR Run with Docker

```bash
docker build -t upload-notifier-bot .
docker run -d --env-file .env -v $(pwd)/data:/app/data --name upbot upload-notifier-bot
```

---

## 📖 Command Reference

### 👑 Owner Commands

| Command | Description |
|---|---|
| `/start` | Show dashboard |
| `/assign` | Assign a task to an admin |
| `/admins` | List all admins |
| `/addadmin` | Add a new admin by user ID |
| `/removeadmin` | Remove an admin |
| `/uploads` | View latest 10 scraped uploads |
| `/broadcast` | Send a message to all admins |

### 🛡 Admin Commands

| Command | Description |
|---|---|
| `/start` | Show dashboard |
| `/mytasks` | View all your assigned tasks |
| `/uploads` | View latest scraped uploads |

---

## 🔄 Task Workflow

```
Owner /assign
    ↓
Admin receives task + [✅ Mark as Done] button
    ↓
Admin completes task → presses Done
    ↓
Owner gets notified with [🔍 Verify Task] button
    ↓
Owner presses Verify → sees [✅ Approve] or [❌ Not Completed]
    ↓
If Approved → Admin notified ✅
If Rejected → Admin notified ❌ with [✅ Mark as Done Again] button
```

---

## 📁 Project Structure

```
upload-notifier-bot/
├── src/
│   ├── bot.py              # Entry point
│   ├── config.py           # Config & env vars
│   ├── database.py         # SQLite ORM
│   ├── states.py           # Conversation states
│   └── handlers/
│       ├── owner.py        # Owner commands
│       ├── admin.py        # Admin commands + done button
│       └── scraper.py      # 1TamilMV scraper job
├── data/                   # SQLite DB (auto-created)
├── .env.example
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## ⚙️ Configuration

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | required | Telegram bot token |
| `OWNER_ID` | required | Your Telegram user ID |
| `CHANNEL_ID` | optional | Channel to forward new uploads |
| `DB_PATH` | `../data/bot.db` | SQLite database path |

---

## 🔧 Updating the Scrape URL

If 1TamilMV changes its domain, update in `src/config.py`:

```python
SCRAPE_URL = "https://www.1tamilmv.newdomain"
```

---

## 🛡 Security Notes

- Only the `OWNER_ID` can manage admins and assign tasks.
- Admins can only mark their own tasks as done.
- All user IDs are validated before any database operation.

---

## 📦 Deploy on VPS (Recommended)

```bash
# On Ubuntu/Debian VPS
apt install python3 python3-pip -y
pip install -r requirements.txt

# Run as background service with screen or systemd
screen -S upbot
python src/bot.py

# Detach: Ctrl+A then D
```

Or use the **Docker** method above for zero-hassle deployment.
