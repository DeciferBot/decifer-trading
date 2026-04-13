# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpha_decay.py                             ║
# ║   Per-trade forward return distribution tracker              ║
# ║                                                              ║
# ║   Measures signal half-life by computing the distribution    ║
# ║   of returns at T+1, T+3, T+5, T+10 bars after entry.       ║
# ║                                                              ║
# ║   Research basis:                                            ║
# ║     Timothy Masters / markrbest.github.io — alpha decay      ║
# ║     charts show return distribution over a trade's life,     ║
# ║     sampled at regular intervals. A clean upward trajectory  ║
# ║     beats one that dips and recovers.                        ║
# ║     MicroAlphas (2025): swing-trade signal effectiveness     ║
# ║     diminishes after 3–10 days.                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta

log = logging.getLogger("decifer.alpha_decay")

HORIZONS = [1, 3, 5, 10]  # trading days after entry
_TRADE_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "trades.json")
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "alpha_decay_cache.json")
_CACHE_TTL = 3600  # seconds — re-use yfinance data for up to 1 hour


# ── Disk cache ───────────────────────────────────────────────────────────────


def _cache_key(trades: list) -> str:
    """
    Stable string key for the current closed-trade set.
    Built from (closed-trade count, latest entry date) so that:
      - a new trade arriving → different key → cache miss
      - same trades, repeated call within TTL → cache hit
    """
    closed = [t for t in trades if t.get("exit_price") is not None or t.get("pnl") is not None]
    count = len(closed)
    if not closed:
        return f"{count}|"
    dates = []
    for t in closed:
        d = _parse_entry_date(t)
        if d:
            dates.append(d.isoformat())
    latest = max(dates) if dates else ""
    return f"{count}|{latest}"


def _load_cache(key: str) -> dict | None:
    """Return cached stats dict if key matches and TTL has not expired, else None."""
    try:
        with open(_CACHE_FILE) as f:
            c = json.load(f)
        if c.get("key") == key and (time.time() - c.get("ts", 0)) < _CACHE_TTL:
            return c.get("data")
    except Exception:
        pass
    return None


def _save_cache(key: str, data: dict) -> None:
    """Persist stats to disk so the next tab-switch is instant."""
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump({"key": key, "ts": time.time(), "data": data}, f)
    except Exception as exc:
        log.debug("alpha_decay cache write failed: %s", exc)


# ── Entry-date parser ──────────────────────────────────────────────────────


def _parse_entry_date(trade: dict) -> date | None:
    """
    Extract the entry date from a trade record.
    Handles multiple timestamp field names and formats used across trade sources.
    """
    for key in ("entry_time", "open_time", "timestamp"):
        raw = trade.get(key)
        if not raw:
            continue
        try:
            raw_str = str(raw).strip().replace("Z", "+00:00")
            # "2026-03-23 15:02:42" — no T separator
            if "T" not in raw_str and " " in raw_str:
                raw_str = raw_str.replace(" ", "T", 1)
            dt = datetime.fromisoformat(raw_str)
            return dt.date()
        except Exception:
            continue
    return None


# ── Forward return fetcher ────────────────────────────────────────────────


def fetch_forward_returns(symbol: str, entry_dt: date, horizons: list) -> dict | None:
    """
    Download daily OHLCV via yfinance and return the % price change at each
    forward horizon relative to the entry-bar closing price.

    Returns {1: 0.0234, 3: 0.0118, 5: -0.0082, 10: -0.0195}
    (positive = price rose from entry close)

    Returns None when data is unavailable (e.g. horizon not reached yet).

    yfinance thread-safety: uses Ticker.history() per call (stateless).
    The caller is responsible for not invoking this concurrently on the same
    symbol (the HTTP handler is single-threaded so this is safe by default).
    """
    try:
        import yfinance as yf

        # Suppress noisy yfinance auth warnings
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        # Download window: entry date – 2 days buffer (for weekends/holidays at start)
        #                  + max_horizon * 2 + 10 days buffer at end
        start = entry_dt - timedelta(days=2)
        end = entry_dt + timedelta(days=max(horizons) * 2 + 14)
        today = date.today()
        if end > today:
            end = today

        # yfinance Ticker.history() is safe to call per-request; each call
        # creates a new session object so there is no shared global state.
        for attempt in range(3):
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    auto_adjust=True,
                    raise_errors=False,
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                pass
            if attempt < 2:
                try:
                    yf.cache.clear()
                except Exception:
                    pass
                time.sleep(0.5)
        else:
            return None

        if df is None or df.empty:
            return None

        # Normalise index to timezone-naive for comparison
        if df.index.tzinfo is not None:
            df = df.copy()
            df.index = df.index.tz_localize(None)

        df = df.sort_index()

        # Find the first trading bar on or after the entry date
        entry_ts = datetime(entry_dt.year, entry_dt.month, entry_dt.day)
        bars_from_entry = df[df.index >= entry_ts]
        if bars_from_entry.empty:
            return None

        base_close = float(bars_from_entry.iloc[0]["Close"])
        if base_close <= 0:
            return None

        results = {}
        for h in horizons:
            if len(bars_from_entry) > h:
                fwd_close = float(bars_from_entry.iloc[h]["Close"])
                results[h] = round((fwd_close - base_close) / base_close, 6)
            # Horizon not reached yet — omit silently

        return results if results else None

    except Exception as exc:
        log.debug("fetch_forward_returns(%s, %s): %s", symbol, entry_dt, exc)
        return None


# ── Per-trade decay computation ───────────────────────────────────────────


def compute_alpha_decay(trades: list | None = None, horizons: list | None = None) -> list:
    """
    For each closed trade compute forward returns at every horizon.

    Returns a list of enriched dicts:
    {
      "symbol":                str,
      "direction":             "LONG" | "SHORT",
      "score":                 int,
      "regime":                str,
      "entry_date":            "YYYY-MM-DD",
      "pnl":                   float | None,
      "forward_returns":       {1: float, ...},    # raw price % change
      "direction_adj_returns": {1: float, ...},    # positive = favourable for direction
    }

    Trades with no parseable entry date or no yfinance data are silently skipped.
    """
    if horizons is None:
        horizons = HORIZONS

    if trades is None:
        if not os.path.exists(_TRADE_LOG_FILE):
            return []
        try:
            with open(_TRADE_LOG_FILE) as f:
                trades = json.load(f)
        except Exception:
            return []

    # Only process closed trades (exit recorded)
    closed = [t for t in trades if t.get("exit_price") is not None or t.get("pnl") is not None]

    results = []
    for trade in closed:
        entry_dt = _parse_entry_date(trade)
        if entry_dt is None:
            continue

        symbol = (trade.get("symbol") or "").upper().strip()
        if not symbol:
            continue

        fwd = fetch_forward_returns(symbol, entry_dt, horizons)
        if not fwd:
            continue

        direction = trade.get("direction") or ""
        if not direction:
            action = (trade.get("action") or "").upper()
            direction = "SHORT" if action == "SELL" else "LONG"
        dir_sign = -1 if direction == "SHORT" else 1
        dir_adj = {h: round(v * dir_sign, 6) for h, v in fwd.items()}

        results.append(
            {
                "symbol": symbol,
                "direction": direction,
                "score": trade.get("score") or 0,
                "regime": trade.get("regime") or "UNKNOWN",
                "entry_date": entry_dt.isoformat(),
                "pnl": trade.get("pnl"),
                "forward_returns": fwd,
                "direction_adj_returns": dir_adj,
                "signal_scores": trade.get("signal_scores") or {},
            }
        )

    return results


# ── Aggregation helpers ───────────────────────────────────────────────────


def _percentile(values: list, p: float) -> float | None:
    """Compute percentile p (0–100) without numpy."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    idx = (len(s) - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (idx - lo) * (s[hi] - s[lo]), 6)


def _complete(records: list, horizons: list) -> list:
    """Return only records that have direction_adj_returns at every horizon."""
    return [r for r in records if all(h in r.get("direction_adj_returns", {}) for h in horizons)]


def _aggregate(records: list, horizons: list) -> dict:
    """
    Return median / p25 / p75 per horizon.

    Uses only records that have data at EVERY horizon (cohort analysis) so that
    the comparison across horizons reflects the same set of trades rather than
    shrinking samples as horizons grow.  Falls back to all records when fewer
    than 5 complete records are available.
    """
    if not records:
        return {
            "median": [None] * len(horizons),
            "p25": [None] * len(horizons),
            "p75": [None] * len(horizons),
            "n": 0,
            "n_total": 0,
        }

    complete = _complete(records, horizons)
    cohort = complete if len(complete) >= 5 else records

    medians, p25s, p75s = [], [], []
    for h in horizons:
        vals = [r["direction_adj_returns"][h] for r in cohort if h in r.get("direction_adj_returns", {})]
        medians.append(_percentile(vals, 50))
        p25s.append(_percentile(vals, 25))
        p75s.append(_percentile(vals, 75))

    return {
        "median": medians,
        "p25": p25s,
        "p75": p75s,
        "n": len(cohort),  # records used for analysis (complete-horizon cohort)
        "n_total": len(records),  # total records in segment (including partial)
    }


# ── Dimension segmentation ────────────────────────────────────────────────

_DIMENSIONS = (
    "trend",
    "momentum",
    "squeeze",
    "flow",
    "breakout",
    "mtf",
    "news",
    "social",
    "reversion",
)


def _dominant_dimension(signal_scores: dict):
    """
    Return the name of the dimension with the highest score, or None if
    signal_scores is empty or contains no recognised dimension keys.
    Ties are broken by iteration order (first key wins).
    """
    if not signal_scores:
        return None
    valid = {k: v for k, v in signal_scores.items() if k in _DIMENSIONS and isinstance(v, (int, float))}
    if not valid:
        return None
    return max(valid, key=lambda k: valid[k])


# ── Public summary API ────────────────────────────────────────────────────


def get_alpha_decay_stats(trades: list | None = None, horizons: list | None = None) -> dict:
    """
    Compute and aggregate forward return distributions by segment.

    Segments:
      all         — every closed trade with price data
      high_score  — conviction score ≥ 38 (high-conviction threshold)
      low_score   — conviction score < 38
      bull        — TRENDING_UP regime
      bear        — TRENDING_DOWN or BEAR regime
      long_only   — LONG direction trades
      short_only  — SHORT direction trades

    Returns:
    {
      "horizons":        [1, 3, 5, 10],
      "groups": {
        "all":       {"median": [...], "p25": [...], "p75": [...], "n": int},
        "high_score": {...},
        ...
      },
      "optimal_horizon": int | None,   # horizon with highest overall median
      "trade_count":     int,          # trades with usable forward-return data
      "computed_at":     str,          # ISO timestamp
    }
    """
    if horizons is None:
        horizons = HORIZONS

    # Load trades upfront (only when caller didn't provide them) so we can
    # build a stable cache key before hitting yfinance.
    _from_file = trades is None
    if _from_file:
        if not os.path.exists(_TRADE_LOG_FILE):
            trades = []
        else:
            try:
                with open(_TRADE_LOG_FILE) as f:
                    trades = json.load(f)
            except Exception:
                trades = []

    # Cache check — only for file-sourced data (caller-supplied trades are
    # typically small test sets, not worth caching).
    cache_key = None
    if _from_file:
        cache_key = _cache_key(trades)
        cached = _load_cache(cache_key)
        if cached is not None:
            log.debug("alpha_decay: cache hit (%s)", cache_key)
            return cached

    records = compute_alpha_decay(trades=trades, horizons=horizons)

    groups = {
        "all": records,
        "high_score": [r for r in records if (r.get("score") or 0) >= 38],
        "low_score": [r for r in records if (r.get("score") or 0) < 38],
        "bull": [
            r
            for r in records
            if str(r.get("regime") or "") in ("TRENDING_UP", "BULL_TRENDING")
            or str(r.get("regime") or "").startswith("BULL")
        ],
        "bear": [
            r
            for r in records
            if str(r.get("regime") or "") in ("TRENDING_DOWN", "RELIEF_RALLY", "CAPITULATION", "BEAR_TRENDING")
            or str(r.get("regime") or "").startswith("BEAR")
        ],
        "long_only": [r for r in records if r.get("direction") == "LONG"],
        "short_only": [r for r in records if r.get("direction") == "SHORT"],
    }

    # Per-dimension segments: trades where that dimension had the highest score.
    # Sparse until live trades with signal_scores accumulate.
    for _dim in _DIMENSIONS:
        groups[f"dim_{_dim}"] = [r for r in records if _dominant_dimension(r.get("signal_scores", {})) == _dim]

    agg = {name: _aggregate(recs, horizons) for name, recs in groups.items()}

    # Optimal horizon: horizon index with the highest median across all trades
    all_medians = agg["all"]["median"]
    optimal = None
    valid = [(i, v) for i, v in enumerate(all_medians) if v is not None]
    if valid:
        best_i = max(valid, key=lambda x: x[1])[0]
        optimal = horizons[best_i]

    complete_count = len(_complete(records, horizons))
    result = {
        "horizons": horizons,
        "groups": agg,
        "optimal_horizon": optimal,
        "trade_count": len(records),  # total records with any price data
        "complete_count": complete_count,  # records with data at ALL horizons (cohort used for chart)
        "computed_at": datetime.now(UTC).isoformat(),
    }

    if cache_key is not None:
        _save_cache(cache_key, result)

    return result
