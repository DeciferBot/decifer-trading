#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   <> Decifer Trading — iMac Setup Script                    ║
# ║   Run once in Terminal: bash setup-imac.sh                  ║
# ╚══════════════════════════════════════════════════════════════╝

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

echo ""
echo "  <> Decifer Trading — iMac Setup"
echo "  ================================"
echo ""

# ── 1. Homebrew ───────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  echo "  [1/6] Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Add brew to PATH for Apple Silicon
  if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
  fi
else
  echo "  [1/6] Homebrew already installed — OK"
fi

# ── 2. Node.js ────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  echo "  [2/6] Installing Node.js..."
  brew install node
else
  echo "  [2/6] Node.js already installed — OK"
fi

# ── 3. Claude Code CLI ───────────────────────────────────────
if ! command -v claude &>/dev/null; then
  echo "  [3/6] Installing Claude Code CLI..."
  npm install -g @anthropic-ai/claude-code
else
  echo "  [3/6] Claude Code CLI already installed — OK"
fi

# ── 4. TA-Lib C library ──────────────────────────────────────
if ! brew list ta-lib &>/dev/null; then
  echo "  [4/6] Installing TA-Lib (C library)..."
  brew install ta-lib
else
  echo "  [4/6] TA-Lib already installed — OK"
fi

# ── 5. Python virtual environment + dependencies ─────────────
echo "  [5/6] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
python3 -c "import nltk; nltk.download('vader_lexicon', quiet=True)"
echo "  Python dependencies installed — OK"

# ── 6. State directories ──────────────────────────────────────
echo "  [6/6] Creating state directories..."
mkdir -p chief-decifer/state/sessions
mkdir -p chief-decifer/state/research
mkdir -p chief-decifer/state/specs
touch chief-decifer/state/backlog.json

# ── .env check ───────────────────────────────────────────────
echo ""
if [ ! -f .env ]; then
  echo "  Creating .env template..."
  cat > .env << 'EOF'
ANTHROPIC_API_KEY=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
IBKR_PAPER_ACCOUNT=
IBKR_ACTIVE_ACCOUNT=
TELEGRAM_BOT_TOKEN=
EOF
  echo "  ⚠️  .env created — fill in IBKR_PAPER_ACCOUNT before launching"
else
  echo "  .env already exists — OK"
fi

# ── Fix ibkr_host for local run ───────────────────────────────
# Reset to localhost since bot runs locally on this iMac
sed -i '' 's/"ibkr_host":.*"192\.[0-9.]*"/"ibkr_host":        "127.0.0.1"/' config.py

echo ""
echo "  ✅  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Fill in your IBKR paper account ID in .env"
echo "  2. Make sure TWS is running on port 7496 with API enabled"
echo "  3. Launch Decifer:  source venv/bin/activate && python3 bot.py"
echo "  4. Or launch Claude Code:  claude"
echo ""
