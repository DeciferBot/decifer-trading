# Decifer Trading — Rebuild Guide

Complete runbook to rebuild the bot on a new machine from scratch.
**Estimated time: 15 minutes** (+ background iCloud historical data sync)

---

## Prerequisites

```bash
brew install python@3.11 ta-lib node
```

---

## Steps

### 1. Clone the repo
```bash
git clone https://github.com/DeciferBot/decifer-trading.git
cd decifer-trading
```

### 2. Create .env from 1Password
Open 1Password → search "Decifer Trading .env" → copy contents into `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
IBKR_ACTIVE_ACCOUNT=DU...
IBKR_PAPER_ACCOUNT=DU...
IBKR_LIVE_1_ACCOUNT=U...
IBKR_LIVE_2_ACCOUNT=U...
```

### 3. Install Python dependencies
```bash
python3.11 -m pip install -r requirements.txt
python3 -c "import nltk; nltk.download('vader_lexicon')"
```

### 4. Restore historical market data
Historical OHLCV data (276 MB) syncs from iCloud automatically.
Copy from iCloud backup:
```bash
cp -r ~/Library/Mobile\ Documents/com~apple~CloudDocs/Decifer-Backup/decifer-trading/data/historical/ data/historical/
```

### 5. Activate backup daemons
```bash
./setup-auto-push.sh   # auto-push commits to GitHub every 2 min
launchctl load ~/Library/LaunchAgents/com.decifer.icloud-sync.plist   # iCloud sync every 5 min
```

### 6. Verify daemons running
```bash
launchctl list | grep decifer
# Should show: com.decifer.auto-push AND com.decifer.icloud-sync
```

### 7. Enable Time Machine
System Settings → General → Time Machine → add a backup disk.

### 8. Make launchers executable
```bash
chmod +x Decifer.command Chief.command launch_decifer.command
```

### 9. Start IBKR Gateway
Open TWS or IB Gateway, log in to paper account, ensure API enabled on port 7496.

### 10. Launch the bot
```bash
python3 bot.py
```
Dashboard opens at http://localhost:8080

---

## Backup Layers (for reference)

| Layer | Mechanism | Covers |
|---|---|---|
| 1 | GitHub (auto-pushed) | All code + state files |
| 2 | iCloud sync (every 5 min) | Uncommitted changes + historical data |
| 3 | 1Password | `.env` secrets |
| 4 | Time Machine | Everything, hourly |

---

## Versioning

Check current version: `python3 -c "from version import __version__; print(__version__)"`

Bump version after a milestone:
```bash
./scripts/bump-version.sh 1.4.0 "IC Weighted Scoring"
```
