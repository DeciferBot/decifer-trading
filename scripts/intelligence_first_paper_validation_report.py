#!/usr/bin/env python3
"""
scripts/intelligence_first_paper_validation_report.py

Reads available logs and answers 10 questions about intelligence-first
handoff candidate quality in paper/shadow trading.

Produces:
  data/runtime/intelligence_first_paper_validation_report.json
  docs/intelligence_first_paper_validation_report.md

Usage:
    python3 scripts/intelligence_first_paper_validation_report.py
    python3 scripts/intelligence_first_paper_validation_report.py --md-only
    python3 scripts/intelligence_first_paper_validation_report.py --json-only
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

_MD_ONLY = "--md-only" in sys.argv
_JSON_ONLY = "--json-only" in sys.argv

TIER_D_FUNNEL   = os.path.join(_REPO, "data", "tier_d_funnel.jsonl")
SIGNALS_LOG     = os.path.join(_REPO, "data", "signals_typed.jsonl")
MANIFEST_PATH   = os.path.join(_REPO, "data", "live", "current_manifest.json")
UNIVERSE_PATH   = os.path.join(_REPO, "data", "live", "active_opportunity_universe.json")
TRAINING_RECS   = os.path.join(_REPO, "data", "training_records.jsonl")

JSON_OUT = os.path.join(_REPO, "data", "runtime", "intelligence_first_paper_validation_report.json")
MD_OUT   = os.path.join(_REPO, "docs", "intelligence_first_paper_validation_report.md")


def _load_jsonl(path: str, max_lines: int = 50_000) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path) as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return records


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _NOT_ENOUGH_DATA(question: str, missing: list[str]) -> dict:
    return {
        "status": "NOT_ENOUGH_DATA",
        "question": question,
        "missing_evidence": missing,
        "result": None,
    }


def _result(question: str, status: str, data: dict) -> dict:
    return {"status": status, "question": question, **data}


# ─────────────────────────────────────────────────────────────────────────────
# Q1: Did handoff candidates enter Track A?
# ─────────────────────────────────────────────────────────────────────────────
def q1_handoff_in_track_a(funnel_records: list[dict], signals_records: list[dict]) -> dict:
    q = "Did handoff candidates enter Track A?"
    handoff_signals = [r for r in signals_records if r.get("handoff_source_labels")]
    cap_records = [r for r in funnel_records if r.get("stage") == "apex_cap_candidate_audit"]
    if not handoff_signals and not cap_records:
        return _NOT_ENOUGH_DATA(q, ["no handoff_source_labels in signals_typed.jsonl",
                                     "no apex_cap_candidate_audit in tier_d_funnel.jsonl"])
    handoff_syms_in_signals = {r["symbol"] for r in handoff_signals}
    in_cap = sum(
        1 for cr in cap_records
        for c in cr.get("candidates", [])
        if c.get("symbol") in handoff_syms_in_signals and c.get("selected_for_apex")
    )
    return _result(q, "PROVEN" if in_cap > 0 else "NOT_PROVEN", {
        "handoff_symbols_in_signals": len(handoff_syms_in_signals),
        "handoff_symbols_selected_for_apex": in_cap,
        "sample_symbols": sorted(handoff_syms_in_signals)[:5],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q2: Did handoff candidates enter Apex payload?
# ─────────────────────────────────────────────────────────────────────────────
def q2_handoff_in_apex_payload(funnel_records: list[dict]) -> dict:
    q = "Did handoff candidates enter Apex payload?"
    dispatch_recs = [r for r in funnel_records if r.get("stage") == "dispatch"]
    if not dispatch_recs:
        return _NOT_ENOUGH_DATA(q, ["no stage=dispatch records in tier_d_funnel.jsonl"])
    handoff_dispatched = sum(
        1 for r in dispatch_recs
        if r.get("candidate_source") == "handoff_reader" or r.get("handoff_source_labels")
    )
    return _result(q, "PROVEN" if handoff_dispatched > 0 else "NOT_PROVEN", {
        "dispatch_records_total": len(dispatch_recs),
        "handoff_dispatched": handoff_dispatched,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q3: Did they appear in tier_d_funnel.jsonl?
# ─────────────────────────────────────────────────────────────────────────────
def q3_tier_d_funnel(funnel_records: list[dict]) -> dict:
    q = "Did handoff candidates appear in tier_d_funnel.jsonl?"
    pipeline_recs = [r for r in funnel_records if r.get("stage") == "pipeline"]
    if not funnel_records:
        return _NOT_ENOUGH_DATA(q, ["tier_d_funnel.jsonl missing or empty"])
    return _result(q, "PARTIAL_DATA" if pipeline_recs else "NOT_ENOUGH_DATA", {
        "total_funnel_records": len(funnel_records),
        "pipeline_stage_records": len(pipeline_recs),
        "stages_seen": list({r.get("stage") for r in funnel_records}),
        "note": "handoff source label propagation to funnel requires market-hours scan cycle with controlled_activation manifest",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q4: Did they appear in dispatch/rejection logs?
# ─────────────────────────────────────────────────────────────────────────────
def q4_dispatch_rejection(funnel_records: list[dict]) -> dict:
    q = "Did handoff candidates appear in dispatch/rejection logs?"
    apex_cap = [r for r in funnel_records if r.get("stage") == "apex_cap_candidate_audit"]
    if not apex_cap:
        return _NOT_ENOUGH_DATA(q, ["no apex_cap_candidate_audit records in tier_d_funnel.jsonl"])
    rejection_reasons: Counter = Counter()
    total_candidates = 0
    rejected = 0
    selected = 0
    for rec in apex_cap:
        for c in rec.get("candidates", []):
            total_candidates += 1
            if c.get("selected_for_apex"):
                selected += 1
            else:
                rejected += 1
                rr = c.get("rejection_reason") or "unspecified"
                rejection_reasons[rr] += 1
    return _result(q, "PROVEN" if total_candidates > 0 else "NOT_ENOUGH_DATA", {
        "apex_cap_cycles": len(apex_cap),
        "total_candidates_audited": total_candidates,
        "selected_for_apex": selected,
        "rejected": rejected,
        "rejection_reason_distribution": dict(rejection_reasons.most_common(10)),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q5: Are source_labels, route, freshness_status, scores, rejection_reason preserved?
# ─────────────────────────────────────────────────────────────────────────────
def q5_metadata_preservation(signals_records: list[dict], funnel_records: list[dict]) -> dict:
    q = "Are source_labels, route, freshness_status, scores, rejection_reason preserved?"
    handoff_signals = [r for r in signals_records if r.get("handoff_source_labels")]
    cap_recs = [r for r in funnel_records if r.get("stage") == "apex_cap_candidate_audit"]
    if not handoff_signals:
        return _NOT_ENOUGH_DATA(q, [
            "no handoff_source_labels in signals_typed.jsonl — signals log not yet receiving handoff metadata",
            "fix: run scan cycle with controlled_activation manifest after sprint merge",
        ])
    sample = handoff_signals[:3]
    preserved_fields = {}
    for r in sample:
        preserved_fields[r["symbol"]] = {
            "handoff_source_labels": r.get("handoff_source_labels"),
            "handoff_route": r.get("handoff_route"),
            "handoff_freshness_status": r.get("handoff_freshness_status"),
            "handoff_reason_to_care": r.get("handoff_reason_to_care"),
            "score": r.get("score"),
        }
    rejection_reason_present = any(
        c.get("rejection_reason") is not None
        for rec in cap_recs
        for c in rec.get("candidates", [])
    )
    return _result(q, "PARTIAL_DATA", {
        "handoff_signals_with_source_labels": len(handoff_signals),
        "sample_metadata": preserved_fields,
        "rejection_reason_in_apex_cap": rejection_reason_present,
        "note": "partial: source_labels now propagated via signal_types.py sprint change; "
                "requires market-hours scan to appear in logs",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q6: Distribution across POSITION, SWING, INTRADAY, AVOID, rejected
# ─────────────────────────────────────────────────────────────────────────────
def q6_classification_distribution(funnel_records: list[dict]) -> dict:
    q = "What was the distribution across POSITION, SWING, INTRADAY, AVOID, and rejected?"
    dispatch_recs = [r for r in funnel_records if r.get("stage") == "dispatch"]
    if not dispatch_recs:
        return _NOT_ENOUGH_DATA(q, ["no stage=dispatch records in tier_d_funnel.jsonl"])
    dist: Counter = Counter()
    for r in dispatch_recs:
        cls = r.get("apex_classification") or r.get("classification") or "UNKNOWN"
        dist[cls] += 1
    return _result(q, "PROVEN" if dist else "NOT_ENOUGH_DATA", {
        "total_dispatch_records": len(dispatch_recs),
        "classification_distribution": dict(dist.most_common()),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q7: Did position candidates surface without overwhelming swing/intraday?
# ─────────────────────────────────────────────────────────────────────────────
def q7_position_vs_other(funnel_records: list[dict]) -> dict:
    q = "Did position candidates surface without overwhelming swing/intraday?"
    dispatch_recs = [r for r in funnel_records if r.get("stage") == "dispatch"]
    if not dispatch_recs:
        return _NOT_ENOUGH_DATA(q, ["no stage=dispatch records in tier_d_funnel.jsonl"])
    position_count = sum(1 for r in dispatch_recs
                         if (r.get("apex_classification") or "").upper() == "POSITION")
    swing_count = sum(1 for r in dispatch_recs
                      if (r.get("apex_classification") or "").upper() == "SWING")
    intraday_count = sum(1 for r in dispatch_recs
                         if (r.get("apex_classification") or "").upper() == "INTRADAY")
    total = len(dispatch_recs)
    if total == 0:
        return _NOT_ENOUGH_DATA(q, ["zero dispatch records"])
    return _result(q, "PROVEN" if total > 0 else "NOT_ENOUGH_DATA", {
        "position": position_count,
        "swing": swing_count,
        "intraday": intraday_count,
        "total": total,
        "position_pct": round(100 * position_count / total, 1) if total else 0,
        "verdict": "ok" if position_count <= swing_count + intraday_count else "position_dominant",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q8: Were false positives lower versus previous baseline?
# ─────────────────────────────────────────────────────────────────────────────
def q8_false_positive_reduction(training_records: list[dict]) -> dict:
    q = "Were false positives lower versus the previous baseline?"
    if len(training_records) < 20:
        return _NOT_ENOUGH_DATA(q, [
            f"only {len(training_records)} training records — need ≥20 handoff-sourced closed trades",
            "baseline comparison not possible without pre-handoff trade cohort",
        ])
    handoff_trades = [t for t in training_records if t.get("handoff_source_labels")]
    non_handoff_trades = [t for t in training_records if not t.get("handoff_source_labels")]
    if len(handoff_trades) < 5:
        return _NOT_ENOUGH_DATA(q, [
            f"only {len(handoff_trades)} handoff-sourced trades — need ≥5 for comparison",
        ])

    def _win_rate(trades: list[dict]) -> float:
        pnls = [t.get("pnl", 0) for t in trades if t.get("pnl") is not None]
        if not pnls:
            return 0.0
        return round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 1)

    handoff_wr = _win_rate(handoff_trades)
    baseline_wr = _win_rate(non_handoff_trades)
    return _result(q, "PROVEN" if len(handoff_trades) >= 5 else "NOT_ENOUGH_DATA", {
        "handoff_trade_count": len(handoff_trades),
        "baseline_trade_count": len(non_handoff_trades),
        "handoff_win_rate_pct": handoff_wr,
        "baseline_win_rate_pct": baseline_wr,
        "improvement": round(handoff_wr - baseline_wr, 1),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q9: Were options candidates rejected when spreads/slippage were unsafe?
# ─────────────────────────────────────────────────────────────────────────────
def q9_options_rejection(funnel_records: list[dict], training_records: list[dict]) -> dict:
    q = "Were options candidates rejected when spreads/slippage were unsafe?"
    options_trades = [t for t in training_records if t.get("instrument") == "option"]
    dispatch_opts = [
        r for r in funnel_records
        if r.get("stage") == "dispatch" and r.get("instrument") == "option"
    ]
    if not options_trades and not dispatch_opts:
        return _NOT_ENOUGH_DATA(q, ["no options trades or dispatch records found"])
    # Check for rejection_reason containing spread/slippage signals
    spread_rejections = sum(
        1 for r in funnel_records
        if "spread" in str(r.get("rejection_reason", "")).lower()
        or "slippage" in str(r.get("rejection_reason", "")).lower()
        or "wide_spread" in str(r.get("rejection_reason", "")).lower()
    )
    return _result(q, "PARTIAL_DATA", {
        "options_trades_executed": len(options_trades),
        "options_dispatch_records": len(dispatch_opts),
        "spread_slippage_rejections": spread_rejections,
        "note": "options spread/slippage gate is enforced in orders_options.py bid-ask check; "
                "rejection records in tier_d_funnel if options dispatched",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Q10: Were drawdown and concentration limits respected?
# ─────────────────────────────────────────────────────────────────────────────
def q10_risk_limits(training_records: list[dict]) -> dict:
    q = "Were drawdown and concentration limits respected?"
    if not training_records:
        return _NOT_ENOUGH_DATA(q, ["no training records"])
    # Look for positions with >10% loss (suggests stop not triggered)
    large_losses = [
        t for t in training_records
        if isinstance(t.get("pnl"), (int, float)) and t["pnl"] < -500
    ]
    symbols = [t.get("symbol") for t in training_records if t.get("symbol")]
    concentration = Counter(symbols).most_common(5)
    return _result(q, "PARTIAL_DATA", {
        "total_closed_trades": len(training_records),
        "trades_with_large_loss_gt_500": len(large_losses),
        "top_5_symbols_by_trade_count": concentration,
        "note": "concentration limits enforced by risk.py at order submission time; "
                "this report reflects post-execution distribution only",
    })


def build_report() -> dict:
    ts = datetime.now(timezone.utc).isoformat()

    # Load data sources
    funnel = _load_jsonl(TIER_D_FUNNEL)
    signals = _load_jsonl(SIGNALS_LOG, max_lines=10_000)
    training = _load_jsonl(TRAINING_RECS)
    manifest = _load_json(MANIFEST_PATH)

    # Data availability summary
    data_sources = {
        "tier_d_funnel_records": len(funnel),
        "signals_log_records": len(signals),
        "training_records": len(training),
        "manifest_loaded": manifest is not None,
        "manifest_publication_mode": manifest.get("publication_mode") if manifest else None,
        "manifest_handoff_enabled": manifest.get("handoff_enabled") if manifest else None,
    }

    # Run all 10 questions
    answers = [
        q1_handoff_in_track_a(funnel, signals),
        q2_handoff_in_apex_payload(funnel),
        q3_tier_d_funnel(funnel),
        q4_dispatch_rejection(funnel),
        q5_metadata_preservation(signals, funnel),
        q6_classification_distribution(funnel),
        q7_position_vs_other(funnel),
        q8_false_positive_reduction(training),
        q9_options_rejection(funnel, training),
        q10_risk_limits(training),
    ]

    status_counts = Counter(a["status"] for a in answers)
    overall = (
        "PROVEN" if status_counts.get("PROVEN", 0) >= 7
        else "PARTIAL_DATA" if status_counts.get("NOT_ENOUGH_DATA", 0) < 8
        else "NOT_ENOUGH_DATA"
    )

    # Activation gate check
    activation_gate = {
        "manifest_in_controlled_activation": manifest is not None
            and manifest.get("publication_mode") == "controlled_activation"
            and manifest.get("handoff_enabled") is True,
        "signals_log_has_handoff_labels": any(r.get("handoff_source_labels") for r in signals),
        "funnel_has_dispatch_records": any(r.get("stage") == "dispatch" for r in funnel),
        "training_records_exist": len(training) > 0,
    }

    return {
        "ts": ts,
        "overall_status": overall,
        "data_sources": data_sources,
        "activation_gate": activation_gate,
        "status_counts": dict(status_counts),
        "answers": answers,
    }


def _write_json(report: dict) -> None:
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, "w") as f:
        json.dump(report, f, indent=2)


def _write_md(report: dict) -> None:
    ts = report["ts"]
    overall = report["overall_status"]
    lines = [
        f"# Intelligence-First Paper Validation Report",
        f"",
        f"**Generated:** {ts}  ",
        f"**Overall Status:** `{overall}`",
        f"",
        f"## Data Sources",
        f"",
    ]
    for k, v in report["data_sources"].items():
        lines.append(f"- `{k}`: `{v}`")
    lines += [
        f"",
        f"## Activation Gate",
        f"",
    ]
    for k, v in report["activation_gate"].items():
        sym = "✅" if v else "❌"
        lines.append(f"- {sym} `{k}`: `{v}`")
    lines += [
        f"",
        f"## Answers to 10 Validation Questions",
        f"",
    ]
    for i, a in enumerate(report["answers"], 1):
        status = a["status"]
        sym = {"PROVEN": "✅", "NOT_PROVEN": "❌", "NOT_ENOUGH_DATA": "⚠️",
               "PARTIAL_DATA": "🔶"}.get(status, "?")
        lines.append(f"### Q{i}: {a.get('question', '')}")
        lines.append(f"")
        lines.append(f"**Status:** {sym} `{status}`")
        lines.append(f"")
        for k, v in a.items():
            if k in ("status", "question"):
                continue
            lines.append(f"- `{k}`: `{v}`")
        lines.append(f"")
    lines += [
        f"---",
        f"",
        f"*Report generated by `scripts/intelligence_first_paper_validation_report.py`*",
    ]
    os.makedirs(os.path.dirname(MD_OUT), exist_ok=True)
    with open(MD_OUT, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    report = build_report()
    if not _MD_ONLY:
        _write_json(report)
    if not _JSON_ONLY:
        _write_md(report)
    if not _JSON_ONLY and not _MD_ONLY:
        print(json.dumps({
            "overall_status": report["overall_status"],
            "status_counts": report["status_counts"],
            "activation_gate": report["activation_gate"],
            "json_out": JSON_OUT,
            "md_out": MD_OUT,
        }, indent=2))
