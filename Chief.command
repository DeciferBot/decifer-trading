#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║   ◆ Chief Decifer — Autonomous Dev Intelligence         ║
# ╚══════════════════════════════════════════════════════════╝

clear
echo ""
echo "   ██████╗██╗  ██╗██╗███████╗███████╗"
echo "  ██╔════╝██║  ██║██║██╔════╝██╔════╝"
echo "  ██║     ███████║██║█████╗  █████╗  "
echo "  ██║     ██╔══██║██║██╔══╝  ██╔══╝  "
echo "  ╚██████╗██║  ██║██║███████╗██║     "
echo "   ╚═════╝╚═╝  ╚═╝╚═╝╚══════╝╚═╝     "
echo ""
echo "  ◆ Chief Decifer — Autonomous Dev Intelligence"
echo ""

# Find chief-decifer
CHIEF_DIR="$HOME/Documents/Claude/Projects/Chief Designer/Chief-Decifer"

if [ ! -f "$CHIEF_DIR/app.py" ]; then
    echo "  ❌ Could not find Chief Decifer at:"
    echo "     $CHIEF_DIR"
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

cd "$CHIEF_DIR"
echo "  📂 Project: $CHIEF_DIR"

# Load .env from Decifer trading dir
if [ -f "$HOME/Documents/Claude/Projects/decifer trading/.env" ]; then
    set -a
    source "$HOME/Documents/Claude/Projects/decifer trading/.env"
    set +a
    echo "  🔑 API key loaded"
elif [ -f ".env" ]; then
    set -a
    source ".env"
    set +a
    echo "  🔑 API key loaded"
else
    echo "  ⚠️  No .env file found — continuing without API key"
fi

# Kill any existing dashboard on 8181
EXISTING=$(lsof -ti:8181 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "  🔄 Stopping existing dashboard (PID: $EXISTING)..."
    kill -9 $EXISTING 2>/dev/null
    sleep 1
fi

echo "  🚀 Starting Chief Decifer..."
echo "  📊 Dashboard: http://127.0.0.1:8181"
echo ""
echo "  ─────────────────────────────────────────────"
echo ""

# Open dashboard in browser after 3 seconds
(sleep 3 && open http://127.0.0.1:8181) &

# Launch dashboard
python3 app.py
