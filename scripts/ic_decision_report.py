#!/usr/bin/env python3
"""
ic_decision_report.py — Offline IC decision funnel report.

Joins signals_log.jsonl + ic_decision_events.jsonl + training_records.jsonl
on observation_id to show how candidates flowed through the pipeline.

Usage:
    python3 scripts/ic_decision_report.py [--days N] [--scan-id SCAN_ID]

Output sections:
    0. Summary — total events, date range
    1. Funnel  — scored → below_threshold → passed_to_apex → apex_selected
                 → apex_rejected → executed → order_failed
    2. Linkage — executed trades with training_record linkage
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

# ── Path setup ─────────────────────────────────────────────────────────────────

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from config import CONFIG  # noqa: E402

_DATA_DIR = CONFIG.get("data_dir", os.path.join(_REPO, "data"))
_SIGNALS_LOG = CONFIG.get("signals_log", os.path.join(_DATA_DIR, "signals_log.jsonl"))
_DECISION_EVENTS = os.path.join(_DATA_DIR, "ic_decision_events.jsonl")
_TRAINING_RECORDS = os.path.join(_DATA_DIR, "training_records.jsonl")


# ── Loaders ────────────────────────────────────────────────────────────────────

def _load_jsonl(path: str, since_date: str | None = None) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if since_date:
                    ts = rec.get("ts") or rec.get("ts_utc") or rec.get("ts_close") or ""
                    if ts and ts[:10] < since_date:
                        continue
                records.append(rec)
            except Exception:
                continue
    return records


def _cutoff_date(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).date().isoformat()


# ── Main ───────────────────────────────────────────────────────────────────────

def run(days: int = 30, scan_id_filter: str | None = None) -> None:
    since = _cutoff_date(days)
    print(f"\n{'='*64}")
    print(f"  IC Decision Funnel Report — last {days} days (since {since})")
    print(f"{'='*64}\n")

    # ── Load signals log ───────────────────────────────────────────────────────
    signals = _load_jsonl(_SIGNALS_LOG, since_date=since)
    if scan_id_filter:
        signals = [s for s in signals if s.get("scan_id") == scan_id_filter]
    sig_by_obs: dict[str, dict] = {}
    for s in signals:
        obs = s.get("observation_id")
        if obs:
            sig_by_obs[obs] = s

    # ── Load decision events ───────────────────────────────────────────────────
    events = _load_jsonl(_DECISION_EVENTS, since_date=since)
    if scan_id_filter:
        events = [e for e in events if e.get("scan_id") == scan_id_filter]
    # Latest event per (observation_id, decision_status) pair
    # Group by observation_id — keep all distinct statuses
    obs_statuses: dict[str, set[str]] = defaultdict(set)
    obs_latest_event: dict[str, dict] = {}
    for evt in events:
        obs = evt.get("observation_id")
        status = evt.get("decision_status")
        if obs and status:
            obs_statuses[obs].add(status)
            obs_latest_event[obs] = evt

    # ── Load training records ─────────────────────────────────────────────────
    training = _load_jsonl(_TRAINING_RECORDS, since_date=since)
    tr_by_obs: dict[str, dict] = {}
    for t in training:
        obs = t.get("observation_id")
        if obs:
            tr_by_obs[obs] = t

    # ── Funnel counts ─────────────────────────────────────────────────────────
    total_signals = len(signals)
    below_threshold = sum(1 for obs, ss in obs_statuses.items() if "below_threshold" in ss)
    passed_to_apex  = sum(1 for obs, ss in obs_statuses.items() if "passed_to_apex" in ss)
    apex_selected   = sum(1 for obs, ss in obs_statuses.items() if "apex_selected" in ss)
    apex_rejected   = sum(1 for obs, ss in obs_statuses.items() if "apex_rejected" in ss)
    executed        = sum(1 for obs, ss in obs_statuses.items() if "executed" in ss)
    order_failed    = sum(1 for obs, ss in obs_statuses.items() if "order_failed" in ss)
    risk_blocked    = sum(1 for obs, ss in obs_statuses.items() if "risk_blocked" in ss)

    # Signals with no decision event (status unknown / not yet processed)
    with_events = set(obs_statuses.keys())
    no_event = sum(1 for obs in sig_by_obs if obs not in with_events)

    # Executed trades with training record linkage
    executed_obs   = {obs for obs, ss in obs_statuses.items() if "executed" in ss}
    linked_to_training = sum(1 for obs in executed_obs if obs in tr_by_obs)

    print("Section 1 — Pipeline Funnel")
    print(f"  Signals logged (signals_log.jsonl) : {total_signals:>7,}")
    print(f"  Below base threshold               : {below_threshold:>7,}")
    print(f"  Passed to Apex                     : {passed_to_apex:>7,}")
    print(f"    └ Apex selected                  : {apex_selected:>7,}")
    print(f"    └ Apex rejected                  : {apex_rejected:>7,}")
    print(f"  Executed (ORDER_INTENT written)    : {executed:>7,}")
    print(f"  Order failed                       : {order_failed:>7,}")
    print(f"  Risk blocked                       : {risk_blocked:>7,}")
    print(f"  No decision event yet              : {no_event:>7,}")
    print()

    print("Section 2 — Training Record Linkage")
    print(f"  Executed trades                    : {len(executed_obs):>7,}")
    print(f"  Linked to training_records.jsonl   : {linked_to_training:>7,}")
    unlinked = len(executed_obs) - linked_to_training
    print(f"  Unlinked (no training record yet)  : {unlinked:>7,}")
    print()

    # IC eligibility breakdown from signals_log
    ic_elig   = sum(1 for s in signals if s.get("ic_eligible") is True)
    ic_inelig = sum(1 for s in signals if s.get("ic_eligible") is False)
    ic_legacy = total_signals - ic_elig - ic_inelig
    print("Section 3 — IC Eligibility (signals_log)")
    print(f"  ic_eligible = True                 : {ic_elig:>7,}")
    print(f"  ic_eligible = False                : {ic_inelig:>7,}")
    print(f"  ic_eligible field absent (legacy)  : {ic_legacy:>7,}")
    print()

    # Direction breakdown
    dirs: dict[str, int] = defaultdict(int)
    for s in signals:
        dirs[s.get("direction", "missing")] += 1
    print("Section 4 — Direction Breakdown (signals_log)")
    for d, n in sorted(dirs.items(), key=lambda x: -x[1]):
        print(f"  {d:<32}: {n:>7,}")
    print()

    print("(End of report)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IC decision funnel report")
    parser.add_argument("--days", type=int, default=30, help="Look-back window in calendar days")
    parser.add_argument("--scan-id", type=str, default=None, help="Filter to a specific scan_id")
    args = parser.parse_args()
    run(days=args.days, scan_id_filter=args.scan_id)
