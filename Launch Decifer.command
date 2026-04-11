#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   <> Decifer Trading — Launch Script                         ║
# ║   Double-click to start the bot + dashboard                  ║
# ╚══════════════════════════════════════════════════════════════╝

clear
echo ""
echo "  ██████╗ ███████╗ ██████╗██╗███████╗███████╗██████╗ "
echo "  ██╔══██╗██╔════╝██╔════╝██║██╔════╝██╔════╝██╔══██╗"
echo "  ██║  ██║█████╗  ██║     ██║█████╗  █████╗  ██████╔╝"
echo "  ██║  ██║██╔══╝  ██║     ██║██╔══╝  ██╔══╝  ██╔══██╗"
echo "  ██████╔╝███████╗╚██████╗██║██║     ███████╗██║  ██║"
echo "  ╚═════╝ ╚══════╝ ╚═════╝╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝"
echo ""
echo "  Autonomous AI Trading System"
echo ""

# ── Locate project directory ───────────────────────────────────────────────────
PROJ_DIR=""
for d in \
    "$(dirname "$0")" \
    "$HOME/Desktop/decifer trading" \
    "$HOME/Documents/Claude/Projects/decifer trading" \
; do
    if [ -f "$d/bot.py" ]; then
        PROJ_DIR="$d"
        break
    fi
done

if [ -z "$PROJ_DIR" ]; then
    echo "  ERROR: Could not find bot.py"
    read -p "  Press Enter to exit..."
    exit 1
fi

cd "$PROJ_DIR"
echo "  Project : $PROJ_DIR"

# ── Set up Python (pyenv 3.11) ─────────────────────────────────────────────────
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &>/dev/null; then
    eval "$(pyenv init - bash)"
    PYTHON="python3.11"
else
    PYTHON="python3"
fi

# ── Set up ta-lib library path ─────────────────────────────────────────────────
export DYLD_LIBRARY_PATH="$HOME/.local/lib:$DYLD_LIBRARY_PATH"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -a; source .env; set +a
    echo "  Secrets : loaded from .env"
else
    # Fallback: iCloud Keychain
    ANTHROPIC_API_KEY=$(security find-generic-password -a "amit@decifer" -s "ANTHROPIC_API_KEY" -w 2>/dev/null)
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        export ANTHROPIC_API_KEY
        export IBKR_ACTIVE_ACCOUNT=$(security find-generic-password -a "amit@decifer" -s "IBKR_ACTIVE_ACCOUNT" -w 2>/dev/null)
        export IBKR_PAPER_ACCOUNT=$(security find-generic-password -a "amit@decifer" -s "IBKR_PAPER_ACCOUNT" -w 2>/dev/null)
        export IBKR_LIVE_1_ACCOUNT=$(security find-generic-password -a "amit@decifer" -s "IBKR_LIVE_1_ACCOUNT" -w 2>/dev/null)
        export IBKR_LIVE_2_ACCOUNT=$(security find-generic-password -a "amit@decifer" -s "IBKR_LIVE_2_ACCOUNT" -w 2>/dev/null)
        echo "  Secrets : loaded from iCloud Keychain"
    else
        echo ""
        echo "  ERROR: No .env file and no secrets in iCloud Keychain."
        echo "  Create .env with your ANTHROPIC_API_KEY and IBKR account IDs."
        read -p "  Press Enter to exit..."
        exit 1
    fi
fi

# ── Check Python version ───────────────────────────────────────────────────────
PY_VERSION=$($PYTHON --version 2>&1)
echo "  Python  : $PY_VERSION"

# ── Ensure required directories exist ────────────────────────────────────────
mkdir -p logs data/historical state

# ── Kill any existing bot on port 8080 ────────────────────────────────────────
EXISTING=$(lsof -ti:8080 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "  Restart : stopping existing bot (PID $EXISTING)..."
    kill -9 $EXISTING 2>/dev/null
    sleep 1
fi

# ── Show version ───────────────────────────────────────────────────────────────
VERSION=$($PYTHON -c "from version import __version__, __codename__; print(f'v{__version__} — {__codename__}')" 2>/dev/null || echo "unknown")
echo "  Version : $VERSION"
echo ""
echo "  ─────────────────────────────────────────────────────"
echo "  Dashboard : http://localhost:8080"
echo "  Stop      : Ctrl+C"
echo "  ─────────────────────────────────────────────────────"
echo ""

# ── Open dashboard in browser after 4 seconds ─────────────────────────────────
(sleep 4 && open http://localhost:8080) &

# ── Launch bot (foreground — Ctrl+C to stop) ──────────────────────────────────
$PYTHON bot.py
