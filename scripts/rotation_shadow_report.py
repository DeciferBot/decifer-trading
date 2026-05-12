#!/usr/bin/env python3
"""
rotation_shadow_report.py — Read-only capital sequencing counterfactual diagnostic.

Answers: if a high-score candidate was blocked by margin, which weak open positions
would have been the most logical shadow rotation candidates, and how much capacity
could they theoretically have freed?

Service layer  : Reporting / diagnostics only
Runtime purpose: Offline analysis — no live bot loop, no broker, no side effects
Imports        : stdlib only — no trading runtime modules
Output         : data/rotation_shadow_reports/report_<UTC_DATE>.json + .txt + stdout

Usage:
    python3 scripts/rotation_shadow_report.py
    python3 scripts/rotation_shadow_report.py --since 2026-05-12
    python3 scripts/rotation_shadow_report.py --since 2026-05-11 --output-dir /tmp/rsr

IMPORTANT:
  This script does NOT recommend live rotation.
  Every finding is labelled "shadow rotation candidate" or "theoretical capacity release".
  Nothing in this script changes trading behaviour, thresholds, or execution logic.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

# ── Repo root ─────────────────────────────────────────────────────────────────
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parent

# ── Static reference data (copied from trade_quality_report.py — no import) ──

ETF_OVERLAP: dict[str, list[str]] = {
    "XLK":  ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ORCL", "ADBE", "CSCO"],
    "QQQ":  ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "AMZN", "META", "GOOGL", "GOOG", "TSLA"],
    "SMH":  ["NVDA", "TSM", "ASML", "AVGO", "AMD", "AMAT", "LRCX", "MU", "INTC"],
    "XLE":  ["XOM", "CVX", "SLB", "OXY", "COP", "EOG"],
    "XLF":  ["GS", "JPM", "BAC", "MS", "WFC", "C", "BLK"],
    "XLP":  ["KO", "PEP", "WMT", "COST", "PG", "PM"],
    "USO":  ["XOM", "CVX", "OXY", "SLB", "XLE"],
    "IWM":  [],
    "SPY":  [],
    "GLD":  [],
    "IBIT": [],
    "IBB":  [],
}
ETF_UNIVERSE: frozenset[str] = frozenset(ETF_OVERLAP)

CLUSTER_MAP: dict[str, str] = {}
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
for _cluster, _members in _CLUSTER_MEMBERS.items():
    for _sym in _members:
        CLUSTER_MAP[_sym] = _cluster


# ── Ranking formula (transparent and deterministic) ───────────────────────────

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
    rotation_shadow_score =
      score_delta                              (blocked_score − pos_score)
      + 10  if position score below 35
      + 8   if ETF overlap flag and ETF score below 50
      + 5   if low-score cluster flag
      + 5   if PRU/discovery displacement flag
      + 3   if position is older than current session (carry)
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


# ── Verdict thresholds ────────────────────────────────────────────────────────

def compute_shadow_verdict(
    outscores_15: int,
    outscores_20: int,
    weak_positions_before_block: int,
    top3_notional: float,
    multi_session: bool,
    confidence: str,
) -> str:
    """
    ROTATION_SHADOW_CONFIRMED requires all four:
      - multi-session pattern (≥2 sessions)
      - at least one blocked candidate outscored book by >20
      - at least three weak positions below 50 before the block
      - top 1–3 shadow rotation candidates could release material NLV
      - confidence MEDIUM or HIGH

    ROTATION_WATCH requires:
      - at least one blocked outscored book by >15
      - at least two weak positions below 50

    NO_ROTATION_EVIDENCE if thresholds not met.
    INSUFFICIENT_DATA if core inputs unavailable.
    """
    if outscores_15 == 0 and outscores_20 == 0:
        if weak_positions_before_block < 2:
            return "NO_ROTATION_EVIDENCE"
        return "NO_ROTATION_EVIDENCE"

    if confidence == "LOW" and top3_notional == 0:
        return "INSUFFICIENT_DATA"

    material_release = top3_notional > 40_000  # >$40K is material

    if (
        multi_session
        and outscores_20 >= 1
        and weak_positions_before_block >= 3
        and material_release
        and confidence in ("MEDIUM", "HIGH")
    ):
        return "ROTATION_SHADOW_CONFIRMED"

    if outscores_15 >= 1 and weak_positions_before_block >= 2:
        return "ROTATION_WATCH"

    if outscores_15 >= 1:
        return "ROTATION_WATCH"

    return "NO_ROTATION_EVIDENCE"


def shadow_verdict_action(verdict: str) -> str:
    return {
        "INSUFFICIENT_DATA":        "FIX_DATA_QUALITY",
        "NO_ROTATION_EVIDENCE":     "KEEP OBSERVING",
        "ROTATION_WATCH":           "RUN_ONE_MORE_SESSION",
        "ROTATION_SHADOW_CONFIRMED": "DESIGN_ROTATION_POLICY_SPEC",
    }.get(verdict, "KEEP OBSERVING")


# ── Data quality ──────────────────────────────────────────────────────────────

class DataQuality:
    def __init__(self) -> None:
        self.missing_files: list[str] = []
        self.malformed_lines: dict[str, int] = {}
        self.warnings: list[str] = []

    def mark_missing(self, path: str) -> None:
        self.missing_files.append(path)

    def add_malformed(self, fname: str, count: int) -> None:
        if count:
            self.malformed_lines[fname] = self.malformed_lines.get(fname, 0) + count

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def cluster_of(symbol: str) -> str:
    return CLUSTER_MAP.get(symbol, "Other")


def _position_score(p: dict) -> float | None:
    for field in ("entry_score", "score"):
        v = p.get(field)
        if v is not None:
            try:
                f = float(v)
                if f:
                    return f
            except (TypeError, ValueError):
                pass
    return None


def _notional(p: dict) -> float:
    px  = p.get("current") or p.get("entry") or 0.0
    qty = p.get("qty") or 0
    try:
        return float(px) * float(qty)
    except (TypeError, ValueError):
        return 0.0


def _open_time(p: dict) -> datetime | None:
    raw = p.get("open_time") or p.get("entry_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _safe_mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.mean(clean) if clean else None


def _fmt_row(*cols: Any, widths: list[int]) -> str:
    parts = []
    for i, c in enumerate(cols):
        w = widths[i] if i < len(widths) else 12
        s = "—" if c is None else str(c)
        parts.append(s[:w].ljust(w))
    return "  ".join(parts)


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_jsonl(path: pathlib.Path, dq: DataQuality, since: date | None = None) -> list[dict]:
    if not path.exists():
        dq.mark_missing(str(path))
        return []
    records: list[dict] = []
    bad = 0
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                bad += 1
                continue
            if since is not None:
                ts_str = (
                    r.get("ts") or r.get("timestamp") or
                    r.get("ts_close") or r.get("ts_fill") or
                    r.get("close_time") or ""
                )
                if ts_str:
                    try:
                        rec_date = datetime.fromisoformat(
                            str(ts_str).replace("Z", "+00:00")
                        ).date()
                        if rec_date < since:
                            continue
                    except (ValueError, TypeError):
                        pass
            records.append(r)
    dq.add_malformed(path.name, bad)
    return records


def load_positions(path: pathlib.Path, dq: DataQuality) -> list[dict]:
    if not path.exists():
        dq.mark_missing(str(path))
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        dq.warn(f"positions.json could not be parsed: {exc}")
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        inner = raw.get("active_trades") or raw.get("positions") or raw
        if isinstance(inner, dict):
            return list(inner.values())
        if isinstance(inner, list):
            return inner
    dq.warn("positions.json: unrecognised structure")
    return []


# ── Log parsers ───────────────────────────────────────────────────────────────

_LOG_TS_RE      = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_MARGIN_BLOCK_RE = re.compile(
    r"Combined exposure block for (\w+): "
    r"Margin gross cap: \$([0-9,]+) deployed \+ \$([0-9,]+) new = ([\d.]+)% \(limit: ([\d.]+)%\)"
)
_NLV_RE  = re.compile(r"margin_snapshot: NLV=([\d.]+)")
_SPREAD_BLOCK_RE = re.compile(r"execute_buy (\w+): spread .+> max")


def _log_date(line: str, since: date) -> tuple[bool, datetime | None]:
    m = _LOG_TS_RE.match(line)
    if not m:
        return True, None
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return ts.date() >= since, ts
    except ValueError:
        return True, None


def parse_margin_blocks(log_path: pathlib.Path, since: date, dq: DataQuality) -> list[dict]:
    """Parse margin cap block events with timestamps from decifer.log."""
    if not log_path.exists():
        dq.mark_missing(str(log_path))
        return []
    events: list[dict] = []
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            include, ts = _log_date(line, since)
            if not include:
                continue
            m = _MARGIN_BLOCK_RE.search(line)
            if m:
                events.append({
                    "ts": ts,
                    "symbol": m.group(1),
                    "deployed": int(m.group(2).replace(",", "")),
                    "new_position": int(m.group(3).replace(",", "")),
                    "total_pct": float(m.group(4)),
                    "limit_pct": float(m.group(5)),
                    "block_reason": "margin_cap",
                })
    return events


def load_margin_blocks_jsonl(
    obs_dir: pathlib.Path,
    since: date,
    dq: DataQuality,
) -> list[dict]:
    """
    Load margin block events from data/rotation_observability/margin_blocks.jsonl.

    Returns a list of block dicts normalized to match the format produced by
    parse_margin_blocks() so downstream code is source-agnostic. Extra fields
    (candidate_score, estimated_notional) are preserved for richer analysis.

    Returns empty list if the file doesn't exist — caller falls back to log parsing.
    """
    path = obs_dir / "margin_blocks.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    malformed = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                ts_raw = rec.get("ts")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts.date() < since:
                    continue
                events.append({
                    "ts":               ts,
                    "symbol":           rec.get("symbol", ""),
                    "candidate_score":  rec.get("candidate_score"),
                    "direction":        rec.get("direction", "LONG"),
                    "exp_code":         rec.get("exp_code", "exposure_block"),
                    "exp_reason":       rec.get("exp_reason", ""),
                    "estimated_notional": rec.get("estimated_notional"),
                    "notional_is_estimate": True,
                    "portfolio_value":  rec.get("portfolio_value"),
                    "open_position_count": rec.get("open_position_count"),
                    "block_reason":     rec.get("exp_code", "margin_cap"),
                    # fields expected by downstream that don't exist in JSONL
                    "deployed":         None,
                    "new_position":     None,
                    "total_pct":        None,
                    "limit_pct":        None,
                })
    except OSError as exc:
        dq.warn(f"margin_blocks.jsonl could not be read: {exc}")
    if malformed:
        dq.warn(f"margin_blocks.jsonl: {malformed} malformed line(s) skipped")
    return events


def load_position_snapshot_at(
    obs_dir: pathlib.Path,
    block_ts: datetime,
) -> list[dict] | None:
    """
    Return the position snapshot from position_snapshots.jsonl whose timestamp
    is closest to and not after block_ts. Returns None if no snapshot is available.

    Used by the shadow report to get an exact book reconstruction at block time
    instead of relying on open_time inference from positions.json.
    """
    path = obs_dir / "position_snapshots.jsonl"
    if not path.exists():
        return None
    best: tuple[float, list[dict]] | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts_raw = rec.get("ts")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts > block_ts:
                    continue
                diff = (block_ts - ts).total_seconds()
                if best is None or diff < best[0]:
                    positions_dict = rec.get("positions") or {}
                    best = (diff, list(positions_dict.values()))
    except OSError:
        return None
    return best[1] if best is not None else None


def build_hold_protected_set(
    apex_audit_path: pathlib.Path,
    since: date,
) -> frozenset[str]:
    """
    Return the set of symbols that received a Track B HOLD decision on or after `since`.

    Reads apex_decision_audit.jsonl for pm_action records with action=HOLD.
    A symbol in this set is flagged as hold_protected in the shadow candidate output
    — informational only, does not remove it from the candidate list.
    """
    if not apex_audit_path.exists():
        return frozenset()
    protected: set[str] = set()
    try:
        with apex_audit_path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts_raw = rec.get("ts")
                if ts_raw:
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts.date() < since:
                            continue
                    except ValueError:
                        pass
                # pm_action records store action + symbol directly
                action = (rec.get("action") or rec.get("pm_action") or "").upper()
                sym = rec.get("symbol")
                if action == "HOLD" and sym:
                    protected.add(sym)
                # Also check nested pm_actions list if present
                for pm in rec.get("pm_actions") or []:
                    if isinstance(pm, dict):
                        a = (pm.get("action") or "").upper()
                        s = pm.get("symbol")
                        if a == "HOLD" and s:
                            protected.add(s)
    except OSError:
        pass
    return frozenset(protected)


def parse_nlv(log_path: pathlib.Path, since: date) -> float | None:
    if not log_path.exists():
        return None
    last: float | None = None
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            include, _ = _log_date(line, since)
            if not include:
                continue
            m = _NLV_RE.search(line)
            if m:
                last = float(m.group(1))
    return last


def parse_spread_blocks(log_path: pathlib.Path, since: date) -> set[str]:
    if not log_path.exists():
        return set()
    blocked: set[str] = set()
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            include, _ = _log_date(line, since)
            if not include:
                continue
            m = _SPREAD_BLOCK_RE.search(line)
            if m:
                blocked.add(m.group(1))
    return blocked


# ── TQR artifact loader ───────────────────────────────────────────────────────

def load_latest_tqr_artifact(
    reports_dir: pathlib.Path,
    since: date,
    dq: DataQuality,
) -> dict | None:
    """
    Load the most recent trade_quality_report JSON artifact for the given date.
    Returns None if none found.
    """
    if not reports_dir.exists():
        dq.warn(f"Trade quality reports directory not found: {reports_dir}")
        return None

    candidates: list[pathlib.Path] = []
    for f in reports_dir.glob("report_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            meta_since = d.get("meta", {}).get("since") or d.get("section_0", {}).get("since")
            if meta_since and str(meta_since) == str(since):
                candidates.append(f)
        except (json.JSONDecodeError, OSError):
            pass

    if not candidates:
        dq.warn(f"No trade quality report artifact found for date {since}")
        return None

    latest = sorted(candidates)[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        dq.warn(f"Could not load TQR artifact {latest.name}: {exc}")
        return None


def load_prior_tqr_sessions(
    reports_dir: pathlib.Path,
    since: date,
    lookback_days: int = 7,
) -> list[dict]:
    """
    Load TQR artifacts from the past `lookback_days` days (excluding `since`).
    Used for multi-session pattern detection.
    """
    if not reports_dir.exists():
        return []

    cutoff = since - timedelta(days=lookback_days)
    sessions: list[dict] = []
    seen_dates: set[str] = set()

    for f in sorted(reports_dir.glob("report_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            meta_since = d.get("meta", {}).get("since") or d.get("section_0", {}).get("since")
            if not meta_since:
                continue
            try:
                rec_date = date.fromisoformat(str(meta_since))
            except ValueError:
                continue
            if rec_date >= since or rec_date < cutoff:
                continue
            if str(meta_since) in seen_dates:
                continue
            seen_dates.add(str(meta_since))
            sessions.append(d)
        except (json.JSONDecodeError, OSError):
            pass

    return sessions


def build_symbol_score_index(apex_records: list[dict]) -> dict[str, float]:
    """Return {symbol: best_apex_cap_score} from apex_candidate records."""
    idx: dict[str, float] = {}
    for r in apex_records:
        if r.get("record_type") != "apex_candidate":
            continue
        sym = r.get("symbol", "")
        sc  = r.get("apex_cap_score") or r.get("raw_score")
        if sc is not None:
            try:
                v = float(sc)
                if v > idx.get(sym, -1):
                    idx[sym] = v
            except (TypeError, ValueError):
                pass
    return idx


def build_pru_symbol_set(apex_records: list[dict]) -> set[str]:
    """Symbols flagged as PRU-sourced or discovery-labelled."""
    pru: set[str] = set()
    for r in apex_records:
        if r.get("record_type") == "apex_candidate":
            if r.get("scanner_tier") == "D" or r.get("pru"):
                sym = r.get("symbol", "")
                if sym:
                    pru.add(sym)
    return pru


# ── Book reconstruction ───────────────────────────────────────────────────────

def book_at_block_time(
    positions: list[dict],
    block_ts: datetime | None,
) -> list[dict]:
    """
    Return the subset of positions that were open at block_ts.
    If block_ts is None (timestamp unavailable), returns the full book
    and marks confidence LOW.
    """
    if block_ts is None:
        return list(positions)

    result = []
    for p in positions:
        ot = _open_time(p)
        if ot is None:
            # Include — we cannot exclude without proof
            result.append(p)
        elif ot <= block_ts:
            result.append(p)
    return result


def book_reconstruction_confidence(
    positions: list[dict],
    block_ts: datetime | None,
) -> str:
    """
    HIGH   — block_ts known, all positions have open_time
    MEDIUM — block_ts known, most positions have open_time
    LOW    — block_ts missing or most positions missing open_time
    """
    if block_ts is None:
        return "LOW"
    timed   = sum(1 for p in positions if _open_time(p) is not None)
    total   = len(positions)
    if total == 0:
        return "LOW"
    ratio = timed / total
    if ratio >= 0.9:
        return "HIGH"
    if ratio >= 0.5:
        return "MEDIUM"
    return "LOW"


# ── Shadow rotation candidate builder ────────────────────────────────────────

def build_shadow_candidates(
    blocked: dict,
    book: list[dict],
    since: date,
    pru_syms: set[str],
    held_syms: frozenset[str],
    hold_protected_syms: frozenset[str] | None = None,
) -> list[dict]:
    """
    For a single blocked candidate, return ranked shadow rotation candidates.

    Eligibility:
      - position was open before the blocked candidate timestamp (handled by caller)
      - entry score below 50
      - OR entry score more than 20 points below blocked candidate score
      - OR flagged ETF overlap with score below 50
      - OR low-score cluster constituent
      - OR PRU/discovery candidate with material score gap
    """
    blocked_score = blocked.get("score")
    if blocked_score is None:
        return []

    # Cluster low-score flags: clusters with avg score < 50
    cluster_scores: dict[str, list[float]] = defaultdict(list)
    for p in book:
        sc = _position_score(p)
        if sc is not None:
            cluster_scores[cluster_of(p.get("symbol", ""))].append(sc)
    low_score_clusters: set[str] = {
        c for c, scores in cluster_scores.items()
        if _safe_mean(scores) is not None and _safe_mean(scores) < 50
    }

    candidates = []
    for p in book:
        sym = p.get("symbol", "")
        pos_score = _position_score(p)

        # Determine eligibility
        etf_overlap_below_50 = False
        if sym in ETF_UNIVERSE:
            overlaps = [s for s in ETF_OVERLAP.get(sym, []) if s in held_syms and s != sym]
            etf_overlap_below_50 = bool(overlaps) and (pos_score is None or pos_score < 50)

        is_low_cluster = cluster_of(sym) in low_score_clusters
        is_pru = sym in pru_syms

        if pos_score is None:
            # Include only if ETF overlap or cluster flag — without a score we can't rank
            if not (etf_overlap_below_50 or is_low_cluster):
                continue

        score_below_50      = pos_score is not None and pos_score < 50
        score_delta_above_20 = pos_score is not None and (blocked_score - pos_score) > 20
        eligible = (
            score_below_50
            or score_delta_above_20
            or etf_overlap_below_50
            or is_low_cluster
        )
        if not eligible:
            continue

        # Don't include positions that scored at or above the blocked candidate
        if pos_score is not None and pos_score >= blocked_score:
            continue

        ot = _open_time(p)
        is_carry = ot is not None and ot.date() < since

        rss = rotation_shadow_score(
            blocked_score=blocked_score,
            pos_score=pos_score if pos_score is not None else 0.0,
            is_below_35=pos_score is not None and pos_score < 35,
            has_etf_overlap_below_50=etf_overlap_below_50,
            is_low_score_cluster=is_low_cluster,
            is_pru_displacement=is_pru,
            is_carry=is_carry,
        )

        notional_val = _notional(p)
        open_time_str = ot.isoformat() if ot else None

        candidates.append({
            "symbol":              sym,
            "score":               pos_score,
            "score_delta":         round(blocked_score - (pos_score or 0), 1),
            "notional":            round(notional_val, 2),
            "rotation_shadow_score": round(rss, 1),
            "below_35":            pos_score is not None and pos_score < 35,
            "etf_overlap_below_50": etf_overlap_below_50,
            "low_score_cluster":   is_low_cluster,
            "pru_displacement":    is_pru,
            "is_carry":            is_carry,
            "open_time":           open_time_str,
            "cluster":             cluster_of(sym),
            "hold_protected":      sym in (hold_protected_syms or frozenset()),
        })

    candidates.sort(key=lambda c: c["rotation_shadow_score"], reverse=True)

    for rank, c in enumerate(candidates, 1):
        c["rotation_shadow_rank"] = rank

    return candidates


# ── Section builders ──────────────────────────────────────────────────────────

def section_0(
    since: date,
    tqr_artifact: dict | None,
    positions: list[dict],
    margin_blocks: list[dict],
    tqr_reports_used: list[str],
    dq: DataQuality,
    files_read: list[str],
) -> tuple[list[str], dict]:
    now = datetime.now(timezone.utc)
    unique_blocked = len({b["symbol"] for b in margin_blocks})
    high_score_blocked = sum(
        1 for b in margin_blocks
        if b.get("gap") is not None and b["gap"] > 15
    )

    lines = [
        "── SECTION 0: SESSION HEADER ──────────────────────────────────────────",
        f"  Report generated : {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"  Session date     : {since}",
        "",
        "  Data files read:",
    ]
    for f in files_read:
        lines.append(f"    {f}")

    if tqr_reports_used:
        lines.append("  Trade quality report artifacts used:")
        for f in tqr_reports_used:
            lines.append(f"    {f}")

    if dq.missing_files:
        lines.append("  Data files MISSING:")
        for f in dq.missing_files:
            lines.append(f"    {f}  ← not found")

    if dq.malformed_lines:
        lines.append("  Malformed lines (skipped, not fatal):")
        for fname, cnt in dq.malformed_lines.items():
            lines.append(f"    {fname}: {cnt} lines")

    lines += [
        "",
        f"  Margin-blocked candidates analysed : {unique_blocked}",
        f"  High-score blocked (gap >15)       : {high_score_blocked}",
        f"  Open positions analysed            : {len(positions)}",
    ]

    if tqr_artifact:
        s0 = tqr_artifact.get("section_0", {})
        nlv = tqr_artifact.get("section_1", {}).get("nlv")
        lines += [
            f"  NLV (from TQR artifact)            : "
            f"{'${:,.2f}'.format(nlv) if nlv else 'INSUFFICIENT DATA'}",
            f"  Reconstruction confidence          : MEDIUM",
        ]
    else:
        lines += [
            "  NLV                                : INSUFFICIENT DATA (no TQR artifact)",
            "  Reconstruction confidence          : LOW",
        ]

    conf = "MEDIUM" if tqr_artifact else "LOW"

    return lines, {
        "since": str(since),
        "generated": now.isoformat(),
        "unique_blocked": unique_blocked,
        "high_score_blocked": high_score_blocked,
        "open_positions": len(positions),
        "tqr_artifact_loaded": tqr_artifact is not None,
        "reconstruction_confidence": conf,
    }


def section_1(
    margin_blocks: list[dict],
    sym_score_index: dict[str, float],
    book_avg: float | None,
    spread_blocked: set[str],
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 1: BLOCKED CANDIDATE SUMMARY ──────────────────────────────",
    ]

    # Deduplicate by symbol, keep first occurrence (earliest block)
    seen: dict[str, dict] = {}
    for b in margin_blocks:
        sym = b["symbol"]
        if sym not in seen:
            row = dict(b)
            sc  = sym_score_index.get(sym)
            row["score"] = sc
            gap = (sc - book_avg) if (sc is not None and book_avg is not None) else None
            row["gap"]         = round(gap, 1) if gap is not None else None
            row["outscores_15"] = gap is not None and gap > 15
            row["outscores_20"] = gap is not None and gap > 20
            row["cluster"]      = cluster_of(sym)
            row["spread_blocked"] = sym in spread_blocked
            seen[sym] = row

    unique_blocks = list(seen.values())

    if not unique_blocks:
        lines.append("  No margin cap blocks detected for this period.")
        return lines, {
            "unique_blocked": 0,
            "rows": [],
            "book_avg": book_avg,
            "high_score_blocked": 0,
        }

    W = [8, 6, 9, 6, 12, 12, 20]
    hdr = _fmt_row("SYMBOL", "SCORE", "BOOK_AVG", "GAP", "GAP_THRESH", "CLUSTER", "TIMESTAMP", widths=W)
    lines += [
        f"  Book avg score (open positions) : "
        f"{'%.1f' % book_avg if book_avg is not None else 'INSUFFICIENT DATA'}",
        "",
        "  " + hdr,
        "  " + "─" * 85,
    ]

    high_score_blocked = 0
    for b in unique_blocks:
        sc    = b.get("score")
        gap   = b.get("gap")
        ts    = b.get("ts")
        ts_str = ts.strftime("%Y-%m-%dT%H:%M") if isinstance(ts, datetime) else "?"

        thresh = "neither"
        if b.get("outscores_20"):
            thresh = ">20 ⚑⚑"
            high_score_blocked += 1
        elif b.get("outscores_15"):
            thresh = ">15 ⚑"
            high_score_blocked += 1

        lines.append("  " + _fmt_row(
            b["symbol"],
            f"{sc:.0f}" if sc is not None else "?",
            f"{book_avg:.1f}" if book_avg is not None else "?",
            f"{gap:+.1f}" if gap is not None else "?",
            thresh,
            b["cluster"],
            ts_str,
            widths=W,
        ))

    lines += [
        "",
        f"  Spread-blocked symbols (excluded from margin analysis): "
        f"{sorted(spread_blocked) or 'none'}",
        f"  High-score blocked (gap >15)  : {high_score_blocked}",
    ]

    return lines, {
        "unique_blocked": len(unique_blocks),
        "high_score_blocked": high_score_blocked,
        "rows": [
            {k: v for k, v in b.items() if k != "ts"}
            for b in unique_blocks
        ],
        "book_avg": book_avg,
    }


def section_2(
    unique_blocks: list[dict],
    positions: list[dict],
    nlv: float | None,
    since: date,
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 2: OPEN BOOK AT TIME OF BLOCK ─────────────────────────────",
    ]

    if not unique_blocks:
        lines.append("  No blocked candidates to reconstruct book against.")
        return lines, {"book_reconstructions": []}

    reconstructions: list[dict] = []

    for b in unique_blocks:
        sym    = b["symbol"]
        ts     = b.get("ts")
        score  = b.get("score")

        if score is None or (b.get("gap") is not None and b["gap"] <= 0):
            # Not a high-value block — still show but abbreviated
            pass

        book = book_at_block_time(positions, ts)
        conf = book_reconstruction_confidence(positions, ts)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(ts, datetime) else "UNKNOWN"

        lines += [
            "",
            f"  Blocked: {sym}  score={score if score is not None else '?'}  "
            f"ts={ts_str}  confidence={conf}",
            f"  Book at block time ({len(book)} position(s)):",
            "",
        ]

        W2 = [6, 19, 6, 11, 8, 24, 8, 8]
        hdr = _fmt_row("SYM", "OPEN_TIME", "SCORE", "NOTIONAL", "NLV%", "CLUSTER", "ETF_FLAG", "CARRY", widths=W2)
        lines += ["    " + hdr, "    " + "─" * 95]

        held_syms = frozenset(p.get("symbol", "") for p in book)
        book_entries: list[dict] = []
        for p in sorted(book, key=lambda x: _open_time(x) or datetime.min.replace(tzinfo=timezone.utc)):
            psym  = p.get("symbol", "?")
            psc   = _position_score(p)
            pn    = _notional(p)
            ot    = _open_time(p)
            ot_str = ot.strftime("%Y-%m-%dT%H:%M") if ot else "?"
            nlv_pct = f"{pn / nlv * 100:.1f}%" if nlv else "?"
            clust = cluster_of(psym)
            etf_flag = ""
            if psym in ETF_UNIVERSE:
                overlaps = [s for s in ETF_OVERLAP.get(psym, []) if s in held_syms and s != psym]
                if overlaps and (psc is None or psc < 50):
                    etf_flag = "ETF_OVERLAP"
            carry_str = "carry" if (ot and ot.date() < since) else "session"
            lines.append("    " + _fmt_row(
                psym,
                ot_str,
                f"{psc:.0f}" if psc is not None else "?",
                f"${pn:,.0f}",
                nlv_pct,
                clust,
                etf_flag or "—",
                carry_str,
                widths=W2,
            ))
            book_entries.append({
                "symbol": psym,
                "score": psc,
                "notional": round(pn, 2),
                "open_time": ot_str,
                "cluster": clust,
                "etf_overlap_flag": bool(etf_flag),
                "is_carry": ot is not None and ot.date() < since,
            })

        reconstructions.append({
            "blocked_symbol": sym,
            "blocked_score": score,
            "block_ts": ts_str,
            "confidence": conf,
            "book_size": len(book),
            "book": book_entries,
        })

    return lines, {"book_reconstructions": reconstructions}


def section_3(
    unique_blocks: list[dict],
    positions: list[dict],
    since: date,
    pru_syms: set[str],
    hold_protected_syms: frozenset[str] | None = None,
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 3: SHADOW ROTATION CANDIDATE RANKING ──────────────────────",
        "  Ranking formula (transparent and deterministic):",
        "    rotation_shadow_score =",
        "      score_delta (blocked_score − position_score)",
        "      + 10  if position score below 35",
        "      + 8   if ETF overlap flag and ETF score below 50",
        "      + 5   if low-score cluster flag",
        "      + 5   if PRU/discovery displacement flag",
        "      + 3   if position is older than current session (carry)",
        "",
        "  This is a diagnostic counterfactual only.",
        "  NEVER interpreted as a sell recommendation or rotation execution.",
        "",
    ]

    high_value_blocks = [b for b in unique_blocks if b.get("outscores_15") or b.get("outscores_20")]

    if not high_value_blocks:
        lines.append("  No high-score blocked candidates (gap >15). No shadow ranking produced.")
        return lines, {"rankings": []}

    held_syms = frozenset(p.get("symbol", "") for p in positions)
    all_rankings: list[dict] = []

    for b in high_value_blocks:
        sym   = b["symbol"]
        score = b.get("score")
        ts    = b.get("ts")

        book = book_at_block_time(positions, ts)
        candidates = build_shadow_candidates(
            b, book, since, pru_syms, held_syms,
            hold_protected_syms=hold_protected_syms or frozenset(),
        )

        lines += [
            f"  ── Blocked: {sym}  score={score}  gap={b.get('gap', '?'):+}  "
            f"({'gap >20' if b.get('outscores_20') else 'gap >15'}) ──",
            "",
        ]

        if not candidates:
            lines.append("    No eligible shadow rotation candidates found.")
            lines.append("")
            all_rankings.append({"blocked": sym, "candidates": []})
            continue

        W3 = [6, 6, 9, 11, 7, 10, 9, 10, 8, 6, 4]
        hdr = _fmt_row(
            "SYM", "SCORE", "Δ_SCORE", "NOTIONAL", "ETF_OV",
            "CLUSTER_F", "PRU_FLAG", "RSR_SCORE", "CARRY", "HOLD_P", "RNK",
            widths=W3,
        )
        lines += ["    " + hdr, "    " + "─" * 97]

        for c in candidates[:10]:
            lines.append("    " + _fmt_row(
                c["symbol"],
                f"{c['score']:.0f}" if c["score"] is not None else "?",
                f"{c['score_delta']:+.0f}",
                f"${c['notional']:,.0f}",
                "Y" if c["etf_overlap_below_50"] else "n",
                "Y" if c["low_score_cluster"] else "n",
                "Y" if c["pru_displacement"] else "n",
                f"{c['rotation_shadow_score']:.1f}",
                "carry" if c["is_carry"] else "sess",
                "Y" if c.get("hold_protected") else "n",
                str(c["rotation_shadow_rank"]),
                widths=W3,
            ))
        lines.append("")

        all_rankings.append({"blocked": sym, "candidates": candidates})

    return lines, {"rankings": all_rankings}


def section_4(
    rankings: list[dict],
    nlv: float | None,
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 4: THEORETICAL CAPACITY RELEASE ───────────────────────────",
        "  Diagnostic only — not an execution recommendation.",
        "  'Shadow rotation candidate' language only.",
        "",
    ]

    release_data: list[dict] = []

    for rk in rankings:
        blocked_sym  = rk["blocked"]
        candidates   = rk["candidates"]
        if not candidates:
            lines += [
                f"  {blocked_sym}: no shadow rotation candidates — capacity analysis skipped.",
                "",
            ]
            release_data.append({
                "blocked_symbol": blocked_sym,
                "top1_release": 0.0,
                "top2_release": 0.0,
                "top3_release": 0.0,
                "capacity_confidence": "LOW",
            })
            continue

        top1 = candidates[0]["notional"] if len(candidates) >= 1 else 0.0
        top2 = sum(c["notional"] for c in candidates[:2])
        top3 = sum(c["notional"] for c in candidates[:3])

        top1_pct = f"{top1 / nlv * 100:.1f}%" if nlv else "?"
        top2_pct = f"{top2 / nlv * 100:.1f}%" if nlv else "?"
        top3_pct = f"{top3 / nlv * 100:.1f}%" if nlv else "?"

        conf = "MEDIUM" if nlv else "LOW"

        lines += [
            f"  ── Blocked: {blocked_sym} ──",
            f"    Blocked candidate notional    : INSUFFICIENT_DATA (not available from logs)",
            f"    Top 1 shadow candidate        : {candidates[0]['symbol']}  "
            f"${top1:,.0f}  ({top1_pct} NLV)  [theoretical freed capacity]",
        ]
        if len(candidates) >= 2:
            lines.append(
                f"    Top 1+2 combined              : "
                f"${top2:,.0f}  ({top2_pct} NLV)  [theoretical freed capacity]"
            )
        if len(candidates) >= 3:
            lines.append(
                f"    Top 1+2+3 combined            : "
                f"${top3:,.0f}  ({top3_pct} NLV)  [theoretical freed capacity]"
            )
        lines += [
            f"    Capacity confidence            : {conf}",
            f"    Note: capacity is theoretical — shadow rotation, not execution.",
            "",
        ]

        release_data.append({
            "blocked_symbol":   blocked_sym,
            "top1_symbol":      candidates[0]["symbol"],
            "top1_release":     round(top1, 2),
            "top2_release":     round(top2, 2),
            "top3_release":     round(top3, 2),
            "top1_pct_nlv":     round(top1 / nlv * 100, 2) if nlv else None,
            "top2_pct_nlv":     round(top2 / nlv * 100, 2) if nlv else None,
            "top3_pct_nlv":     round(top3 / nlv * 100, 2) if nlv else None,
            "capacity_confidence": conf,
        })

    return lines, {"capacity_release": release_data}


def section_5(
    rankings: list[dict],
    positions: list[dict],
    prior_sessions: list[dict],
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 5: ETF OVERLAP WITHIN ROTATION ────────────────────────────",
        "  ETF suppression is NOT implemented. This is a diagnostic flag only.",
        "",
    ]

    held_syms = frozenset(p.get("symbol", "") for p in positions)
    etf_rows: list[dict] = []

    # Collect all ETF shadow candidates across all blocked candidates
    etf_candidates: dict[str, dict] = {}
    for rk in rankings:
        for c in rk["candidates"]:
            sym = c["symbol"]
            if sym in ETF_UNIVERSE and c["etf_overlap_below_50"]:
                if sym not in etf_candidates:
                    overlaps = [s for s in ETF_OVERLAP.get(sym, []) if s in held_syms and s != sym]
                    etf_candidates[sym] = {
                        "symbol": sym,
                        "score":  c["score"],
                        "overlapping_singles": overlaps,
                        "notional": c["notional"],
                        "in_top3": c["rotation_shadow_rank"] <= 3,
                        "blocked_symbols": [],
                    }
                etf_candidates[sym]["blocked_symbols"].append(rk["blocked"])

    # Check if this ETF overlap repeated across prior sessions
    prior_etf_flags: set[str] = set()
    for prior in prior_sessions:
        s5 = prior.get("section_5", {})
        for sym in s5.get("flagged_etfs", []):
            prior_etf_flags.add(sym)

    if not etf_candidates:
        lines.append("  No low-score ETF shadow rotation candidates found.")
        return lines, {"etf_shadow_candidates": []}

    W5 = [6, 6, 24, 11, 12, 6, 8]
    hdr = _fmt_row("ETF", "SCORE", "OVERLAPPING_SINGLES", "NOTIONAL", "IN_TOP3", "REPEAT", "CATEGORY", widths=W5)
    lines += ["  " + hdr, "  " + "─" * 80]

    for sym, row in sorted(etf_candidates.items()):
        repeat   = sym in prior_etf_flags
        category = "low-score ETF with single-name overlap"
        lines.append("  " + _fmt_row(
            sym,
            f"{row['score']:.0f}" if row["score"] is not None else "?",
            str(row["overlapping_singles"]),
            f"${row['notional']:,.0f}",
            "YES" if row["in_top3"] else "no",
            "REPEAT" if repeat else "new",
            category,
            widths=W5,
        ))
        etf_rows.append({**row, "repeats_across_sessions": repeat})

    lines += [
        "",
        "  ETF suppression is not implemented. No action recommended.",
        "  These ETF positions appear as shadow rotation candidates due to:",
        "    - low entry score (<50)",
        "    - overlap with held single-name positions",
    ]

    return lines, {"etf_shadow_candidates": etf_rows}


def section_6(
    rankings: list[dict],
    positions: list[dict],
    unique_blocks: list[dict],
    nlv: float | None,
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 6: CLUSTER QUALITY WITHIN ROTATION ────────────────────────",
        "",
    ]

    # Find clusters that contributed shadow candidates
    cluster_candidates: dict[str, list[dict]] = defaultdict(list)
    for rk in rankings:
        for c in rk["candidates"]:
            cluster_candidates[c["cluster"]].append(c)

    if not cluster_candidates:
        lines.append("  No cluster data available.")
        return lines, {"clusters": {}}

    blocked_clusters = {b.get("cluster") for b in unique_blocks}
    cluster_data: dict[str, dict] = {}

    for clust in sorted(cluster_candidates):
        cands     = cluster_candidates[clust]
        syms      = list({c["symbol"] for c in cands})
        scores    = [c["score"] for c in cands if c["score"] is not None]
        avg_sc    = _safe_mean(scores)
        total_n   = sum(c["notional"] for c in cands)
        pct_nlv   = total_n / nlv * 100 if nlv else None
        same_cluster_as_blocked = clust in blocked_clusters
        swap_note = (
            "Freeing these would swap within cluster — concentration unchanged"
            if same_cluster_as_blocked else
            "Freeing these would reduce cluster concentration"
        )
        lines += [
            f"  {clust}",
            f"    Shadow candidates  : {len(syms)}  {syms}",
            f"    Avg score          : {'%.1f' % avg_sc if avg_sc else '?'}",
            f"    Total notional     : ${'%.0f' % total_n}  "
            f"({'%.1f' % pct_nlv + '%' if pct_nlv else '?'} NLV)",
            f"    Blocked candidate same cluster : {same_cluster_as_blocked}",
            f"    Concentration note : {swap_note}",
            "",
        ]
        cluster_data[clust] = {
            "symbols":     syms,
            "avg_score":   round(avg_sc, 1) if avg_sc else None,
            "total_notional": round(total_n, 2),
            "pct_nlv":     round(pct_nlv, 2) if pct_nlv else None,
            "same_cluster_as_blocked": same_cluster_as_blocked,
            "swap_within_cluster":     same_cluster_as_blocked,
        }

    return lines, {"clusters": cluster_data}


def section_7(
    rankings: list[dict],
    pru_syms: set[str],
    unique_blocks: list[dict],
    positions: list[dict],
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 7: PRU / DISCOVERY WITHIN ROTATION ────────────────────────",
        "  (Legacy 'Tier D' labels treated as source metadata only.)",
        "  (No tier-led allocation, promotion, or suppression is recommended.)",
        "",
    ]

    pru_shadow_cands: list[dict] = []
    for rk in rankings:
        for c in rk["candidates"]:
            if c["pru_displacement"]:
                pru_shadow_cands.append({**c, "blocked_symbol": rk["blocked"]})

    pru_positions = [p for p in positions if p.get("symbol") in pru_syms]
    pru_below_50  = [p for p in pru_positions if (_position_score(p) or 99) < 50]

    # Did PRU positions consume capacity before stronger normal-path candidates were blocked?
    pru_capacity_consumption = len(pru_shadow_cands) > 0 and len(unique_blocks) > 0

    lines += [
        f"  PRU/discovery positions in open book       : {len(pru_positions)}",
        f"  PRU/discovery positions scoring below 50   : {len(pru_below_50)}",
        f"  PRU/discovery appearing as shadow candidates: {len(pru_shadow_cands)}",
        "",
    ]

    if pru_shadow_cands:
        lines.append("  PRU/discovery shadow rotation candidates:")
        for c in pru_shadow_cands[:10]:
            lines.append(
                f"    {c['symbol']:6}  score={c['score'] if c['score'] is not None else '?':4}  "
                f"Δ={c['score_delta']:+.0f}  ${c['notional']:,.0f}  "
                f"blocked_by={c['blocked_symbol']}"
            )
        lines.append("")

    if pru_capacity_consumption:
        conclusion = "PRU_DISCOVERY_CAPACITY_CONSUMPTION_WATCH"
        lines += [
            "  ⚑ PRU_DISCOVERY_CAPACITY_CONSUMPTION_WATCH:",
            "    PRU/discovery-sourced positions appeared as shadow rotation candidates",
            "    in the same session that high-score candidates were blocked by margin.",
            "    This is a diagnostic flag only — no tier-led action is recommended.",
    ]
    elif pru_below_50:
        conclusion = "PRU_DISCOVERY_ROTATION_WATCH"
        lines += [
            "  PRU_DISCOVERY_ROTATION_WATCH:",
            "    PRU/discovery positions scoring below 50 exist in the open book.",
            "    None appeared as shadow candidates in this analysis.",
        ]
    elif not pru_positions:
        conclusion = "PRU_DISCOVERY_INSUFFICIENT_DATA"
        lines.append("  PRU_DISCOVERY_INSUFFICIENT_DATA: no PRU/discovery labels available.")
    else:
        conclusion = "PRU_DISCOVERY_NOT_ROTATION_RELEVANT"
        lines.append("  PRU_DISCOVERY_NOT_ROTATION_RELEVANT: no material rotation overlap.")

    lines += [
        "",
        f"  Conclusion : {conclusion}",
        "  No PRU rescue, tier promotion, or tier suppression is recommended.",
    ]

    return lines, {
        "pru_open_count": len(pru_positions),
        "pru_below_50": len(pru_below_50),
        "pru_shadow_count": len(pru_shadow_cands),
        "pru_capacity_consumption": pru_capacity_consumption,
        "conclusion": conclusion,
    }


def section_8(
    rankings: list[dict],
    positions: list[dict],
    unique_blocks: list[dict],
    since: date,
    prior_sessions: list[dict],
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 8: COUNTERFACTUAL SESSION SUMMARY ─────────────────────────",
        "",
    ]

    # 1. Strongest missed opportunity
    best_blocked: dict | None = None
    best_gap = -999.0
    for b in unique_blocks:
        gap = b.get("gap")
        if gap is not None and gap > best_gap:
            best_gap = gap
            best_blocked = b

    # 2. Weakest capacity consumers (lowest-scoring shadow candidates)
    all_candidates: list[dict] = []
    for rk in rankings:
        all_candidates.extend(rk["candidates"])
    weakest = sorted(all_candidates, key=lambda c: (c["score"] or 99))[:5]

    # 3. Recurring weak positions across sessions
    prior_weak_syms: set[str] = set()
    for prior in prior_sessions:
        s4 = prior.get("section_4", {})
        for bkt_name in ("QUESTIONABLE (<35)", "LOW (35-49)"):
            syms = s4.get("open_buckets", {}).get(bkt_name, {}).get("symbols", [])
            prior_weak_syms.update(syms)

    current_weak_syms = {c["symbol"] for c in all_candidates}
    recurring = sorted(current_weak_syms & prior_weak_syms)

    # 4. Would top 1, 2, 3 free enough capacity?
    top1_n = top2_n = top3_n = 0.0
    for rk in rankings:
        cands = rk["candidates"]
        if cands:
            top1_n = max(top1_n, cands[0]["notional"] if len(cands) >= 1 else 0)
            top2_n = max(top2_n, sum(c["notional"] for c in cands[:2]))
            top3_n = max(top3_n, sum(c["notional"] for c in cands[:3]))

    # 5. Root cause categorisation
    below_35_count  = len([p for p in positions if (_position_score(p) or 99) < 35])
    etf_flags       = sum(
        1 for p in positions
        if p.get("symbol") in ETF_UNIVERSE
        and (_position_score(p) or 99) < 50
        and any(
            s in {x.get("symbol") for x in positions}
            for s in ETF_OVERLAP.get(p.get("symbol", ""), [])
        )
    )
    pru_in_shadow   = sum(1 for c in all_candidates if c["pru_displacement"])

    causes: list[str] = []
    if below_35_count >= 2:
        causes.append(f"low book quality ({below_35_count} positions below 35)")
    if etf_flags:
        causes.append(f"ETF overlap ({etf_flags} low-score ETF flag(s))")
    if pru_in_shadow:
        causes.append(f"PRU/discovery over-selection ({pru_in_shadow} shadow candidate(s))")

    # Cluster concentration
    cluster_scores: dict[str, list[float]] = defaultdict(list)
    for p in positions:
        sc = _position_score(p)
        if sc is not None:
            cluster_scores[cluster_of(p.get("symbol", ""))].append(sc)
    for clust, scores in cluster_scores.items():
        avg = _safe_mean(scores)
        if avg is not None and avg < 50 and len(scores) >= 3:
            causes.append(f"cluster concentration ({clust} avg {avg:.0f})")
            break

    if not causes:
        causes.append("insufficient data for root cause identification")

    lines += [
        "  1. Strongest missed opportunity (highest-score blocked candidate):",
        f"     {best_blocked['symbol'] if best_blocked else 'N/A'}  "
        f"score={best_blocked['score'] if best_blocked else '?'}  "
        f"gap={best_gap:+.1f} vs book avg"
        if best_blocked else "     N/A",
        "",
        "  2. Weakest capacity consumers (lowest-score shadow candidates):",
    ]
    if weakest:
        for c in weakest:
            lines.append(
                f"     {c['symbol']:6}  score={c['score'] if c['score'] is not None else '?':4}  "
                f"${c['notional']:,.0f}  rank={c.get('rotation_shadow_rank', '?')}"
            )
    else:
        lines.append("     none identified")

    lines += [
        "",
        "  3. Weak positions recurring across sessions:",
        f"     {recurring if recurring else 'none identified from prior session data'}",
        "",
        f"  4. Would removing top 1 shadow candidate free material capacity?",
        f"     Top 1 theoretical release: ${top1_n:,.0f}  "
        f"{'— YES, material' if top1_n > 40_000 else '— marginal or insufficient'}",
        "",
        f"  5. Would removing top 2 or top 3 shadow candidates free more?",
        f"     Top 2: ${top2_n:,.0f}  Top 3: ${top3_n:,.0f}",
        "",
        "  6. Root cause categorisation:",
        f"     Primary: {', '.join(causes)}",
        "",
        "  7. Is live rotation justified today?",
        "     No live rotation yet. Shadow evidence only.",
        "     Moving from shadow report to rotation policy specification",
        "     requires explicit Amit approval of the policy design.",
    ]

    return lines, {
        "strongest_blocked": {
            "symbol": best_blocked["symbol"] if best_blocked else None,
            "score":  best_blocked["score"]  if best_blocked else None,
            "gap":    best_gap if best_blocked else None,
        },
        "weakest_consumers": [c["symbol"] for c in weakest],
        "recurring_weak_symbols": recurring,
        "top1_release": round(top1_n, 2),
        "top2_release": round(top2_n, 2),
        "top3_release": round(top3_n, 2),
        "root_causes": causes,
        "live_rotation_justified": False,
    }


def section_9(
    unique_blocks: list[dict],
    positions: list[dict],
    rankings: list[dict],
    prior_sessions: list[dict],
    nlv: float | None,
    since: date,
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 9: SHADOW VERDICT ─────────────────────────────────────────",
        "",
    ]

    # Collect metrics
    outscores_15 = sum(1 for b in unique_blocks if b.get("outscores_15"))
    outscores_20 = sum(1 for b in unique_blocks if b.get("outscores_20"))

    all_candidates: list[dict] = []
    for rk in rankings:
        all_candidates.extend(rk["candidates"])

    weak_before_block = len({c["symbol"] for c in all_candidates})
    top3_n = 0.0
    for rk in rankings:
        cands = rk["candidates"]
        if cands:
            top3_n = max(top3_n, sum(c["notional"] for c in cands[:3]))

    # Multi-session detection
    prior_weak_sessions = [
        p for p in prior_sessions
        if p.get("section_8", {}).get("verdict") in (
            "WEAK_ENTRIES_DETECTED", "CAPITAL_SEQUENCING_FAILURE"
        )
        or (p.get("section_9", {}).get("verdict") in (
            "ROTATION_WATCH", "ROTATION_SHADOW_CONFIRMED"
        ))
        or (p.get("section_2", {}).get("outscores_15_count", 0) >= 1)
    ]
    multi_session = len(prior_weak_sessions) >= 1

    conf = "MEDIUM" if positions else "LOW"

    verdict = compute_shadow_verdict(
        outscores_15=outscores_15,
        outscores_20=outscores_20,
        weak_positions_before_block=weak_before_block,
        top3_notional=top3_n,
        multi_session=multi_session,
        confidence=conf,
    )
    action = shadow_verdict_action(verdict)

    lines += [
        f"  A. High-score blocked (gap >15)        : {outscores_15}",
        f"  B. High-score blocked (gap >20)        : {outscores_20}",
        f"  C. Weak shadow candidates found        : {weak_before_block}",
        f"  D. Top-3 theoretical NLV release       : ${top3_n:,.0f}",
        f"  E. Multi-session pattern detected      : {multi_session}",
        f"  F. Reconstruction confidence           : {conf}",
        "",
        f"  Shadow Verdict           : {verdict}",
        f"  Recommended next action  : {action}",
        "",
    ]

    if verdict == "ROTATION_SHADOW_CONFIRMED":
        lines += [
            "  ⚑ ROTATION_SHADOW_CONFIRMED:",
            "    Pattern appears across multiple sessions.",
            "    At least one blocked candidate outscored book by >20.",
            "    At least three weak positions below 50 existed before the block.",
            "    Top shadow candidates could theoretically free material NLV.",
            "",
            "  NEXT STEP: DESIGN_ROTATION_POLICY_SPEC",
            "    Draft a policy specification for rotation logic.",
            "    DO NOT wire into live execution until spec is reviewed by Amit.",
        ]
    elif verdict == "ROTATION_WATCH":
        lines += [
            "  ROTATION_WATCH: threshold met on one session.",
            "  Run one more session before designing rotation policy.",
        ]
    elif verdict == "NO_ROTATION_EVIDENCE":
        lines.append("  NO_ROTATION_EVIDENCE: pattern not confirmed this session.")
    elif verdict == "INSUFFICIENT_DATA":
        lines.append("  INSUFFICIENT_DATA: fix data quality before drawing conclusions.")

    return lines, {
        "outscores_15": outscores_15,
        "outscores_20": outscores_20,
        "weak_before_block": weak_before_block,
        "top3_release": round(top3_n, 2),
        "multi_session": multi_session,
        "verdict": verdict,
        "recommended_action": action,
    }


def section_10(
    dq: DataQuality,
    positions: list[dict],
    margin_blocks: list[dict],
    nlv: float | None,
) -> tuple[list[str], dict]:
    lines = [
        "",
        "── SECTION 10: DATA QUALITY AND OBSERVABILITY GAPS ──────────────────",
    ]

    issues: list[str] = []

    for f in dq.missing_files:
        issues.append(f"Missing file: {f}")
    for fname, cnt in dq.malformed_lines.items():
        issues.append(f"Malformed JSONL lines in {fname}: {cnt} (skipped)")
    for w in dq.warnings:
        issues.append(f"Warning: {w}")

    # Score completeness
    missing_scores = [p.get("symbol", "?") for p in positions if _position_score(p) is None]
    if missing_scores:
        issues.append(f"Missing entry score on positions: {missing_scores}")

    # Timestamp completeness
    missing_ts = [p.get("symbol", "?") for p in positions if _open_time(p) is None]
    if missing_ts:
        issues.append(
            f"Missing open_time on {len(missing_ts)} position(s) — "
            "book reconstruction at block time may be incomplete"
        )

    # Block timestamp completeness
    ts_less_blocks = [b["symbol"] for b in margin_blocks if b.get("ts") is None]
    if ts_less_blocks:
        issues.append(
            f"Missing log timestamp for block events {ts_less_blocks} — "
            "confidence degraded to LOW"
        )

    # NLV
    if nlv is None:
        issues.append("NLV not found in log — NLV% columns will show '?' — capacity analysis is absolute-only")

    # Structural limitations
    issues += [
        "positions.json reflects end-of-session state only — "
        "positions closed during session are not included in book reconstruction",
        "blocked candidate notional is not available from logs — "
        "exact capacity match is INSUFFICIENT_DATA; theoretical release is directional only",
        "cannot distinguish protected/manual conviction positions from ordinary entries",
        "carry vs same-session distinction uses --since date as cutoff — "
        "positions opened before market open on --since date are classified as carry",
    ]

    for issue in issues:
        lines.append(f"  • {issue}")

    return lines, {"issues": issues}


# ── Report orchestrator ───────────────────────────────────────────────────────

def run_report(
    since: date,
    repo_root: pathlib.Path,
    output_dir: pathlib.Path,
) -> tuple[str, dict]:
    dq = DataQuality()

    # ── File paths ────────────────────────────────────────────────────────────
    data_dir         = repo_root / "data"
    log_path         = repo_root / "logs" / "decifer.log"
    positions_path   = data_dir / "positions.json"
    apex_audit_path  = data_dir / "apex_decision_audit.jsonl"
    tier_d_path      = data_dir / "tier_d_funnel.jsonl"
    tqr_dir          = data_dir / "trade_quality_reports"
    obs_dir          = data_dir / "rotation_observability"
    output_dir.mkdir(parents=True, exist_ok=True)

    files_read: list[str] = []
    tqr_reports_used: list[str] = []

    # ── Load data ─────────────────────────────────────────────────────────────
    positions   = load_positions(positions_path, dq)
    if positions_path.exists():
        files_read.append(str(positions_path))

    apex_records = load_jsonl(apex_audit_path, dq, since)
    if apex_audit_path.exists():
        files_read.append(str(apex_audit_path))

    tier_d_records = load_jsonl(tier_d_path, dq, since)
    if tier_d_path.exists():
        files_read.append(str(tier_d_path))

    # Prefer structured JSONL over log parsing — falls back to log if JSONL is absent
    margin_blocks_jsonl = load_margin_blocks_jsonl(obs_dir, since, dq)
    if margin_blocks_jsonl:
        margin_blocks = margin_blocks_jsonl
        files_read.append(str(obs_dir / "margin_blocks.jsonl"))
    else:
        margin_blocks = parse_margin_blocks(log_path, since, dq)
    spread_blocked = parse_spread_blocks(log_path, since)
    nlv            = parse_nlv(log_path, since)
    if log_path.exists():
        files_read.append(str(log_path))

    # Hold-protected set — Track B HOLD decisions from apex_decision_audit
    hold_protected_syms = build_hold_protected_set(apex_audit_path, since)

    tqr_artifact   = load_latest_tqr_artifact(tqr_dir, since, dq)
    if tqr_artifact:
        # Find which artifact file was used
        for f in sorted(tqr_dir.glob("report_*.json")):
            try:
                d = json.loads(f.read_text())
                ms = d.get("meta", {}).get("since") or d.get("section_0", {}).get("since")
                if str(ms) == str(since):
                    tqr_reports_used.append(str(f))
            except (json.JSONDecodeError, OSError):
                pass
        tqr_reports_used = sorted(set(tqr_reports_used))[-1:]

    prior_sessions = load_prior_tqr_sessions(tqr_dir, since)

    # Override NLV from TQR artifact if not in log
    if nlv is None and tqr_artifact:
        nlv = tqr_artifact.get("section_1", {}).get("nlv")

    # ── Build derived structures ──────────────────────────────────────────────
    sym_score_index = build_symbol_score_index(apex_records)
    pru_syms        = build_pru_symbol_set(apex_records)

    # Augment sym_score_index from TQR artifact section_2 rows
    if tqr_artifact:
        for row in tqr_artifact.get("section_2", {}).get("rows", []):
            sym = row.get("symbol")
            sc  = row.get("score")
            if sym and sc is not None:
                try:
                    v = float(sc)
                    if v > sym_score_index.get(sym, -1):
                        sym_score_index[sym] = v
                except (TypeError, ValueError):
                    pass

    # Book average
    book_avg = tqr_artifact.get("section_2", {}).get("book_avg_score") if tqr_artifact else None
    if book_avg is None:
        scores = [s for p in positions if (s := _position_score(p)) is not None]
        from statistics import mean as _mean
        book_avg = _mean(scores) if scores else None

    # Seed sym_score_index with candidate_score from JSONL blocks (exact at block time)
    for b in margin_blocks:
        cs = b.get("candidate_score")
        sym = b.get("symbol", "")
        if cs is not None and sym:
            try:
                v = float(cs)
                if v > sym_score_index.get(sym, -1):
                    sym_score_index[sym] = v
            except (TypeError, ValueError):
                pass

    # Deduplicate margin blocks by symbol (keep first / earliest)
    seen_syms: dict[str, dict] = {}
    for b in margin_blocks:
        sym = b["symbol"]
        sc  = sym_score_index.get(sym)
        gap = (sc - book_avg) if (sc is not None and book_avg is not None) else None
        b   = {
            **b,
            "score":        sc,
            "gap":          round(gap, 1) if gap is not None else None,
            "outscores_15": gap is not None and gap > 15,
            "outscores_20": gap is not None and gap > 20,
            "cluster":      cluster_of(sym),
        }
        if sym not in seen_syms:
            seen_syms[sym] = b
    unique_blocks = list(seen_syms.values())

    # ── Sections ──────────────────────────────────────────────────────────────
    s0_lines, s0_data = section_0(since, tqr_artifact, positions, unique_blocks, tqr_reports_used, dq, files_read)
    s1_lines, s1_data = section_1(unique_blocks, sym_score_index, book_avg, spread_blocked)
    s2_lines, s2_data = section_2(unique_blocks, positions, nlv, since)
    s3_lines, s3_data = section_3(unique_blocks, positions, since, pru_syms, hold_protected_syms=hold_protected_syms)
    s4_lines, s4_data = section_4(s3_data["rankings"], nlv)
    s5_lines, s5_data = section_5(s3_data["rankings"], positions, prior_sessions)
    s6_lines, s6_data = section_6(s3_data["rankings"], positions, unique_blocks, nlv)
    s7_lines, s7_data = section_7(s3_data["rankings"], pru_syms, unique_blocks, positions)
    s8_lines, s8_data = section_8(s3_data["rankings"], positions, unique_blocks, since, prior_sessions)
    s9_lines, s9_data = section_9(unique_blocks, positions, s3_data["rankings"], prior_sessions, nlv, since)
    s10_lines, s10_data = section_10(dq, positions, margin_blocks, nlv)

    # ── Assemble text report ─────────────────────────────────────────────────
    banner = "═" * 68
    header_lines = [
        banner,
        "  DECIFER ROTATION SHADOW REPORT  |  " + str(since),
        banner,
    ]
    footer_lines = [
        "",
        banner,
        "",
        "  IMPORTANT: This report is diagnostic only.",
        "  No position in this report is recommended for sale or rotation.",
        "  All rankings are labelled 'shadow rotation candidate'.",
        "  All capacity estimates are labelled 'theoretical'.",
        "  Live rotation requires explicit policy specification and Amit approval.",
        "",
        banner,
    ]

    all_text_lines = (
        header_lines
        + s0_lines + s1_lines + s2_lines + s3_lines + s4_lines
        + s5_lines + s6_lines + s7_lines + s8_lines + s9_lines + s10_lines
        + footer_lines
    )
    text_report = "\n".join(all_text_lines)

    # ── Assemble JSON report ─────────────────────────────────────────────────
    json_report = {
        "meta": {
            "since":     str(since),
            "generated": datetime.now(timezone.utc).isoformat(),
            "script":    "rotation_shadow_report.py",
        },
        "section_0":  s0_data,
        "section_1":  s1_data,
        "section_2":  s2_data,
        "section_3":  s3_data,
        "section_4":  s4_data,
        "section_5":  s5_data,
        "section_6":  s6_data,
        "section_7":  s7_data,
        "section_8":  s8_data,
        "section_9":  s9_data,
        "section_10": s10_data,
        "data_quality": {
            "missing_files":   dq.missing_files,
            "malformed_lines": dq.malformed_lines,
            "warnings":        dq.warnings,
        },
    }

    # ── Write artifacts ───────────────────────────────────────────────────────
    now_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    txt_path  = output_dir / f"report_{now_str}.txt"
    json_path = output_dir / f"report_{now_str}.json"

    txt_path.write_text(text_report + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(json_report, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    return text_report, json_report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rotation Shadow Report — diagnostic-only capital sequencing counterfactual."
    )
    parser.add_argument(
        "--since",
        default=date.today().isoformat(),
        help="Session date to analyse (YYYY-MM-DD). Default: today UTC.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "data" / "rotation_shadow_reports"),
        help="Directory to write report artifacts. Default: data/rotation_shadow_reports/",
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="Override repo root for data/logs paths. Default: inferred from script location.",
    )
    args = parser.parse_args(argv)

    try:
        since = date.fromisoformat(args.since)
    except ValueError:
        print(f"ERROR: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
        sys.exit(1)

    output_dir = pathlib.Path(args.output_dir)
    repo_root  = pathlib.Path(args.repo_root)

    text_report, json_report = run_report(
        since=since,
        repo_root=repo_root,
        output_dir=output_dir,
    )

    print(text_report)

    # Report artifact paths
    now_str  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Find the most recently written artifacts
    artifacts = sorted(output_dir.glob("report_*.txt"))
    if artifacts:
        latest_txt  = artifacts[-1]
        latest_json = latest_txt.with_suffix(".json")
        print(f"\nwrote: {latest_txt}")
        print(f"wrote: {latest_json}")


if __name__ == "__main__":
    main()
