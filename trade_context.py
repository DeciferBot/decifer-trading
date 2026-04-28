# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  trade_context.py                          ║
# ║   Single responsibility: build a TradeContext envelope       ║
# ║   for every entry decision.                                  ║
# ║                                                              ║
# ║   TradeContext assembles ALL inputs required to classify     ║
# ║   a setup as INTRADAY / SWING / POSITION and to validate     ║
# ║   it before an order fires.                                  ║
# ║                                                              ║
# ║   Fast path  — Alpaca streaming (intraday data, < 100ms)    ║
# ║   Slow path  — FMP cache (fundamental data, 24h TTL)        ║
# ║   Graceful   — any field can be None; entry_gate handles it ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timezone
from typing import Optional

from config import CONFIG

log = logging.getLogger("decifer.trade_context")


# ── TradeContext dataclass ────────────────────────────────────────────────────


@dataclass
class TradeContext:
    """
    Full context envelope for a single entry decision.

    Populated by build_context() before the intelligence layer runs.
    Stored on the trade record for post-trade IC analysis.

    All fields are Optional — missing data degrades gracefully.
    The entry_gate treats None as "unknown" and applies conservative defaults.
    """

    symbol: str
    direction: str              # "LONG" | "SHORT"
    current_price: float

    # ── Intraday / technical (fast path — Alpaca) ─────────────────────────────
    signal_age_minutes: Optional[float] = None   # minutes since signal dimension fired
    vwap: Optional[float] = None                 # session VWAP from signal engine
    vwap_distance_pct: Optional[float] = None    # (price - vwap) / vwap * 100
    rel_volume: Optional[float] = None           # today's vol-so-far / avg at this time
    hod: Optional[float] = None                  # high of day so far
    hod_distance_pct: Optional[float] = None     # (price - hod) / hod * 100  (≤ 0)
    bid_ask_spread_pct: Optional[float] = None   # live spread as % of mid
    time_of_day_window: Optional[str] = None     # "OPEN" | "MIDDAY" | "PRIME_PM" | "CLOSE"
    in_dead_window: bool = False                  # True if 11:00–14:30 ET

    # ── Catalyst / event (catalyst_engine + FMP) ──────────────────────────────
    catalyst_score: Optional[float] = None       # from catalyst_engine.py
    catalyst_type: Optional[str] = None          # "earnings"|"upgrade"|"sector"|"news"|"none"
    earnings_days_away: Optional[int] = None     # days to next earnings event
    recent_upgrade: bool = False                  # analyst upgrade in last 10 days
    recent_downgrade: bool = False                # analyst downgrade in last 10 days

    # ── Analyst data (FMP — 24h cache) ────────────────────────────────────────
    analyst_consensus: Optional[str] = None      # "STRONG_BUY"|"BUY"|"HOLD"|"SELL"|"STRONG_SELL"
    analyst_pt: Optional[float] = None           # consensus price target
    analyst_upside_pct: Optional[float] = None   # (pt - price) / price * 100
    analyst_pt_days_old: Optional[int] = None    # days since PT was issued
    short_float_pct: Optional[float] = None      # short interest as % of float

    # ── Fundamental / growth (FMP — 24h cache) ────────────────────────────────
    revenue_growth_yoy: Optional[float] = None   # YoY revenue growth %
    revenue_growth_qoq: Optional[float] = None   # QoQ revenue growth %
    revenue_decelerating: bool = False            # True if QoQ growth < prior QoQ
    eps_accelerating: bool = False                # True if EPS growth rate improving
    eps_beat_rate: Optional[float] = None        # % of last 4 quarters beating estimate

    # ── Sector context (Alpaca — session cache) ───────────────────────────────
    sector_etf: Optional[str] = None             # e.g. "XLK"
    sector_above_50d: Optional[bool] = None      # sector ETF above 50-day MA
    sector_3m_vs_spy: Optional[float] = None     # sector ETF 3m return minus SPY 3m return
    sector_days_since_breakout: Optional[int] = None  # trading days since ETF crossed 50d MA

    # ── Regime (regime_detector — already exists) ─────────────────────────────
    regime: Optional[str] = None                 # "TRENDING_UP"|"BEAR_TRENDING"|"PANIC" etc.

    # ── Insider trading (FMP Form 4 — 2h cache) ───────────────────────────────
    insider_net_sentiment: Optional[str] = None     # "BUYING"|"SELLING"|"NEUTRAL"
    insider_buy_value_3m: Optional[float] = None    # net insider buy value $M last 3 months

    # ── Congressional trading (FMP Senate/House — 6h cache) ──────────────────
    congressional_sentiment: Optional[str] = None   # "BUYING"|"SELLING"|"NEUTRAL"|"NONE"

    # ── Financial quality metrics (FMP TTM — 24h cache) ──────────────────────
    gross_margin: Optional[float] = None            # gross margin % e.g. 45.0
    net_margin: Optional[float] = None             # net profit margin % (>0 = profitable)
    is_profitable: Optional[bool] = None           # True if net_margin > 0
    fcf_yield: Optional[float] = None              # free cash flow yield %
    pe_ratio: Optional[float] = None               # trailing P/E ratio
    dcf_upside_pct: Optional[float] = None         # DCF fair value upside %

    # ── Institutional ownership (FMP 13F — 24h cache) ────────────────────────
    institutional_ownership_pct: Optional[float] = None     # % of float held by institutions
    institutional_ownership_change: Optional[float] = None  # QoQ change in percentage points

    # ── Price structure (Alpaca — session cache) ──────────────────────────────
    week52_high: Optional[float] = None             # 52-week high
    week52_high_distance_pct: Optional[float] = None  # (price - 52wk_high) / 52wk_high * 100
    stock_above_200d: Optional[bool] = None         # closing price > 200-day SMA (long-term trend)

    # ── Analyst grade breakdown (FMP — 30min cache) ───────────────────────────
    analyst_buy_count: Optional[int] = None         # number of buy/strong-buy ratings
    analyst_sell_count: Optional[int] = None        # number of sell/strong-sell ratings
    next_eps_estimate: Optional[float] = None       # consensus EPS estimate next quarter

    # ── Derived / metadata ────────────────────────────────────────────────────
    built_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data_quality: str = "unknown"                # "full"|"partial"|"minimal" — set by builder

    def to_dict(self) -> dict:
        """JSON-safe serialisation for trade record storage."""
        return {k: v for k, v in asdict(self).items()}


# ── Time-of-day window helper ─────────────────────────────────────────────────

def _time_window(dt: datetime) -> tuple[str, bool]:
    """
    Return (window_name, in_dead_window) for a given datetime (ET assumed).
    """
    h = dt.hour
    m = dt.minute

    if (h == 9 and m >= 30) or h == 10 or (h == 11 and m < 30):
        return "OPEN", False
    elif (h == 11 and m >= 30) or h == 12 or h == 13 or (h == 14 and m < 30):
        return "MIDDAY", True
    elif (h == 14 and m >= 30) or h == 15:
        return "PRIME_PM", False
    else:
        return "CLOSE", False


# ── Signal age helper ─────────────────────────────────────────────────────────

def _signal_age_minutes(signal_timestamp: datetime) -> float:
    """Minutes since the signal was generated."""
    now = datetime.now(UTC)
    sig_ts = signal_timestamp
    if sig_ts.tzinfo is None:
        sig_ts = sig_ts.replace(tzinfo=UTC)
    return max(0.0, (now - sig_ts).total_seconds() / 60.0)


# ── Main builder ──────────────────────────────────────────────────────────────


def build_context(
    symbol: str,
    direction: str,
    signal,                     # Signal dataclass from signal_types.py
    current_price: float,
    *,
    vwap: float | None = None,           # pass from signal engine if available
    rel_volume: float | None = None,     # pass from execution_agent if available
    catalyst_score: float | None = None, # pass from catalyst_engine if available
    catalyst_type: str | None = None,
    earnings_days_away: int | None = None,
    regime: str | None = None,
) -> TradeContext:
    """
    Build a TradeContext for a symbol+signal before entry classification.

    Fast path fields (Alpaca, streaming, signal engine) are assembled inline.
    Slow path fields (FMP fundamentals) are fetched with 24h cache — add < 50ms
    on cache hit, up to 1–2s on cache miss for a new symbol.

    Graceful: any fetch failure leaves the field as None.
    """
    ctx = TradeContext(
        symbol=symbol,
        direction=direction,
        current_price=current_price,
    )

    # ── 1. Signal age ─────────────────────────────────────────────────────────
    try:
        ctx.signal_age_minutes = _signal_age_minutes(signal.timestamp)
    except Exception:
        pass

    # ── 2. Intraday: VWAP ─────────────────────────────────────────────────────
    # Prefer caller-supplied vwap (from signal engine). Fallback: skip.
    if vwap is not None and vwap > 0 and current_price > 0:
        ctx.vwap = vwap
        ctx.vwap_distance_pct = round((current_price - vwap) / vwap * 100, 3)

    # ── 3. Intraday: HOD ─────────────────────────────────────────────────────
    try:
        from alpaca_data import get_intraday_hod
        hod = get_intraday_hod(symbol)
        if hod and hod > 0 and current_price > 0:
            ctx.hod = hod
            ctx.hod_distance_pct = round((current_price - hod) / hod * 100, 3)
    except Exception as exc:
        log.debug("build_context: HOD fetch failed for %s — %s", symbol, exc)

    # ── 4. Relative volume ────────────────────────────────────────────────────
    if rel_volume is not None:
        ctx.rel_volume = rel_volume

    # ── 5. Bid-ask spread ─────────────────────────────────────────────────────
    try:
        from alpaca_stream import SpreadTracker
        spread = SpreadTracker.instance().get_spread_pct(symbol)
        if spread is not None:
            ctx.bid_ask_spread_pct = spread
    except Exception:
        pass

    # ── 6. Time of day ────────────────────────────────────────────────────────
    try:
        import pytz
        et = pytz.timezone("America/New_York")
        now_et = datetime.now(UTC).astimezone(et)
        ctx.time_of_day_window, ctx.in_dead_window = _time_window(now_et)
    except Exception:
        pass

    # ── 7. Catalyst / event data ──────────────────────────────────────────────
    ctx.catalyst_score = catalyst_score
    ctx.catalyst_type  = catalyst_type or "none"
    ctx.earnings_days_away = earnings_days_away

    # Overnight drift: when the signal's overnight_drift dimension is dominant (≥6)
    # and no stronger catalyst is present, mark it so _validate_swing can qualify
    # it as a SWING candidate (EOD/post-close entries are SWING, never INTRADAY).
    try:
        _od = getattr(signal, "dimension_scores", {}).get("overnight_drift", 0)
        if _od >= 6 and ctx.catalyst_type in (None, "none"):
            ctx.catalyst_type = "overnight_drift"
    except Exception:
        pass
    ctx.regime = regime or (signal.regime_context if hasattr(signal, "regime_context") else None)

    # ── 8. FMP: analyst consensus + price target ──────────────────────────────
    try:
        from fmp_client import get_analyst_consensus, get_price_target, get_analyst_changes
        consensus = get_analyst_consensus(symbol)
        if consensus:
            ctx.analyst_consensus = consensus.get("consensus")
            ctx.analyst_pt        = consensus.get("target_consensus")
            if ctx.analyst_pt and current_price > 0:
                ctx.analyst_upside_pct = round(
                    (ctx.analyst_pt - current_price) / current_price * 100, 2
                )
            last_updated = consensus.get("last_updated") or ""
            if last_updated:
                try:
                    from datetime import date as _date
                    lu = datetime.fromisoformat(last_updated).date()
                    ctx.analyst_pt_days_old = (datetime.now(UTC).date() - lu).days
                except Exception:
                    pass

        # Recent upgrade / downgrade (last 10 days)
        changes = get_analyst_changes(symbols=[symbol], hours_back=240)  # 10 days
        for c in changes:
            action = c.get("action", "").lower()
            if "upgrade" in action or action == "init":
                ctx.recent_upgrade = True
            elif "downgrade" in action:
                ctx.recent_downgrade = True

    except Exception as exc:
        log.debug("build_context: FMP analyst fetch failed for %s — %s", symbol, exc)

    # ── 9. FMP: short interest ────────────────────────────────────────────────
    try:
        from fmp_client import get_short_interest
        si = get_short_interest(symbol)
        if si:
            ctx.short_float_pct = si.get("short_float_pct")
    except Exception as exc:
        log.debug("build_context: FMP short interest failed for %s — %s", symbol, exc)

    # ── 10. FMP: revenue growth + EPS acceleration ────────────────────────────
    try:
        from fmp_client import get_revenue_growth, get_eps_acceleration
        rev = get_revenue_growth(symbol)
        if rev:
            ctx.revenue_growth_yoy  = rev.get("revenue_growth_yoy")
            ctx.revenue_growth_qoq  = rev.get("revenue_growth_qoq")
            ctx.revenue_decelerating = rev.get("revenue_deceleration", False)

        eps = get_eps_acceleration(symbol)
        if eps:
            ctx.eps_accelerating = eps.get("eps_accelerating", False)
            ctx.eps_beat_rate    = eps.get("eps_beat_rate")
    except Exception as exc:
        log.debug("build_context: FMP fundamentals failed for %s — %s", symbol, exc)

    # ── 11. Sector ETF context ────────────────────────────────────────────────
    try:
        from fmp_client import get_company_sector
        from alpaca_data import get_sector_etf_context
        sector_etf = get_company_sector(symbol)
        if sector_etf:
            ctx.sector_etf = sector_etf
            etf_ctx = get_sector_etf_context(sector_etf)
            if etf_ctx:
                ctx.sector_above_50d          = etf_ctx.get("above_50d")
                ctx.sector_3m_vs_spy          = etf_ctx.get("return_3m_vs_spy")
                ctx.sector_days_since_breakout = etf_ctx.get("days_since_breakout")
    except Exception as exc:
        log.debug("build_context: sector ETF context failed for %s — %s", symbol, exc)

    # ── 12. FMP: analyst grade breakdown ─────────────────────────────────────
    try:
        from fmp_client import get_analyst_grades
        grades = get_analyst_grades(symbol)
        if grades:
            ctx.analyst_buy_count  = (grades.get("strong_buy") or 0) + (grades.get("buy") or 0)
            ctx.analyst_sell_count = (grades.get("sell") or 0) + (grades.get("strong_sell") or 0)
    except Exception as exc:
        log.debug("build_context: analyst grades failed for %s — %s", symbol, exc)

    # ── 13. FMP: analyst estimates ────────────────────────────────────────────
    try:
        from fmp_client import get_analyst_estimates
        est = get_analyst_estimates(symbol)
        if est:
            ctx.next_eps_estimate = est.get("next_eps_estimate")
    except Exception as exc:
        log.debug("build_context: analyst estimates failed for %s — %s", symbol, exc)

    # ── 14. FMP: insider sentiment ────────────────────────────────────────────
    try:
        from fmp_client import get_insider_sentiment
        insider = get_insider_sentiment(symbol, days=90)
        if insider:
            ctx.insider_net_sentiment = insider.get("net_sentiment")
            net_val = insider.get("net_value_usd")
            if net_val is not None:
                ctx.insider_buy_value_3m = round(net_val / 1e6, 2)  # convert to $M
    except Exception as exc:
        log.debug("build_context: insider sentiment failed for %s — %s", symbol, exc)

    # ── 15. FMP: congressional trading ────────────────────────────────────────
    try:
        from fmp_client import get_congressional_trades
        congress = get_congressional_trades(symbol, days=90)
        if congress:
            ctx.congressional_sentiment = congress.get("net_sentiment")
    except Exception as exc:
        log.debug("build_context: congressional trades failed for %s — %s", symbol, exc)

    # ── 16. FMP: key metrics + DCF ────────────────────────────────────────────
    try:
        from fmp_client import get_key_metrics_ttm, get_dcf_value
        metrics = get_key_metrics_ttm(symbol)
        if metrics:
            ctx.gross_margin  = metrics.get("gross_margin")
            ctx.net_margin    = metrics.get("net_margin")
            ctx.is_profitable = (ctx.net_margin > 0) if ctx.net_margin is not None else None
            ctx.fcf_yield     = metrics.get("fcf_yield")
            ctx.pe_ratio      = metrics.get("pe_ratio")

        dcf = get_dcf_value(symbol)
        if dcf:
            ctx.dcf_upside_pct = dcf.get("upside_pct")
    except Exception as exc:
        log.debug("build_context: key metrics/DCF failed for %s — %s", symbol, exc)

    # ── 17. FMP: institutional ownership ─────────────────────────────────────
    try:
        from fmp_client import get_institutional_ownership
        inst = get_institutional_ownership(symbol)
        if inst:
            ctx.institutional_ownership_pct    = inst.get("ownership_pct")
            ctx.institutional_ownership_change = inst.get("ownership_change")
    except Exception as exc:
        log.debug("build_context: institutional ownership failed for %s — %s", symbol, exc)

    # ── 18. Alpaca: 52-week high ──────────────────────────────────────────────
    try:
        from alpaca_data import get_52wk_high
        w52h = get_52wk_high(symbol)
        if w52h and w52h > 0 and current_price > 0:
            ctx.week52_high = w52h
            ctx.week52_high_distance_pct = round((current_price - w52h) / w52h * 100, 3)
    except Exception as exc:
        log.debug("build_context: 52wk high failed for %s — %s", symbol, exc)

    # ── 19. Alpaca: 200-day MA (long-term trend for POSITION trades) ─────────
    try:
        from alpaca_data import get_stock_above_200d
        ctx.stock_above_200d = get_stock_above_200d(symbol)
    except Exception as exc:
        log.debug("build_context: 200d MA failed for %s — %s", symbol, exc)

    # ── 20. Data quality assessment ───────────────────────────────────────────
    critical_fields = [
        ctx.signal_age_minutes,
        ctx.vwap_distance_pct,
        ctx.rel_volume,
        ctx.hod_distance_pct,
    ]
    fundamental_fields = [
        ctx.analyst_consensus,
        ctx.revenue_growth_yoy,
        ctx.sector_above_50d,
        ctx.insider_net_sentiment,
        ctx.gross_margin,
    ]
    n_critical = sum(1 for f in critical_fields if f is not None)
    n_fundamental = sum(1 for f in fundamental_fields if f is not None)

    if n_critical >= 3 and n_fundamental >= 3:
        ctx.data_quality = "full"
    elif n_critical >= 2:
        ctx.data_quality = "partial"
    else:
        ctx.data_quality = "minimal"

    log.debug(
        "build_context: %s %s | quality=%s | age=%.1fmin hod_dist=%.2f%% vwap_dist=%.2f%% "
        "rel_vol=%.1fx | consensus=%s pt_upside=%.1f%%",
        symbol, direction,
        ctx.data_quality,
        ctx.signal_age_minutes or -1,
        ctx.hod_distance_pct or 0,
        ctx.vwap_distance_pct or 0,
        ctx.rel_volume or 0,
        ctx.analyst_consensus or "n/a",
        ctx.analyst_upside_pct or 0,
    )

    return ctx
