#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║   <> Decifer Trading 2.0 — Launcher                     ║
# ╚══════════════════════════════════════════════════════════╝

clear
echo ""
echo "  ██████╗ ███████╗ ██████╗██╗███████╗███████╗██████╗ "
echo "  ██╔══██╗██╔════╝██╔════╝██║██╔════╝██╔════╝██╔══██╗"
echo "  ██║  ██║█████╗  ██║     ██║█████╗  █████╗  ██████╔╝"
echo "  ██║  ██║██╔══╝  ██║     ██║██╔══╝  ██╔══╝  ██╔══██╗"
echo "  ██████╔╝███████╗╚██████╗██║██║     ███████╗██║  ██║"
echo "  ╚═════╝ ╚══════╝ ╚═════╝╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝"
echo ""
echo "  <> Autonomous AI Trading System v2.0"
echo ""

# Find the project folder
PROJ_DIR=""
for d in \
    "$HOME/Desktop/decifer trading" \
    "$(dirname "$0")" \
    "$HOME/Documents/Claude/Projects/decifer trading" \
    "$HOME/decifer trading" \
; do
    if [ -f "$d/bot.py" ]; then
        PROJ_DIR="$d"
        break
    fi
done

if [ -z "$PROJ_DIR" ] || [ ! -f "$PROJ_DIR/bot.py" ]; then
    echo "  ❌ Could not find bot.py in any known location."
    echo "  Searched:"
    echo "    ~/Documents/Claude/Projects/decifer trading"
    echo "    $(dirname "$0")"
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

echo "  📂 Project: $PROJ_DIR"
cd "$PROJ_DIR"

# Load environment
if [ -f .env ]; then
    set -a
    source .env
    set +a
    echo "  🔑 API key loaded"
else
    echo "  ❌ .env file not found!"
    read -p "  Press Enter to exit..."
    exit 1
fi

# Kill any existing bot on port 8080
EXISTING=$(lsof -ti:8080 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "  🔄 Stopping existing bot (PID: $EXISTING)..."
    kill -9 $EXISTING 2>/dev/null
    sleep 1
fi

echo "  🚀 Starting Decifer Trading 2.0..."
echo "  📊 Dashboard: http://localhost:8080"
echo ""
echo "  ─────────────────────────────────────────────"
echo ""

# Open dashboard in browser after 4 seconds
(sleep 4 && open http://localhost:8080) &

# Run the bot (stays in foreground so you see output)
python3 bot.py
