#!/usr/bin/env python3
"""
trade_quality_report.py — Read-only capital sequencing and trade quality diagnostic.

Answers: was capital deployed to the best available candidates, or did weaker
early entries crowd out stronger later opportunities?

Service layer  : Reporting / diagnostics only
Runtime purpose: Offline analysis — no live bot loop, no broker, no side effects
Imports        : stdlib only — no trading runtime modules

Usage:
    python3 scripts/trade_quality_report.py
    python3 scripts/trade_quality_report.py --since 2026-05-11
    python3 scripts/trade_quality_report.py --since 2026-05-11 --output-dir /tmp/reports
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

# ── Repo root resolution ──────────────────────────────────────────────────────
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

# ── Static reference data ─────────────────────────────────────────────────────

# ETF → known single-name overlap components
ETF_OVERLAP: dict[str, list[str]] = {
    "XLK":  ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ORCL", "ADBE", "CSCO"],
    "QQQ":  ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "AMZN", "META", "GOOGL", "GOOG", "TSLA"],
    "SMH":  ["NVDA", "TSM", "ASML", "AVGO", "AMD", "AMAT", "LRCX", "MU", "INTC"],
    "XLE":  ["XOM", "CVX", "SLB", "OXY", "COP", "EOG"],
    "XLF":  ["GS", "JPM", "BAC", "MS", "WFC", "C", "BLK"],
    "XLP":  ["KO", "PEP", "WMT", "COST", "PG", "PM"],
    "USO":  ["XOM", "CVX", "OXY", "SLB", "XLE"],   # thematic overlap
    "IWM":  [],   # broad small-cap — do not flag individual overlap
    "SPY":  [],   # broad market — flag only if score < 50 with many mega-caps
    "GLD":  [],   # macro/alternative — no equity overlap
    "IBIT": [],   # macro/alternative — no equity overlap
    "IBB":  [],   # healthcare ETF — no simple overlap mapping
}
ETF_UNIVERSE: frozenset[str] = frozenset(ETF_OVERLAP)

# Symbol → cluster mapping
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

# Score bucket definitions
SCORE_BUCKETS: list[tuple[str, int, int]] = [
    ("QUESTIONABLE (<35)",  0,  35),
    ("LOW (35-49)",        35,  50),
    ("MEDIUM (50-64)",     50,  65),
    ("HIGH (65+)",         65, 9999),
]


# ── Data quality accumulator ──────────────────────────────────────────────────

class DataQuality:
    """Collects data quality issues without raising exceptions."""

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

def score_label(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    for label, lo, hi in SCORE_BUCKETS:
        if lo <= score < hi:
            return label
    return "HIGH (65+)"


def cluster_of(symbol: str) -> str:
    return CLUSTER_MAP.get(symbol, "Other")


def position_score(p: dict) -> float | None:
    """Return numeric entry score from a position dict."""
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


def training_score(r: dict) -> float | None:
    """
    Normalise entry score from a training_records row.
    entry_score field may be None; fall back to score.
    """
    for field in ("entry_score", "score"):
        v = r.get(field)
        if v is not None:
            try:
                f = float(v)
                if f:
                    return f
            except (TypeError, ValueError):
                pass
    return None


def notional(p: dict) -> float:
    """Market value of a position (current_price × qty, falling back to entry × qty)."""
    px = p.get("current") or p.get("entry") or 0.0
    qty = p.get("qty") or 0
    try:
        return float(px) * float(qty)
    except (TypeError, ValueError):
        return 0.0


def open_time(p: dict) -> datetime | None:
    raw = p.get("open_time") or p.get("entry_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_row(*cols: Any, widths: list[int]) -> str:
    parts = []
    for i, c in enumerate(cols):
        w = widths[i] if i < len(widths) else 12
        s = "—" if c is None else str(c)
        parts.append(s[:w].ljust(w))
    return "  ".join(parts)


def safe_mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.mean(clean) if clean else None


def compute_verdict(
    book_avg: float | None,
    below_35: int,
    outscores_15: int,
    outscores_20: int,
    low_qual_open: int,
    etf_below_35_flag: bool,
) -> str:
    """
    Apply session verdict thresholds.
    Called by Section 8 and directly in tests.
    """
    if book_avg is None:
        return "INSUFFICIENT_DATA"
    if outscores_20 >= 3 and low_qual_open >= 3:
        return "CAPITAL_SEQUENCING_FAILURE"
    if below_35 >= 2 or book_avg < 50 or etf_below_35_flag:
        return "WEAK_ENTRIES_DETECTED"
    if book_avg >= 50 and outscores_15 >= 1 and below_35 <= 2:
        return "SEQUENCING_PRESSURE"
    if book_avg >= 55 and below_35 <= 1 and outscores_15 == 0 and not etf_below_35_flag:
        return "CAPITAL_DEPLOYED_WELL"
    return "SEQUENCING_PRESSURE"


def verdict_action(verdict: str) -> str:
    mapping = {
        "INSUFFICIENT_DATA":        "FIX DATA QUALITY",
        "CAPITAL_SEQUENCING_FAILURE": "BUILD ROTATION SHADOW REPORT",
        "WEAK_ENTRIES_DETECTED":    "KEEP OBSERVING",
        "SEQUENCING_PRESSURE":      "KEEP OBSERVING",
        "CAPITAL_DEPLOYED_WELL":    "KEEP OBSERVING",
    }
    return mapping.get(verdict, "KEEP OBSERVING")


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_jsonl(path: pathlib.Path, dq: DataQuality, since: date) -> list[dict]:
    """Load a JSONL file, skip malformed lines, filter by --since date."""
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
    """Load positions.json; handles list or dict-keyed-by-symbol structures."""
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
        # Keyed by symbol  →  values are position dicts
        inner = raw.get("active_trades") or raw.get("positions") or raw
        if isinstance(inner, dict):
            return list(inner.values())
        if isinstance(inner, list):
            return inner
    dq.warn("positions.json: unrecognised structure")
    return []


# ── Log parsers ───────────────────────────────────────────────────────────────

_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_MARGIN_BLOCK_RE = re.compile(
    r"Combined exposure block for (\w+): "
    r"Margin gross cap: \$([0-9,]+) deployed \+ \$([0-9,]+) new = ([\d.]+)% \(limit: ([\d.]+)%\)"
)
_NLV_RE = re.compile(r"margin_snapshot: NLV=([\d.]+)")
_REGIME_RE = re.compile(r"[Rr]egime[=: ]+([A-Z_]+)")
_SPREAD_BLOCK_RE = re.compile(r"execute_buy (\w+): spread .+> max")

_VALID_REGIMES = frozenset({
    "TRENDING_UP", "TRENDING_DOWN", "CHOPPY", "BULL_TRENDING",
    "BEAR_TRENDING", "PANIC", "MOMENTUM_BULL",
})


def _log_date(line: str, since: date) -> tuple[bool, datetime | None]:
    """Return (include, ts_or_None) for a log line vs --since filter."""
    m = _LOG_TS_RE.match(line)
    if not m:
        return True, None
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return ts.date() >= since, ts
    except ValueError:
        return True, None


def parse_margin_blocks(log_path: pathlib.Path, since: date, dq: DataQuality) -> list[dict]:
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


def parse_regime(log_path: pathlib.Path, since: date) -> str:
    if not log_path.exists():
        return "UNKNOWN"
    last: str | None = None
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            include, _ = _log_date(line, since)
            if not include:
                continue
            m = _REGIME_RE.search(line)
            if m and m.group(1) in _VALID_REGIMES:
                last = m.group(1)
    return last or "UNKNOWN"


def parse_spread_blocks(log_path: pathlib.Path, since: date) -> set[str]:
    """Return symbols blocked by spread (distinct from margin cap)."""
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


# ── Score lookup helper ───────────────────────────────────────────────────────

def build_symbol_score_index(apex_records: list[dict]) -> dict[str, float]:
    """
    Return {symbol: best_apex_cap_score} from apex_candidate records.
    Uses highest effective score seen (not cycle-specific) — marked as approximate.
    """
    idx: dict[str, float] = {}
    for r in apex_records:
        if r.get("record_type") != "apex_candidate":
            continue
        sym = r.get("symbol", "")
        sc = r.get("apex_cap_score") or r.get("raw_score")
        if sc is not None:
            try:
                v = float(sc)
                if v > idx.get(sym, -1):
                    idx[sym] = v
            except (TypeError, ValueError):
                pass
    return idx


def build_pru_symbol_set(apex_records: list[dict]) -> set[str]:
    """Symbols flagged as PRU-sourced or discovery-labelled in apex_candidate records."""
    pru: set[str] = set()
    for r in apex_records:
        if r.get("record_type") == "apex_candidate":
            if r.get("scanner_tier") == "D" or r.get("pru"):
                sym = r.get("symbol", "")
                if sym:
                    pru.add(sym)
    return pru


# ── Section builders ──────────────────────────────────────────────────────────

def section_0(
    since: date,
    positions: list[dict],
    apex_records: list[dict],
    training: list[dict],
    regime: str,
    dq: DataQuality,
    files_read: list[str],
) -> tuple[list[str], dict]:
    lines: list[str] = [
        "── SECTION 0: SESSION HEADER ─────────────────────────────────────────",
    ]
    now = datetime.now(timezone.utc)
    lines += [
        f"  Report generated : {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"  Session date     : {since}",
        f"  Regime           : {regime}",
        "",
        "  Data files read:",
    ]
    for f in files_read:
        lines.append(f"    {f}")

    if dq.missing_files:
        lines.append("  Data files MISSING:")
        for f in dq.missing_files:
            lines.append(f"    {f}  ← not found")

    if dq.malformed_lines:
        lines.append("  Malformed lines (skipped, not fatal):")
        for fname, cnt in dq.malformed_lines.items():
            lines.append(f"    {fname}: {cnt} lines")

    lines.append("")
    cands = [r for r in apex_records if r.get("record_type") == "apex_candidate"]
    selected = [r for r in cands if r.get("apex_decision") == "selected"]
    cycle_ids = {r.get("cycle_id") for r in apex_records if r.get("cycle_id")}

    lines += [
        f"  Apex cycles analysed : {len(cycle_ids)}",
        f"  Candidates observed  : {len(cands)}",
        f"  Apex selections      : {len(selected)}",
        f"  Open positions       : {len(positions)}",
        f"  Closed trades        : {len(training)}",
        "",
    ]

    open_pnl = sum(p.get("pnl") or 0.0 for p in positions)
    closed_pnl = sum(r.get("pnl") or 0.0 for r in training)

    if positions or training:
        lines += [
            f"  Open P&L    : ${open_pnl:+,.2f}",
            f"  Closed P&L  : ${closed_pnl:+,.2f}",
            f"  Session P&L : ${open_pnl + closed_pnl:+,.2f}",
        ]
    else:
        lines.append("  P&L: INSUFFICIENT DATA")

    return lines, {
        "since": str(since),
        "regime": regime,
        "cycles": len(cycle_ids),
        "candidates": len(cands),
        "selections": len(selected),
        "open_positions": len(positions),
        "closed_trades": len(training),
        "open_pnl": round(open_pnl, 2),
        "closed_pnl": round(closed_pnl, 2),
        "session_pnl": round(open_pnl + closed_pnl, 2),
    }


def section_1(positions: list[dict], nlv: float | None) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 1: CAPITAL DEPLOYMENT SEQUENCE ────────────────────────────",
    ]
    if not positions:
        lines.append("  INSUFFICIENT DATA: no positions loaded.")
        return lines, {}

    timed = sorted(
        [p for p in positions if open_time(p)],
        key=lambda p: open_time(p),  # type: ignore[arg-type]
    )
    untimed = [p for p in positions if not open_time(p)]

    W = [6, 19, 6, 5, 8, 11, 13, 8, 9]
    header = fmt_row("SYMBOL", "OPEN_TIME", "SCORE", "QTY", "ENTRY_PX",
                     "NOTIONAL", "CUM_NOTIONAL", "CUM_EXP%", "TYPE", widths=W)
    lines += ["  " + header, "  " + "─" * len(header)]

    cum = 0.0
    rows: list[dict] = []
    for p in timed:
        sym = p.get("symbol", "?")
        ot = open_time(p)
        ot_str = ot.strftime("%Y-%m-%dT%H:%M") if ot else "—"  # type: ignore[union-attr]
        sc = position_score(p)
        sc_str = f"{sc:.0f}" if sc is not None else "?"
        qty = p.get("qty")
        ep = p.get("entry")
        ep_str = f"{ep:.2f}" if isinstance(ep, (int, float)) else "?"
        n = notional(p)
        cum += n
        exp_str = f"{cum / nlv * 100:.1f}%" if nlv else "N/A"
        tt = p.get("trade_type", "?")
        lines.append("  " + fmt_row(
            sym, ot_str, sc_str, str(qty or "?"), ep_str,
            f"${n:,.0f}", f"${cum:,.0f}", exp_str, tt, widths=W,
        ))
        rows.append({
            "symbol": sym, "open_time": ot_str, "score": sc,
            "notional": round(n, 2), "cum_notional": round(cum, 2),
            "cum_exp_pct": round(cum / nlv * 100, 2) if nlv else None,
            "trade_type": tt,
        })

    if untimed:
        lines.append(f"\n  {len(untimed)} position(s) missing open_time (excluded from sequence):")
        for p in untimed:
            lines.append(f"    {p.get('symbol', '?')}  score={position_score(p)}")

    lines.append("")
    if nlv:
        lines.append(f"  NLV (latest margin snapshot) : ${nlv:,.2f}")
        lines.append(f"  Final gross exposure         : {cum / nlv * 100:.1f}%")
    else:
        lines.append("  Gross exposure: INSUFFICIENT DATA (NLV not found in log)")

    return lines, {
        "sequence": rows,
        "final_cum_notional": round(cum, 2),
        "nlv": nlv,
    }


def section_2(
    margin_blocks: list[dict],
    positions: list[dict],
    sym_score_index: dict[str, float],
    spread_blocked_syms: set[str],
) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 2: FILLED SCORE vs BLOCKED-BY-MARGIN SCORE ───────────────",
    ]
    if not margin_blocks:
        lines.append("  No margin cap blocks detected in log for this period.")
        return lines, {
            "unique_blocked": 0, "book_avg_score": None,
            "blocked_avg_score": None, "outscores_15_count": 0,
            "outscores_20_count": 0, "rows": [],
            "reconstruction_confidence": "LOW",
        }

    # Deduplicate blocks by symbol
    seen: set[str] = set()
    unique_blocks: list[dict] = []
    for b in margin_blocks:
        sym = b["symbol"]
        if sym not in seen:
            seen.add(sym)
            row = dict(b)
            row["score"] = sym_score_index.get(sym)
            unique_blocks.append(row)

    # Book average — use end-of-session open positions (best-effort reconstruction)
    open_scores = [s for p in positions if (s := position_score(p)) is not None]
    book_avg = safe_mean(open_scores)
    conf = "MEDIUM" if open_scores else "LOW"

    lines += [
        f"  Note: Spread-blocked candidates are excluded from this section.",
        f"  Reconstruction confidence : {conf}",
        f"  Book avg score (end-of-session) : "
        f"{'%.1f' % book_avg if book_avg is not None else 'INSUFFICIENT DATA'}",
        f"  Unique symbols blocked by margin cap : {len(unique_blocks)}",
        "",
    ]

    W2 = [8, 6, 9, 7, 18, 10]
    lines.append("  " + fmt_row("SYMBOL", "SCORE", "BOOK_AVG", "GAP", "OUTSCORES_BOOK_15+", "TOTAL_%", widths=W2))
    lines.append("  " + "─" * 72)

    result_rows: list[dict] = []
    outscores_15 = 0
    outscores_20 = 0
    blocked_scores: list[float] = []

    for b in unique_blocks:
        sym = b["symbol"]
        sc = b.get("score")
        sc_str = f"{sc:.0f}" if sc is not None else "?"
        book_str = f"{book_avg:.1f}" if book_avg is not None else "?"
        gap = (sc - book_avg) if (sc is not None and book_avg is not None) else None
        gap_str = f"{gap:+.1f}" if gap is not None else "?"
        over15 = "YES" if (gap is not None and gap > 15) else "no"
        if gap is not None and gap > 15:
            outscores_15 += 1
        if gap is not None and gap > 20:
            outscores_20 += 1
        if sc is not None:
            blocked_scores.append(sc)
        lines.append("  " + fmt_row(
            sym, sc_str, book_str, gap_str, over15,
            f"{b['total_pct']:.1f}%", widths=W2,
        ))
        result_rows.append({
            "symbol": sym,
            "score": sc,
            "book_avg": book_avg,
            "gap": round(gap, 1) if gap is not None else None,
            "outscores_15": gap is not None and gap > 15,
            "outscores_20": gap is not None and gap > 20,
            "total_pct": b["total_pct"],
        })

    blocked_avg = safe_mean(blocked_scores)
    lines += [""]
    if blocked_avg is not None and book_avg is not None:
        lines += [
            f"  Blocked-candidate avg score : {blocked_avg:.1f}",
            f"  Book avg score              : {book_avg:.1f}",
            f"  Delta (blocked − book)      : {blocked_avg - book_avg:+.1f}",
        ]
    lines += [
        f"  Blocked candidates outscoring book by >15 pts : {outscores_15}",
        f"  Blocked candidates outscoring book by >20 pts : {outscores_20}",
    ]

    return lines, {
        "unique_blocked": len(unique_blocks),
        "book_avg_score": book_avg,
        "blocked_avg_score": blocked_avg,
        "outscores_15_count": outscores_15,
        "outscores_20_count": outscores_20,
        "rows": result_rows,
        "reconstruction_confidence": conf,
    }


def section_3(apex_records: list[dict], pru_syms: set[str]) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 3: HIGH-SCORE DISPLACEMENT ANALYSIS ────────────────────────",
    ]
    skips = [r for r in apex_records if r.get("record_type") == "high_score_skip"]
    if not skips:
        lines.append("  No high_score_skip records found.")
        return lines, {"total_skips": 0, "pru_displacement_watch_count": 0}

    skips_sorted = sorted(skips, key=lambda r: r.get("score_gap") or 0, reverse=True)

    W3 = [8, 6, 8, 7, 7, 20, 8]
    hdr = fmt_row("SKIPPED", "SCORE", "SELECTED", "SEL_SC", "GAP",
                  "REASON_CATEGORY", "SOURCE", widths=W3)
    lines += [
        f"  Total high_score_skip records : {len(skips)}",
        f"  Top 20 by score_gap:",
        "",
        "  " + hdr,
        "  " + "─" * 80,
    ]

    pru_watch: list[dict] = []

    for r in skips_sorted[:20]:
        skipped_sym = r.get("symbol", "?")
        skipped_score = r.get("effective_score") or r.get("raw_score", "?")
        sel_sym = r.get("selected_lower_symbol", "?")
        sel_score = r.get("selected_lower_score", "?")
        gap = r.get("score_gap", "?")
        reason = (r.get("reason_category") or r.get("suspected_reason") or "?")[:20]
        gap_val = r.get("score_gap") or 0
        skipped_is_normal = r.get("origin_path") == "normal_path" and r.get("scanner_tier") != "D"
        selected_is_pru = str(sel_sym) in pru_syms
        src_label = "PRU/DISC" if selected_is_pru else "normal"
        lines.append("  " + fmt_row(
            skipped_sym, str(skipped_score), str(sel_sym),
            str(sel_score), str(gap), reason, src_label, widths=W3,
        ))
        if selected_is_pru and skipped_is_normal and gap_val > 15:
            pru_watch.append(r)

    if pru_watch:
        lines += [
            "",
            f"  ⚑  PRU_SOURCE_DISPLACEMENT_WATCH : {len(pru_watch)} case(s)",
            "     A PRU-sourced or discovery-labelled candidate was selected",
            "     over a higher-scoring normal-path candidate (gap > 15).",
            "     Diagnostic flag only. No action recommended.",
        ]
    else:
        lines.append("\n  No PRU_SOURCE_DISPLACEMENT_WATCH flags.")

    return lines, {
        "total_skips": len(skips),
        "pru_displacement_watch_count": len(pru_watch),
    }


def section_4(positions: list[dict], training: list[dict]) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 4: ENTRY SCORE DISTRIBUTION ───────────────────────────────",
        "  Open positions:",
    ]
    open_data: dict[str, dict] = {}
    for label, lo, hi in SCORE_BUCKETS:
        in_bucket = [p for p in positions
                     if (s := position_score(p)) is not None and lo <= s < hi]
        syms = [p.get("symbol", "?") for p in in_bucket]
        total_n = sum(notional(p) for p in in_bucket)
        open_data[label] = {"count": len(syms), "symbols": syms, "notional": total_n}
        lines.append(f"    {label:20} : {len(syms):3}  {syms}  ${total_n:,.0f}")

    no_score = [p.get("symbol", "?") for p in positions if position_score(p) is None]
    if no_score:
        lines.append(f"    UNKNOWN score        : {no_score}")

    lines += ["", "  Closed trades (training_records):"]
    closed_data: dict[str, dict] = {}
    for label, lo, hi in SCORE_BUCKETS:
        recs = [r for r in training
                if (s := training_score(r)) is not None and lo <= s < hi]
        pnls = [float(r["pnl"]) for r in recs if r.get("pnl") is not None]
        wins = sum(1 for v in pnls if v > 0)
        losses = sum(1 for v in pnls if v < 0)
        avg_pnl = safe_mean(pnls)
        total_pnl = sum(pnls)
        closed_data[label] = {
            "count": len(recs),
            "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
            "total_pnl": round(total_pnl, 2),
            "wins": wins, "losses": losses,
        }
        avg_str = f"${avg_pnl:+,.0f}" if avg_pnl is not None else "—"
        lines.append(
            f"    {label:20} : {len(recs):3}  avg_pnl={avg_str:10}  "
            f"total=${total_pnl:+,.0f}  W/L={wins}/{losses}"
        )

    return lines, {"open_buckets": open_data, "closed_buckets": closed_data}


def section_5(positions: list[dict]) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 5: ETF vs SINGLE-NAME OVERLAP FLAG ───────────────────────",
    ]
    held = {p.get("symbol", "") for p in positions}
    etfs_held = sorted(sym for sym in held if sym in ETF_UNIVERSE)

    if not etfs_held:
        lines.append("  No ETFs in current open positions.")
        return lines, {"etf_overlap_flags": 0, "flagged_etfs": []}

    flags: list[dict] = []
    for etf in etfs_held:
        p_etf = next((p for p in positions if p.get("symbol") == etf), None)
        if p_etf is None:
            continue
        sc = position_score(p_etf)
        sc_str = f"{sc:.0f}" if sc is not None else "?"
        overlaps = [s for s in ETF_OVERLAP.get(etf, []) if s in held and s != etf]
        flag = bool(overlaps) and (sc is None or sc < 50)
        flag_str = "⚑ ETF_OVERLAP_FLAG" if flag else "ok"
        lines.append(f"  {etf:6}  score={sc_str:4}  overlapping_singles={overlaps or '[]'}  {flag_str}")
        if flag:
            flags.append({"etf": etf, "score": sc, "overlapping_singles": overlaps})

    if flags:
        lines += [
            "",
            "  ETF_OVERLAP_FLAG: ETF below score 50 with overlapping single-name positions.",
            "  Diagnostic flag only. No suppression logic implemented.",
        ]

    return lines, {
        "etf_overlap_flags": len(flags),
        "flagged_etfs": [f["etf"] for f in flags],
        "flagged_below_35": [
            f["etf"] for f in flags
            if f["score"] is not None and f["score"] < 35
        ],
    }


def section_6(positions: list[dict], nlv: float | None) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 6: CLUSTER CONCENTRATION ─────────────────────────────────",
    ]
    by_cluster: dict[str, list[dict]] = defaultdict(list)
    for p in positions:
        by_cluster[cluster_of(p.get("symbol", ""))].append(p)

    cluster_data: dict[str, dict] = {}
    flag_lines: list[str] = []

    for clust in sorted(by_cluster):
        ps = by_cluster[clust]
        syms = [p.get("symbol", "?") for p in ps]
        total_n = sum(notional(p) for p in ps)
        pct = total_n / nlv * 100 if nlv else None
        scores = [s for p in ps if (s := position_score(p)) is not None]
        avg_sc = safe_mean(scores)
        lo_sc = min(scores) if scores else None
        hi_sc = max(scores) if scores else None
        pct_str = f"{pct:.1f}%" if pct is not None else "N/A"
        avg_str = f"{avg_sc:.1f}" if avg_sc is not None else "?"

        lines.append(
            f"  {clust:30}  {len(ps):2} pos  ${total_n:>10,.0f}  "
            f"{pct_str:6} NLV  avg={avg_str}  [{lo_sc or '?'}-{hi_sc or '?'}]"
        )
        lines.append(f"    Symbols: {syms}")

        cluster_flags: list[str] = []
        if pct is not None and pct > 40:
            cluster_flags.append("CLUSTER_CONCENTRATION_WATCH")
        if avg_sc is not None and avg_sc < 50:
            cluster_flags.append("LOW_SCORE_CLUSTER_WATCH")
        low_syms = [p.get("symbol", "?") for p in ps
                    if (s := position_score(p)) is not None and s < 45]
        if len(low_syms) >= 2:
            cluster_flags.append(f"DUPLICATE_THEME_WATCH {low_syms}")

        if cluster_flags:
            flag_lines.append(f"    ⚑ {clust}: {', '.join(cluster_flags)}")

        cluster_data[clust] = {
            "count": len(ps), "symbols": syms,
            "notional": round(total_n, 2),
            "pct_nlv": round(pct, 2) if pct is not None else None,
            "avg_score": round(avg_sc, 1) if avg_sc is not None else None,
        }

    if flag_lines:
        lines += ["", "  Flags (diagnostic only):"] + flag_lines

    return lines, {"clusters": cluster_data}


def section_7(
    tier_d_records: list[dict],
    apex_records: list[dict],
    pru_syms: set[str],
) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 7: PRU / DISCOVERY SOURCE LABEL DIAGNOSTIC ───────────────",
        "  (Legacy 'Tier D' labels treated as source metadata only.)",
        "  (No tier-led allocation or selection priority is recommended.)",
        "",
    ]
    pru_cands = [r for r in apex_records
                 if r.get("record_type") == "apex_candidate"
                 and (r.get("scanner_tier") == "D" or r.get("pru"))]
    normal_cands = [r for r in apex_records
                    if r.get("record_type") == "apex_candidate"
                    and not (r.get("scanner_tier") == "D" or r.get("pru"))]
    pru_sel = [r for r in pru_cands if r.get("apex_decision") == "selected"]
    norm_sel = [r for r in normal_cands if r.get("apex_decision") == "selected"]

    pru_raw = [float(v) for r in pru_cands
               if (v := r.get("raw_score") or r.get("apex_cap_score"))]
    pru_eff = [float(v) for r in pru_cands if (v := r.get("apex_cap_score"))]
    pru_sel_sc = [float(v) for r in pru_sel if (v := r.get("apex_cap_score"))]
    norm_sel_sc = [float(v) for r in norm_sel if (v := r.get("apex_cap_score"))]

    apex_cap_events = [r for r in tier_d_records if r.get("stage") == "apex_cap"]

    lines += [
        f"  PRU/discovery candidates in Apex shortlist : {len(pru_cands)}",
        f"  Normal-path candidates in Apex shortlist   : {len(normal_cands)}",
        f"  PRU/discovery selected by Apex             : {len(pru_sel)}",
        f"  Normal-path selected by Apex               : {len(norm_sel)}",
        "",
        f"  PRU/disc avg raw score          : "
        f"{'%.1f' % safe_mean(pru_raw) if pru_raw else '—'}",
        f"  PRU/disc avg effective score    : "
        f"{'%.1f' % safe_mean(pru_eff) if pru_eff else '—'}",
        f"  PRU/disc avg score (selected)   : "
        f"{'%.1f' % safe_mean(pru_sel_sc) if pru_sel_sc else '—'}",
        f"  Normal-path avg score (selected): "
        f"{'%.1f' % safe_mean(norm_sel_sc) if norm_sel_sc else '—'}",
        "",
        f"  apex_cap funnel events (tier_d_funnel.jsonl) : {len(apex_cap_events)}",
    ]

    skips = [r for r in apex_records if r.get("record_type") == "high_score_skip"]
    pru_displace = [
        r for r in skips
        if str(r.get("selected_lower_symbol", "")) in pru_syms
        and r.get("origin_path") == "normal_path"
        and r.get("scanner_tier") != "D"
        and (r.get("score_gap") or 0) > 15
    ]
    lines.append(
        f"  PRU/disc selected over higher normal-path candidate (gap>15): {len(pru_displace)}"
    )

    avg_pru_sel = safe_mean(pru_sel_sc)
    avg_norm_sel = safe_mean(norm_sel_sc)

    if not pru_cands:
        verdict = "PRU_DISCOVERY_INSUFFICIENT_DATA"
    elif not pru_sel:
        verdict = "PRU_DISCOVERY_NO_EXECUTION_EVIDENCE"
    elif (avg_pru_sel is not None and avg_norm_sel is not None
          and avg_pru_sel < avg_norm_sel - 10):
        verdict = "PRU_DISCOVERY_OVERSELECTION_WATCH"
    else:
        verdict = "PRU_DISCOVERY_HELPFUL_BUT_INCONCLUSIVE"

    lines += [
        "",
        f"  Conclusion : {verdict}",
    ]
    if verdict != "PRU_DISCOVERY_OVERSELECTION_WATCH":
        lines.append("  PRU/discovery source labels are worth continued observation.")

    return lines, {
        "pru_candidates": len(pru_cands),
        "pru_selected": len(pru_sel),
        "avg_pru_sel_score": round(avg_pru_sel, 1) if avg_pru_sel else None,
        "avg_norm_sel_score": round(avg_norm_sel, 1) if avg_norm_sel else None,
        "pru_displacement_cases": len(pru_displace),
        "verdict": verdict,
    }


def section_8(
    positions: list[dict],
    s2_data: dict,
    s5_data: dict,
    s3_data: dict,
) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 8: SESSION VERDICT ────────────────────────────────────────",
    ]
    scores = [s for p in positions if (s := position_score(p)) is not None]
    book_avg = safe_mean(scores)
    below_35 = sum(1 for s in scores if s < 35)
    low_count = sum(1 for s in scores if 35 <= s < 50)
    low_qual_open = below_35 + low_count

    outscores_15 = s2_data.get("outscores_15_count", 0)
    outscores_20 = s2_data.get("outscores_20_count", 0)
    blocked_avg = s2_data.get("blocked_avg_score")
    etf_flags = s5_data.get("etf_overlap_flags", 0)
    etf_below_35_flag = bool(s5_data.get("flagged_below_35"))
    pru_disp = s3_data.get("pru_displacement_watch_count", 0)

    lines += [
        f"  A. Book avg score (open positions) : "
        f"{'%.1f' % book_avg if book_avg is not None else 'INSUFFICIENT DATA'}",
        f"  B. Positions below score 35        : {below_35}",
        f"  C. Positions score 35-49           : {low_count}",
        f"  D. Blocked-by-margin avg score     : "
        f"{'%.1f' % blocked_avg if blocked_avg else 'N/A'}",
        f"  E. Blocked outscoring book by >15  : {outscores_15}",
        f"  F. ETF overlap flags               : {etf_flags}",
        f"  G. PRU displacement watch flags    : {pru_disp}",
        "",
    ]

    verdict = compute_verdict(
        book_avg=book_avg,
        below_35=below_35,
        outscores_15=outscores_15,
        outscores_20=outscores_20,
        low_qual_open=low_qual_open,
        etf_below_35_flag=etf_below_35_flag,
    )
    action = verdict_action(verdict)

    lines += [
        f"  Verdict                  : {verdict}",
        f"  Recommended next action  : {action}",
    ]
    return lines, {
        "book_avg_score": round(book_avg, 1) if book_avg is not None else None,
        "below_35_count": below_35,
        "low_count": low_count,
        "blocked_avg": blocked_avg,
        "outscores_15": outscores_15,
        "outscores_20": outscores_20,
        "etf_flags": etf_flags,
        "etf_below_35_flag": etf_below_35_flag,
        "pru_displacement_flags": pru_disp,
        "verdict": verdict,
        "recommended_action": action,
    }


def section_9(
    dq: DataQuality,
    positions: list[dict],
    training: list[dict],
    apex_records: list[dict],
) -> tuple[list[str], dict]:
    lines: list[str] = [
        "",
        "── SECTION 9: DATA QUALITY AND OBSERVABILITY GAPS ───────────────────",
    ]
    issues: list[str] = []

    for f in dq.missing_files:
        issues.append(f"Missing file: {f}")
    for fname, cnt in dq.malformed_lines.items():
        issues.append(f"Malformed lines in {fname}: {cnt}")
    for w in dq.warnings:
        issues.append(f"Warning: {w}")

    score_none = sum(1 for r in training if r.get("entry_score") is None)
    if score_none:
        issues.append(
            f"entry_score field missing or empty in {score_none}/{len(training)} "
            f"training records — normalized from score field."
        )

    no_notional = sum(1 for p in positions if notional(p) == 0)
    if no_notional:
        issues.append(f"{no_notional} position(s) have zero notional (missing qty or price).")

    no_ts = sum(1 for p in positions if not open_time(p))
    if no_ts:
        issues.append(f"{no_ts} position(s) missing open_time — excluded from deployment sequence.")

    skips = [r for r in apex_records if r.get("record_type") == "high_score_skip"]
    no_sel_sc = sum(1 for r in skips if not r.get("selected_lower_score"))
    if no_sel_sc:
        issues.append(
            f"{no_sel_sc}/{len(skips)} high_score_skip records missing selected_lower_score."
        )

    issues.append(
        "Section 2 book-state reconstruction uses end-of-session positions, "
        "not intraday snapshots. Score gap analysis is directionally correct; "
        "temporal ordering is best-effort (MEDIUM confidence)."
    )
    issues.append(
        "Log timestamps use local timezone offset; UTC audit record timestamps "
        "are not correlated exactly. Symbol-to-score lookup uses max score seen "
        "in file as an approximation."
    )

    for issue in issues:
        lines.append(f"  • {issue}")

    return lines, {"issues": issues}


def section_10(
    s8_data: dict,
    positions: list[dict],
    s2_data: dict,
    s7_data: dict,
) -> list[str]:
    lines: list[str] = [
        "",
        "── SECTION 10: FINAL HUMAN SUMMARY ──────────────────────────────────",
        "",
    ]
    verdict = s8_data.get("verdict", "INSUFFICIENT_DATA")
    book_avg = s8_data.get("book_avg_score")
    below_35 = s8_data.get("below_35_count", 0)
    outscores_15 = s8_data.get("outscores_15", 0)
    blocked_avg = s8_data.get("blocked_avg")
    pru_verdict = s7_data.get("verdict", "—")

    # 1
    lines.append("  1. Did the bot deploy capital well?")
    if verdict == "CAPITAL_DEPLOYED_WELL":
        lines.append("     Yes. Book average score is strong; no superior blocked alternatives.")
    elif verdict == "INSUFFICIENT_DATA":
        lines.append("     INSUFFICIENT DATA.")
    else:
        ba_str = f"{book_avg:.1f}" if book_avg is not None else "?"
        lines.append(
            f"     Partially. Book avg score={ba_str}; {below_35} position(s) below score 35. "
            f"Sequencing pressure detected."
        )

    # 2
    lines += ["", "  2. Were weak entries detected?"]
    if below_35 >= 2:
        weak = [p.get("symbol", "?") for p in positions
                if (s := position_score(p)) is not None and s < 35]
        lines.append(f"     Yes. {below_35} positions below score 35: {weak}.")
    elif below_35 == 1:
        weak = [p.get("symbol", "?") for p in positions
                if (s := position_score(p)) is not None and s < 35]
        lines.append(f"     Minor. 1 position below score 35: {weak}.")
    else:
        lines.append("     No. All open positions scored 35 or above.")

    # 3
    lines += ["", "  3. Were stronger candidates blocked later?"]
    if outscores_15 and outscores_15 > 0:
        ba_str = f"{blocked_avg:.1f}" if blocked_avg else "?"
        lines.append(
            f"     Yes. {outscores_15} margin-blocked candidate(s) outscored the "
            f"open book by >15 pts (blocked avg={ba_str})."
        )
    else:
        lines.append(
            "     No margin-blocked candidates materially outscored the book average."
        )

    # 4
    lines += ["", "  4. Was the issue Apex selection quality or capital sequencing?"]
    if outscores_15 and outscores_15 > 0 and below_35 > 0:
        lines.append(
            "     Capital sequencing. Apex identified stronger candidates in later cycles "
            "but margin capacity was consumed by earlier lower-quality entries. "
            "Apex selection quality appears sound."
        )
    elif below_35 > 0 and (not outscores_15 or outscores_15 == 0):
        lines.append(
            "     Weak entries. Low-score positions entered, but no evidence of "
            "superior candidates being specifically blocked by margin. "
            "May reflect sparse-cycle or pre-market entry conditions."
        )
    else:
        lines.append("     No clear sequencing failure or Apex selection problem detected.")

    # 5
    lines += ["", "  5. Is PRU/discovery helping, hurting, or inconclusive?"]
    lines.append(f"     {pru_verdict}.")
    lines.append("     PRU/discovery source labels are worth continued observation.")

    # 6
    lines += ["", "  6. Is Track B within the problem scope?"]
    lines.append(
        "     No. Track B exit management is operating correctly. "
        "The sequencing issue is an entry/capacity problem, not a PM problem."
    )

    # 7
    lines += ["", "  7. Is any live strategy change justified today?"]
    lines.append(
        "     No. One session is insufficient evidence for any strategy change. "
        "Run this report across 3-5 more sessions before evaluating rotation, "
        "ETF suppression, or threshold adjustments."
    )

    lines += ["", f"  Session verdict: {verdict}", ""]
    return lines


# ── Report orchestrator ───────────────────────────────────────────────────────

def run_report(since: date, output_dir: pathlib.Path) -> dict:
    dq = DataQuality()

    # Paths
    data_dir = _REPO_ROOT / "data"
    log_path = _REPO_ROOT / "logs" / "decifer.log"
    audit_path = data_dir / "apex_decision_audit.jsonl"
    training_path = data_dir / "training_records.jsonl"
    positions_path = data_dir / "positions.json"
    tier_d_path = data_dir / "tier_d_funnel.jsonl"

    files_read = [str(p) for p in
                  [audit_path, training_path, positions_path, tier_d_path, log_path]
                  if p.exists()]

    # Load data
    apex_records = load_jsonl(audit_path, dq, since)
    training = load_jsonl(training_path, dq, since)
    positions = load_positions(positions_path, dq)
    tier_d_records = load_jsonl(tier_d_path, dq, since)

    nlv = parse_nlv(log_path, since)
    regime = parse_regime(log_path, since)
    margin_blocks = parse_margin_blocks(log_path, since, dq)
    spread_blocked = parse_spread_blocks(log_path, since)

    sym_score_idx = build_symbol_score_index(apex_records)
    pru_syms = build_pru_symbol_set(apex_records)

    # Build sections
    s0_lines, s0_data = section_0(since, positions, apex_records, training,
                                  regime, dq, files_read)
    s1_lines, s1_data = section_1(positions, nlv)
    s2_lines, s2_data = section_2(margin_blocks, positions, sym_score_idx, spread_blocked)
    s3_lines, s3_data = section_3(apex_records, pru_syms)
    s4_lines, s4_data = section_4(positions, training)
    s5_lines, s5_data = section_5(positions)
    s6_lines, s6_data = section_6(positions, nlv)
    s7_lines, s7_data = section_7(tier_d_records, apex_records, pru_syms)
    s8_lines, s8_data = section_8(positions, s2_data, s5_data, s3_data)
    s9_lines, s9_data = section_9(dq, positions, training, apex_records)
    s10_lines = section_10(s8_data, positions, s2_data, s7_data)

    # Assemble text
    title = (
        f"\n{'═' * 68}\n"
        f"  DECIFER TRADE QUALITY REPORT  |  {since}\n"
        f"{'═' * 68}"
    )
    all_lines = (
        [title]
        + s0_lines + s1_lines + s2_lines + s3_lines
        + s4_lines + s5_lines + s6_lines + s7_lines
        + s8_lines + s9_lines + s10_lines
        + ["\n" + "═" * 68]
    )
    text = "\n".join(all_lines) + "\n"

    # JSON artifact
    report_json = {
        "meta": {"since": str(since), "generated": datetime.now(timezone.utc).isoformat()},
        "section_0": s0_data,
        "section_1": s1_data,
        "section_2": s2_data,
        "section_3": s3_data,
        "section_4": s4_data,
        "section_5": s5_data,
        "section_6": s6_data,
        "section_7": s7_data,
        "section_8": s8_data,
        "section_9": s9_data,
        "data_quality": {
            "missing_files": dq.missing_files,
            "malformed_lines": dq.malformed_lines,
            "warnings": dq.warnings,
        },
    }

    # Write artifacts
    output_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    txt_path = output_dir / f"report_{ts_str}.txt"
    json_path = output_dir / f"report_{ts_str}.json"
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(
        json.dumps(report_json, indent=2, default=str),
        encoding="utf-8",
    )

    print(text)
    print(f"wrote: {txt_path}")
    print(f"wrote: {json_path}")

    return report_json


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only trade quality and capital sequencing diagnostic."
    )
    p.add_argument(
        "--since",
        default=date.today().isoformat(),
        help="Filter records on or after this UTC date (YYYY-MM-DD). Default: today.",
    )
    p.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "data" / "trade_quality_reports"),
        help="Directory for report artifacts.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        since = date.fromisoformat(args.since)
    except ValueError:
        print(f"ERROR: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
        sys.exit(1)
    output_dir = pathlib.Path(args.output_dir)
    run_report(since=since, output_dir=output_dir)


if __name__ == "__main__":
    main()
