#!/bin/bash
# Upload Notifier Bot — Quick Setup Script
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖 Upload Notifier Bot — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 not found. Install it first."
  exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Create .env if missing
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "⚙️  Created .env from .env.example"
  echo "👉 Edit .env with your BOT_TOKEN and OWNER_ID before running!"
  echo ""
  read -p "Open .env now? (y/n): " open_env
  if [ "$open_env" = "y" ]; then
    ${EDITOR:-nano} .env
  fi
fi

# Create data dir
mkdir -p data
echo "✅ Data directory ready."

# Install deps
echo ""
echo "📦 Installing dependencies..."
pip install -r requirements.txt --quiet

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Setup complete!"
echo ""
echo "▶  To run the bot:"
echo "   python src/bot.py"
echo ""
echo "🐳 To run with Docker:"
echo "   docker-compose up -d"
echo ""
echo "🔧 To install as a systemd service:"
echo "   sudo cp upbot.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now upbot"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
