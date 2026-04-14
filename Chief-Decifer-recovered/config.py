"""
Chief Decifer configuration.
All paths and settings are read from .env — never hardcoded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Path to the decifer-trading repo (for git history, test results, code health).
# Required — no fallback. Chief's state is ALWAYS at $DECIFER_REPO_PATH/chief-decifer/state/.
DECIFER_REPO_PATH = Path(os.getenv("DECIFER_REPO_PATH", "")).expanduser()
if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
    # Fallback: assume this file lives inside the repo at chief-decifer/config.py or
    # Chief-Decifer-recovered/config.py (both under the trading repo root).
    DECIFER_REPO_PATH = Path(__file__).parent.parent

# Single sacred state dir — everything Chief reads and Cowork writes.
STATE_DIR     = DECIFER_REPO_PATH / "chief-decifer" / "state"
SESSIONS_DIR  = STATE_DIR / "sessions"
RESEARCH_DIR  = STATE_DIR / "research"
SPECS_DIR     = STATE_DIR / "specs"
BACKLOG_FILE  = STATE_DIR / "backlog.json"
VISION_FILE   = STATE_DIR / "vision.json"

# Chief-internal compute artifacts (catalyst snapshots, brain analysis, activity log).
# Not part of the Cowork↔Chief data contract — used only by Chief's panels.
INTERNAL_DIR  = STATE_DIR / "internal"
DOCS_DIR      = INTERNAL_DIR / "docs"
ACTIVITY_FILE = INTERNAL_DIR / "activity.jsonl"

# Dashboard settings
PORT = int(os.getenv("PORT", 8181))
REFRESH_INTERVAL_MS = int(os.getenv("REFRESH_INTERVAL_MS", 30_000))

# Feature lifecycle order for pipeline display
LIFECYCLE_ORDER = ["spec_complete", "backlog", "in_progress", "complete", "blocked"]

LIFECYCLE_COLORS = {
    "backlog":       "secondary",
    "spec_complete": "info",
    "in_progress":   "warning",
    "complete":      "success",
    "blocked":       "danger",
}

LIFECYCLE_LABELS = {
    "backlog":       "Backlog",
    "spec_complete": "Proposal",
    "in_progress":   "In Progress",
    "complete":      "Shipped",
    "blocked":       "Blocked",
}

# ── Catalyst Signal Layer ──────────────────────────────────────────────────────

CATALYST_DIR = INTERNAL_DIR / "catalyst"

# Thresholds for the M&A target fundamental screen (signals/catalyst_screen.py).
# Override any subset here; defaults fill in the rest.
CATALYST_THRESHOLDS = {
    "ev_revenue_max":     3.0,    # EV/Revenue below this = cheap relative to peers
    "revenue_growth_min": 0.10,   # ≥10% YoY revenue growth
    "market_cap_min":     1e9,    # $1 B minimum
    "market_cap_max":     50e9,   # $50 B maximum (larger deals are rarer)
    "target_sectors": [
        "Healthcare",
        "Technology",
        "Industrials",
        "Communication Services",
        "Consumer Discretionary",
    ],
}

# How often (seconds) the EDGAR monitor should be polled when running as a
# background task.  10 minutes matches the SEC RSS update cadence.
EDGAR_POLL_INTERVAL = int(os.getenv("EDGAR_POLL_INTERVAL", 600))

# How often (seconds) to re-run the fundamental M&A screen.
# ~500 tickers × 0.15 s throttle ≈ 75 s minimum; 4 hours is comfortable.
CATALYST_SCREEN_INTERVAL = int(os.getenv("CATALYST_SCREEN_INTERVAL", 4 * 3600))  # 14400

# How often (seconds) to re-run the options anomaly scan.
OPTIONS_ANOMALY_INTERVAL = int(os.getenv("OPTIONS_ANOMALY_INTERVAL", 30 * 60))   # 1800

# How often (seconds) to re-run the multi-source sentiment scoring pipeline.
# Yahoo RSS + Finviz fetch + Claude API call per ticker; 15 min is comfortable.
SENTIMENT_SCORER_INTERVAL = int(os.getenv("SENTIMENT_SCORER_INTERVAL", 15 * 60))  # 900
