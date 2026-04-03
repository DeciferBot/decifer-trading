# Decifer Trading — Rebuild Guide

> **FOR CLAUDE:** If Amit says "rebuild decifer trading", follow this document exactly.
> Run each command using your Bash tool. Do not ask for manual input — everything
> is automated. The only prerequisite is that this Mac is signed into Amit's Apple ID
> (iCloud Keychain and iCloud Drive will supply all secrets and data automatically).

---

## One-Command Bootstrap

Run this single command from any terminal on any Mac:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DeciferBot/decifer-trading/master/scripts/setup.sh)
```

This script:
1. Installs Homebrew (if missing)
2. Installs `python@3.11`, `ta-lib`, `node`, `git` via Homebrew
3. Clones `https://github.com/DeciferBot/decifer-trading.git` to `~/Documents/Claude/Projects/decifer trading/`
4. Installs all Python packages (`requirements.txt`) + NLTK VADER
5. Writes `.env` from **iCloud Keychain** (auto-synced via Apple ID — no manual input)
6. Restores `data/historical/` (276 MB) from iCloud Drive backup
7. Loads `com.decifer.auto-push` and `com.decifer.icloud-sync` launchd daemons
8. Verifies the installation

**Estimated time: 10–15 minutes** (plus background historical data sync)

---

## After Setup

Start TWS or IB Gateway (paper account, port 7496), then:

```bash
cd ~/Documents/Claude/Projects/decifer\ trading
python3 bot.py
```

Dashboard: http://localhost:8080

---

## How Secrets Are Stored

Secrets are stored in **iCloud Keychain** under account `amit@decifer`:

| Key | iCloud Keychain Service Name |
|---|---|
| ANTHROPIC_API_KEY | `ANTHROPIC_API_KEY` |
| IBKR_ACTIVE_ACCOUNT | `IBKR_ACTIVE_ACCOUNT` |
| IBKR_PAPER_ACCOUNT | `IBKR_PAPER_ACCOUNT` |
| IBKR_LIVE_1_ACCOUNT | `IBKR_LIVE_1_ACCOUNT` |
| IBKR_LIVE_2_ACCOUNT | `IBKR_LIVE_2_ACCOUNT` |

iCloud Keychain syncs automatically to any Mac signed into the same Apple ID.

To verify secrets are in keychain:
```bash
security find-generic-password -a "amit@decifer" -s "ANTHROPIC_API_KEY" -w
```

To re-store secrets after changing `.env`:
```bash
./scripts/store-secrets.sh
```

---

## 5-Layer Backup Architecture

| Layer | Mechanism | Covers | Frequency |
|---|---|---|---|
| 1 | GitHub (`com.decifer.auto-push`) | All committed code + state | Every 2 min |
| 2 | iCloud Drive (`com.decifer.icloud-sync`) | Uncommitted changes + `data/historical/` | Every 5 min |
| 3 | iCloud Keychain | `.env` secrets | Real-time sync |
| 4 | iCloud Drive `.env` file | Backup of `.env` | Every 5 min |
| 5 | Time Machine | Full disk | Hourly |

---

## Versioning

```bash
# Check current version
python3 -c "from version import __version__, __codename__; print(f'v{__version__} — {__codename__}')"

# Bump version after a milestone
./scripts/bump-version.sh 1.4.0 "IC Weighted Scoring"
```

---

## Project Structure Reference

```
decifer trading/
├── bot.py              — main orchestrator (python3 bot.py)
├── config.py           — all configuration (risk, thresholds, accounts)
├── version.py          — semantic version (v1.3.0 "Regime Router")
├── requirements.txt    — Python dependencies
├── REBUILD.md          — this file
├── scripts/
│   ├── setup.sh        — full automated setup (entry point)
│   ├── store-secrets.sh — re-sync .env to iCloud Keychain
│   ├── bump-version.sh  — version bump + git tag
│   └── icloud-sync.sh  — iCloud rsync script
├── data/
│   ├── historical/     — 276 MB OHLCV data (iCloud backed up)
│   ├── trades.json     — trade records (git tracked)
│   └── orders.json     — order records (git tracked)
├── chief-decifer/state/ — session logs + specs (git tracked)
└── .claude/memory/     — Claude's persistent memory (git tracked)
```

---

## GitHub Repository

`https://github.com/DeciferBot/decifer-trading.git`

Branch: `master`
Latest tag: `v1.3.0`
