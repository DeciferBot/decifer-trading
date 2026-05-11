#!/usr/bin/env python3
"""
operational_health_report.py — Read-only operational health summary.

Parses the bot log file (or stdin) and emits four structured summaries:
  1. Account staleness
  2. Exit lifecycle
  3. Price source health
  4. Bracket health

Usage:
  python3 scripts/operational_health_report.py [--log LOG_FILE] [--hours N]

Defaults to the most recent decifer.log under logs/ and the last 24 hours.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── Log parsing helpers ───────────────────────────────────────────────────────

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")


def _parse_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        raw = m.group(1).replace(" ", "T")
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_lines(log_path: Path, since: datetime) -> list[str]:
    lines = []
    try:
        with log_path.open(errors="replace") as fh:
            for line in fh:
                ts = _parse_ts(line)
                if ts and ts >= since:
                    lines.append(line.rstrip())
    except FileNotFoundError:
        print(f"[warn] log file not found: {log_path}", file=sys.stderr)
    return lines


# ── Section 1: Account staleness ─────────────────────────────────────────────

def _account_summary(lines: list[str]) -> dict:
    stale_blocks = 0
    missing_blocks = 0
    stale_ages: list[float] = []
    blocked_symbols: set[str] = set()
    refresh_attempts = 0
    refresh_successes = 0
    refresh_failures = 0

    _age_re = re.compile(r"account_values_age_seconds=([\d.]+)")
    _sym_re = re.compile(r"blocked.*?([A-Z]{1,5})\b|([A-Z]{1,5}).*?blocked")
    _stale_msg_re = re.compile(r"Account values stale.*?([\d.]+)s ago")

    for line in lines:
        if "account_values_stale_block" in line:
            stale_blocks += 1
            m = _age_re.search(line)
            if m:
                stale_ages.append(float(m.group(1)))
            m2 = _stale_msg_re.search(line)
            if m2:
                stale_ages.append(float(m2.group(1)))
        if "account_values_missing_block" in line:
            missing_blocks += 1
        if "refresh_requested=true" in line:
            if "refresh_failed=true" in line:
                refresh_failures += 1
            elif "last_refresh_attempt" in line:
                refresh_attempts += 1
            else:
                refresh_successes += 1

    return {
        "total_stale_blocks": stale_blocks,
        "total_missing_blocks": missing_blocks,
        "max_staleness_s": max(stale_ages, default=0),
        "avg_staleness_s": round(sum(stale_ages) / len(stale_ages), 1) if stale_ages else 0,
        "refresh_attempts": refresh_attempts,
        "refresh_successes": refresh_successes,
        "refresh_failures": refresh_failures,
    }


# ── Section 2: Exit lifecycle ─────────────────────────────────────────────────

def _exit_summary(lines: list[str]) -> dict:
    repeated_exit_attempts: defaultdict[str, int] = defaultdict(int)
    duplicate_prevented: list[str] = []
    broker_flat_reconciled: list[str] = []
    long_exiting: list[str] = []

    _sym_re = re.compile(r"execute_sell ([A-Z]{1,5})")
    _dedup_re = re.compile(r"Exit already in flight for ([A-Z]{1,5})")
    _close_re = re.compile(r"POSITION_CLOSED.*?([A-Z]{1,5})")
    _flat_re = re.compile(r"reconcile.*?([A-Z]{1,5}).*?flat|([A-Z]{1,5}).*?CLOSED.*?reconcile", re.I)

    for line in lines:
        m = _sym_re.search(line)
        if m:
            repeated_exit_attempts[m.group(1)] += 1
        m2 = _dedup_re.search(line)
        if m2 and m2.group(1) not in duplicate_prevented:
            duplicate_prevented.append(m2.group(1))
        if "broker_flat" in line.lower() or "POSITION_CLOSED" in line:
            m3 = re.search(r"([A-Z]{1,5})", line)
            if m3 and m3.group(1) not in broker_flat_reconciled:
                broker_flat_reconciled.append(m3.group(1))

    repeated = {sym: cnt for sym, cnt in repeated_exit_attempts.items() if cnt > 1}
    return {
        "symbols_with_repeated_exit_calls": repeated,
        "duplicate_close_prevented_symbols": duplicate_prevented,
        "broker_flat_reconciliations": len(broker_flat_reconciled),
    }


# ── Section 3: Price source health ───────────────────────────────────────────

def _price_summary(lines: list[str]) -> dict:
    stale_ibkr_symbols: set[str] = set()
    drift_rejections: defaultdict[str, int] = defaultdict(int)
    fallback_used: defaultdict[str, int] = defaultdict(int)
    no_update_events = 0

    _ph_re = re.compile(r"\[price_health\] symbol=(\S+).*?decision=(\S+)")

    for line in lines:
        m = _ph_re.search(line)
        if m:
            sym, decision = m.group(1), m.group(2)
            if decision == "reject_alpaca":
                drift_rejections[sym] += 1
            elif decision == "accept_ibkr_stale_skipped":
                stale_ibkr_symbols.add(sym)
            elif decision == "fallback":
                fallback_used[sym] += 1
            elif decision == "no_update":
                no_update_events += 1

    return {
        "symbols_with_stale_ibkr_anchor": sorted(stale_ibkr_symbols),
        "drift_rejections_by_symbol": dict(drift_rejections),
        "fallback_source_used_by_symbol": dict(fallback_used),
        "no_update_events": no_update_events,
    }


# ── Section 4: Bracket health ─────────────────────────────────────────────────

def _bracket_summary(lines: list[str]) -> dict:
    no_sl = 0
    no_tp = 0
    cancelled = 0
    repaired = 0
    skipped_closed = 0

    for line in lines:
        if "BRACKET_AUDIT" not in line and "bracket" not in line.lower():
            continue
        if "missing SL" in line or "no SL" in line.lower() or "sl_order_id=None" in line:
            no_sl += 1
        if "missing TP" in line or "no TP" in line.lower() or "tp_order_id=None" in line:
            no_tp += 1
        if "Pass2 orphan" in line and "cancelled" in line:
            cancelled += 1
        if "repaired" in line.lower() or "SL placed" in line or "TP placed" in line:
            repaired += 1
        if "CLOSED" in line and "skip" in line.lower():
            skipped_closed += 1

    return {
        "positions_missing_sl": no_sl,
        "positions_missing_tp": no_tp,
        "orphan_protective_orders_cancelled": cancelled,
        "brackets_repaired": repaired,
        "skipped_closed_or_exiting": skipped_closed,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Decifer operational health report")
    parser.add_argument("--log", default=None, help="Path to log file (default: logs/decifer.log)")
    parser.add_argument("--hours", type=float, default=24.0, help="Look back N hours (default: 24)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    log_path = Path(args.log) if args.log else repo_root / "logs" / "decifer.log"
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    lines = _read_lines(log_path, since)
    if not lines:
        print(f"No log lines found in {log_path} since {since.isoformat()}", file=sys.stderr)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "log_file": str(log_path),
        "lookback_hours": args.hours,
        "lines_parsed": len(lines),
        "account_staleness": _account_summary(lines),
        "exit_lifecycle": _exit_summary(lines),
        "price_source_health": _price_summary(lines),
        "bracket_health": _bracket_summary(lines),
    }

    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  DECIFER OPERATIONAL HEALTH REPORT")
        print(f"  {report['generated_at']}  |  last {args.hours:.0f}h  |  {len(lines)} log lines")
        print(f"{'='*60}\n")
        for section, data in report.items():
            if not isinstance(data, dict):
                continue
            print(f"── {section.upper().replace('_', ' ')} ──")
            for k, v in data.items():
                print(f"  {k}: {v}")
            print()


if __name__ == "__main__":
    main()
