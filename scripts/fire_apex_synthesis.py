#!/usr/bin/env python3
"""
fire_apex_synthesis.py — Manual one-shot Apex synthesis trigger.

Fires a positions-only Apex synthesis (execute=False) and appends the result
to data/apex_conversation_log.jsonl so the dashboard's Apex Synthesis View
refreshes immediately. Use when the bot is running and its 20-minute
_run_closed_synthesis() cooldown hasn't elapsed yet, or when the bot is not
running and you want a fresh market_read.

Reads portfolio value + daily_pnl live from the running bot's dashboard
(/api/state on http://127.0.0.1:8080); falls back to 0/0 if the dashboard is
unreachable (synthesis still works, just without portfolio context).

Never executes trades. Never modifies positions. Pure read + append.

Usage:
    python3.11 scripts/fire_apex_synthesis.py
"""
from __future__ import annotations
import json
import os
import sys
from datetime import UTC, datetime

import zoneinfo

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)

_ET = zoneinfo.ZoneInfo("America/New_York")

if os.path.exists(".env"):
    with open(".env") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ[_k.strip()] = _v.strip()


def _read_portfolio_from_dashboard() -> tuple[float, float]:
    """Fetch live portfolio_value + daily_pnl from the running bot. Returns
    (0.0, 0.0) on any failure — synthesis still runs, just without those
    figures in the prompt."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8080/api/state", timeout=2) as r:
            d = json.loads(r.read())
        pv = float(d.get("portfolio_value") or 0.0)
        pnl_raw = d.get("daily_pnl") or 0.0
        try:
            pnl = float(pnl_raw)
        except (TypeError, ValueError):
            pnl = 0.0
        return pv, pnl
    except Exception:
        return 0.0, 0.0


def _load_open_positions() -> list[dict]:
    try:
        with open("data/positions.json") as f:
            pos_data = json.load(f)
    except FileNotFoundError:
        return []
    out: list[dict] = []
    for sym, p in pos_data.items():
        out.append({
            "symbol": sym,
            "quantity": p.get("quantity", p.get("qty", 0)),
            "entry_price": p.get("entry_price", p.get("avg_cost", 0)),
            "current_price": p.get("current_price", 0) or p.get("entry_price", 0),
            "direction": p.get("direction", "LONG"),
            "trade_type": p.get("trade_type", ""),
            "entry_regime": p.get("entry_regime", ""),
            "current_pnl": p.get("current_pnl", 0) or 0,
        })
    return out


def _load_regime() -> dict:
    try:
        with open("data/intelligence/live_driver_state.json") as f:
            ld = json.load(f)
        return {"regime": "TRENDING_UP", "active_drivers": ld.get("active_drivers", [])}
    except Exception:
        return {}


def main() -> int:
    import apex_orchestrator as aorch

    open_positions = _load_open_positions()
    pv, pnl = _read_portfolio_from_dashboard()
    last_regime = _load_regime()

    portfolio_state = {
        "portfolio_value": pv,
        "daily_pnl": pnl,
        "position_count": len(open_positions),
        "position_slots_remaining": 0,
        "open_positions": open_positions,
    }
    apex_input = aorch.build_scan_cycle_apex_input(
        candidates=[],
        review_positions=[],
        portfolio_state=portfolio_state,
        regime=last_regime,
    )
    print(f"firing apex with {len(open_positions)} positions, "
          f"pv=${pv:,.0f} pnl=${pnl:+,.0f}, execute=False", flush=True)

    result = aorch._run_apex_pipeline(apex_input, {}, execute=False)
    decision = result.get("decision") or {}
    meta = decision.get("_meta") or {}
    now_str = datetime.now(_ET).strftime("%H:%M:%S")
    entry = {
        "agent": "Apex Synthesizer",
        "role": "Manual overnight synthesis — positions only, no execution",
        "time": now_str,
        "ts_utc": datetime.now(UTC).timestamp(),
        "session_character": decision.get("session_character") or "",
        "macro_bias": decision.get("macro_bias") or "",
        "market_read": decision.get("market_read") or "",
        "new_entries": [],
        "portfolio_actions": decision.get("portfolio_actions") or [],
        "latency_ms": meta.get("latency_ms"),
        "output_tokens": meta.get("output_tokens"),
    }
    with open("data/apex_conversation_log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")

    print(f"SUCCESS at {now_str}")
    print(f"  session_character: {entry['session_character']}")
    print(f"  macro_bias:        {entry['macro_bias']}")
    print(f"  market_read:       {(entry['market_read'] or '')[:400]}")
    print(f"  portfolio_actions: {len(entry['portfolio_actions'])}")
    print(f"  latency_ms:        {entry['latency_ms']}")
    print(f"  output_tokens:     {entry['output_tokens']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
