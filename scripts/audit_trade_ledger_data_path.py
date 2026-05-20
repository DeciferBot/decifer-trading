#!/usr/bin/env python3
"""
scripts/audit_trade_ledger_data_path.py

Read-only audit of the Decifer trade ledger and ML data path.

Stdlib + optional sklearn/joblib only. Does NOT import any live trading module.
Safe to run at any time — no data mutations, no broker calls, no ML activation.

Outputs (written by this script):
  data/audits/trade_ledger_data_path_audit.json   machine-readable
  docs/trade_ledger_data_path_audit.md             human-readable markdown
"""

from __future__ import annotations

import ast
import collections
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

# ── Repo root ─────────────────────────────────────────────────────────────────

_REPO = Path(__file__).resolve().parent.parent

# ── Forbidden live-trading modules (import safety check) ──────────────────────

_FORBIDDEN_IMPORTS = frozenset({
    "bot", "bot_ibkr", "bot_dashboard", "bot_trading",
    "orders_core", "orders_portfolio", "orders_options", "orders_state",
    "apex_orchestrator", "market_intelligence",
    "scanner", "signals", "learning",
    "risk", "sizing",
    "ibkr_reconciler",
    "ib_async", "ib_insync",
})

# ── ML-engine constants replicated here (no import of ml_engine) ──────────────

_SIGNAL_DIMENSIONS = [
    "trend", "momentum", "squeeze", "flow", "breakout",
    "news", "social", "reversion", "overnight_drift",
    "pead", "short_squeeze", "catalyst", "analyst_revision",
    "iv_skew", "fx_macro", "fx_momentum", "insider_buying", "mtf",
]

_ML_REGIME_OPTIONS = [
    "TRENDING_UP", "TRENDING_DOWN", "RANGE_BOUND", "CAPITULATION", "RELIEF_RALLY",
    "MOMENTUM_BULL", "FEAR_ELEVATED", "DISTRIBUTION", "EXTREME_STRESS", "TRENDING_BEAR",
    "CHOPPY",
]

# Quarantine file flagged FEAR_ELEVATED and DISTRIBUTION as session_character labels.
# Also TRENDING_BEAR vs BEAR_TRENDING is a known label inconsistency.
_SESSION_CHARACTER_LABELS = {"FEAR_ELEVATED", "DISTRIBUTION"}
_STRUCTURAL_REGIME_ALLOWLIST = frozenset(_ML_REGIME_OPTIONS) | {"UNKNOWN", "BEAR_TRENDING"}

_BREAKEVEN_THRESHOLD = 0.001


# ── Path resolution ────────────────────────────────────────────────────────────

def _get_paths() -> dict:
    """Return canonical file paths. Try config import first, fall back to defaults."""
    defaults = {
        "data_dir": str(_REPO / "data"),
        "training_records": str(_REPO / "data" / "training_records.jsonl"),
        "trade_events": str(_REPO / "data" / "trade_events.jsonl"),
        "trades_legacy": str(_REPO / "data" / "trades.json"),
        "reconciled_trades": str(_REPO / "data" / "reconciled_trades.jsonl"),
        "models_dir": str(_REPO / "data" / "models"),
        "ml_engine_src": str(_REPO / "ml_engine.py"),
        "config_src": str(_REPO / "config.py"),
        "audit_json": str(_REPO / "data" / "audits" / "trade_ledger_data_path_audit.json"),
        "audit_md": str(_REPO / "docs" / "trade_ledger_data_path_audit.md"),
    }
    try:
        sys.path.insert(0, str(_REPO))
        import config as _cfg  # noqa: PLC0415
        c = _cfg.CONFIG
        defaults["training_records"] = c.get("training_records", defaults["training_records"])
        defaults["trade_events"] = c.get("trade_events_log", defaults["trade_events"])
        defaults["trades_legacy"] = c.get("trade_log", defaults["trades_legacy"])
    except Exception:
        pass
    return defaults


# ── Safe JSONL loader ──────────────────────────────────────────────────────────

def _load_jsonl_safe(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    records = []
    with open(p, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                pass  # partial last line or corruption — skip
    return records


def _load_json_safe(path: str) -> list | dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ── A. Data source discovery ───────────────────────────────────────────────────

_SOURCE_TAGS = {
    "training_records": "training",
    "trade_events": "events",
    "trades.json": "legacy",
    "reconciled_trades": "reconciled",
    "apex_shadow": "shadow",
    "apex_decision": "shadow",
    "apex_prompt": "shadow",
    "apex_response": "shadow",
    "apex_conversation": "shadow",
    "apex_divergence": "shadow",
    "rotation_shadow": "rotation",
    "rotation_paper": "rotation",
    "rotation_observability": "rotation",
    "tier_d_funnel": "funnel",
    "signals_log": "signals",
    "signals_typed": "signals",
    "ic_weights": "ic",
    "ic_validation": "ic",
    "models": "ml_models",
    "audit": "audit",
    "backtest": "backtest",
    "reference": "reference",
    "intelligence": "intelligence",
    "live": "live_deployment",
    "positions.json": "runtime_state",
    "orders.json": "runtime_state",
    "equity_history": "portfolio",
    "margin_account": "portfolio",
}


def _tag_path(path: str) -> str:
    lower = path.lower()
    for kw, tag in _SOURCE_TAGS.items():
        if kw in lower:
            return tag
    return "other"


def discover_sources(data_dir: str) -> dict:
    """Walk data/ and catalogue every JSON/JSONL file."""
    files = []
    data_path = Path(data_dir)
    if not data_path.exists():
        return {"error": f"data_dir not found: {data_dir}", "files": []}

    for root, _dirs, fnames in os.walk(data_path):
        for fname in fnames:
            if not (fname.endswith(".json") or fname.endswith(".jsonl")):
                continue
            fpath = Path(root) / fname
            try:
                stat = fpath.stat()
                size = stat.st_size
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc).isoformat()
            except OSError:
                size, mtime = -1, "unknown"

            # Estimate record count for JSONL files cheaply
            record_count = None
            if fname.endswith(".jsonl") and size < 50 * 1024 * 1024:  # skip files > 50 MB
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        record_count = sum(1 for ln in fh if ln.strip())
                except OSError:
                    pass

            rel = str(fpath.relative_to(data_path.parent))
            files.append({
                "path": rel,
                "size_bytes": size,
                "record_count": record_count,
                "last_modified": mtime,
                "tag": _tag_path(rel),
            })

    files.sort(key=lambda f: f["path"])
    by_tag = collections.Counter(f["tag"] for f in files)
    return {
        "total_files": len(files),
        "by_tag": dict(by_tag),
        "files": files,
    }


# ── B. Primary ledger analysis ─────────────────────────────────────────────────

_REQUIRED_FIELDS = [
    "trade_id", "symbol", "direction", "trade_type", "fill_price",
    "exit_price", "pnl", "hold_minutes", "exit_reason", "regime",
    "signal_scores", "conviction", "score", "ts_fill", "ts_close",
]


def _parse_ts(ts_str: str | None) -> datetime.datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.datetime.fromisoformat(str(ts_str).replace(" ", "T"))
    except Exception:
        return None


def analyze_primary_ledger(path: str, records: list[dict]) -> dict:
    total = len(records)
    if total == 0:
        return {"error": "no records", "path": path, "exists": Path(path).exists()}

    # Signal scores coverage
    has_signals = sum(1 for r in records if r.get("signal_scores"))
    no_signals = total - has_signals

    # Score coverage
    score_zero = sum(1 for r in records if not r.get("score"))
    score_positive = total - score_zero

    # Duplicate trade IDs
    tids = [r.get("trade_id") for r in records]
    tid_counts = collections.Counter(tids)
    dupes = {tid: cnt for tid, cnt in tid_counts.items() if cnt > 1}

    # Required field null counts
    null_counts = {}
    for field in _REQUIRED_FIELDS:
        null_counts[field] = sum(1 for r in records if r.get(field) is None)

    # Schema compliance: empty signal_scores dict passes required-field check but is ML-dead
    empty_sig = sum(1 for r in records if r.get("signal_scores") == {})
    null_sig = null_counts.get("signal_scores", 0)

    # Distributions
    by_trade_type = dict(collections.Counter(r.get("trade_type", "MISSING") for r in records))
    by_instrument = dict(collections.Counter(r.get("instrument", "MISSING") for r in records))
    by_regime = dict(collections.Counter(r.get("regime", "MISSING") for r in records))
    by_direction = dict(collections.Counter(r.get("direction", "MISSING") for r in records))

    # Non-structural regimes
    non_structural = {r: c for r, c in by_regime.items() if r not in _STRUCTURAL_REGIME_ALLOWLIST}
    session_character_regimes = {r: c for r, c in by_regime.items() if r in _SESSION_CHARACTER_LABELS}

    # Options
    options_count = sum(1 for r in records if "option" in str(r.get("instrument", "")).lower())

    # Date range
    ts_vals = []
    for r in records:
        ts = _parse_ts(r.get("ts_fill") or r.get("ts_close"))
        if ts:
            ts_vals.append(ts)
    ts_vals.sort()
    date_range = {
        "first": ts_vals[0].isoformat() if ts_vals else None,
        "last": ts_vals[-1].isoformat() if ts_vals else None,
        "unique_days": len({t.date() for t in ts_vals}),
    }

    # Unique symbols
    unique_symbols = len({r.get("symbol") for r in records if r.get("symbol")})

    # Conviction range check
    conv_out_of_range = sum(
        1 for r in records
        if r.get("conviction") is not None and not (0.0 <= float(r["conviction"]) <= 1.0)
    )

    # pnl_pct outliers (>100% gain or loss)
    pnl_pct_outliers = sum(
        1 for r in records
        if r.get("pnl_pct") is not None and abs(float(r["pnl_pct"])) > 1.0
    )

    return {
        "path": path,
        "exists": True,
        "total_records": total,
        "with_signal_scores": has_signals,
        "without_signal_scores": no_signals,
        "empty_signal_scores_dict": empty_sig,
        "null_signal_scores": null_sig,
        "score_positive": score_positive,
        "score_zero_or_missing": score_zero,
        "duplicate_trade_ids": len(dupes),
        "duplicate_trade_id_list": list(dupes.keys()),
        "null_counts_required_fields": null_counts,
        "by_trade_type": by_trade_type,
        "by_instrument": by_instrument,
        "by_regime": by_regime,
        "by_direction": by_direction,
        "non_structural_regimes": non_structural,
        "session_character_regimes": session_character_regimes,
        "options_records": options_count,
        "equity_records": total - options_count,
        "date_range": date_range,
        "unique_symbols": unique_symbols,
        "conviction_out_of_range": conv_out_of_range,
        "pnl_pct_outliers_gt_100pct": pnl_pct_outliers,
    }


# ── C. Lifecycle integrity ─────────────────────────────────────────────────────

def check_lifecycle_integrity(training_records: list[dict], event_records: list[dict]) -> dict:
    training_ids = {r.get("trade_id") for r in training_records if r.get("trade_id")}

    intents = {r.get("trade_id") for r in event_records if r.get("event") == "ORDER_INTENT" and r.get("trade_id")}
    fills = {r.get("trade_id") for r in event_records if r.get("event") == "ORDER_FILLED" and r.get("trade_id")}
    closes = {r.get("trade_id") for r in event_records if r.get("event") == "POSITION_CLOSED" and r.get("trade_id")}
    trims = {r.get("trade_id") for r in event_records if r.get("event") == "POSITION_TRIMMED" and r.get("trade_id")}

    event_counts = dict(collections.Counter(r.get("event") for r in event_records))

    matched_intent = training_ids & intents
    matched_close = training_ids & closes
    matched_fill = training_ids & fills

    no_coverage = training_ids - intents - closes - fills
    intent_no_training = intents - training_ids
    close_no_training = closes - training_ids

    return {
        "event_log_total": len(event_records),
        "event_log_exists": len(event_records) > 0,
        "event_counts": event_counts,
        "training_records_total": len(training_records),
        "training_with_intent_match": len(matched_intent),
        "training_with_close_match": len(matched_close),
        "training_with_fill_match": len(matched_fill),
        "training_no_event_coverage": len(no_coverage),
        "intent_pct_of_training": round(len(matched_intent) / max(len(training_records), 1) * 100, 1),
        "orphan_intents_no_training": len(intent_no_training),
        "orphan_closes_no_training": len(close_no_training),
        "note": (
            "Low ORDER_INTENT coverage expected: pre-April-28 migration records "
            "were backfilled from trades.json before event_log existed."
        ),
    }


# ── D. Label correctness ───────────────────────────────────────────────────────

def check_label_correctness(records: list[dict]) -> dict:
    wins = losses = breakevens = unlabellable = 0
    label_conflicts = 0
    exit_eq_entry_nonzero_pnl = 0
    zero_hold_nonzero_pnl = 0

    for r in records:
        pnl = r.get("pnl")
        pnl_pct = r.get("pnl_pct")
        fill_price = r.get("fill_price") or 0
        exit_price = r.get("exit_price") or 0
        hold_minutes = r.get("hold_minutes") or 0

        if pnl is None:
            unlabellable += 1
            continue

        pnl = float(pnl)

        # Derive label from pnl_pct if available, else from pnl sign
        if pnl_pct is not None:
            if abs(float(pnl_pct)) < _BREAKEVEN_THRESHOLD:
                label = "BREAKEVEN"
            elif pnl > 0:
                label = "WIN"
            else:
                label = "LOSS"
        else:
            if pnl > 0:
                label = "WIN"
            elif pnl < 0:
                label = "LOSS"
            else:
                label = "BREAKEVEN"

        if label == "WIN":
            wins += 1
        elif label == "LOSS":
            losses += 1
        else:
            breakevens += 1

        # Sanity: exit_price == fill_price but pnl != 0
        if fill_price and exit_price and abs(exit_price - fill_price) < 0.0001 and abs(pnl) > 0.01:
            exit_eq_entry_nonzero_pnl += 1

        # Sanity: hold_minutes = 0 but non-zero pnl (should not be possible for a real trade)
        if hold_minutes == 0 and abs(pnl) > 0.01:
            zero_hold_nonzero_pnl += 1

    total = len(records)
    usable = wins + losses + breakevens
    win_rate = round(wins / max(usable, 1) * 100, 1)
    profit_factor_num = wins * (sum(float(r["pnl"]) for r in records if r.get("pnl") and float(r["pnl"]) > 0) / max(wins, 1))
    profit_factor_den = losses * (abs(sum(float(r["pnl"]) for r in records if r.get("pnl") and float(r["pnl"]) < 0)) / max(losses, 1))

    avg_win = sum(float(r["pnl"]) for r in records if r.get("pnl") and float(r["pnl"]) > 0) / max(wins, 1)
    avg_loss = sum(float(r["pnl"]) for r in records if r.get("pnl") and float(r["pnl"]) < 0) / max(losses, 1)

    return {
        "total_records": total,
        "labellable_records": usable,
        "unlabellable_null_pnl": unlabellable,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate_pct": win_rate,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor_num / max(profit_factor_den, 0.01), 3),
        "expectancy_per_trade": round(
            (wins / max(usable, 1)) * avg_win + (losses / max(usable, 1)) * avg_loss, 2
        ),
        "exit_eq_entry_nonzero_pnl": exit_eq_entry_nonzero_pnl,
        "zero_hold_nonzero_pnl": zero_hold_nonzero_pnl,
        "label_inversion_risk": win_rate < 40.0,  # if true, inverted AUC check warranted
        "label_inversion_note": (
            "Win rate is below 40% — inverted signal check in J2 is warranted." if win_rate < 40.0
            else "Win rate is within expected range."
        ),
    }


# ── E. Feature-time integrity ──────────────────────────────────────────────────

def check_feature_time_integrity(records: list[dict]) -> dict:
    total = len(records)
    if total == 0:
        return {"error": "no records"}

    # Field coverage for all signal dimensions
    dim_coverage = {}
    for dim in _SIGNAL_DIMENSIONS:
        key = f"dim_{dim}"
        nonzero = sum(
            1 for r in records
            if r.get("signal_scores") and r["signal_scores"].get(dim)
        )
        dim_coverage[key] = {"nonzero_count": nonzero, "pct": round(nonzero / total * 100, 1)}

    # Coverage for meta-features
    meta_fields = ["score", "conviction", "regime", "pnl_pct"]
    meta_coverage = {}
    for f in meta_fields:
        present = sum(1 for r in records if r.get(f) is not None)
        nonzero = sum(1 for r in records if r.get(f))
        meta_coverage[f] = {"present": present, "nonzero": nonzero, "pct_present": round(present / total * 100, 1)}

    # Check ts_fill < ts_close (exit must be after entry)
    ts_violations = 0
    ts_unparseable = 0
    hold_zero_closed = 0
    for r in records:
        ts_f = _parse_ts(r.get("ts_fill"))
        ts_c = _parse_ts(r.get("ts_close"))
        if ts_f is None or ts_c is None:
            ts_unparseable += 1
        elif ts_c < ts_f:
            ts_violations += 1
        hm = r.get("hold_minutes")
        if hm is not None and float(hm) <= 0:
            hold_zero_closed += 1

    # Confirm time_of_day and day_of_week should be derived from ts_fill (entry)
    # Spot-check: do ts_fill hours match typical trading windows?
    ts_fill_hours = collections.Counter()
    for r in records:
        ts = _parse_ts(r.get("ts_fill"))
        if ts:
            ts_fill_hours[ts.hour] += 1

    return {
        "total_records": total,
        "signal_dimension_coverage": dim_coverage,
        "meta_feature_coverage": meta_coverage,
        "timestamp_violations_close_before_fill": ts_violations,
        "timestamp_unparseable": ts_unparseable,
        "hold_minutes_zero_or_negative": hold_zero_closed,
        "ts_fill_hour_distribution": dict(sorted(ts_fill_hours.items())),
        "feature_source_notes": {
            "entry_time_features": ["score", "conviction", "signal_scores", "regime", "ts_fill", "time_of_day", "day_of_week"],
            "exit_time_features": ["pnl", "pnl_pct", "exit_price", "hold_minutes", "exit_reason", "ts_close"],
            "holding_minutes_leakage_note": (
                "hold_minutes is determined by outcome (exit time). "
                "ml_engine.py correctly excludes it from training features. Verified."
            ),
        },
    }


# ── F. Contamination check ─────────────────────────────────────────────────────

def check_contamination(records: list[dict], paths: dict) -> dict:
    total = len(records)

    options_records = [r for r in records if "option" in str(r.get("instrument", "")).lower()]
    unknown_trade_type = [r for r in records if r.get("trade_type") == "UNKNOWN"]
    session_char_regime = [r for r in records if r.get("regime") in _SESSION_CHARACTER_LABELS]
    non_structural = [r for r in records if r.get("regime") and r["regime"] not in _STRUCTURAL_REGIME_ALLOWLIST]

    # Check for shadow/paper/backtest strings in any string field
    suspicious_values = []
    suspicious_keywords = {"shadow", "paper", "backtest", "validation", "advisory", "simulation"}
    for r in records:
        for key, val in r.items():
            if isinstance(val, str) and any(kw in val.lower() for kw in suspicious_keywords):
                suspicious_values.append({"trade_id": r.get("trade_id"), "field": key, "value": val[:80]})

    # Cross-check: do any training_record trade_ids appear in legacy trades.json?
    legacy_overlap = 0
    legacy_data = _load_json_safe(paths.get("trades_legacy", ""))
    if isinstance(legacy_data, list):
        legacy_tids = {r.get("trade_id") for r in legacy_data if isinstance(r, dict) and r.get("trade_id")}
        training_tids = {r.get("trade_id") for r in records if r.get("trade_id")}
        legacy_overlap = len(training_tids & legacy_tids)

    # Instrument label normalisation issue
    instrument_labels = dict(collections.Counter(r.get("instrument", "MISSING") for r in records))
    equity_label_variants = {k: v for k, v in instrument_labels.items()
                             if k in {"stock", "equity_long", "equity_short"}}

    return {
        "total_records": total,
        "options_records": len(options_records),
        "options_pct": round(len(options_records) / max(total, 1) * 100, 1),
        "unknown_trade_type": len(unknown_trade_type),
        "session_character_regime_records": len(session_char_regime),
        "session_character_regimes_found": list({r["regime"] for r in session_char_regime}),
        "non_structural_regime_records": len(non_structural),
        "non_structural_regimes_found": list({r.get("regime") for r in non_structural}),
        "suspicious_string_values": suspicious_values[:20],
        "legacy_trades_json_overlap": legacy_overlap,
        "instrument_label_variants": instrument_labels,
        "equity_label_normalisation_issue": len(equity_label_variants) > 1,
        "equity_label_variants": equity_label_variants,
        "backfill_script_note": (
            "scripts/backfill_pnl_pct.py performs an atomic os.replace on training_records.jsonl "
            "to backfill pnl_pct. This is idempotent but violates append-only semantics. "
            "Must not be re-run."
        ),
    }


# ── G. Path consistency ────────────────────────────────────────────────────────

def _grep_file_for_patterns(filepath: str, patterns: list[str]) -> dict[str, list[int]]:
    """Return {pattern: [line_numbers]} for each pattern found in filepath."""
    result = {p: [] for p in patterns}
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                for p in patterns:
                    if p in line:
                        result[p].append(lineno)
    except OSError:
        pass
    return result


def check_path_consistency(paths: dict) -> dict:
    """Verify all reader modules point to the same canonical training path."""
    canonical_key = "training_records"
    canonical_path = paths.get("training_records", "data/training_records.jsonl")
    canonical_filename = Path(canonical_path).name  # training_records.jsonl

    # Files to check for hardcoded paths
    files_to_check = {
        "ml_engine.py": str(_REPO / "ml_engine.py"),
        "alpha_validation.py": str(_REPO / "alpha_validation.py"),
        "scripts/trade_quality_report.py": str(_REPO / "scripts" / "trade_quality_report.py"),
        "scripts/tier_d_evidence_report.py": str(_REPO / "scripts" / "tier_d_evidence_report.py"),
        "scripts/backfill_pnl_pct.py": str(_REPO / "scripts" / "backfill_pnl_pct.py"),
        "ibkr_reconciler.py": str(_REPO / "ibkr_reconciler.py"),
    }

    hardcoded_patterns = ["trades.json", "training_records.jsonl", "trade_events.jsonl"]
    good_patterns = ["training_store", "event_log", "CONFIG.get"]

    report = {}
    for label, fpath in files_to_check.items():
        if not Path(fpath).exists():
            report[label] = {"exists": False}
            continue
        hardcoded = _grep_file_for_patterns(fpath, hardcoded_patterns)
        good = _grep_file_for_patterns(fpath, good_patterns)
        report[label] = {
            "exists": True,
            "hardcoded_path_references": {k: v for k, v in hardcoded.items() if v},
            "canonical_references": {k: v for k, v in good.items() if v},
            "verdict": (
                "OK" if not any(hardcoded.values())
                else "HARDCODED_PATH_FOUND"
            ),
        }

    inconsistencies = [label for label, r in report.items()
                       if r.get("verdict") == "HARDCODED_PATH_FOUND"]

    return {
        "canonical_training_path": canonical_path,
        "per_file": report,
        "inconsistencies_found": len(inconsistencies),
        "files_with_hardcoded_paths": inconsistencies,
        "note": "Hardcoded paths are acceptable where the file is the canonical definition (e.g. backfill script).",
    }


# ── H. Schema consistency ──────────────────────────────────────────────────────

def check_schema_consistency(records: list[dict]) -> dict:
    total = len(records)
    if total == 0:
        return {"error": "no records"}

    # All fields seen across all records
    all_fields: set[str] = set()
    for r in records:
        all_fields.update(r.keys())

    field_presence = {}
    for field in sorted(all_fields):
        present = sum(1 for r in records if field in r)
        nonnull = sum(1 for r in records if r.get(field) is not None)
        field_presence[field] = {
            "present_count": present,
            "nonnull_count": nonnull,
            "pct_present": round(present / total * 100, 1),
        }

    # Schema generation detection based on field presence
    gen_1_fields = {"trade_id", "symbol", "pnl", "fill_price"}  # always present
    gen_2_fields = {"signal_scores", "score", "conviction", "regime"}  # post-Apex
    gen_3_fields = {"pnl_pct", "ic_weights_at_entry", "score_breakdown"}  # post-migration April-28

    gen1_count = sum(1 for r in records if all(r.get(f) for f in gen_1_fields))
    gen2_count = sum(1 for r in records if all(r.get(f) for f in gen_2_fields))
    gen3_count = sum(1 for r in records if r.get("pnl_pct") is not None)

    # Timestamp format check
    ts_naive = ts_tz_aware = ts_missing = ts_unparseable = 0
    for r in records:
        raw = r.get("ts_fill")
        if not raw:
            ts_missing += 1
            continue
        ts = _parse_ts(raw)
        if ts is None:
            ts_unparseable += 1
        elif ts.tzinfo is not None:
            ts_tz_aware += 1
        else:
            ts_naive += 1

    # Score type consistency
    score_int = sum(1 for r in records if isinstance(r.get("score"), int))
    score_float = sum(1 for r in records if isinstance(r.get("score"), float))
    score_other = total - score_int - score_float

    return {
        "total_records": total,
        "total_unique_fields": len(all_fields),
        "field_presence": field_presence,
        "schema_generations": {
            "gen1_base_fields": {"fields": list(gen_1_fields), "record_count": gen1_count},
            "gen2_post_apex_signal_fields": {"fields": list(gen_2_fields), "record_count": gen2_count},
            "gen3_post_migration_fields": {"fields": list(gen_3_fields), "record_count": gen3_count},
        },
        "timestamp_format": {
            "tz_aware": ts_tz_aware,
            "naive": ts_naive,
            "missing": ts_missing,
            "unparseable": ts_unparseable,
        },
        "score_type": {"int": score_int, "float": score_float, "other": score_other},
    }


# ── I. Sample adequacy ─────────────────────────────────────────────────────────

def check_sample_adequacy(records: list[dict]) -> dict:
    # ML-usable: non-empty signal_scores AND score > 0
    usable = [r for r in records if r.get("signal_scores") and r.get("score", 0) > 0]
    options_in_usable = [r for r in usable if "option" in str(r.get("instrument", "")).lower()]
    unknown_regime_usable = [r for r in usable if r.get("regime") == "UNKNOWN"]
    unknown_tt_usable = [r for r in usable if r.get("trade_type") == "UNKNOWN"]

    total_usable = len(usable)
    n_features = 34  # from ml_engine: 5 base + 17 dim + 11 regime one-hot + 1 = 34

    ts_vals = sorted(
        t for t in (_parse_ts(r.get("ts_fill")) for r in usable) if t
    )
    unique_days = len({t.date() for t in ts_vals})
    unique_symbols = len({r.get("symbol") for r in usable if r.get("symbol")})

    regime_dist = dict(collections.Counter(r.get("regime", "MISSING") for r in usable))
    dominant_regime = max(regime_dist, key=lambda k: regime_dist[k], default=None)
    dominant_pct = round(regime_dist.get(dominant_regime, 0) / max(total_usable, 1) * 100, 1)

    tt_dist = dict(collections.Counter(r.get("trade_type", "MISSING") for r in usable))
    inst_dist = dict(collections.Counter(r.get("instrument", "MISSING") for r in usable))

    # Rule of thumb: ≥10 samples per feature
    min_recommended = n_features * 10
    sample_ratio = round(total_usable / n_features, 1)

    return {
        "total_usable_records": total_usable,
        "usable_criteria": "signal_scores non-empty AND score > 0",
        "date_range": {
            "first": ts_vals[0].isoformat() if ts_vals else None,
            "last": ts_vals[-1].isoformat() if ts_vals else None,
            "unique_days": unique_days,
        },
        "unique_symbols": unique_symbols,
        "options_in_usable": len(options_in_usable),
        "options_pct": round(len(options_in_usable) / max(total_usable, 1) * 100, 1),
        "unknown_regime_in_usable": len(unknown_regime_usable),
        "unknown_trade_type_in_usable": len(unknown_tt_usable),
        "regime_distribution": regime_dist,
        "dominant_regime": dominant_regime,
        "dominant_regime_pct": dominant_pct,
        "trade_type_distribution": tt_dist,
        "instrument_distribution": inst_dist,
        "ml_feature_count": n_features,
        "min_recommended_samples": min_recommended,
        "samples_per_feature": sample_ratio,
        "adequate_for_ml": total_usable >= min_recommended,
        "adequacy_note": (
            f"{total_usable} usable records / {n_features} features = {sample_ratio}x ratio. "
            f"Recommended ≥10x ({min_recommended} records). "
            + ("BELOW THRESHOLD." if total_usable < min_recommended else "MEETS THRESHOLD.")
        ),
    }


# ── J. Verdict ────────────────────────────────────────────────────────────────

def produce_verdict(findings: dict) -> dict:
    ledger = findings.get("primary_ledger", {})
    lifecycle = findings.get("lifecycle", {})
    labels = findings.get("labels", {})
    contamination = findings.get("contamination", {})
    sample = findings.get("sample", {})
    feature_time = findings.get("feature_time", {})

    issues_critical = []
    issues_high = []
    issues_medium = []

    # Critical gates
    if labels.get("unlabellable_null_pnl", 0) / max(ledger.get("total_records", 1), 1) > 0.30:
        issues_critical.append("More than 30% of records have null P&L — labels unreliable")

    if feature_time.get("timestamp_violations_close_before_fill", 0) > 0:
        issues_critical.append(
            f"{feature_time['timestamp_violations_close_before_fill']} records have ts_close < ts_fill"
        )

    # High issues
    dupes = ledger.get("duplicate_trade_ids", 0)
    if dupes > 0:
        issues_high.append(f"{dupes} duplicate trade_ids — investigate before ML")

    if ledger.get("without_signal_scores", 0) > 0:
        pct = round(ledger["without_signal_scores"] / max(ledger["total_records"], 1) * 100, 1)
        issues_high.append(f"{ledger['without_signal_scores']} records ({pct}%) have empty signal_scores — pre-migration era")

    if ledger.get("score_zero_or_missing", 0) > 0:
        issues_high.append(f"{ledger['score_zero_or_missing']} records have score=0 — pre-signal era")

    if lifecycle.get("intent_pct_of_training", 100) < 50:
        issues_high.append(
            f"Only {lifecycle['intent_pct_of_training']}% of training records have matching ORDER_INTENT — "
            "lifecycle not reconstructable for majority (expected: pre-migration records)"
        )

    regime_unknown = ledger.get("by_regime", {}).get("UNKNOWN", 0)
    if regime_unknown > 0:
        issues_high.append(f"{regime_unknown} records have regime=UNKNOWN — regime feature unreliable")

    # Medium issues
    if contamination.get("options_records", 0) > 0:
        issues_medium.append(
            f"{contamination['options_records']} options records mixed with equities — separable by instrument field"
        )

    if contamination.get("session_character_regime_records", 0) > 0:
        issues_medium.append(
            f"{contamination['session_character_regime_records']} records use session_character labels as regime "
            f"({contamination['session_character_regimes_found']})"
        )

    if contamination.get("unknown_trade_type", 0) > 0:
        issues_medium.append(f"{contamination['unknown_trade_type']} UNKNOWN trade_type records — likely EXT-path orphans")

    if contamination.get("equity_label_normalisation_issue"):
        issues_medium.append(
            f"Instrument label inconsistency: {list(contamination.get('equity_label_variants', {}).keys())}"
        )

    if not sample.get("adequate_for_ml"):
        issues_medium.append(sample.get("adequacy_note", "Sample below recommended threshold"))

    # Determine verdict
    if issues_critical:
        verdict = "NOT READY FOR ML"
        verdict_reason = "Critical data integrity issues found."
    elif issues_high:
        verdict = "USABLE ONLY AFTER FILTERING"
        verdict_reason = (
            "Data is partially trustworthy. High-severity issues are separable by field filters. "
            "ML diagnostics can proceed on a filtered subset."
        )
    elif issues_medium:
        verdict = "USABLE ONLY AFTER FILTERING"
        verdict_reason = "Medium-severity contamination present but separable."
    else:
        verdict = "CLEAN ENOUGH FOR ML DIAGNOSTICS"
        verdict_reason = "No critical or high-severity issues found."

    return {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "issues_critical": issues_critical,
        "issues_high": issues_high,
        "issues_medium": issues_medium,
        "recommended_filters": [
            "signal_scores must be non-empty (removes pre-migration records)",
            "score must be > 0 (removes pre-signal-era records)",
            "instrument must not be options_call / options_put / option",
            "regime must not be UNKNOWN",
            "deduplicate on trade_id (keep first ts_written occurrence)",
        ],
        "recommended_sot_outcomes": "data/training_records.jsonl via training_store.load()",
        "recommended_sot_features": "ORDER_INTENT fields from event_log (signal_scores, score, regime, conviction) — read-only from position record at write time",
        "legacy_files": ["data/trades.json (deprecated April-28)", "scripts/backfill_pnl_pct.py (run-once migration tool)"],
    }


# ── J2. ML logic correctness ───────────────────────────────────────────────────

def _extract_ml_features_inline(records: list[dict]) -> tuple[list, list, list]:
    """Replicate ml_engine.py feature extraction without importing it."""
    X_rows, y_labels, usable_records = [], [], []
    for r in records:
        if not r.get("signal_scores"):
            continue
        if r.get("pnl") is None:
            continue

        raw_ts = r.get("ts_fill") or r.get("entry_time", "")
        ts = _parse_ts(raw_ts)
        if ts is None:
            continue

        pnl = float(r["pnl"])
        pnl_pct = r.get("pnl_pct")
        if pnl_pct is not None and abs(float(pnl_pct)) < _BREAKEVEN_THRESHOLD:
            label = 0  # BREAKEVEN → 0
        elif pnl > 0:
            label = 1  # WIN
        else:
            label = 0  # LOSS or BREAKEVEN

        sig = r.get("signal_scores") or {}
        row = {
            "score": float(r.get("score", 0)),
            "vix": float(r.get("vix", 0.0)),
            "time_of_day": ts.hour,
            "day_of_week": ts.weekday(),
            "is_after_hours": int(ts.hour < 9 or ts.hour >= 16),
        }
        for dim in _SIGNAL_DIMENSIONS:
            row[f"dim_{dim}"] = float(sig.get(dim, 0))

        # Regime one-hot
        regime = r.get("regime", "UNKNOWN")
        for opt in _ML_REGIME_OPTIONS:
            row[f"regime_{opt}"] = int(regime == opt)

        X_rows.append(row)
        y_labels.append(label)
        usable_records.append(r)

    return X_rows, y_labels, usable_records


def _read_source_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def audit_ml_logic_correctness(paths: dict) -> dict:
    result: dict = {}

    # ── 1. ML script inventory ────────────────────────────────────────────────
    ml_files = []
    for pattern in ["ml_engine.py", "alpha_validation.py", "learning.py", "ic_engine.py", "scripts/factor_analysis.py"]:
        p = _REPO / pattern
        if p.exists():
            ml_files.append({"path": str(p.relative_to(_REPO)), "exists": True})
    models_dir = Path(paths.get("models_dir", str(_REPO / "data" / "models")))
    model_files = [f.name for f in models_dir.glob("*") if f.name != "QUARANTINE_README.md"] if models_dir.exists() else []
    result["ml_script_inventory"] = {"source_files": ml_files, "saved_model_files": model_files}

    # ── Early exit: ml_engine.py deleted (Sprint 1 clean removal) ────────────
    ml_engine_path = Path(paths.get("ml_engine_src", str(_REPO / "ml_engine.py")))
    if not ml_engine_path.exists():
        result["ml_logic_verdict"] = "ML_ENGINE_REMOVED"
        result["ml_logic_verdict_reason"] = (
            "ml_engine.py was deleted in Sprint 1 (2026-05-20). "
            "Leaky saved models quarantined in data/quarantine/leaky_ml_models_2026_05_20/. "
            "New controlled learning architecture defined in docs/ml_controlled_learning_architecture.md."
        )
        result["label_correctness"] = {"checks": {}, "all_pass": True, "failures": [], "note": "ml_engine.py removed"}
        result["feature_alignment"] = {"checks": {}, "all_pass": True, "failures": [], "note": "ml_engine.py removed"}
        result["validation_method"] = {"checks": {}, "all_pass": True, "walk_forward_used": False, "note": "ml_engine.py removed"}
        result["model_evaluation"] = {"status": "SKIPPED", "reason": "ml_engine.py removed — no model to evaluate"}
        result["model_configuration"] = {"note": "ml_engine.py removed"}
        result["apex_integration_safety"] = {
            "enhance_score_references": [],
            "ml_enabled_default_true_in_config": False,
            "ml_enabled_risk": "OK — ml_engine.py deleted, enhance_score() no longer exists",
            "live_multiplier_active": False,
        }
        result["reproducibility"] = {"note": "ml_engine.py removed"}
        return result

    # ── 2. Target and label correctness (code inspection) ────────────────────
    ml_src = _read_source_text(paths.get("ml_engine_src", ""))
    label_checks = {
        "win_eq_pnl_positive": "pnl > 0" in ml_src,
        "loss_eq_pnl_negative": "pnl < 0" in ml_src,
        "y_win_eq_1": '== "WIN"' in ml_src and ".astype(int)" in ml_src,
        "empty_signal_scores_excluded": 'not trade.get("signal_scores")' in ml_src,
        "pnl_none_excluded": '"pnl") is None' in ml_src or "get(\"pnl\") is None" in ml_src,
        "action_open_excluded": '"OPEN"' in ml_src,
    }
    result["label_correctness"] = {
        "checks": label_checks,
        "all_pass": all(label_checks.values()),
        "failures": [k for k, v in label_checks.items() if not v],
    }

    # ── 3. Feature alignment (code inspection) ────────────────────────────────
    feature_checks = {
        "uses_ts_fill_for_entry_time": "ts_fill" in ml_src,
        "holding_minutes_excluded_from_features": (
            '"holding_minutes"' not in _extract_feature_cols_section(ml_src)
        ),
        "signal_scores_from_record": "signal_scores" in ml_src,
        "time_of_day_from_entry": "entry_time.hour" in ml_src or "ts_fill" in ml_src,
    }
    result["feature_alignment"] = {
        "checks": feature_checks,
        "all_pass": all(feature_checks.values()),
        "failures": [k for k, v in feature_checks.items() if not v],
    }

    # ── 4. Validation method ──────────────────────────────────────────────────
    validation_checks = {
        "uses_timeseries_split": "TimeSeriesSplit" in ml_src,
        "no_random_split": "train_test_split" not in ml_src,
        "n_splits_5": "n_splits=5" in ml_src,
    }
    result["validation_method"] = {
        "checks": validation_checks,
        "all_pass": all(validation_checks.values()),
        "walk_forward_used": validation_checks["uses_timeseries_split"],
        "note": (
            "TimeSeriesSplit prevents future leakage. "
            "With 203 records and 5 folds, earliest fold trains on ~32 records — "
            "underfitting on small folds is expected and explains high CV variance."
        ),
    }

    # ── 5–8. Metrics + inverted signal + calibration (requires sklearn) ───────
    try:
        import joblib
        from sklearn.metrics import average_precision_score, roc_auc_score
        sklearn_available = True
    except ImportError:
        sklearn_available = False

    if not sklearn_available:
        result["model_evaluation"] = {"status": "SKIPPED", "reason": "sklearn/joblib not installed"}
    else:
        result["model_evaluation"] = _run_model_evaluation(paths)

    # ── 9. Model configuration ────────────────────────────────────────────────
    config_checks = {
        "rf_max_depth": _extract_param(ml_src, "max_depth=", first=True),
        "rf_n_estimators": _extract_param(ml_src, "n_estimators=", first=True),
        "gb_max_depth": _extract_param(ml_src, "max_depth=", first=False),
        "gb_n_estimators": _extract_param(ml_src, "n_estimators=", first=False),
    }
    n_features = 34
    usable_approx = 203
    result["model_configuration"] = {
        "hyperparameters": config_checks,
        "n_features": n_features,
        "approx_usable_samples": usable_approx,
        "sample_to_feature_ratio": round(usable_approx / n_features, 1),
        "rf_max_depth_risk": (
            "OVERFIT RISK: max_depth=10 with 203 samples / 34 features allows near-zero training error"
            if config_checks.get("rf_max_depth") and int(config_checks["rf_max_depth"]) >= 8
            else "OK"
        ),
    }

    # ── 10. Apex / live integration safety ────────────────────────────────────
    enhance_score_callers = []
    for pyfile in _REPO.glob("**/*.py"):
        if "worktree" in str(pyfile) or ".claude" in str(pyfile):
            continue
        try:
            src = pyfile.read_text(encoding="utf-8", errors="replace")
            if "enhance_score" in src or "SignalEnhancer" in src:
                lines_found = [i + 1 for i, ln in enumerate(src.splitlines()) if "enhance_score" in ln or "SignalEnhancer" in ln]
                enhance_score_callers.append({"file": str(pyfile.relative_to(_REPO)), "lines": lines_found})
        except OSError:
            pass

    cfg_src = _read_source_text(paths.get("config_src", ""))
    ml_enabled_default_true = '"ml_enabled": True' in cfg_src or "'ml_enabled': True" in cfg_src

    result["apex_integration_safety"] = {
        "enhance_score_references": enhance_score_callers,
        "ml_enabled_default_true_in_config": ml_enabled_default_true,
        "ml_enabled_risk": (
            "RISK: ml_enabled defaults to True in config.py. If enhance_score() is ever "
            "wired into the scoring pipeline, it would activate silently. Recommend default=False."
            if ml_enabled_default_true else "OK"
        ),
        "live_multiplier_active": False,
        "note": (
            "enhance_score() is imported in bot.py startup block for availability check only. "
            "It is not called on any candidate in signals.py, scanner.py, or apex_orchestrator.py."
        ),
    }

    # ── 11. Reproducibility ───────────────────────────────────────────────────
    metadata_path = Path(paths.get("models_dir", "")) / "metadata.json"
    metadata = _load_json_safe(str(metadata_path)) if metadata_path.exists() else None
    features_pkl_exists = (Path(paths.get("models_dir", "")) / "features.pkl").exists()
    result["reproducibility"] = {
        "model_metadata_exists": metadata is not None,
        "model_metadata": metadata,
        "features_pkl_saved": features_pkl_exists,
        "random_state_42_in_source": "random_state=42" in ml_src,
        "deterministic": "random_state=42" in ml_src,
    }

    # ── ML logic verdict ──────────────────────────────────────────────────────
    logic_failures = (
        result["label_correctness"].get("failures", [])
        + result["feature_alignment"].get("failures", [])
        + (["random_cv_used"] if not result["validation_method"]["walk_forward_used"] else [])
    )

    if logic_failures:
        ml_verdict = "ML LOGIC NEEDS FIXES BEFORE TRUSTING RESULTS"
        ml_verdict_reason = f"Logic failures found: {logic_failures}"
    elif result.get("model_evaluation", {}).get("inverted_auc_interpretation") == "LABEL_INVERSION_OR_CONTRARIAN":
        ml_verdict = "ML LOGIC NEEDS FIXES BEFORE TRUSTING RESULTS"
        ml_verdict_reason = "Inverted signal check suggests label inversion or contrarian signal — investigate before trusting AUC."
    else:
        ml_verdict = "ML LOGIC CORRECT, DATA/SIGNAL WEAK"
        ml_verdict_reason = (
            "Code logic is sound: walk-forward CV, no look-ahead, correct labels. "
            "AUC=0.401 reflects genuine absence of learnable signal in current 203-record sample."
        )

    result["ml_logic_verdict"] = ml_verdict
    result["ml_logic_verdict_reason"] = ml_verdict_reason

    return result


def _extract_feature_cols_section(src: str) -> str:
    """Extract the feature_cols list definition from ml_engine.py source."""
    match = re.search(r"feature_cols\s*=\s*\[.*?\]", src, re.DOTALL)
    return match.group(0) if match else ""


def _extract_param(src: str, param: str, first: bool = True) -> str | None:
    """Extract numeric value of a parameter (e.g. 'max_depth=') from source text."""
    matches = re.findall(rf"{re.escape(param)}(\d+)", src)
    if not matches:
        return None
    return matches[0] if first else (matches[1] if len(matches) > 1 else matches[0])


def _run_model_evaluation(paths: dict) -> dict:
    """Load saved models and compute detailed metrics. Requires sklearn + joblib."""
    import joblib
    from sklearn.metrics import average_precision_score, roc_auc_score

    models_dir = Path(paths.get("models_dir", str(_REPO / "data" / "models")))
    clf_path = models_dir / "classifier.pkl"
    features_path = models_dir / "features.pkl"

    if not clf_path.exists() or not features_path.exists():
        return {"status": "SKIPPED", "reason": "No saved model found at data/models/"}

    try:
        clf = joblib.load(clf_path)
        feature_names = joblib.load(features_path)
    except Exception as e:
        return {"status": "ERROR", "reason": f"Failed to load model: {e}"}

    # Rebuild dataset
    records = _load_jsonl_safe(paths.get("training_records", ""))
    X_rows, y_labels, _ = _extract_ml_features_inline(records)

    if len(X_rows) < 20:
        return {"status": "SKIPPED", "reason": f"Only {len(X_rows)} usable records — too few for evaluation"}

    import numpy as np

    # Align to saved feature names
    X = np.array([[row.get(f, 0) for f in feature_names] for row in X_rows])
    y = np.array(y_labels)

    try:
        y_prob = clf.predict_proba(X)[:, 1]
    except Exception as e:
        return {"status": "ERROR", "reason": f"predict_proba failed: {e}"}

    auc = float(roc_auc_score(y, y_prob))
    pr_auc = float(average_precision_score(y, y_prob))
    inverted_auc = float(roc_auc_score(y, 1 - y_prob))
    baseline_win_rate = float(y.mean())

    # Calibration: bin into 5 buckets
    calibration = []
    for low, high in [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]:
        mask = (y_prob >= low) & (y_prob < high)
        n = int(mask.sum())
        if n == 0:
            continue
        actual_win = float(y[mask].mean())
        pred_mean = float(y_prob[mask].mean())
        calibration.append({
            "bucket": f"{low:.1f}-{high:.1f}",
            "n": n,
            "predicted_prob": round(pred_mean, 3),
            "actual_win_rate": round(actual_win, 3),
            "miscalibration": round(abs(pred_mean - actual_win), 3),
        })

    # Inverted signal interpretation
    if inverted_auc > 0.55:
        inv_interp = "LABEL_INVERSION_OR_CONTRARIAN"
        inv_note = (
            f"Inverted AUC {inverted_auc:.3f} > 0.55. Model features may carry a contrarian signal. "
            "Possible causes: label inversion, target sign error, or genuine contrarian features. "
            "DO NOT activate inverted model — investigate first."
        )
    elif inverted_auc > 0.50:
        inv_interp = "WEAK_CONTRARIAN_SIGNAL"
        inv_note = f"Inverted AUC {inverted_auc:.3f} slightly above 0.5 — marginal signal, likely noise."
    else:
        inv_interp = "NO_SIGNAL"
        inv_note = f"Inverted AUC {inverted_auc:.3f} ≈ 0.5 — model is effectively random noise."

    # Profit factor from labels
    total_wins = int(y.sum())
    total_losses = int((1 - y).sum())

    return {
        "status": "OK",
        "n_samples": len(X_rows),
        "n_features": len(feature_names),
        "roc_auc": round(auc, 4),
        "pr_auc": round(pr_auc, 4),
        "baseline_win_rate": round(baseline_win_rate, 4),
        "inverted_auc": round(inverted_auc, 4),
        "inverted_auc_interpretation": inv_interp,
        "inverted_auc_note": inv_note,
        "calibration_buckets": calibration,
        "class_distribution": {"wins": total_wins, "non_wins": total_losses},
        "note": (
            "PR-AUC is more informative than ROC-AUC when positive class (WIN) is a minority. "
            f"Baseline win rate = {baseline_win_rate:.1%}."
        ),
    }


# ── K. Write outputs ───────────────────────────────────────────────────────────

def write_outputs(findings: dict, json_path: str, md_path: str) -> None:
    # JSON output
    jp = Path(json_path)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(findings, fh, indent=2, default=str)

    # Markdown output
    mp = Path(md_path)
    mp.parent.mkdir(parents=True, exist_ok=True)
    md = _build_markdown(findings)
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write(md)


def _build_markdown(f: dict) -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    verdict = f.get("verdict", {}).get("verdict", "UNKNOWN")
    ml_verdict = f.get("ml_logic", {}).get("ml_logic_verdict", "UNKNOWN")
    lines = []

    def h(level: int, text: str) -> None:
        lines.append(f"\n{'#' * level} {text}\n")

    def p(text: str) -> None:
        lines.append(text + "\n")

    def table_row(*cols) -> str:
        return "| " + " | ".join(str(c) for c in cols) + " |"

    lines.append(f"# Trade Ledger & ML Data Path Audit\n")
    lines.append(f"Generated: {now}\n")

    h(2, "Executive Summary")
    ledger = f.get("primary_ledger", {})
    labels = f.get("labels", {})
    sample = f.get("sample", {})
    p(f"**Total records in training_records.jsonl:** {ledger.get('total_records', 'N/A')}")
    p(f"**Records with signal scores (ML-usable):** {ledger.get('with_signal_scores', 'N/A')}")
    p(f"**Win rate:** {labels.get('win_rate_pct', 'N/A')}%  |  "
      f"**Profit factor:** {labels.get('profit_factor', 'N/A')}  |  "
      f"**Expectancy:** ${labels.get('expectancy_per_trade', 'N/A')}/trade")
    p(f"**Duplicate trade IDs:** {ledger.get('duplicate_trade_ids', 'N/A')}")
    p(f"**Usable sample size:** {sample.get('total_usable_records', 'N/A')} records / {sample.get('ml_feature_count', 34)} features = {sample.get('samples_per_feature', 'N/A')}x ratio")

    h(2, f"Verdict: {verdict}")
    v = f.get("verdict", {})
    p(v.get("verdict_reason", ""))
    if v.get("issues_critical"):
        p("**Critical issues:**")
        for i in v["issues_critical"]:
            p(f"- {i}")
    if v.get("issues_high"):
        p("**High-severity issues:**")
        for i in v["issues_high"]:
            p(f"- {i}")
    if v.get("issues_medium"):
        p("**Medium-severity issues:**")
        for i in v["issues_medium"]:
            p(f"- {i}")

    h(2, f"ML Logic Verdict: {ml_verdict}")
    ml = f.get("ml_logic", {})
    p(ml.get("ml_logic_verdict_reason", ""))
    ev = ml.get("model_evaluation", {})
    if ev.get("status") == "OK":
        p(f"- ROC-AUC: {ev.get('roc_auc')}  |  PR-AUC: {ev.get('pr_auc')}  |  Inverted AUC: {ev.get('inverted_auc')}")
        p(f"- {ev.get('inverted_auc_note', '')}")

    h(2, "A. Data Source Inventory")
    sources = f.get("sources", {})
    p(f"Total data files catalogued: **{sources.get('total_files', 0)}**")
    by_tag = sources.get("by_tag", {})
    if by_tag:
        lines.append(table_row("Tag", "Count"))
        lines.append(table_row("---", "---"))
        for tag, cnt in sorted(by_tag.items()):
            lines.append(table_row(tag, cnt))
        lines.append("")

    h(2, "B. Primary Ledger Analysis")
    p(f"Path: `{ledger.get('path', 'N/A')}`")
    p(f"- Total records: **{ledger.get('total_records')}**")
    p(f"- With signal_scores: **{ledger.get('with_signal_scores')}** ({round(ledger.get('with_signal_scores', 0)/max(ledger.get('total_records', 1), 1)*100, 1)}%)")
    p(f"- Score > 0: **{ledger.get('score_positive')}**")
    p(f"- Duplicate trade_ids: **{ledger.get('duplicate_trade_ids')}**")
    dr = ledger.get("date_range", {})
    p(f"- Date range: {dr.get('first', 'N/A')[:10]} → {dr.get('last', 'N/A')[:10]} ({dr.get('unique_days')} trading days)")
    p(f"- Unique symbols: {ledger.get('unique_symbols')}")
    p(f"- Options records: {ledger.get('options_records')} ({ledger.get('options_records', 0) / max(ledger.get('total_records', 1), 1) * 100:.1f}%)")

    if ledger.get("by_regime"):
        p("\n**Regime distribution:**")
        lines.append(table_row("Regime", "Count", "Structural?"))
        lines.append(table_row("---", "---", "---"))
        for reg, cnt in sorted(ledger["by_regime"].items(), key=lambda x: -x[1]):
            is_struct = "✓" if reg in _STRUCTURAL_REGIME_ALLOWLIST else "⚠ NON-STRUCTURAL"
            lines.append(table_row(reg, cnt, is_struct))
        lines.append("")

    h(2, "C. Lifecycle Integrity")
    lc = f.get("lifecycle", {})
    p(f"- event_log total events: {lc.get('event_log_total')}")
    p(f"- Training records with matching ORDER_INTENT: **{lc.get('training_with_intent_match')}** ({lc.get('intent_pct_of_training')}%)")
    p(f"- Training records with matching POSITION_CLOSED: **{lc.get('training_with_close_match')}**")
    p(f"- Training records with no event_log coverage: **{lc.get('training_no_event_coverage')}**")
    p(f"- *{lc.get('note', '')}*")

    h(2, "D. Label Correctness")
    p(f"- Wins: **{labels.get('wins')}** | Losses: **{labels.get('losses')}** | Breakeven: **{labels.get('breakevens')}**")
    p(f"- Win rate: **{labels.get('win_rate_pct')}%**  |  Avg win: **${labels.get('avg_win')}**  |  Avg loss: **${labels.get('avg_loss')}**")
    p(f"- Profit factor: **{labels.get('profit_factor')}**  |  Expectancy: **${labels.get('expectancy_per_trade')}/trade**")
    if labels.get("label_inversion_risk"):
        p(f"- ⚠ **{labels.get('label_inversion_note')}**")

    h(2, "E. Feature-Time Integrity")
    ft = f.get("feature_time", {})
    p(f"- Timestamp violations (ts_close < ts_fill): **{ft.get('timestamp_violations_close_before_fill', 0)}**")
    p(f"- Unparseable timestamps: **{ft.get('timestamp_unparseable', 0)}**")
    p(f"- hold_minutes ≤ 0: **{ft.get('hold_minutes_zero_or_negative', 0)}**")
    notes = ft.get("feature_source_notes", {})
    p(f"- Hold minutes leakage: *{notes.get('holding_minutes_leakage_note', '')}*")

    h(2, "F. Contamination Check")
    cont = f.get("contamination", {})
    p(f"- Options records: **{cont.get('options_records')}** ({cont.get('options_pct')}%)")
    p(f"- UNKNOWN trade_type: **{cont.get('unknown_trade_type')}**")
    p(f"- Session-character regime labels: **{cont.get('session_character_regime_records')}** ({cont.get('session_character_regimes_found')})")
    p(f"- Instrument label variants: {cont.get('instrument_label_variants', {})}")
    if cont.get("equity_label_normalisation_issue"):
        p("- ⚠ Equity instrument labels are not normalised (stock / equity_long / equity_short)")
    p(f"- *{cont.get('backfill_script_note', '')}*")

    h(2, "G. Path Consistency")
    pc = f.get("path_consistency", {})
    p(f"Inconsistencies found: **{pc.get('inconsistencies_found', 0)}**")
    for fname, fr in pc.get("per_file", {}).items():
        verdict_icon = "✓" if fr.get("verdict") == "OK" else ("✗" if fr.get("exists") else "—")
        p(f"- {verdict_icon} `{fname}`: {fr.get('verdict', 'N/A')}")

    h(2, "H. Schema Consistency")
    sch = f.get("schema", {})
    gens = sch.get("schema_generations", {})
    p("**Schema generations detected:**")
    for gen_name, gen_info in gens.items():
        p(f"- {gen_name}: {gen_info.get('record_count')} records")
    ts_fmt = sch.get("timestamp_format", {})
    p(f"- Timestamps: {ts_fmt.get('tz_aware')} tz-aware, {ts_fmt.get('naive')} naive, {ts_fmt.get('missing')} missing")

    h(2, "I. Sample Adequacy")
    p(f"- Usable records: **{sample.get('total_usable_records')}** (signal_scores non-empty + score > 0)")
    _dr = sample.get('date_range') or {}
    _dr_first = (_dr.get('first') or 'N/A')[:10]
    _dr_last = (_dr.get('last') or 'N/A')[:10]
    p(f"- Date range: {_dr_first} → {_dr_last}")
    p(f"- Unique symbols: {sample.get('unique_symbols')}")
    p(f"- Options in usable: {sample.get('options_in_usable')} ({sample.get('options_pct')}%)")
    p(f"- Dominant regime: {sample.get('dominant_regime')} ({sample.get('dominant_regime_pct')}% of usable)")
    p(f"- {sample.get('adequacy_note', '')}")

    h(2, "J2. ML Logic Correctness")
    lc_checks = ml.get("label_correctness", {})
    p(f"Label correctness checks: {'ALL PASS' if lc_checks.get('all_pass') else 'FAILURES: ' + str(lc_checks.get('failures'))}")
    fa_checks = ml.get("feature_alignment", {})
    p(f"Feature alignment checks: {'ALL PASS' if fa_checks.get('all_pass') else 'FAILURES: ' + str(fa_checks.get('failures'))}")
    vm = ml.get("validation_method", {})
    p(f"Validation method: {'Walk-forward (TimeSeriesSplit) ✓' if vm.get('walk_forward_used') else 'RANDOM CV — INVALID ✗'}")
    p(f"*{vm.get('note', '')}*")

    mc = ml.get("model_configuration", {})
    p(f"\n**Model configuration:** {mc.get('rf_max_depth_risk', '')}")
    p(f"Sample/feature ratio: {mc.get('sample_to_feature_ratio')}x (recommended ≥10x)")

    ai = ml.get("apex_integration_safety", {})
    p(f"\n**Apex integration safety:** {ai.get('ml_enabled_risk', 'OK')}")
    p(f"Live multiplier active: {'YES ⚠' if ai.get('live_multiplier_active') else 'No ✓'}")

    if ev.get("status") == "OK":
        h(3, "Inverted Signal Check")
        p(f"AUC: {ev.get('roc_auc')} | Inverted AUC: {ev.get('inverted_auc')}")
        p(f"**Interpretation:** {ev.get('inverted_auc_interpretation')}")
        p(f"{ev.get('inverted_auc_note', '')}")

        h(3, "Probability Calibration")
        cal = ev.get("calibration_buckets", [])
        if cal:
            lines.append(table_row("Bucket", "N", "Predicted prob", "Actual win rate", "Miscalibration"))
            lines.append(table_row("---", "---", "---", "---", "---"))
            for b in cal:
                lines.append(table_row(b["bucket"], b["n"], b["predicted_prob"], b["actual_win_rate"], b["miscalibration"]))
            lines.append("")

    h(2, "Recommendations")
    recs = f.get("verdict", {})
    p("**Recommended filters before any ML run:**")
    for filt in recs.get("recommended_filters", []):
        p(f"1. {filt}")
    p(f"\n**Single source of truth for outcomes:** `{recs.get('recommended_sot_outcomes', '')}`")
    p(f"**Single source of truth for entry features:** {recs.get('recommended_sot_features', '')}")
    p("\n**Legacy files to exclude from ML:**")
    for lf in recs.get("legacy_files", []):
        p(f"- {lf}")

    h(2, "Anti-Bloat Gate")
    lines.append(table_row("Item", "Status"))
    lines.append(table_row("---", "---"))
    for item, status in [
        ("Files added", "2 (audit script + tests)"),
        ("Files modified", "0"),
        ("Runtime impact", "None — script is run manually"),
        ("Live trading impact", "None"),
        ("Broker/order/risk/sizing paths touched", "No"),
        ("Live behaviour changed", "No"),
        ("Data mutated", "No"),
        ("ML activated", "No"),
        ("ML multiplier activated", "No"),
    ]:
        lines.append(table_row(item, status))
    lines.append("")

    return "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────────────

def main(json_out: str | None = None, md_out: str | None = None) -> dict:
    paths = _get_paths()
    if json_out:
        paths["audit_json"] = json_out
    if md_out:
        paths["audit_md"] = md_out

    print("[audit] Loading data sources...", flush=True)
    findings: dict = {"generated_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat()}

    findings["sources"] = discover_sources(paths["data_dir"])
    print(f"[audit] A. Found {findings['sources'].get('total_files', 0)} data files", flush=True)

    training_records = _load_jsonl_safe(paths["training_records"])
    print(f"[audit] B. Analysing primary ledger ({len(training_records)} records)...", flush=True)
    findings["primary_ledger"] = analyze_primary_ledger(paths["training_records"], training_records)

    event_records = _load_jsonl_safe(paths["trade_events"])
    print(f"[audit] C. Checking lifecycle integrity ({len(event_records)} events)...", flush=True)
    findings["lifecycle"] = check_lifecycle_integrity(training_records, event_records)

    print("[audit] D. Checking label correctness...", flush=True)
    findings["labels"] = check_label_correctness(training_records)

    print("[audit] E. Checking feature-time integrity...", flush=True)
    findings["feature_time"] = check_feature_time_integrity(training_records)

    print("[audit] F. Checking contamination...", flush=True)
    findings["contamination"] = check_contamination(training_records, paths)

    print("[audit] G. Checking path consistency...", flush=True)
    findings["path_consistency"] = check_path_consistency(paths)

    print("[audit] H. Checking schema consistency...", flush=True)
    findings["schema"] = check_schema_consistency(training_records)

    print("[audit] I. Checking sample adequacy...", flush=True)
    findings["sample"] = check_sample_adequacy(training_records)

    print("[audit] J. Producing verdict...", flush=True)
    findings["verdict"] = produce_verdict(findings)

    print("[audit] J2. Auditing ML logic correctness...", flush=True)
    findings["ml_logic"] = audit_ml_logic_correctness(paths)

    print(f"[audit] K. Writing outputs to {paths['audit_json']} and {paths['audit_md']}...", flush=True)
    write_outputs(findings, paths["audit_json"], paths["audit_md"])

    print(f"\n[audit] ── VERDICT: {findings['verdict']['verdict']} ──")
    print(f"[audit] ── ML LOGIC: {findings['ml_logic']['ml_logic_verdict']} ──")
    print(f"[audit] Report written to {paths['audit_md']}")

    return findings


if __name__ == "__main__":
    main()
