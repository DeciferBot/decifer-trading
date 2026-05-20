#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  scripts/ml_outcome_joiner.py              ║
# ║   Sprint 3 — Offline outcome joiner for ML learning loop    ║
# ║                                                              ║
# ║   Reads:   data/ml/ml_observations.jsonl                     ║
# ║            data/trade_events.jsonl                           ║
# ║            data/training_records.jsonl                       ║
# ║            data/ml/closed_trade_training_ledger.jsonl        ║
# ║                                                              ║
# ║   Writes:  data/ml/canonical_learning_dataset.jsonl          ║
# ║            data/ml/canonical_learning_dataset_summary.json   ║
# ║                                                              ║
# ║   Offline only — NEVER imported by the live trading bot.     ║
# ║   No model training. No model loading. No predictions.       ║
# ║   No score changes. No execution. No order routing.          ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Offline outcome joiner — Sprint 3.

Joins signal observations to realised trade outcomes and writes a canonical
learning dataset. Every observation record is included — traded or not.
Non-traded observations become pass rows (trade_taken=False, outcome_label=None).

Architecture rule: this file is NEVER imported by the live trading bot.
Run only via:  python3 scripts/ml_outcome_joiner.py [--dry-run] [-v]

Layer classification
--------------------
  Runtime layer  : NONE — offline script only
  Execution layer: NONE — no orders, positions, or scores touched
  Training layer : data preparation only — no model fit, no predict
  Dependencies   : stdlib only (json, logging, os, sys, argparse, pathlib, datetime)

LEAKAGE_FIELDS
--------------
These fields are stored in the output record for offline analysis but are
excluded from ML_FEATURE_FIELDS. The training script (Sprint 7) must only
use ML_FEATURE_FIELDS as model inputs — never LEAKAGE_FIELDS.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("decifer.ml_outcome_joiner")

# ── Constants ──────────────────────────────────────────────────────────────────

SCHEMA_VERSION = "sprint3_v1"

# Outcome boundary: strictly zero per Sprint 3 spec.
# pnl_pct > 0 → WIN  |  pnl_pct < 0 → LOSS  |  pnl_pct == 0 → BREAKEVEN
BREAKEVEN_THRESHOLD: float = 0.0

# Fallback join window: ORDER_INTENT timestamp within ±N seconds of observation.
FALLBACK_WINDOW_SECONDS: int = 300

# Post-outcome fields stored for analysis — NEVER used as model input features.
LEAKAGE_FIELDS: frozenset[str] = frozenset({
    "hold_minutes",
    "holding_minutes",
    "exit_price",
    "exit_reason",
    "realised_pnl",
    "realised_pnl_pct",
    "pnl_pct",
    "pnl",
    "outcome_label",
    "position_closed",
    "exit_timestamp",
    "ts_exit",
    "ts_close",
})

# Fields safe for ML model input — no post-outcome, no execution context.
# dim_* fields are also feature fields but are dynamic; check by key prefix.
ML_FEATURE_FIELDS: frozenset[str] = frozenset({
    "base_score",
    "ranking_position",
    "ranking_total",
    "vix",
    "time_of_day",
    "day_of_week",
    "is_after_hours",
    "regime",
    "passed_base_threshold",
})

_REPO_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent


# ── Path resolution ────────────────────────────────────────────────────────────

def _canonical_paths() -> dict[str, Path]:
    """Return canonical paths for all input/output files."""
    ml_dir = _REPO_ROOT / "data" / "ml"
    data_dir = _REPO_ROOT / "data"
    return {
        "observations": ml_dir / "ml_observations.jsonl",
        "events":       data_dir / "trade_events.jsonl",
        "training":     data_dir / "training_records.jsonl",
        "ledger":       ml_dir / "closed_trade_training_ledger.jsonl",
        "output":       ml_dir / "canonical_learning_dataset.jsonl",
        "summary":      ml_dir / "canonical_learning_dataset_summary.json",
    }


# ── JSONL helpers ──────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    """Load all records from a JSONL file. Returns [] if file absent or corrupt."""
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                log.warning(
                    "ml_outcome_joiner: corrupt JSON at %s line %d — skipped",
                    path.name, lineno,
                )
    return records


# ── Timestamp helpers ──────────────────────────────────────────────────────────

def _parse_ts(ts_str: str | None) -> datetime | None:
    """Parse an ISO timestamp string to a UTC-aware datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def _ts_diff_seconds(ts_a: str | None, ts_b: str | None) -> float | None:
    """Return |ts_a − ts_b| in seconds, or None if either is unparseable."""
    a, b = _parse_ts(ts_a), _parse_ts(ts_b)
    if a is None or b is None:
        return None
    return abs((a - b).total_seconds())


# ── Event indexing ─────────────────────────────────────────────────────────────

def _index_events(events: list[dict]) -> dict:
    """
    Build lookup indices over trade events.

    Returns:
      intents_by_obs_id : observation_id → ORDER_INTENT   (exact join path)
      intents_list      : all ORDER_INTENTs sorted by ts   (fallback join path)
      fills             : trade_id → ORDER_FILLED
      closings          : trade_id → POSITION_CLOSED (most recent per trade)
    """
    intents_by_obs_id: dict[str, dict] = {}
    intents_list: list[dict] = []
    fills: dict[str, dict] = {}
    closings: dict[str, dict] = {}

    for rec in events:
        ev = rec.get("event")
        tid = rec.get("trade_id")
        if not tid:
            continue
        if ev == "ORDER_INTENT":
            obs_id = rec.get("observation_id")
            if obs_id:
                intents_by_obs_id[obs_id] = rec
            intents_list.append(rec)
        elif ev == "ORDER_FILLED":
            fills[tid] = rec
        elif ev == "POSITION_CLOSED":
            existing = closings.get(tid)
            if not existing or rec.get("ts", "") > existing.get("ts", ""):
                closings[tid] = rec

    intents_list.sort(key=lambda r: r.get("ts", ""))
    return {
        "intents_by_obs_id": intents_by_obs_id,
        "intents_list": intents_list,
        "fills": fills,
        "closings": closings,
    }


# ── Outcome indexing ───────────────────────────────────────────────────────────

def _index_outcomes(training: list[dict], ledger: list[dict]) -> dict[str, dict]:
    """
    Build outcome index: trade_id → outcome record.

    Ledger (richer schema) overwrites training_records where both exist.
    """
    index: dict[str, dict] = {}
    for rec in training:
        tid = rec.get("trade_id")
        if tid:
            index[tid] = rec
    for rec in ledger:   # ledger takes precedence
        tid = rec.get("trade_id")
        if tid:
            index[tid] = rec
    return index


# ── Join logic ─────────────────────────────────────────────────────────────────

def _exact_join(obs_id: str, intents_by_obs_id: dict) -> dict | None:
    """Return ORDER_INTENT matching observation_id exactly, or None."""
    return intents_by_obs_id.get(obs_id) if obs_id else None


def _fallback_join(obs: dict, intents_list: list[dict]) -> dict | None:
    """
    Fallback join: find ORDER_INTENT for the same symbol + direction within
    FALLBACK_WINDOW_SECONDS of the observation timestamp.

    Returns the closest matching intent or None.
    """
    sym = obs.get("symbol")
    direction = obs.get("direction")
    obs_ts = obs.get("timestamp_utc")
    if not (sym and direction and obs_ts):
        return None

    best: dict | None = None
    best_diff = float("inf")

    for intent in intents_list:
        if intent.get("symbol") != sym:
            continue
        if intent.get("direction") != direction:
            continue
        diff = _ts_diff_seconds(obs_ts, intent.get("ts"))
        if diff is None or diff > FALLBACK_WINDOW_SECONDS:
            continue
        if diff < best_diff:
            best, best_diff = intent, diff

    return best


# ── Outcome extraction ─────────────────────────────────────────────────────────

def _outcome_label(pnl_pct: float | None) -> str | None:
    """Assign WIN / LOSS / BREAKEVEN.  Returns None when outcome unknown."""
    if pnl_pct is None:
        return None
    if pnl_pct > BREAKEVEN_THRESHOLD:
        return "WIN"
    if pnl_pct < -BREAKEVEN_THRESHOLD:
        return "LOSS"
    return "BREAKEVEN"


def _extract_outcome(trade_id: str | None, outcomes: dict[str, dict]) -> dict:
    """Extract outcome fields from the outcome index by trade_id."""
    empty: dict = {
        "exit_price": None, "exit_reason": None,
        "realised_pnl": None, "realised_pnl_pct": None,
        "exit_timestamp": None, "hold_minutes": None,
    }
    if not trade_id:
        return empty
    rec = outcomes.get(trade_id)
    if not rec:
        return empty
    return {
        "exit_price":       rec.get("exit_price"),
        "exit_reason":      rec.get("exit_reason"),
        "realised_pnl":     rec.get("realised_pnl") or rec.get("pnl"),
        "realised_pnl_pct": rec.get("pnl_pct"),
        "exit_timestamp":   rec.get("ts_exit") or rec.get("ts_close"),
        "hold_minutes":     rec.get("hold_minutes"),
    }


# ── ML eligibility ─────────────────────────────────────────────────────────────

def _classify_eligible(rec: dict) -> tuple[bool, str | None]:
    """
    Determine if a canonical record is eligible for ML training.

    Returns (eligible, exclusion_reason).  exclusion_reason is None when eligible.
    """
    if not rec.get("observation_id"):
        return False, "missing_observation_id"
    if not rec.get("signal_scores"):
        return False, "missing_signal_scores"
    if rec.get("direction", "NEUTRAL") not in ("LONG", "SHORT"):
        return False, "direction_not_directional"
    if not rec.get("trade_taken"):
        return False, "no_realised_trade_outcome"
    if not rec.get("order_filled"):
        return False, "order_not_filled"
    if not rec.get("position_closed"):
        return False, "position_not_closed"
    if rec.get("realised_pnl_pct") is None:
        return False, "pnl_pct_missing"
    if rec.get("join_quality") != "exact":
        return False, "fallback_join_not_eligible"
    return True, None


# ── Record section builders ────────────────────────────────────────────────────

def _obs_fields(obs: dict) -> dict:
    """Extract observation-context fields for the canonical record."""
    return {
        "session_date":         obs.get("session_date"),
        "scan_id":              obs.get("scan_id"),
        "observation_id":       obs.get("observation_id") or "",
        "symbol":               obs.get("symbol"),
        "direction":            obs.get("direction"),
        "candidate_source":     obs.get("candidate_source"),
        "base_score":           obs.get("base_score"),
        "ranking_position":     obs.get("ranking_position"),
        "ranking_total":        obs.get("ranking_total"),
        "signal_scores":        obs.get("signal_scores"),
        "regime":               obs.get("regime"),
        "vix":                  obs.get("vix"),
        "time_of_day":          obs.get("time_of_day"),
        "day_of_week":          obs.get("day_of_week"),
        "is_after_hours":       obs.get("is_after_hours"),
        "passed_base_threshold": obs.get("passed_base_threshold"),
    }


def _intent_fields(intent: dict | None) -> dict:
    """Extract ORDER_INTENT linkage fields for the canonical record."""
    return {
        "trade_taken":           intent is not None,
        "order_intent_seen":     intent is not None,
        "order_intent_timestamp": intent.get("ts") if intent else None,
        "order_action":          intent.get("trade_type") if intent else None,
        "order_reason":          intent.get("reasoning") if intent else None,
        "skip_reason":           None,
        "trade_id":              intent.get("trade_id") if intent else None,
    }


def _fill_fields(fill: dict | None, trade_id: str | None) -> dict:
    """Extract ORDER_FILLED fields for the canonical record."""
    return {
        "order_filled":    bool(fill),
        "fill_timestamp":  fill.get("ts") if fill else None,
        "entry_price":     fill.get("fill_price") if fill else None,
        "quantity":        fill.get("fill_qty") if fill else None,
        "broker_order_id": fill.get("order_id") if fill else None,
        "position_id":     trade_id,
    }


def _outcome_fields(closing: dict | None, outcome: dict, pnl_pct: float | None) -> dict:
    """Extract outcome fields (leakage — NOT model inputs) for the canonical record."""
    position_closed = bool(closing) or (pnl_pct is not None)
    return {
        "position_closed":  position_closed,
        "exit_timestamp":   outcome.get("exit_timestamp"),
        "exit_price":       outcome.get("exit_price"),
        "exit_reason":      outcome.get("exit_reason"),
        "realised_pnl":     outcome.get("realised_pnl"),
        "realised_pnl_pct": pnl_pct,
        "hold_minutes":     outcome.get("hold_minutes"),
        "outcome_label":    _outcome_label(pnl_pct),
    }


# ── Record builder ─────────────────────────────────────────────────────────────

def _build_record(
    obs: dict,
    intent: dict | None,
    fill: dict | None,
    closing: dict | None,
    outcome: dict,
    join_quality: str,
    ts_now: datetime,
    source_files: list[str],
) -> dict:
    """Assemble one canonical learning record from observation + joined events."""
    trade_id = intent.get("trade_id") if intent else None
    pnl_pct = outcome.get("realised_pnl_pct")

    rec: dict = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": ts_now.isoformat(),
        **_obs_fields(obs),
        **_intent_fields(intent),
        **_fill_fields(fill, trade_id),
        **_outcome_fields(closing, outcome, pnl_pct),
        "join_quality":       join_quality,
        "ml_eligible":        False,   # set below
        "exclusion_reason":   None,    # set below
        "data_quality_flags": [],
        "source_files_used":  source_files,
    }
    # Flatten dim_* features from the observation record
    for key, val in obs.items():
        if key.startswith("dim_"):
            rec[key] = val
    # Classify eligibility last — sees the complete record
    eligible, reason = _classify_eligible(rec)
    rec["ml_eligible"] = eligible
    rec["exclusion_reason"] = reason
    return rec


# ── Input loading ──────────────────────────────────────────────────────────────

def _load_inputs(
    obs_file: Path,
    events_file: Path,
    training_file: Path,
    ledger_file: Path,
) -> tuple[list[dict], dict, dict[str, dict], list[str]]:
    """
    Load all input files and return (observations, event_index, outcome_index, source_files).
    """
    source_files: list[str] = []
    observations = _load_jsonl(obs_file)
    if observations:
        source_files.append(obs_file.name)

    events = _load_jsonl(events_file)
    if events:
        source_files.append(events_file.name)

    training = _load_jsonl(training_file)
    if training:
        source_files.append(training_file.name)

    ledger = _load_jsonl(ledger_file)
    if ledger:
        source_files.append(ledger_file.name)

    event_idx = _index_events(events)
    outcome_idx = _index_outcomes(training, ledger)
    return observations, event_idx, outcome_idx, source_files


# ── Summary builder ────────────────────────────────────────────────────────────

def _aggregate_counts(records: list[dict]) -> dict:
    """Compute aggregate counts over the canonical records list."""
    joins: dict[str, int] = {}
    labels: dict[str | None, int] = {}
    regime_dist: dict[str, int] = {}
    source_dist: dict[str, int] = {}
    direction_dist: dict[str, int] = {}
    dates: list[str] = []

    for rec in records:
        jq = rec.get("join_quality", "no_match")
        joins[jq] = joins.get(jq, 0) + 1
        lbl = rec.get("outcome_label")
        labels[lbl] = labels.get(lbl, 0) + 1
        if rec.get("regime"):
            regime_dist[rec["regime"]] = regime_dist.get(rec["regime"], 0) + 1
        if rec.get("candidate_source"):
            source_dist[rec["candidate_source"]] = source_dist.get(rec["candidate_source"], 0) + 1
        if rec.get("direction"):
            direction_dist[rec["direction"]] = direction_dist.get(rec["direction"], 0) + 1
        if rec.get("session_date"):
            dates.append(rec["session_date"])

    return {
        "joins": joins, "labels": labels,
        "regime_dist": regime_dist, "source_dist": source_dist,
        "direction_dist": direction_dist, "dates": dates,
    }


def _build_summary(records: list[dict], source_files: list[str], ts_now: datetime) -> dict:
    """Compute and return the canonical_learning_dataset_summary dict."""
    base: dict = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": ts_now.isoformat(),
        "total_observations": len(records),
        "source_files": source_files,
    }
    if not records:
        base.update({
            "joined_exact": 0, "joined_fallback": 0, "no_match": 0,
            "trade_taken": 0, "order_filled": 0, "position_closed": 0,
            "ml_eligible": 0, "win_count": 0, "loss_count": 0,
            "breakeven_count": 0, "outcome_label_null": 0,
            "date_range": {"earliest": None, "latest": None},
            "regime_distribution": {}, "candidate_source_distribution": {},
            "direction_distribution": {},
        })
        return base

    agg = _aggregate_counts(records)
    joins, labels = agg["joins"], agg["labels"]
    dates = agg["dates"]
    base.update({
        "joined_exact":    joins.get("exact", 0),
        "joined_fallback": joins.get("fallback", 0),
        "no_match":        joins.get("no_match", 0),
        "trade_taken":     sum(1 for r in records if r.get("trade_taken")),
        "order_filled":    sum(1 for r in records if r.get("order_filled")),
        "position_closed": sum(1 for r in records if r.get("position_closed")),
        "ml_eligible":     sum(1 for r in records if r.get("ml_eligible")),
        "win_count":       labels.get("WIN", 0),
        "loss_count":      labels.get("LOSS", 0),
        "breakeven_count": labels.get("BREAKEVEN", 0),
        "outcome_label_null": labels.get(None, 0),
        "date_range": {
            "earliest": min(dates) if dates else None,
            "latest":   max(dates) if dates else None,
        },
        "regime_distribution":         agg["regime_dist"],
        "candidate_source_distribution": agg["source_dist"],
        "direction_distribution":      agg["direction_dist"],
    })
    return base


# ── Output writers ─────────────────────────────────────────────────────────────

def _write_outputs(records: list[dict], summary: dict, out_file: Path, summ_file: Path) -> None:
    """Write canonical_learning_dataset.jsonl and summary JSON to disk."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    log.info("ml_outcome_joiner: wrote %d records → %s", len(records), out_file)

    summ_file.parent.mkdir(parents=True, exist_ok=True)
    with open(summ_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    log.info("ml_outcome_joiner: wrote summary → %s", summ_file)


# ── Main join function ─────────────────────────────────────────────────────────

def join_outcomes(
    obs_path: str | Path | None = None,
    events_path: str | Path | None = None,
    training_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    output_path: str | Path | None = None,
    summary_path: str | Path | None = None,
    dry_run: bool = False,
) -> tuple[list[dict], dict]:
    """
    Join observations to outcomes and write the canonical learning dataset.

    All path parameters are overridable for testing.
    Production always uses canonical paths from _canonical_paths().

    Returns (records, summary).
    """
    paths = _canonical_paths()
    obs_file      = Path(obs_path)      if obs_path      else paths["observations"]
    events_file   = Path(events_path)   if events_path   else paths["events"]
    training_file = Path(training_path) if training_path else paths["training"]
    ledger_file   = Path(ledger_path)   if ledger_path   else paths["ledger"]
    out_file      = Path(output_path)   if output_path   else paths["output"]
    summ_file     = Path(summary_path)  if summary_path  else paths["summary"]

    ts_now = datetime.now(UTC)
    observations, event_idx, outcome_idx, source_files = _load_inputs(
        obs_file, events_file, training_file, ledger_file,
    )

    if not observations:
        log.info("ml_outcome_joiner: no observations — writing empty dataset")
        summary = _build_summary([], source_files, ts_now)
        if not dry_run:
            _write_outputs([], summary, out_file, summ_file)
        return [], summary

    records: list[dict] = []
    for obs in observations:
        obs_id = obs.get("observation_id") or ""
        intent = _exact_join(obs_id, event_idx["intents_by_obs_id"])
        join_quality = "exact" if intent else None
        if intent is None:
            intent = _fallback_join(obs, event_idx["intents_list"])
            join_quality = "fallback" if intent else "no_match"

        trade_id = intent.get("trade_id") if intent else None
        fill     = event_idx["fills"].get(trade_id) if trade_id else None
        closing  = event_idx["closings"].get(trade_id) if trade_id else None
        outcome  = _extract_outcome(trade_id, outcome_idx)

        records.append(_build_record(obs, intent, fill, closing, outcome,
                                     join_quality, ts_now, source_files))

    summary = _build_summary(records, source_files, ts_now)
    if not dry_run:
        _write_outputs(records, summary, out_file, summ_file)

    log.info(
        "ml_outcome_joiner: %d obs → %d records "
        "(exact=%d fallback=%d no_match=%d ml_eligible=%d)",
        len(observations), len(records),
        summary["joined_exact"], summary["joined_fallback"],
        summary["no_match"], summary["ml_eligible"],
    )
    return records, summary


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Decifer ML — offline outcome joiner (Sprint 3). "
            "Joins signal observations to realised trade outcomes."
        )
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Parse and join without writing output files")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Enable DEBUG logging")
    return p


def main() -> int:
    args = _build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    _, summary = join_outcomes(dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
