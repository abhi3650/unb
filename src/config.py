"""
Configuration — fill in your values in .env or directly here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── REQUIRED ───────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID    = int(os.getenv("OWNER_ID", "0"))          # Your Telegram user ID
CHANNEL_ID  = os.getenv("CHANNEL_ID", "")              # Optional: channel to post new uploads

# ─── SCRAPER ────────────────────────────────────────────────────
SCRAPE_URL  = "https://www.1tamilmv.durban"             # Current live domain (June 2026)
# Fallback mirrors tried automatically: 1tamilmv.fi → redirects to current domain
SCRAPE_INTERVAL = 300                                   # seconds between checks (5 min)

# ─── DATABASE ───────────────────────────────────────────────────
DB_PATH     = os.getenv("DB_PATH", "../data/bot.db")   # SQLite path

# ─── MISC ───────────────────────────────────────────────────────
MAX_TASK_DISPLAY = 10   # Max tasks shown in a list
