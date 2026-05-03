# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  universe_position.py                      ║
# ║                                                              ║
# ║   Position Research Universe (Tier D).                       ║
# ║                                                              ║
# ║   Broad discovery net built from the committed Master        ║
# ║   Universe.  Bypasses the gap/premarket-volume promoter.     ║
# ║   Scored via weighted discovery signals (fundamental +       ║
# ║   technical/relative-strength).  Built weekly or on demand.  ║
# ║                                                              ║
# ║   Entry criterion: discovery_points >= 2  OR  any strong     ║
# ║   signal (3 pts)  OR  ≥1 matched POSITION archetype.        ║
# ║                                                              ║
# ║   Missing data = 0 points, never rejection.                  ║
# ║   Hard blocks: unusable price, sub-50k liquidity only.       ║
# ║                                                              ║
# ║   Shadow mode controls live execution — this file only       ║
# ║   builds the research universe; execution is gated           ║
# ║   separately in entry_gate.py.                               ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from alpaca_data import fetch_bars, fetch_snapshots_batched
from config import CONFIG
from universe_committed import load_committed_universe

log = logging.getLogger("decifer.universe_position")

_PRU_PATH = os.path.join("data", "position_research_universe.json")

# Sector ETFs for relative-strength signals — must match fmp_client.GICS_ETF_MAP values
_SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLY", "XLI", "XLC", "XLRE", "XLP", "XLU", "XLB"]

# Discovery signal point weights
_WEAK = 1
_MODERATE = 2
_STRONG = 3

# Schema fields required on every symbol entry
_REQUIRED_SYMBOL_FIELDS = (
    "ticker", "discovery_score", "matched_position_archetypes",
    "discovery_signals", "discovery_signal_points", "missing_data_fields",
    "universe_source", "scanner_tier", "position_research_universe_member",
    "active_trading_universe_member", "priority_overlap", "universe_entry_reason",
)


# ── Sector ETF data (fetched once per build run) ───────────────────────────────


def _fetch_etf_context(etfs: list[str]) -> tuple[dict[str, float | None], dict[str, bool]]:
    """
    Fetch 90d daily bars for a list of ETFs (~63 trading days, enough for 50d MA).
    Returns:
        returns_map:   {etf: 1-month return pct | None}
        above_50ma_map: {etf: True if current close > 50d MA}
    """
    returns_map: dict[str, float | None] = {}
    above_50ma_map: dict[str, bool] = {}

    for etf in etfs:
        try:
            df = fetch_bars(etf, period="90d", interval="1d")
            if df is None or df.empty or len(df) < 5:
                returns_map[etf] = None
                above_50ma_map[etf] = False
                continue

            closes = df["Close"].tolist()
            cur = closes[-1]

            # 1-month return (~21 trading days)
            if len(closes) >= 22:
                returns_map[etf] = (cur - closes[-22]) / closes[-22] * 100.0
            else:
                returns_map[etf] = None

            # 50-day MA
            if len(closes) >= 50:
                above_50ma_map[etf] = cur > (sum(closes[-50:]) / 50.0)
            else:
                above_50ma_map[etf] = False

        except Exception as e:
            log.debug("_fetch_etf_context %s: %s", etf, e)
            returns_map[etf] = None
            above_50ma_map[etf] = False

    return returns_map, above_50ma_map


# ── Symbol bar fetching (parallel) ────────────────────────────────────────────


def _fetch_symbol_bars_batch(symbols: list[str], workers: int = 20) -> dict[str, object]:
    """Fetch 90d daily bars for symbols in parallel (~63 trading days, enough for 50d MA). Returns {symbol: DataFrame | None}."""
    results: dict[str, object] = {}

    def _one(sym: str):
        try:
            return sym, fetch_bars(sym, period="90d", interval="1d")
        except Exception:
            return sym, None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pru_bars") as ex:
        futs = {ex.submit(_one, s): s for s in symbols}
        for fut in as_completed(futs):
            sym, df = fut.result()
            results[sym] = df
    return results


# ── FMP sector mapping (parallel) ─────────────────────────────────────────────


def _fetch_sector_map(symbols: list[str], workers: int = 20) -> dict[str, str | None]:
    """Pre-fetch sector ETF ticker for each symbol. Returns {symbol: etf | None}."""
    import fmp_client

    results: dict[str, str | None] = {}

    def _one(sym: str):
        try:
            return sym, fmp_client.get_company_sector(sym)
        except Exception:
            return sym, None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pru_sector") as ex:
        futs = {ex.submit(_one, s): s for s in symbols}
        for fut in as_completed(futs):
            sym, etf = fut.result()
            results[sym] = etf
    return results


# ── Technical signal scoring ───────────────────────────────────────────────────


def _compute_technical_signals(
    df,
    spy_1m_return: float | None,
    sector_1m_return: float | None,
    sector_etf_above_50ma: bool,
) -> tuple[dict[str, int], list[str]]:
    """
    Compute technical discovery signals from 90d daily bars (~63 trading days).
    Returns (signal_points_dict, missing_fields_list).
    Missing bars or too-short history → 0 pts for affected signals, not rejection.
    """
    pts: dict[str, int] = {}
    missing: list[str] = []

    if df is None or df.empty or len(df) < 5:
        missing.append("daily_bars")
        return pts, missing

    try:
        closes = df["Close"].tolist()
    except Exception:
        missing.append("daily_bars")
        return pts, missing

    n = len(closes)
    cur = closes[-1]

    # 1-month return for this symbol (~21 trading days)
    sym_1m: float | None = None
    if n >= 22:
        sym_1m = (cur - closes[-22]) / closes[-22] * 100.0 if closes[-22] > 0 else None
    else:
        missing.append("1m_return")

    # Outperforming SPY over 1 month (strong — differentiates vs broad market)
    if sym_1m is not None and spy_1m_return is not None:
        if sym_1m > spy_1m_return:
            pts["outperforming_spy_1m"] = _STRONG
    elif sym_1m is None and spy_1m_return is not None:
        pass  # already in missing

    # Outperforming sector ETF over 1 month
    if sym_1m is not None and sector_1m_return is not None:
        if sym_1m > sector_1m_return:
            pts["outperforming_sector_1m"] = _MODERATE

    # Above 50-day MA
    if n >= 50:
        ma50 = sum(closes[-50:]) / 50.0
        if cur > ma50:
            pts["above_50d_ma"] = _WEAK
    else:
        missing.append("50d_ma")

    # Higher lows: current price > 30d ago AND > 60d ago
    if n >= 60:
        if cur > closes[-30] and cur > closes[-60]:
            pts["higher_lows"] = _WEAK
    elif n >= 30:
        if cur > closes[-30]:
            pts["higher_lows"] = _WEAK

    # Base-building after large drawdown: >20% below 40d high, recovering >5% from 20d low
    if n >= 40:
        high_40d = max(closes[-40:])
        low_20d = min(closes[-20:])
        off_high = (high_40d - cur) / high_40d * 100.0 if high_40d > 0 else 0.0
        recovery = (cur - low_20d) / low_20d * 100.0 if low_20d > 0 else 0.0
        if off_high > 20.0 and recovery > 5.0:
            pts["base_building_after_drawdown"] = _MODERATE

    # Sector ETF above 50MA (computed once per build run, passed in)
    if sector_etf_above_50ma:
        pts["sector_etf_above_50ma"] = _WEAK

    return pts, missing


# ── Fundamental signal scoring ─────────────────────────────────────────────────


def _compute_fundamental_signals(
    symbol: str,
    current_price: float,
    recent_upgrade_syms: set[str],
) -> tuple[dict[str, int], list[str], dict]:
    """
    Compute fundamental discovery signals from FMP data.
    Returns (signal_points_dict, missing_fields_list, raw_fmp_snapshot).

    raw_fmp_snapshot holds the raw values fetched so entry_gate shadow validation
    can detect data-flow gaps (PRU had a value that TradeContext later shows as None).
    FMP failures = 0 pts + add to missing, never rejection.
    """
    import fmp_client

    pts: dict[str, int] = {}
    missing: list[str] = []

    # Revenue growth
    rev_data: dict = {}
    try:
        rev_data = fmp_client.get_revenue_growth(symbol) or {}
    except Exception:
        missing.append("revenue_growth")

    rev_yoy = rev_data.get("revenue_growth_yoy")
    rev_decel = rev_data.get("revenue_deceleration", False)

    if rev_yoy is None:
        missing.append("revenue_growth_yoy")
    elif rev_yoy > 10.0:
        pts["revenue_yoy_gt_10pct"] = _STRONG
    elif rev_yoy > 5.0:
        pts["revenue_yoy_gt_5pct"] = _MODERATE
    elif rev_yoy > 0.0:
        pts["revenue_yoy_positive"] = _WEAK
    elif not rev_decel:
        # Revenue negative but decline not accelerating — still a discovery signal
        pts["revenue_decline_slowing"] = _WEAK

    # Key metrics: gross margin, debt
    metrics: dict = {}
    try:
        metrics = fmp_client.get_key_metrics_ttm(symbol) or {}
    except Exception:
        missing.append("key_metrics")

    gross_margin = metrics.get("gross_margin")
    if gross_margin is None:
        missing.append("gross_margin")
    elif gross_margin > 0.0:
        pts["gross_margin_positive"] = _WEAK

    dte = metrics.get("debt_to_equity")
    if dte is not None and dte < 3.0:
        pts["debt_not_dangerous"] = _WEAK

    # Analyst price target upside (computed from pt_consensus vs current_price)
    pt_data: dict = {}
    try:
        pt_data = fmp_client.get_price_target(symbol) or {}
    except Exception:
        missing.append("price_target")

    pt_consensus = pt_data.get("pt_consensus")
    analyst_upside: float | None = None
    if pt_consensus and current_price > 0:
        analyst_upside = (pt_consensus - current_price) / current_price * 100.0

    if analyst_upside is None:
        missing.append("analyst_price_target")
    elif analyst_upside > 15.0:
        pts["analyst_upside_gt_15pct"] = _STRONG
    elif analyst_upside > 5.0:
        pts["analyst_upside_positive"] = _WEAK

    # Analyst consensus score
    grade_data: dict = {}
    try:
        grade_data = fmp_client.get_analyst_grades(symbol) or {}
    except Exception:
        missing.append("analyst_grades")

    consensus_score = grade_data.get("consensus_score")
    if consensus_score is None:
        missing.append("analyst_consensus")
    elif consensus_score >= 3.0:
        # Consensus at or above HOLD — not net negative
        pts["consensus_not_negative"] = _WEAK

    # Recent analyst upgrade (last 10 days — pre-fetched set)
    if symbol in recent_upgrade_syms:
        pts["recent_analyst_upgrade"] = _MODERATE

    raw_snapshot = {
        "revenue_growth_yoy": rev_yoy,
        "revenue_decelerating": rev_decel,
        "gross_margin": gross_margin,
        "analyst_upside_pct": analyst_upside,
        "consensus_score": consensus_score,
        "debt_to_equity": dte,
    }
    return pts, missing, raw_snapshot


# ── Archetype matching ─────────────────────────────────────────────────────────


def _match_archetypes(
    fundamental_pts: dict[str, int],
    technical_pts: dict[str, int],
) -> list[str]:
    """Match POSITION archetypes based on fired signal keys. Multiple may match."""
    all_pts = {**fundamental_pts, **technical_pts}

    rev_strong = "revenue_yoy_gt_10pct" in all_pts
    rev_moderate = "revenue_yoy_gt_5pct" in all_pts
    rev_positive = "revenue_yoy_positive" in all_pts
    any_rev_pos = rev_strong or rev_moderate or rev_positive
    margin_ok = "gross_margin_positive" in all_pts
    outperform_spy = "outperforming_spy_1m" in all_pts
    above_50ma = "above_50d_ma" in all_pts
    rs_positive = outperform_spy or above_50ma
    sector_above = "sector_etf_above_50ma" in all_pts
    recent_upgrade = "recent_analyst_upgrade" in all_pts
    upside_high = "analyst_upside_gt_15pct" in all_pts
    upside_low = "analyst_upside_positive" in all_pts
    consensus_ok = "consensus_not_negative" in all_pts
    base_build = "base_building_after_drawdown" in all_pts

    archetypes: list[str] = []

    # Quality Compounder: strong revenue + positive margin + relative strength
    if rev_strong and margin_ok and rs_positive:
        archetypes.append("Quality Compounder")

    # Growth Leader: high revenue growth OR analyst upgrade with significant upside
    if rev_strong or (any_rev_pos and rev_moderate) or (recent_upgrade and upside_high):
        archetypes.append("Growth Leader")

    # Re-rating Candidate: analyst upgrade OR positive consensus + upside
    if recent_upgrade or (upside_low and consensus_ok):
        archetypes.append("Re-rating Candidate")

    # Turnaround/Inflection: base-building recovery + any positive revenue signal
    if base_build and any_rev_pos:
        archetypes.append("Turnaround/Inflection")

    # Sector/RS Leader: outperforming SPY AND sector ETF healthy
    if outperform_spy and sector_above:
        archetypes.append("Sector/RS Leader")

    return archetypes


# ── Per-symbol scoring ─────────────────────────────────────────────────────────


def _score_symbol(
    symbol: str,
    snap: dict,
    df,
    spy_1m_return: float | None,
    sector_etf_returns: dict[str, float | None],
    sector_etf_above_50ma_map: dict[str, bool],
    sector_etf_for_symbol: str | None,
    recent_upgrade_syms: set[str],
    active_trading_syms: set[str],
) -> dict | None:
    """
    Score one symbol for Tier D admission.
    Returns full metadata dict or None if hard-blocked or insufficient discovery.
    """
    current_price = snap.get("price") or 0.0
    prev_volume = snap.get("prev_volume") or 0

    # Hard blocks — only these exclude a symbol
    if not current_price or current_price <= 0:
        log.debug("PRU hard-block %s: unusable price %s", symbol, current_price)
        return None
    if prev_volume < 50_000:
        log.debug("PRU hard-block %s: liquidity %d < 50k", symbol, prev_volume)
        return None

    # Determine sector ETF context for this symbol
    sector_1m_return: float | None = None
    sector_above_50ma = False
    if sector_etf_for_symbol and sector_etf_for_symbol in sector_etf_returns:
        sector_1m_return = sector_etf_returns[sector_etf_for_symbol]
        sector_above_50ma = sector_etf_above_50ma_map.get(sector_etf_for_symbol, False)

    # Score signals
    fund_pts, fund_missing, pru_fmp_snapshot = _compute_fundamental_signals(
        symbol, current_price, recent_upgrade_syms,
    )
    tech_pts, tech_missing = _compute_technical_signals(
        df, spy_1m_return, sector_1m_return, sector_above_50ma,
    )

    all_pts = {**fund_pts, **tech_pts}
    discovery_score = sum(all_pts.values())
    has_strong = any(v >= _STRONG for v in all_pts.values())
    archetypes = _match_archetypes(fund_pts, tech_pts)

    # Admission gate
    min_score = int(CONFIG.get("position_research_min_score", 2))
    if discovery_score < min_score and not has_strong and not archetypes:
        return None

    # Build human-readable entry reason
    strong_signals = [k for k, v in all_pts.items() if v >= _STRONG]
    reason_parts: list[str] = []
    if strong_signals:
        reason_parts.append("strong: " + ",".join(strong_signals))
    if archetypes:
        reason_parts.append("archetypes: " + ",".join(archetypes))
    if not reason_parts:
        reason_parts.append(f"discovery_score={discovery_score}")
    entry_reason = "; ".join(reason_parts)

    in_active = symbol in active_trading_syms

    return {
        "ticker": symbol,
        "discovery_score": discovery_score,
        "matched_position_archetypes": archetypes,
        "discovery_signals": list(all_pts.keys()),
        "discovery_signal_points": all_pts,
        "missing_data_fields": sorted(set(fund_missing + tech_missing)),
        "pru_fmp_snapshot": pru_fmp_snapshot,
        "universe_source": "position_research",
        "scanner_tier": "D",
        "position_research_universe_member": True,
        "active_trading_universe_member": in_active,
        "priority_overlap": in_active,
        "universe_entry_reason": entry_reason,
    }


# ── Schema validation ──────────────────────────────────────────────────────────


def _validate_schema(payload: dict) -> bool:
    """Validate required top-level and per-symbol fields before atomic write."""
    if not all(k in payload for k in ("built_at", "count", "symbols")):
        log.error("PRU schema: missing top-level fields in payload")
        return False
    for entry in payload.get("symbols", []):
        missing = [k for k in _REQUIRED_SYMBOL_FIELDS if k not in entry]
        if missing:
            log.error("PRU schema: symbol %s missing fields %s", entry.get("ticker", "?"), missing)
            return False
    return True


# ── Build entry point ──────────────────────────────────────────────────────────


def build_position_research_universe(
    committed_symbols: list[str],
    top_n: int | None = None,
    active_trading_syms: set[str] | None = None,
) -> list[dict]:
    """
    Build the Position Research Universe from the committed Master Universe.
    Scores each symbol across fundamental + technical discovery signals.
    Returns the top N scored symbols (full metadata dicts).
    Does NOT write to disk — call refresh_position_research_universe() for that.
    """
    if top_n is None:
        top_n = int(CONFIG.get("position_research_universe_size", 150))
    if active_trading_syms is None:
        active_trading_syms = set()

    log.info("PRU build: starting — %d committed symbols", len(committed_symbols))

    # Phase 1: Snapshots — hard-block filter (price, liquidity)
    log.info("PRU build: fetching snapshots for %d symbols...", len(committed_symbols))
    snaps = fetch_snapshots_batched(committed_symbols, batch_size=100)
    log.info("PRU build: %d snapshots returned", len(snaps))

    # Only score symbols we have snapshot data for (hard-block filter applied in _score_symbol)
    eligible = [s for s in committed_symbols if s in snaps]
    log.info("PRU build: %d eligible (have snapshot data)", len(eligible))

    # Phase 2: ETF context — fetch once for all sector ETFs + SPY
    log.info("PRU build: fetching ETF context (SPY + %d sector ETFs)...", len(_SECTOR_ETFS))
    etf_returns, etf_above_50ma = _fetch_etf_context(["SPY"] + _SECTOR_ETFS)
    spy_1m_return = etf_returns.get("SPY")
    sector_etf_returns = {etf: etf_returns.get(etf) for etf in _SECTOR_ETFS}
    sector_etf_above_50ma_map = {etf: etf_above_50ma.get(etf, False) for etf in _SECTOR_ETFS}
    log.info("PRU build: SPY 1m return = %s", f"{spy_1m_return:.2f}%" if spy_1m_return else "N/A")

    # Phase 3: Sector mapping — pre-fetch sector ETF for each symbol
    log.info("PRU build: fetching sector mapping for %d symbols...", len(eligible))
    sector_map = _fetch_sector_map(eligible, workers=5)  # 5 concurrent avoids FMP rate-limit cascade

    # Phase 4: Symbol daily bars (parallel)
    log.info("PRU build: fetching 90d daily bars for %d symbols...", len(eligible))
    symbol_bars = _fetch_symbol_bars_batch(eligible, workers=20)

    # Phase 5: Recent analyst upgrades — one call covers all symbols (10 days = 240h)
    import fmp_client
    recent_upgrade_syms: set[str] = set()
    try:
        changes = fmp_client.get_analyst_changes(hours_back=240) or []
        recent_upgrade_syms = {
            r.get("symbol", "").upper()
            for r in changes
            if r.get("action", "").lower() in ("upgrade", "upgraded", "initiates", "reinstates")
        }
        log.info("PRU build: %d recent upgrade symbols (last 10d)", len(recent_upgrade_syms))
    except Exception as e:
        log.warning("PRU build: get_analyst_changes failed: %s", e)

    # Phase 6: Score each symbol — parallel via ThreadPoolExecutor.
    # Each _score_symbol call makes ~4 sequential FMP HTTP requests (I/O bound).
    # ThreadPoolExecutor overlaps those waits across symbols; GIL is not a concern.
    # Workers=20 keeps us within FMP 750 calls/min: 20 × 4 = 80 concurrent calls max.
    scored: list[dict] = []
    hard_blocked = 0
    insufficient = 0

    _score_workers = min(20, len(eligible))

    def _score_one(sym: str) -> tuple[str, dict | None]:
        snap = snaps.get(sym, {})
        df = symbol_bars.get(sym)
        return sym, _score_symbol(
            sym, snap, df,
            spy_1m_return,
            sector_etf_returns,
            sector_etf_above_50ma_map,
            sector_map.get(sym),
            recent_upgrade_syms,
            active_trading_syms,
        )

    with ThreadPoolExecutor(max_workers=_score_workers, thread_name_prefix="pru_score") as ex:
        futs = {ex.submit(_score_one, sym): sym for sym in eligible}
        for fut in as_completed(futs):
            sym, entry = fut.result()
            if entry is None:
                snap = snaps.get(sym, {})
                price = snap.get("price") or 0.0
                vol = snap.get("prev_volume") or 0
                if price <= 0 or vol < 50_000:
                    hard_blocked += 1
                else:
                    insufficient += 1
            else:
                scored.append(entry)

    scored.sort(key=lambda r: r["discovery_score"], reverse=True)
    top = scored[:top_n]

    log.info(
        "PRU build: admitted=%d hard_blocked=%d insufficient=%d | top_n=%d",
        len(scored), hard_blocked, insufficient, len(top),
    )
    if top:
        ex = top[0]
        log.info(
            "PRU build: top scorer: %s score=%d archetypes=%s reason=%s",
            ex["ticker"], ex["discovery_score"],
            ex["matched_position_archetypes"], ex["universe_entry_reason"],
        )
    return top


# ── Public API ─────────────────────────────────────────────────────────────────


def refresh_position_research_universe() -> list[dict]:
    """
    Rebuild the PRU from the committed Master Universe and write to disk atomically.
    Schema-validates before writing — never overwrites a good file with a bad one.
    Returns the admitted symbol list. Raises on write failure.
    """
    committed = load_committed_universe()
    if not committed:
        log.error("PRU refresh: committed_universe is empty — cannot build PRU")
        return []

    active_trading_syms: set[str] = set()
    try:
        from universe_promoter import load_promoted_universe
        active_trading_syms = set(load_promoted_universe())
    except Exception:
        pass

    top = build_position_research_universe(committed, active_trading_syms=active_trading_syms)

    payload = {
        "built_at": datetime.now(UTC).isoformat(),
        "count": len(top),
        "symbols": top,
    }

    if not _validate_schema(payload):
        log.error("PRU refresh: schema validation failed — not writing file")
        return top

    _dir = os.path.dirname(os.path.abspath(_PRU_PATH))
    os.makedirs(_dir, exist_ok=True)
    _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
    try:
        with os.fdopen(_fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(_tmp, _PRU_PATH)
        log.info("PRU refresh: wrote %s — %d symbols", _PRU_PATH, len(top))
    except Exception as e:
        try:
            os.unlink(_tmp)
        except OSError:
            pass
        log.error("PRU refresh: write failed: %s", e)
        raise

    return top


def load_position_research_universe(
    max_staleness_days: int | None = None,
) -> tuple[list[str], list[dict]]:
    """
    Load the Position Research Universe from disk.

    Returns (ticker_list, full_metadata_list).
    Returns ([], []) on missing, malformed, or stale file — graceful degradation,
    never crashes the bot, never uses stale data for live execution.
    """
    if max_staleness_days is None:
        max_staleness_days = int(CONFIG.get("position_research_max_staleness_days", 8))

    try:
        with open(_PRU_PATH) as f:
            payload = json.load(f)
    except FileNotFoundError:
        log.debug("PRU: file not found at %s", _PRU_PATH)
        return [], []
    except json.JSONDecodeError as e:
        log.warning("PRU: malformed JSON — %s — returning empty", e)
        return [], []
    except Exception as e:
        log.warning("PRU: unexpected read error — %s — returning empty", e)
        return [], []

    built_at_str = payload.get("built_at", "")
    try:
        built_at = datetime.fromisoformat(built_at_str.replace("Z", "+00:00"))
        age_days = (datetime.now(UTC) - built_at).total_seconds() / 86400.0
        if age_days > max_staleness_days:
            log.warning(
                "PRU: file is %.1fd old (>%dd) — returning empty (stale data not used)",
                age_days, max_staleness_days,
            )
            return [], []
    except Exception as e:
        log.warning("PRU: timestamp parse failed (%s) — returning empty", e)
        return [], []

    symbols_list = payload.get("symbols", [])
    if not isinstance(symbols_list, list):
        log.warning("PRU: 'symbols' field is not a list — malformed file")
        return [], []

    tickers = [r["ticker"] for r in symbols_list if isinstance(r, dict) and "ticker" in r]
    log.info("PRU: loaded %d symbols (age=%.1fd)", len(tickers), age_days)
    return tickers, symbols_list


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = refresh_position_research_universe()
    print(f"\nTop 10:")
    for r in result[:10]:
        print(f"  {r['ticker']:6s} score={r['discovery_score']:3d}  "
              f"archetypes={r['matched_position_archetypes']}  "
              f"reason={r['universe_entry_reason']}")
