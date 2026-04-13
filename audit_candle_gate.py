"""
audit_candle_gate.py — Trade Journal Candle Gate Audit
=======================================================
Reads data/trades.json and audits every OPEN record for candle gate compliance.

Categories
----------
UNKNOWN  — trade predates candle_gate field logging (pre-fix); exclude from
           performance baseline as gate status cannot be determined.
ANOMALY  — candle_gate == "BLOCKED" on an OPEN record; should never happen
           because a blocked signal should not reach order execution.
SKIPPED  — candle_gate == "SKIPPED" (MTF hard gate fired first); technically
           valid — note but do not flag as invalid.
PASS     — candle_gate == "PASS"; gate evaluated and passed.

Output
------
Prints a summary to stdout.
Writes flagged trades (UNKNOWN + ANOMALY) to data/candle_gate_audit.json.
"""

import json
import os
import sys
from datetime import datetime

TRADE_LOG = os.path.join(os.path.dirname(__file__), "data", "trades.json")
AUDIT_OUT = os.path.join(os.path.dirname(__file__), "data", "candle_gate_audit.json")


def _load_trades(path: str) -> list:
    if not os.path.exists(path):
        print(f"[AUDIT] Trade log not found: {path}")
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[AUDIT] Failed to load trade log: {e}")
        return []


def run_audit(trade_log_path: str = TRADE_LOG, output_path: str = AUDIT_OUT) -> dict:
    """
    Audit trade journal for candle gate compliance.

    Returns a summary dict:
        {
            "total_open":      int,
            "valid":           int,   # candle_gate == "PASS"
            "skipped":         int,   # candle_gate == "SKIPPED" (MTF fired first)
            "flagged_unknown": int,   # no candle_gate field (pre-fix trades)
            "flagged_anomaly": int,   # candle_gate == "BLOCKED" on OPEN record
            "flagged_trades":  list,  # UNKNOWN + ANOMALY trade records
        }
    """
    trades = _load_trades(trade_log_path)
    open_trades = [t for t in trades if t.get("action") == "OPEN"]

    valid = []
    skipped = []
    flagged_unknown = []
    flagged_anomaly = []

    for trade in open_trades:
        gate = trade.get("candle_gate")
        if gate is None:
            flagged_unknown.append({**trade, "_audit_flag": "UNKNOWN"})
        elif gate == "BLOCKED":
            flagged_anomaly.append({**trade, "_audit_flag": "ANOMALY"})
        elif gate == "SKIPPED":
            skipped.append(trade)
        else:
            # "PASS" or any future valid value
            valid.append(trade)

    flagged = flagged_unknown + flagged_anomaly

    summary = {
        "audit_timestamp": datetime.utcnow().isoformat() + "Z",
        "total_open": len(open_trades),
        "valid": len(valid),
        "skipped": len(skipped),
        "flagged_unknown": len(flagged_unknown),
        "flagged_anomaly": len(flagged_anomaly),
        "flagged_trades": flagged,
    }

    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    return summary


def _print_summary(s: dict) -> None:
    print("\n── Candle Gate Audit ─────────────────────────────────")
    print(f"  Total OPEN records:   {s['total_open']}")
    print(f"  Valid (gate PASS):    {s['valid']}")
    print(f"  Skipped (MTF first):  {s['skipped']}")
    print(f"  Flagged UNKNOWN:      {s['flagged_unknown']}  ← pre-fix; exclude from baseline")
    print(f"  Flagged ANOMALY:      {s['flagged_anomaly']}  ← BLOCKED signal reached order layer")
    print(f"\n  Output written to:    {AUDIT_OUT}")

    if s["flagged_anomaly"] > 0:
        print("\n  ⚠️  ANOMALY TRADES (candle_gate=BLOCKED on OPEN record):")
        for t in s["flagged_trades"]:
            if t.get("_audit_flag") == "ANOMALY":
                print(
                    f"     {t.get('timestamp', '?')}  {t.get('symbol', '?')}  "
                    f"score={t.get('score', '?')}  regime={t.get('regime', '?')}"
                )

    if s["flagged_unknown"] > 0:
        print(
            f"\n  ℹ️  {s['flagged_unknown']} trade(s) predate gate logging — "
            "exclude from performance calculations until gate is verified."
        )
    print("──────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    summary = run_audit()
    _print_summary(summary)
    # Exit non-zero if anomalies found (useful in CI)
    if summary["flagged_anomaly"] > 0:
        sys.exit(1)
