#!/usr/bin/env python3
"""
rotation_paper_validation.py — Paper-validation harness for the Decifer rotation policy.

This is a READ-ONLY diagnostic tool.  It does NOT:
  - connect to any broker
  - generate any orders
  - modify any runtime files
  - import any trading runtime modules

It reads rotation observability JSONL and training records to simulate what a
rotation policy would have done, then compares the hypothetical rotated book
against actual outcomes (where available).

Service layer : paper validation / offline simulation
Runtime purpose: none — no live trading purpose, no broker dependency,
                 no order dependency, no execution side-effects.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

UTC = timezone.utc

# ── Cluster and ETF maps (identical to rotation_shadow_report.py) ─────────────
# Copied here so this script has zero runtime-module imports.

_CLUSTER_MEMBERS: dict[str, list[str]] = {
    "Tech / AI / Semis": [
        "NVDA", "AAPL", "MSFT", "AMD", "TSLA", "TSM", "ASML", "AVGO", "XLK",
        "SMH", "MU", "SNDK", "WDC", "INTC", "AMAT", "LRCX", "QQQ", "CRWD",
        "ALAB", "IREN",
    ],
    "Energy": ["CVX", "XOM", "USO", "OXY", "SLB", "XLE", "COP", "EOG"],
    "Power / Infrastructure": ["VRT", "PWR", "CEG", "ETN", "DOV", "EME", "STRL"],
    "Healthcare / Biotech": ["HIMS", "NBIS", "IBB", "LLY", "UNH", "PFE", "MRK", "ISRG"],
    "Financials": ["GS", "JPM", "BAC", "MS", "XLF", "BLK", "C", "WFC"],
    "Consumer / Defensive": ["XLP", "KO", "PEP", "WMT", "COST", "PG", "PM"],
    "Macro / Alternative": ["GLD", "IBIT", "TLT", "UUP"],
}
CLUSTER_MAP: dict[str, str] = {}
for _cluster, _members in _CLUSTER_MEMBERS.items():
    for _sym in _members:
        CLUSTER_MAP[_sym] = _cluster

ETF_OVERLAP: dict[str, list[str]] = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ORCL", "ADBE", "CSCO"],
    "QQQ": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "AMZN", "META", "GOOGL", "GOOG", "TSLA"],
    "SMH": ["NVDA", "TSM", "ASML", "AVGO", "AMD", "AMAT", "LRCX", "MU", "INTC"],
    "XLE": ["XOM", "CVX", "SLB", "OXY", "COP", "EOG"],
    "XLF": ["GS", "JPM", "BAC", "MS", "WFC", "C", "BLK"],
    "XLP": ["KO", "PEP", "WMT", "COST", "PG", "PM"],
    "USO": ["XOM", "CVX", "OXY", "SLB", "XLE"],
    "IWM": [], "SPY": [], "GLD": [], "IBIT": [], "IBB": [],
}
ETF_UNIVERSE: frozenset[str] = frozenset(ETF_OVERLAP)

# Block types that paper-validation can evaluate (margin cap → rotation can help)
_QUALIFYING_BLOCK_TYPES: frozenset[str] = frozenset({"margin_gross_cap_block"})


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def cluster_of(symbol: str) -> str:
    return CLUSTER_MAP.get(symbol, "Other")


def _has_etf_overlap(symbol: str, held_symbols: set[str]) -> bool:
    """True if symbol is an ETF and at least one component is also held."""
    if symbol not in ETF_UNIVERSE:
        return False
    components = set(ETF_OVERLAP.get(symbol, []))
    return bool(components & held_symbols)


def rotation_shadow_score(
    blocked_score: float,
    pos_score: float,
    is_below_35: bool,
    has_etf_overlap_below_50: bool,
    is_low_score_cluster: bool,
    is_pru_displacement: bool,
    is_carry: bool,
) -> float:
    """
    Identical formula to rotation_shadow_report.py:
      score_delta
      + 10  if position score < 35
      + 8   if ETF overlap flag and ETF score < 50
      + 5   if low-score cluster
      + 5   if PRU/discovery displacement
      + 3   if carry position
    """
    s = blocked_score - pos_score
    if is_below_35:
        s += 10
    if has_etf_overlap_below_50:
        s += 8
    if is_low_score_cluster:
        s += 5
    if is_pru_displacement:
        s += 5
    if is_carry:
        s += 3
    return s


def _book_avg_score(positions: dict[str, dict]) -> Optional[float]:
    scores = [float(p["score"]) for p in positions.values() if p.get("score") is not None]
    return (sum(scores) / len(scores)) if scores else None


# ── Data loading ──────────────────────────────────────────────────────────────

def load_margin_blocks(path: pathlib.Path, since: date) -> tuple[list[dict], int]:
    """Load margin_blocks.jsonl filtered to >= since.  Returns (records, malformed)."""
    records: list[dict] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = _parse_ts(r.get("ts"))
                if ts and ts.date() >= since:
                    r["_ts"] = ts
                    records.append(r)
            except Exception:
                malformed += 1
    return records, malformed


def load_position_snapshots(path: pathlib.Path) -> tuple[list[dict], int]:
    """Load position_snapshots.jsonl (all records).  Returns (records, malformed)."""
    records: list[dict] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = _parse_ts(r.get("ts"))
                if ts:
                    r["_ts"] = ts
                    records.append(r)
            except Exception:
                malformed += 1
    return records, malformed


def find_snapshot_at(
    snapshots: list[dict],
    block_ts: datetime,
    symbol: str = "",
    tolerance_seconds: float = 5.0,
) -> Optional[dict]:
    """
    Find the position snapshot that matches a block event.

    Primary: match by trigger field (``"margin_block:<symbol>"``).  This is the
    semantically correct match — the snapshot was explicitly written for that block.

    Fallback: closest snapshot whose ts is within tolerance_seconds of block_ts.
    The snapshot is always written a few milliseconds AFTER the block event, so a
    strict ``ts <= block_ts`` comparison always fails.  The tolerance window covers
    that sub-second write lag without accidentally picking up a snapshot from a
    completely different block.
    """
    # Primary: match by trigger (most precise)
    if symbol:
        trigger_key = f"margin_block:{symbol}"
        triggered = [
            s for s in snapshots
            if s.get("trigger") == trigger_key
            and s.get("_ts") is not None
            and abs((s["_ts"] - block_ts).total_seconds()) <= tolerance_seconds
        ]
        if triggered:
            # Return the closest to block_ts
            return min(triggered, key=lambda s: abs((s["_ts"] - block_ts).total_seconds()))

    # Fallback: any snapshot within the tolerance window of block_ts
    candidates = [
        s for s in snapshots
        if s.get("_ts") is not None
        and abs((s["_ts"] - block_ts).total_seconds()) <= tolerance_seconds
    ]
    return min(candidates, key=lambda s: abs((s["_ts"] - block_ts).total_seconds())) if candidates else None


def load_training_records(path: pathlib.Path) -> dict[str, list[dict]]:
    """Load training_records.jsonl indexed by symbol."""
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    if not path.exists():
        return by_symbol
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                sym = r.get("symbol")
                if sym:
                    by_symbol[sym].append(r)
            except Exception:
                pass
    return by_symbol


def find_forward_outcome(
    symbol: str,
    block_ts: datetime,
    training_by_symbol: dict[str, list[dict]],
    lookahead_hours: int,
    mode: str,
) -> Optional[dict]:
    """
    mode="blocked_candidate": find a new fill (ts_fill) after block_ts within window.
    mode="shadow_exit":        find a close (ts_close) after block_ts within window.

    Returns a dict with outcome fields, or None.
    """
    deadline = block_ts + timedelta(hours=lookahead_hours)
    for rec in training_by_symbol.get(symbol, []):
        ts_fill = _parse_ts(rec.get("ts_fill"))
        ts_close = _parse_ts(rec.get("ts_close"))

        if mode == "blocked_candidate":
            if ts_fill and block_ts <= ts_fill <= deadline:
                return {
                    "outcome_type": "NEW_ENTRY_AFTER_BLOCK",
                    "ts_fill": ts_fill.isoformat(),
                    "ts_close": ts_close.isoformat() if ts_close else None,
                    "pnl": rec.get("pnl"),
                    "pnl_pct": rec.get("pnl_pct"),
                    "fill_price": rec.get("fill_price"),
                    "exit_price": rec.get("exit_price"),
                    "hold_minutes": rec.get("hold_minutes"),
                    "note": "total trade P&L from entry to close",
                }
        elif mode == "shadow_exit":
            if ts_close and block_ts <= ts_close <= deadline:
                return {
                    "outcome_type": "POSITION_CLOSED_AFTER_BLOCK",
                    "ts_fill": ts_fill.isoformat() if ts_fill else None,
                    "ts_close": ts_close.isoformat(),
                    "pnl": rec.get("pnl"),
                    "pnl_pct": rec.get("pnl_pct"),
                    "fill_price": rec.get("fill_price"),
                    "exit_price": rec.get("exit_price"),
                    "hold_minutes": rec.get("hold_minutes"),
                    "note": "total realized P&L (not only the post-block portion)",
                }
    return None


# ── Shadow exit ranking ───────────────────────────────────────────────────────

def rank_shadow_exits(
    positions: dict[str, dict],
    blocked_score: float,
    session_date: date,
) -> list[dict]:
    """
    Rank open positions as hypothetical shadow exit candidates.
    Uses the identical rotation_shadow_score formula.
    """
    held_symbols = set(positions)

    cluster_scores: dict[str, list[float]] = defaultdict(list)
    for p in positions.values():
        sc = p.get("score")
        if sc is not None:
            cluster_scores[cluster_of(p.get("symbol", ""))].append(float(sc))
    low_score_clusters = {
        c for c, scores in cluster_scores.items()
        if scores and (sum(scores) / len(scores)) < 50
    }

    candidates = []
    for sym, pos in positions.items():
        pos_score_raw = pos.get("score")
        if pos_score_raw is None:
            continue
        pos_score = float(pos_score_raw)

        open_time = _parse_ts(pos.get("open_time"))
        is_carry = bool(open_time and open_time.date() < session_date)

        is_below_35 = pos_score < 35
        etf_overlap = _has_etf_overlap(sym, held_symbols)
        etf_overlap_below_50 = etf_overlap and pos_score < 50
        is_low_cluster = cluster_of(sym) in low_score_clusters

        rss = rotation_shadow_score(
            blocked_score=blocked_score,
            pos_score=pos_score,
            is_below_35=is_below_35,
            has_etf_overlap_below_50=etf_overlap_below_50,
            is_low_score_cluster=is_low_cluster,
            is_pru_displacement=False,
            is_carry=is_carry,
        )

        entry = float(pos.get("entry") or 0.0)
        qty = float(pos.get("qty") or 0)
        notional = qty * entry

        candidates.append({
            "symbol": sym,
            "score": pos_score,
            "rotation_shadow_score": rss,
            "score_delta": blocked_score - pos_score,
            "notional": round(notional, 2),
            "below_35": is_below_35,
            "etf_overlap_below_50": etf_overlap_below_50,
            "low_score_cluster": is_low_cluster,
            "is_carry": is_carry,
            "cluster": cluster_of(sym),
            "open_time": pos.get("open_time"),
        })

    # Deterministic sort: rss desc → score_delta desc → symbol asc
    candidates.sort(key=lambda c: (-c["rotation_shadow_score"], -c["score_delta"], c["symbol"]))
    for i, c in enumerate(candidates):
        c["rank"] = i + 1
    return candidates


# ── Block qualification ───────────────────────────────────────────────────────

def qualify_block(
    block: dict,
    snapshots: list[dict],
    min_blocked_score: float,
    min_gap_vs_book: float,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Returns (opportunity, None) when the block qualifies for paper validation.
    Returns (None, skip_reason) otherwise.
    """
    exp_code = block.get("exp_code", "")
    symbol = block.get("symbol", "UNKNOWN")
    candidate_score = block.get("candidate_score")
    ts = block.get("_ts")

    if exp_code not in _QUALIFYING_BLOCK_TYPES:
        return None, f"{symbol}: excluded block type '{exp_code}'"

    if candidate_score is None:
        return None, f"{symbol}: missing candidate_score"

    if ts is None:
        return None, f"{symbol}: missing timestamp"

    snapshot = find_snapshot_at(snapshots, ts, symbol=symbol)
    if snapshot is None:
        return None, f"{symbol}: no position snapshot found at block time"

    positions = snapshot.get("positions", {})
    if not positions:
        return None, f"{symbol}: empty position snapshot"

    avg = _book_avg_score(positions)
    if avg is None:
        return None, f"{symbol}: cannot compute book average score (no scored positions)"

    gap = float(candidate_score) - avg

    if float(candidate_score) < min_blocked_score:
        return None, f"{symbol}: score {candidate_score} < min_blocked_score {min_blocked_score}"

    if gap < min_gap_vs_book:
        return None, f"{symbol}: gap {gap:.1f} < min_gap_vs_book {min_gap_vs_book}"

    scores_below_50 = sum(
        1 for p in positions.values()
        if p.get("score") is not None and float(p["score"]) < 50
    )
    scores_below_35 = sum(
        1 for p in positions.values()
        if p.get("score") is not None and float(p["score"]) < 35
    )

    if scores_below_50 < 3:
        return None, f"{symbol}: only {scores_below_50} positions below 50 (need ≥3)"
    if scores_below_35 < 1:
        return None, f"{symbol}: no positions below 35 (need ≥1)"

    return {
        "block_ts": ts.isoformat(),
        "_block_ts": ts,
        "blocked_symbol": symbol,
        "blocked_score": float(candidate_score),
        "block_type": exp_code,
        "book_avg_score": round(avg, 2),
        "gap_vs_book": round(gap, 2),
        "estimated_notional": block.get("estimated_notional"),
        "notional_is_estimate": bool(block.get("notional_is_estimate", True)),
        "portfolio_value": block.get("portfolio_value"),
        "open_position_count": len(positions),
        "scores_below_50": scores_below_50,
        "scores_below_35": scores_below_35,
        "snapshot_ts": snapshot.get("ts"),
        "positions": positions,
    }, None


# ── Scenario building ─────────────────────────────────────────────────────────

def build_scenarios(
    opportunity: dict,
    training_by_symbol: dict[str, list[dict]],
    lookahead_hours: int,
    max_shadow_exits: int,
) -> list[dict]:
    """
    Build Scenario A (top-1 exit), B (top-2 exits), C (top-3 exits).
    Each scenario is self-contained and labelled ESTIMATE where notional is provisional.
    """
    ts = opportunity["_block_ts"]
    session_date = ts.date()
    positions = opportunity["positions"]
    blocked_symbol = opportunity["blocked_symbol"]
    blocked_score = opportunity["blocked_score"]
    estimated_notional = float(opportunity.get("estimated_notional") or 0.0)

    shadow_ranks = rank_shadow_exits(positions, blocked_score, session_date)
    top_n = shadow_ranks[:max_shadow_exits]

    blocked_outcome = find_forward_outcome(
        blocked_symbol, ts, training_by_symbol, lookahead_hours, "blocked_candidate"
    )

    scenarios = []
    for n in range(1, min(max_shadow_exits, len(top_n)) + 1):
        exit_set = top_n[:n]
        cap_released = sum(c["notional"] for c in exit_set)
        cap_sufficient = (cap_released >= estimated_notional) if estimated_notional > 0 else None

        exit_outcomes = []
        for c in exit_set:
            out = find_forward_outcome(
                c["symbol"], ts, training_by_symbol, lookahead_hours, "shadow_exit"
            )
            exit_outcomes.append({
                "symbol": c["symbol"],
                "score": c["score"],
                "notional": c["notional"],
                "rotation_shadow_score": c["rotation_shadow_score"],
                "rank": c["rank"],
                "outcome": out,
            })

        blocked_avail = blocked_outcome is not None
        exits_avail = all(e["outcome"] is not None for e in exit_outcomes)
        all_avail = blocked_avail and exits_avail
        outcome_status = "OUTCOME_AVAILABLE" if all_avail else "OUTCOME_PENDING"

        relative_uplift = None
        actual_book_forward_result = None
        hypothetical_forward_result = None

        if all_avail:
            blocked_pnl = float(blocked_outcome.get("pnl") or 0.0)
            exit_pnl_sum = sum(
                float(e["outcome"].get("pnl") or 0.0)
                for e in exit_outcomes if e["outcome"]
            )
            hypothetical_forward_result = round(blocked_pnl, 2)
            actual_book_forward_result = round(exit_pnl_sum, 2)
            relative_uplift = round(blocked_pnl - exit_pnl_sum, 2)

        label = chr(ord("A") + n - 1)
        scenarios.append({
            "scenario": label,
            "exit_set_size": n,
            "block_ts": opportunity["block_ts"],
            "blocked_symbol": blocked_symbol,
            "blocked_score": blocked_score,
            "block_type": opportunity["block_type"],
            "book_avg_score": opportunity["book_avg_score"],
            "gap_vs_book": opportunity["gap_vs_book"],
            "estimated_notional": opportunity["estimated_notional"],
            "notional_is_estimate": opportunity["notional_is_estimate"],
            "shadow_exit_candidates": exit_outcomes,
            "theoretical_capacity_released": round(cap_released, 2),
            "capacity_sufficient_estimated": cap_sufficient,
            "actual_outcome_available": all_avail,
            "blocked_candidate_outcome": blocked_outcome,
            "actual_book_forward_result": actual_book_forward_result,
            "hypothetical_forward_result": hypothetical_forward_result,
            "relative_uplift": relative_uplift,
            "outcome_status": outcome_status,
            "live_action_permitted": False,
        })

    return scenarios


# ── Verdict ───────────────────────────────────────────────────────────────────

def compute_validation_verdict(scenarios: list[dict]) -> tuple[str, str]:
    """Returns (verdict, recommended_action)."""
    if not scenarios:
        return "PAPER_VALIDATION_NO_OPPORTUNITIES", "KEEP_RUNNING_PAPER_VALIDATION"

    available = [s for s in scenarios if s["outcome_status"] == "OUTCOME_AVAILABLE"]
    pending   = [s for s in scenarios if s["outcome_status"] == "OUTCOME_PENDING"]

    if not available and pending:
        return "PAPER_VALIDATION_PENDING_OUTCOMES", "KEEP_RUNNING_PAPER_VALIDATION"

    if not available and not pending:
        return "PAPER_VALIDATION_INSUFFICIENT_DATA", "FIX_VALIDATION_DATA"

    positive = sum(
        1 for s in available
        if s.get("relative_uplift") is not None and s["relative_uplift"] > 0
    )

    if positive >= 2 and len(available) >= 2:
        return "PAPER_VALIDATION_SUPPORTS_ROTATION", "DESIGN_PAPER_ONLY_POLICY_SIMULATION"

    if positive == 0:
        return "PAPER_VALIDATION_WEAK_SIGNAL", "KEEP_RUNNING_PAPER_VALIDATION"

    return "PAPER_VALIDATION_WEAK_SIGNAL", "EXTEND_LOOKAHEAD_WINDOW"


# ── Report output ─────────────────────────────────────────────────────────────

def _write_txt(report: dict, path: pathlib.Path) -> None:
    W = 72
    RULE = "═" * W
    DIV  = "─" * W

    def _line(*parts: str) -> str:
        return "  " + "  ".join(parts)

    lines = [
        RULE,
        "  DECIFER ROTATION PAPER VALIDATION REPORT",
        RULE,
        _line(f"Generated : {report['report_ts']}"),
        _line(f"Since     : {report['since']}"),
        _line(f"Lookahead : {report['lookahead_hours']}h"),
        "",
        _line(f"VERDICT   : {report['verdict']}"),
        _line(f"Next step : {report['recommended_action']}"),
        "",
        _line(f"Opportunities detected  : {report['opportunities_detected']}"),
        _line(f"Opportunities evaluated : {report['opportunities_evaluated']}"),
        _line(f"Blocks skipped          : {len(report['skipped_blocks'])}"),
        "",
        _line(f"live_rotation_allowed    : {report['live_rotation_allowed']}"),
        _line(f"broker_connected         : {report['broker_connected']}"),
        _line(f"order_generation_allowed : {report['order_generation_allowed']}"),
        RULE,
    ]

    if report["skipped_blocks"]:
        lines += ["", "── SKIPPED BLOCKS ──────────────────────────────────────────────────", ""]
        for sb in report["skipped_blocks"]:
            lines.append(f"  {sb}")

    for sc in report["scenarios"]:
        est_label = " [ESTIMATE — G10 provisional]" if sc["notional_is_estimate"] else ""
        lines += [
            "",
            DIV,
            f"  Scenario {sc['scenario']} — exit top {sc['exit_set_size']} shadow candidate(s)",
            f"  Blocked  : {sc['blocked_symbol']}  score={sc['blocked_score']:.0f}"
            f"  gap=+{sc['gap_vs_book']:.1f}  book_avg={sc['book_avg_score']:.1f}",
            f"  Block type        : {sc['block_type']}",
            f"  Estimated notional: ${sc['estimated_notional']:,.0f}{est_label}",
            f"  Cap released      : ${sc['theoretical_capacity_released']:,.0f}",
            f"  Cap sufficient    : {sc['capacity_sufficient_estimated']}",
            "",
            "  Shadow exit candidates:",
        ]
        for c in sc["shadow_exit_candidates"]:
            out_str = ""
            if c["outcome"] and c["outcome"].get("pnl") is not None:
                out_str = f"  → actual P&L ${c['outcome']['pnl']:+,.0f}"
            lines.append(
                f"    [{c['rank']}] {c['symbol']:<6s} score={c['score']:3.0f}"
                f"  notional=${c['notional']:>10,.0f}"
                f"  rss={c['rotation_shadow_score']:.0f}{out_str}"
            )

        lines += ["", f"  Outcome status    : {sc['outcome_status']}"]
        if sc["actual_outcome_available"]:
            lines += [
                f"  Blocked cand P&L (hypothetical entry)  : ${sc['hypothetical_forward_result']:+,.0f}",
                f"  Shadow exits actual P&L (total trade)  : ${sc['actual_book_forward_result']:+,.0f}",
                f"  Relative uplift (hyp − actual)         : ${sc['relative_uplift']:+,.0f}",
            ]
        else:
            lines.append(
                "  Outcomes pending — lookahead window not elapsed or positions still open."
            )
        lines.append(f"  live_action_permitted : {sc['live_action_permitted']}")

    if report["scenarios"]:
        lines.append(DIV)

    lines += [
        "",
        "── DATA QUALITY GAPS ────────────────────────────────────────────────",
        "",
    ]
    for gap_note in report["data_quality_gaps"]:
        lines.append(f"  • {gap_note}")

    lines += [
        "",
        RULE,
        "  IMPORTANT: Paper-validation simulation only.",
        "  No positions are recommended for sale or entry.",
        "  No orders generated.  No broker connected.",
        "  All notional figures marked [ESTIMATE] are provisional (G10 not met).",
        RULE,
    ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clean_for_json(o: Any) -> Any:
    """Strip internal _-prefixed keys and serialize datetimes."""
    if isinstance(o, dict):
        return {k: _clean_for_json(v) for k, v in o.items() if not k.startswith("_")}
    if isinstance(o, list):
        return [_clean_for_json(i) for i in o]
    if isinstance(o, datetime):
        return o.isoformat()
    return o


def write_reports(report: dict, output_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"report_{ts_str}.json"
    txt_path  = output_dir / f"report_{ts_str}.txt"
    json_path.write_text(json.dumps(_clean_for_json(report), indent=2), encoding="utf-8")
    _write_txt(report, txt_path)
    return json_path, txt_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        description="Rotation paper-validation harness (read-only, no broker, no orders)."
    )
    parser.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    parser.add_argument("--max-shadow-exits", type=int, default=3)
    parser.add_argument("--min-blocked-score", type=float, default=70.0)
    parser.add_argument("--min-gap-vs-book", type=float, default=20.0)
    args = parser.parse_args(argv)

    since = date.fromisoformat(args.since)
    repo = _repo_root()
    obs_dir  = repo / "data" / "rotation_observability"
    data_dir = repo / "data"
    output_dir = (
        pathlib.Path(args.output_dir)
        if args.output_dir
        else repo / "data" / "rotation_paper_validation"
    )

    blocks,    blocks_bad = load_margin_blocks(obs_dir / "margin_blocks.jsonl", since)
    snapshots, snaps_bad  = load_position_snapshots(obs_dir / "position_snapshots.jsonl")
    training   = load_training_records(data_dir / "training_records.jsonl")

    dq_gaps: list[str] = []
    if not (obs_dir / "margin_blocks.jsonl").exists():
        dq_gaps.append("margin_blocks.jsonl not found — no observability data available")
    if not (obs_dir / "position_snapshots.jsonl").exists():
        dq_gaps.append("position_snapshots.jsonl not found — book reconstruction unavailable")
    if not (data_dir / "training_records.jsonl").exists():
        dq_gaps.append("training_records.jsonl not found — forward outcomes unavailable")
    if blocks_bad:
        dq_gaps.append(f"margin_blocks.jsonl: {blocks_bad} malformed line(s) skipped")
    if snaps_bad:
        dq_gaps.append(f"position_snapshots.jsonl: {snaps_bad} malformed line(s) skipped")
    dq_gaps.append(
        "estimated_notional = portfolio_value × max_single_pct"
        " — upper-bound estimate, not sizing-engine output (G10 provisional)"
    )
    dq_gaps.append(
        "shadow exit P&L = total realized P&L from original entry to close"
        " — not only the post-block portion"
    )
    dq_gaps.append(
        "positions.json reflects end-of-session state"
        " — positions closed intra-session may be absent from reconstruction"
    )

    def _empty_report(verdict: str, skipped: list[str]) -> dict:
        return {
            "report_ts": datetime.now(UTC).isoformat(),
            "since": args.since,
            "lookahead_hours": args.lookahead_hours,
            "verdict": verdict,
            "recommended_action": "KEEP_RUNNING_PAPER_VALIDATION",
            "opportunities_detected": 0,
            "opportunities_evaluated": 0,
            "skipped_blocks": skipped,
            "scenarios": [],
            "data_quality_gaps": dq_gaps,
            "live_rotation_allowed": False,
            "broker_connected": False,
            "order_generation_allowed": False,
        }

    if not blocks:
        report = _empty_report(
            "PAPER_VALIDATION_NO_OPPORTUNITIES",
            ["No margin blocks found for the given date range"],
        )
        jp, tp = write_reports(report, output_dir)
        print(f"wrote: {jp}")
        print(f"wrote: {tp}")
        return report

    # Qualify and deduplicate (first qualifying block per symbol+date)
    opportunities: list[dict] = []
    skipped: list[str] = []
    seen: set[tuple[str, str]] = set()

    for block in sorted(blocks, key=lambda b: b.get("_ts") or datetime.min.replace(tzinfo=UTC)):
        sym = block.get("symbol", "UNKNOWN")
        ts  = block.get("_ts")
        key = (sym, ts.date().isoformat() if ts else "unknown")

        opp, reason = qualify_block(block, snapshots, args.min_blocked_score, args.min_gap_vs_book)
        if opp is None:
            skipped.append(reason)
            continue
        if key in seen:
            skipped.append(f"{sym}: duplicate opportunity (same symbol+date) — first qualifying block used")
            continue
        seen.add(key)
        opportunities.append(opp)

    all_scenarios: list[dict] = []
    for opp in opportunities:
        all_scenarios.extend(
            build_scenarios(opp, training, args.lookahead_hours, args.max_shadow_exits)
        )

    verdict, recommended_action = compute_validation_verdict(all_scenarios)

    report = {
        "report_ts": datetime.now(UTC).isoformat(),
        "since": args.since,
        "lookahead_hours": args.lookahead_hours,
        "verdict": verdict,
        "recommended_action": recommended_action,
        "opportunities_detected": len(opportunities),
        "opportunities_evaluated": len(opportunities),
        "skipped_blocks": skipped,
        "scenarios": all_scenarios,
        "data_quality_gaps": dq_gaps,
        "live_rotation_allowed": False,
        "broker_connected": False,
        "order_generation_allowed": False,
    }

    jp, tp = write_reports(report, output_dir)
    print(f"wrote: {jp}")
    print(f"wrote: {tp}")
    return report


if __name__ == "__main__":
    main()
