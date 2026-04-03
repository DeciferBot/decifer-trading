#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   DECIFER TRADING — Full Machine Setup                       ║
# ║                                                              ║
# ║   Bootstraps a complete Decifer environment from scratch.    ║
# ║   Run this on any Mac and everything will be configured.     ║
# ║                                                              ║
# ║   Usage (one command from anywhere):                         ║
# ║     bash <(curl -fsSL https://raw.githubusercontent.com/     ║
# ║       DeciferBot/decifer-trading/master/scripts/setup.sh)    ║
# ╚══════════════════════════════════════════════════════════════╝
set -e

REPO_URL="https://github.com/DeciferBot/decifer-trading.git"
INSTALL_DIR="$HOME/Documents/Claude/Projects/decifer trading"
ICLOUD_BACKUP="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Decifer-Backup/decifer-trading"
KEYCHAIN_ACCOUNT="amit@decifer"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║    DECIFER TRADING — AUTOMATED SETUP             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Homebrew ──────────────────────────────────────────────────────────
echo "[1/8] Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    echo "  → Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add to PATH for Apple Silicon
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo "  ✓ Homebrew already installed"
fi

# ── Step 2: System dependencies (read from scripts/brew-deps.txt) ─────────────
echo "[2/8] Installing system dependencies..."
BREW_DEPS_FILE="$(dirname "$0")/brew-deps.txt"
if [ -f "$BREW_DEPS_FILE" ]; then
    while IFS= read -r dep || [ -n "$dep" ]; do
        [[ "$dep" =~ ^#.*$ || -z "$dep" ]] && continue  # skip comments/blanks
        brew install "$dep" 2>/dev/null | grep -E "Installing|Already installed|✓" || true
        echo "  ✓ $dep"
    done < "$BREW_DEPS_FILE"
else
    brew install python@3.11 ta-lib node git 2>/dev/null | grep -E "Installing|Already|✓" || true
fi

# ── Step 3: Clone or update repo ──────────────────────────────────────────────
echo "[3/8] Cloning/updating repo..."
mkdir -p "$HOME/Documents/Claude/Projects"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  → Repo already exists, pulling latest..."
    git -C "$INSTALL_DIR" pull
else
    echo "  → Cloning from GitHub..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
echo "  ✓ Repo at: $INSTALL_DIR"

# ── Step 4: Python dependencies ───────────────────────────────────────────────
echo "[4/8] Installing Python packages..."
python3.11 -m pip install -r requirements.txt --quiet
python3.11 -c "import nltk; nltk.download('vader_lexicon', quiet=True)" 2>/dev/null || true
echo "  ✓ All packages installed"

# ── Step 5: Restore .env secrets ──────────────────────────────────────────────
echo "[5/8] Restoring secrets..."

HAVE_SECRETS=false

# Try iCloud Keychain first (syncs automatically across Macs)
if security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "ANTHROPIC_API_KEY" -w &>/dev/null; then
    echo "  → Found secrets in iCloud Keychain"
    ANTHROPIC_KEY=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "ANTHROPIC_API_KEY" -w)
    IBKR_ACTIVE=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "IBKR_ACTIVE_ACCOUNT" -w)
    IBKR_PAPER=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "IBKR_PAPER_ACCOUNT" -w)
    IBKR_LIVE1=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "IBKR_LIVE_1_ACCOUNT" -w)
    IBKR_LIVE2=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "IBKR_LIVE_2_ACCOUNT" -w)
    cat > .env << ENVEOF
# Decifer Trading — Environment Variables
# ⚠️ Never commit this file to version control

ANTHROPIC_API_KEY=$ANTHROPIC_KEY

# IBKR Account IDs
IBKR_ACTIVE_ACCOUNT=$IBKR_ACTIVE
IBKR_PAPER_ACCOUNT=$IBKR_PAPER
IBKR_LIVE_1_ACCOUNT=$IBKR_LIVE1
IBKR_LIVE_2_ACCOUNT=$IBKR_LIVE2
ENVEOF
    echo "  ✓ .env written from iCloud Keychain"
    HAVE_SECRETS=true
fi

# Fallback: try iCloud Drive backup copy
if [ "$HAVE_SECRETS" = false ] && [ -f "$ICLOUD_BACKUP/.env" ]; then
    echo "  → Found .env in iCloud Drive backup"
    cp "$ICLOUD_BACKUP/.env" .env
    echo "  ✓ .env restored from iCloud Drive"
    HAVE_SECRETS=true
fi

if [ "$HAVE_SECRETS" = false ]; then
    echo "  ⚠ No secrets found in iCloud Keychain or iCloud Drive."
    echo "  You will need to create .env manually with:"
    echo "    ANTHROPIC_API_KEY=..."
    echo "    IBKR_ACTIVE_ACCOUNT=..."
    echo "    IBKR_PAPER_ACCOUNT=..."
    echo "    IBKR_LIVE_1_ACCOUNT=..."
    echo "    IBKR_LIVE_2_ACCOUNT=..."
fi

# ── Step 6: Restore historical market data ────────────────────────────────────
echo "[6/8] Restoring historical market data..."
if [ -d "$ICLOUD_BACKUP/data/historical" ]; then
    echo "  → Copying 276 MB of historical OHLCV data from iCloud..."
    rsync -a "$ICLOUD_BACKUP/data/historical/" data/historical/ 2>/dev/null && echo "  ✓ Historical data restored" || echo "  ⚠ rsync failed — data/historical/ will be rebuilt on first run"
else
    echo "  ⚠ No iCloud backup found — data/historical/ will be rebuilt on first run (takes ~30 min)"
fi

# ── Step 7: Make scripts executable ──────────────────────────────────────────
echo "[7/8] Setting up launchers and daemons..."
chmod +x Decifer.command Chief.command launch_decifer.command auto-push.sh scripts/*.sh 2>/dev/null || true

# Install auto-push daemon
if [ ! -f ~/Library/LaunchAgents/com.decifer.auto-push.plist ]; then
    cp scripts/com.decifer.auto-push.plist ~/Library/LaunchAgents/ 2>/dev/null || true
fi
launchctl load ~/Library/LaunchAgents/com.decifer.auto-push.plist 2>/dev/null || true

# Install iCloud sync daemon
if [ ! -f ~/Library/LaunchAgents/com.decifer.icloud-sync.plist ]; then
    cp scripts/com.decifer.icloud-sync.plist ~/Library/LaunchAgents/ 2>/dev/null || true
fi
launchctl load ~/Library/LaunchAgents/com.decifer.icloud-sync.plist 2>/dev/null || true

# Run initial iCloud sync from Terminal (Terminal has Full Disk Access)
bash scripts/icloud-sync.sh &
echo "  ✓ Daemons loaded, initial iCloud sync started in background"

# ── Step 8: Done ─────────────────────────────────────────────────────────────
echo ""
echo "[8/8] Verifying installation..."
python3.11 -c "from version import __version__; print(f'  ✓ Decifer Trading v{__version__}')"
echo "  ✓ Setup complete"
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  NEXT STEPS:                                     ║"
echo "║  1. Start TWS or IB Gateway on port 7496         ║"
echo "║  2. Run: python3 bot.py                          ║"
echo "║  3. Open: http://localhost:8080                  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
