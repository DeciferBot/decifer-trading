#!/usr/bin/env python3
"""
apex_shadow_report.py — Phase 7C.2 shadow metrics roll-up.

Read-only operator tool. Reads the Apex shadow-mode audit artifacts:
  - data/apex_shadow_log.jsonl     (from apex_orchestrator.log_shadow_result)
  - data/apex_divergence_log.jsonl (from apex_divergence.write_divergence_record)

…and produces an aggregated JSON + human-readable report for operator review.

This script imports NOTHING from the trading runtime (no orders_core, no
market_intelligence, no bot_*). It is a pure analysis tool: open files, count,
percentile, write artifact. Zero coupling to live behavior.

Usage:
    python3 scripts/apex_shadow_report.py
        → writes data/apex_shadow_reports/report_<UTCTS>.{json,txt}
        → prints the text report to stdout

    python3 scripts/apex_shadow_report.py --since 2026-04-20
        → filter records by UTC date (inclusive on `ts` field)

    python3 scripts/apex_shadow_report.py --shadow-log /path --divergence-log /path
        → override default paths

The output schema is stable for Phase 7C.2. Future phases can extend it but
should not break existing keys.

Metrics produced (per the Phase 7C.2 spec):
  - counts by divergence category                    → events.by_category
  - counts by severity                               → events.by_severity
  - fallback rate                                    → apex.fallback_rate
  - schema reject rate                               → apex.schema_reject_rate
  - semantic removal rate (proxy: AVOID rejections)  → apex.semantic_rejection_rate
  - p50 / p95 latency                                → apex.latency.p50_ms / p95_ms
  - entries per side                                 → entries.legacy / entries.apex
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from statistics import median
from typing import Any

# Defaults mirror apex_orchestrator and apex_divergence module constants.
_DEFAULT_SHADOW_LOG = "data/apex_shadow_log.jsonl"
_DEFAULT_DIVERGENCE_LOG = "data/apex_divergence_log.jsonl"
_DEFAULT_REPORT_DIR = "data/apex_shadow_reports"


# ── Core aggregation ────────────────────────────────────────────────────────

def load_jsonl(path: str) -> list[dict]:
    """Read a .jsonl file. Missing file → []. Corrupt lines are skipped."""
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def filter_by_date(
    records: list[dict],
    since: str | None,
    until: str | None,
    ts_field: str = "ts",
) -> list[dict]:
    """Inclusive UTC date filter on the record's ts field (ISO 8601 string)."""
    if not since and not until:
        return records
    out = []
    for r in records:
        ts = r.get(ts_field)
        if not ts:
            continue
        date_str = ts[:10]  # YYYY-MM-DD prefix of ISO timestamp
        if since and date_str < since:
            continue
        if until and date_str > until:
            continue
        out.append(r)
    return out


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Returns None on empty input."""
    if not values:
        return None
    vs = sorted(values)
    if pct <= 0:
        return vs[0]
    if pct >= 100:
        return vs[-1]
    k = max(0, min(len(vs) - 1, int(round((pct / 100.0) * (len(vs) - 1)))))
    return vs[k]


def aggregate_shadow(shadow_records: list[dict]) -> dict:
    """Fallback/schema-reject/semantic-removal counts + latency percentiles."""
    total = len(shadow_records)
    fallback = 0
    schema_reject = 0
    would_dispatch_total = 0
    rejected_total = 0
    latencies: list[float] = []
    attempts: list[float] = []
    input_tokens: list[float] = []
    output_tokens: list[float] = []
    cache_read_tokens: list[float] = []
    by_trigger: Counter[str] = Counter()
    models: Counter[str] = Counter()

    for rec in shadow_records:
        by_trigger[rec.get("trigger_type") or "UNKNOWN"] += 1
        would_dispatch_total += len(rec.get("would_dispatch") or [])
        rejected_total += len(rec.get("rejected") or [])

        meta = rec.get("apex_meta") or {}
        err = (meta.get("error") or "").lower() if isinstance(meta, dict) else ""
        if err:
            if "schema" in err:
                schema_reject += 1
            else:
                fallback += 1

        if isinstance(meta, dict):
            if meta.get("latency_ms") is not None:
                latencies.append(float(meta["latency_ms"]))
            if meta.get("attempts") is not None:
                attempts.append(float(meta["attempts"]))
            if meta.get("input_tokens") is not None:
                input_tokens.append(float(meta["input_tokens"]))
            if meta.get("output_tokens") is not None:
                output_tokens.append(float(meta["output_tokens"]))
            if meta.get("cache_read_tokens") is not None:
                cache_read_tokens.append(float(meta["cache_read_tokens"]))
            if meta.get("model"):
                models[meta["model"]] += 1

    entries_total = would_dispatch_total + rejected_total

    return {
        "total_shadow_cycles": total,
        "by_trigger_type": dict(by_trigger),
        "apex": {
            "fallback_count": fallback,
            "fallback_rate": _rate(fallback, total),
            "schema_reject_count": schema_reject,
            "schema_reject_rate": _rate(schema_reject, total),
            "semantic_rejection_count": rejected_total,
            "semantic_rejection_rate": _rate(rejected_total, entries_total),
            "models": dict(models),
            "latency": {
                "n": len(latencies),
                "p50_ms": percentile(latencies, 50),
                "p95_ms": percentile(latencies, 95),
                "max_ms": max(latencies) if latencies else None,
                "mean_ms": (sum(latencies) / len(latencies)) if latencies else None,
            },
            "attempts": {
                "n": len(attempts),
                "mean": (sum(attempts) / len(attempts)) if attempts else None,
                "p95": percentile(attempts, 95),
            },
            "tokens": {
                "input_mean": (sum(input_tokens) / len(input_tokens)) if input_tokens else None,
                "output_mean": (sum(output_tokens) / len(output_tokens)) if output_tokens else None,
                "cache_read_mean": (sum(cache_read_tokens) / len(cache_read_tokens)) if cache_read_tokens else None,
            },
        },
        "entries": {
            "would_dispatch_total": would_dispatch_total,
            "semantic_rejected_total": rejected_total,
        },
    }


def aggregate_divergence(divergence_records: list[dict]) -> dict:
    """Event category/severity counts + per-side entry totals."""
    total_records = len(divergence_records)
    by_category: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    legacy_entries = 0
    apex_entries = 0
    legacy_pm_actions = 0
    apex_pm_actions = 0
    agree_cycles = 0
    by_trigger: Counter[str] = Counter()

    for rec in divergence_records:
        by_trigger[rec.get("trigger_type") or "UNKNOWN"] += 1
        events = rec.get("events") or []
        # A cycle "agrees" if its only event is AGREE (no per-entry divergence).
        if events and all(ev.get("category") == "AGREE" for ev in events):
            agree_cycles += 1
        for ev in events:
            by_category[ev.get("category") or "UNKNOWN"] += 1
            by_severity[ev.get("severity") or "UNKNOWN"] += 1

        legacy = rec.get("legacy") or {}
        apex = rec.get("apex") or {}
        legacy_entries += len(legacy.get("new_entries") or [])
        apex_entries += len(apex.get("new_entries") or [])
        legacy_pm_actions += len(legacy.get("portfolio_actions") or [])
        apex_pm_actions += len(apex.get("portfolio_actions") or [])

    events_total = sum(by_category.values())
    # AGREE-rate across cycles is a directional benchmark (not a hard gate).
    agree_rate_cycles = _rate(agree_cycles, total_records)

    return {
        "total_divergence_records": total_records,
        "by_trigger_type": dict(by_trigger),
        "events": {
            "total": events_total,
            "by_category": dict(by_category),
            "by_severity": dict(by_severity),
        },
        "agree_cycles": agree_cycles,
        "agree_rate_cycles": agree_rate_cycles,
        "entries": {
            "legacy_new_entries_total": legacy_entries,
            "apex_new_entries_total": apex_entries,
            "legacy_portfolio_actions_total": legacy_pm_actions,
            "apex_portfolio_actions_total": apex_pm_actions,
        },
    }


def _rate(numer: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return round(numer / denom, 4)


def build_report(
    shadow_records: list[dict],
    divergence_records: list[dict],
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Assemble the final report dict. No I/O."""
    return {
        "report_ts": datetime.now(UTC).isoformat(),
        "filters": {"since": since, "until": until},
        "shadow": aggregate_shadow(shadow_records),
        "divergence": aggregate_divergence(divergence_records),
    }


# ── Text rendering ──────────────────────────────────────────────────────────

def render_text(report: dict) -> str:
    sh = report["shadow"]
    dv = report["divergence"]
    apex = sh["apex"]
    lat = apex["latency"]
    tok = apex["tokens"]

    lines: list[str] = []
    lines.append("━━━ Apex Shadow Report ━━━")
    lines.append(f"generated: {report['report_ts']}")
    f = report["filters"]
    if f.get("since") or f.get("until"):
        lines.append(f"filters:   since={f.get('since') or '-'}  until={f.get('until') or '-'}")
    lines.append("")
    lines.append("── Shadow log ──")
    lines.append(f"total cycles:              {sh['total_shadow_cycles']}")
    lines.append(f"by trigger:                {sh['by_trigger_type']}")
    lines.append(f"fallback count / rate:     {apex['fallback_count']}  /  {_fmt_rate(apex['fallback_rate'])}")
    lines.append(f"schema reject count/rate:  {apex['schema_reject_count']}  /  {_fmt_rate(apex['schema_reject_rate'])}")
    lines.append(
        f"semantic-rejection count:  {apex['semantic_rejection_count']}  "
        f"(rate over total entries: {_fmt_rate(apex['semantic_rejection_rate'])})"
    )
    lines.append(f"models used:               {apex['models']}")
    lines.append("")
    lines.append(f"latency p50/p95/max (ms):  "
                 f"{_fmt_num(lat['p50_ms'])} / {_fmt_num(lat['p95_ms'])} / {_fmt_num(lat['max_ms'])}"
                 f"    (n={lat['n']})")
    lines.append(f"attempts mean / p95:       {_fmt_num(apex['attempts']['mean'])} / {_fmt_num(apex['attempts']['p95'])}")
    lines.append(f"tokens in/out/cache_read:  {_fmt_num(tok['input_mean'])} / "
                 f"{_fmt_num(tok['output_mean'])} / {_fmt_num(tok['cache_read_mean'])}  (means)")
    lines.append("")
    lines.append("── Divergence log ──")
    lines.append(f"total records:             {dv['total_divergence_records']}")
    lines.append(f"by trigger:                {dv['by_trigger_type']}")
    lines.append(f"agree cycles / rate:       {dv['agree_cycles']}  /  {_fmt_rate(dv['agree_rate_cycles'])}")
    lines.append("")
    lines.append(f"events total:              {dv['events']['total']}")
    lines.append(f"by severity:               {dv['events']['by_severity']}")
    lines.append("by category:")
    for cat, n in sorted(dv["events"]["by_category"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {cat:<24} {n}")
    lines.append("")
    lines.append("entries per side:")
    e = dv["entries"]
    lines.append(f"  legacy new_entries       {e['legacy_new_entries_total']}")
    lines.append(f"  apex   new_entries       {e['apex_new_entries_total']}")
    lines.append(f"  legacy portfolio_actions {e['legacy_portfolio_actions_total']}")
    lines.append(f"  apex   portfolio_actions {e['apex_portfolio_actions_total']}")
    lines.append("")
    return "\n".join(lines)


def _fmt_rate(r: float | None) -> str:
    return "—" if r is None else f"{r*100:.2f}%"


def _fmt_num(n: float | None) -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and n >= 100:
        return f"{n:.0f}"
    return f"{n:.2f}" if isinstance(n, float) else str(n)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apex shadow metrics roll-up")
    ap.add_argument("--shadow-log", default=_DEFAULT_SHADOW_LOG)
    ap.add_argument("--divergence-log", default=_DEFAULT_DIVERGENCE_LOG)
    ap.add_argument("--since", help="UTC date filter, inclusive (YYYY-MM-DD)")
    ap.add_argument("--until", help="UTC date filter, inclusive (YYYY-MM-DD)")
    ap.add_argument("--out-dir", default=_DEFAULT_REPORT_DIR)
    ap.add_argument("--no-write", action="store_true",
                    help="Print text report only; do not write artifacts.")
    args = ap.parse_args(argv)

    shadow = filter_by_date(load_jsonl(args.shadow_log), args.since, args.until)
    divergence = filter_by_date(load_jsonl(args.divergence_log), args.since, args.until)
    report = build_report(shadow, divergence, since=args.since, until=args.until)

    text = render_text(report)
    print(text)

    if not args.no_write:
        os.makedirs(args.out_dir, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = os.path.join(args.out_dir, f"report_{stamp}.json")
        txt_path = os.path.join(args.out_dir, f"report_{stamp}.txt")
        with open(json_path, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        with open(txt_path, "w") as fh:
            fh.write(text)
        print(f"\nwrote: {json_path}\nwrote: {txt_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
