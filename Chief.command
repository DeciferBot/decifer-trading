#!/bin/bash
# ◆ Chief Decifer — local launcher

# Resolve the dir this script lives in (Decifer trading project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHIEF_DIR="$SCRIPT_DIR/Chief-Decifer-recovered"

# `clear` can fail when TERM isn't set (e.g. Finder launch) — ignore
clear 2>/dev/null || printf '\n'
echo ""
echo "  ◆ Chief Decifer"
echo ""

if [ ! -f "$CHIEF_DIR/app.py" ]; then
    echo "  ❌ $CHIEF_DIR/app.py not found"
    read -p "  Press Enter to exit..."
    exit 1
fi

# Require python3.11 (has all deps installed globally)
PYTHON="/usr/local/bin/python3.11"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3.11 || true)"
fi
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    echo "  ❌ python3.11 not found — install with: brew install python@3.11"
    read -p "  Press Enter to exit..."
    exit 1
fi

# Point Chief at the trading repo for git / test / code-health panels
export DECIFER_REPO_PATH="$SCRIPT_DIR"

# Load API keys from project .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Free port 8181 if something's holding it
EXISTING="$(lsof -ti:8181 2>/dev/null || true)"
if [ -n "$EXISTING" ]; then
    echo "  Stopping existing dashboard (PID: $EXISTING)..."
    kill -9 $EXISTING 2>/dev/null || true
    sleep 1
fi

echo "  Project:  $CHIEF_DIR"
echo "  Python:   $($PYTHON --version)"
echo "  Repo:     $DECIFER_REPO_PATH"
echo "  URL:      http://127.0.0.1:8181"
echo ""

cd "$CHIEF_DIR"

# Open browser once the server is ready (poll up to 30s), in background
(
    for _ in $(seq 1 60); do
        if curl -fsS -o /dev/null -m 1 http://127.0.0.1:8181/ 2>/dev/null; then
            open http://127.0.0.1:8181
            exit 0
        fi
        sleep 0.5
    done
) &

# Launch (stays in foreground so closing the terminal stops the server)
exec "$PYTHON" app.py
