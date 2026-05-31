"""
One-time watchlist analysis script.

Scores a fixed set of symbols through the signal engine, runs a standalone
Apex call for advisory conviction, then POSTs the results to the Vercel
/api/send-analysis route which emails via Resend.

Usage:
    python3 scripts/watchlist_analysis.py

No trading. No order submission. No state mutation.
"""

import json
import os
import sys
import requests
from datetime import datetime, timezone

# ── Bootstrap repo path ──────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from signals import score_universe
from market_intelligence import apex_call
from scanner import get_market_regime

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS = ["ARM", "NVDA", "TSM", "ALAB", "NOW", "IBM", "ORCL", "MSFT"]

VERCEL_URL = os.getenv(
    "VERCEL_APP_URL", "https://mobile.decifertrading.com"
)
SEND_ENDPOINT = f"{VERCEL_URL}/api/send-analysis"

RECIPIENTS = ["amit@decifer.io", "rehan.merchant@engworldwide.com"]


def main() -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Watchlist analysis starting")
    print(f"Symbols: {', '.join(SYMBOLS)}")

    # ── 1. Market regime ─────────────────────────────────────────────────────
    print("Fetching market regime...")
    try:
        regime_info = get_market_regime()
        regime = regime_info if isinstance(regime_info, str) else regime_info.get("regime", "UNKNOWN")
    except Exception as e:
        print(f"  Regime fetch failed ({e}), using UNKNOWN")
        regime = "UNKNOWN"
    print(f"  Regime: {regime}")

    # ── 2. Score symbols ──────────────────────────────────────────────────────
    print("Scoring symbols through signal engine...")
    try:
        above_threshold, all_scored = score_universe(SYMBOLS, regime=regime)
    except Exception as e:
        print(f"  score_universe failed: {e}")
        sys.exit(1)

    scored_map = {r["symbol"]: r for r in all_scored}
    print(f"  Scored {len(all_scored)} symbols, {len(above_threshold)} above threshold")

    # ── 3. Build Apex input ───────────────────────────────────────────────────
    # Pass all symbols as candidates regardless of threshold — advisory only.
    candidates = []
    for r in sorted(all_scored, key=lambda x: x["score"], reverse=True):
        candidates.append({
            "symbol": r["symbol"],
            "score": r["score"],
            "direction": r.get("direction", "NEUTRAL"),
            "signal": r.get("signal", ""),
            "source": "watchlist_analysis",
            "reason_to_care": f"Watchlist review — score {r['score']:.1f}, direction {r.get('direction', 'NEUTRAL')}",
        })

    apex_input = {
        "trigger_type": "WATCHLIST_ADVISORY",
        "trigger_context": {
            "note": (
                "Advisory-only analysis of a pre-selected watchlist. "
                "No order submission. Assess conviction and risks for each symbol."
            ),
            "symbols": SYMBOLS,
        },
        "track_a": {"candidates": candidates},
        "track_b": [],
        "market_context": {
            "regime": {"regime": regime},
            "overnight_research": None,
            "options_flow": [],
            "driver_notes": [],
        },
        "portfolio_state": {},
        "scan_ts": datetime.now(timezone.utc).isoformat(),
        "recently_closed": [],
        "failed_thesis_closed": [],
    }

    # ── 4. Apex call ──────────────────────────────────────────────────────────
    print("Running Apex advisory call...")
    try:
        decision = apex_call(apex_input)
    except Exception as e:
        print(f"  Apex call failed: {e}")
        decision = {}

    apex_entries = (decision.get("new_entries") or [])
    apex_reasoning = decision.get("reasoning") or decision.get("summary") or ""
    print(f"  Apex returned {len(apex_entries)} entries, reasoning: {bool(apex_reasoning)}")

    # ── 5. Build payload for Vercel email route ───────────────────────────────
    scored_rows = []
    for sym in SYMBOLS:
        r = scored_map.get(sym, {})
        apex_entry = next((e for e in apex_entries if e.get("symbol") == sym), None)
        scored_rows.append({
            "symbol": sym,
            "score": round(r.get("score", 0), 1),
            "direction": r.get("direction", "N/A"),
            "signal": r.get("signal", "N/A"),
            "apex_action": apex_entry.get("action") if apex_entry else None,
            "apex_conviction": apex_entry.get("conviction") if apex_entry else None,
            "apex_reasoning": apex_entry.get("reasoning") or apex_entry.get("rationale") if apex_entry else None,
        })

    payload = {
        "recipients": RECIPIENTS,
        "subject": f"Decifer Watchlist Analysis — {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')} UTC",
        "regime": regime,
        "scored_rows": scored_rows,
        "apex_summary": apex_reasoning,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Advisory only. Not a trade instruction. Not financial advice.",
    }

    # ── 6. POST to Vercel email route ─────────────────────────────────────────
    print(f"Sending results to {SEND_ENDPOINT}...")
    try:
        resp = requests.post(
            SEND_ENDPOINT,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"  Email sent: {result}")
    except Exception as e:
        print(f"  Email delivery failed: {e}")
        print("\n── Fallback: analysis results ──────────────────────────────")
        print(json.dumps(payload, indent=2, default=str))
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
