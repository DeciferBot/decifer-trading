# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  risk.py                                    ║
# ║   Five-layer risk management — hardcoded, agent-proof        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple
import pytz
import pandas_market_calendars as mcal
from config import CONFIG

log = logging.getLogger("decifer.risk")

EST = pytz.timezone("America/New_York")

# ── Loss-tracking state ────────────────────────────────────────
_consecutive_losses    = 0
_pause_until           = None
_daily_loss_hit        = False
_session_start_value   = None
_current_strategy_mode = "NORMAL"

# ── Drawdown-from-peak tracking ────────────────────────────────
_equity_high_water_mark = None
_drawdown_halt          = False
_last_known_equity      = None

# ── HWM state file — survives equity_history truncation ────────
_BASE          = os.path.dirname(os.path.abspath(__file__))
HWM_STATE_FILE = os.path.join(_BASE, "data", "hwm_state.json")

# ── Intraday adaptive strategy state ───────────────────────────
_session_opening_regime: Optional[str] = None  # Regime at session open (set on first scan)
_session_regime_set:     bool       = False  # Guard: only set once per trading day
_strategy_size_multiplier: float    = 1.0   # Applied inside calculate_position_size()

# ── VIX-rank Kelly state ────────────────────────────────────────
_vix_rank_cache:      Optional[float]    = None
_vix_rank_cache_ts:   Optional[datetime] = None
_last_vix_rank:       float           = 0.5  # Latest computed rank (for dashboard)
_last_kelly_fraction: float           = 0.5  # Latest computed fraction (for dashboard)

# ── Sub-module re-exports ──────────────────────────────────────
# pdt_rule: Pattern Day Trader enforcement (stateless — safe to extract)
from pdt_rule import (
    _count_day_trades_remaining_local,
    _get_day_trades_remaining,
)

# position_sizing: Pure stop/sizing utilities (stateless — safe to extract)
from position_sizing import (
    calculate_stops,
    position_size,
    get_short_size_multiplier,
)


def reset_daily_state(portfolio_value: float):
    """Call at the start of each trading day."""
    global _consecutive_losses, _pause_until, _daily_loss_hit, _session_start_value
    global _equity_high_water_mark
    global _session_opening_regime, _session_regime_set, _strategy_size_multiplier
    _consecutive_losses  = 0
    _pause_until         = None
    _daily_loss_hit      = False
    _session_start_value = portfolio_value
    # Initialize HWM if not yet set (don't reset to current — preserve peak across days)
    if _equity_high_water_mark is None:
        _equity_high_water_mark = portfolio_value
    # Reset intraday adaptive strategy state for new day
    _session_opening_regime   = None
    _session_regime_set       = False
    _strategy_size_multiplier = 1.0
    log.info(f"Daily risk state reset. Session start value: ${portfolio_value:,.2f} | HWM: ${_equity_high_water_mark:,.2f}")


def record_loss(source: str = "bot"):
    """
    Record a losing trade — may trigger consecutive loss pause.
    source: "bot" = from bot-initiated trade, "external" = from stop loss / TP hit
    External closes do NOT extend the pause — they're not new bad decisions.
    """
    global _consecutive_losses, _pause_until
    if source == "external" and _pause_until is not None:
        log.info(f"External close loss recorded — not extending existing pause")
        return
    _consecutive_losses += 1
    if _consecutive_losses >= CONFIG["consecutive_loss_pause"]:
        from datetime import timedelta
        if _pause_until is None or datetime.now(EST) >= _pause_until:
            _pause_until = datetime.now(EST) + timedelta(hours=2)
            log.warning(f"⚠️  {_consecutive_losses} consecutive losses — pausing until {_pause_until.strftime('%H:%M')}")
        else:
            log.info(f"Loss #{_consecutive_losses} — pause already active until {_pause_until.strftime('%H:%M')}, not extending")


def record_win():
    """Reset consecutive loss counter on a win."""
    global _consecutive_losses
    _consecutive_losses = 0


def _get_ibkr_cash(ib, account: str) -> Optional[float]:
    """
    Get actual cash from IBKR account.
    Uses TotalCashValue — the real USD cash in the account.
    """
    if ib is None:
        return None
    try:
        vals = ib.accountValues(account)
        for v in vals:
            if v.tag == "TotalCashValue" and v.currency == "USD":
                return float(v.value)
    except Exception as e:
        log.warning(f"Could not fetch IBKR cash: {e}")
    return None


def load_hwm_state() -> Optional[float]:
    """
    Load the persisted all-time HWM from data/hwm_state.json.
    Returns the stored float, or None if the file is missing or corrupt.
    """
    try:
        if not os.path.exists(HWM_STATE_FILE):
            return None
        with open(HWM_STATE_FILE) as f:
            data = json.load(f)
        val = data.get("hwm")
        return float(val) if val is not None else None
    except Exception as e:
        log.warning(f"load_hwm_state: could not read {HWM_STATE_FILE} — {e}")
        return None


def save_hwm_state(hwm: float) -> None:
    """
    Persist the all-time HWM to data/hwm_state.json so it survives
    equity_history truncation and bot restarts.
    """
    try:
        os.makedirs(os.path.dirname(HWM_STATE_FILE), exist_ok=True)
        with open(HWM_STATE_FILE, "w") as f:
            json.dump({"hwm": hwm, "updated": datetime.now(timezone.utc).isoformat()}, f)
    except Exception as e:
        log.error(f"save_hwm_state: failed to write {HWM_STATE_FILE} — {e}")


def check_risk_conditions(portfolio_value: float, daily_pnl: float, regime: dict,
              open_positions: list = None, ib=None) -> Tuple[bool, str]:
    """
    Master check — can we take new trades right now?
    Returns (bool, reason_if_not).
    This is hardcoded. No agent can override this.
    """
    global _daily_loss_hit, _pause_until

    now_est = datetime.now(EST)
    now_time = now_est.time()

    # ── Layer 6: Drawdown from peak circuit breaker ───────────
    dd_ok, dd_reason = check_drawdown()
    if not dd_ok:
        return False, dd_reason

    # ── Layer 5: Panic regime ─────────────────────────────────
    if regime.get("regime") == "CAPITULATION":
        return False, "CAPITULATION regime — VIX too high or spiking. No new trades."

    # ── Layer 5.5: PDT Rule (Pattern Day Trader) ──────────────
    pdt_cfg = CONFIG.get("pdt", {})
    if pdt_cfg.get("enabled", True) and portfolio_value > 0:
        pdt_threshold  = pdt_cfg.get("threshold", 25_000)
        active_account = CONFIG.get("active_account", "")
        paper_account  = CONFIG.get("accounts", {}).get("paper", "")
        is_live = active_account != "" and active_account != paper_account
        if is_live and portfolio_value < pdt_threshold:
            remaining = _get_day_trades_remaining(ib, active_account)
            if remaining is not None and remaining <= 0:
                return False, (
                    f"PDT rule: 0 day trades remaining "
                    f"(account ${portfolio_value:,.0f} < ${pdt_threshold:,.0f} threshold). "
                    f"New entries blocked until next trading day."
                )

    # ── Layer 3: Daily loss limit ─────────────────────────────
    daily_loss_limit = portfolio_value * CONFIG["daily_loss_limit"]
    if portfolio_value > 0 and daily_pnl < 0 and daily_pnl <= -daily_loss_limit:
        _daily_loss_hit = True
        return False, f"Daily loss limit hit (${daily_pnl:,.2f}). Bot halted for today."

    # ── Layer 3: Consecutive loss pause ──────────────────────
    if _pause_until and datetime.now(EST) < _pause_until:
        return False, f"Consecutive loss pause active until {_pause_until.strftime('%H:%M')} EST"
    elif _pause_until and datetime.now(EST) >= _pause_until:
        _pause_until = None

    # ── Layer 4: Min cash reserve ────────────────────────────
    if open_positions and portfolio_value > 0:
        ibkr_cash = _get_ibkr_cash(ib, CONFIG.get("active_account", ""))
        if ibkr_cash is not None:
            cash_pct = ibkr_cash / portfolio_value
        else:
            deployed = sum(p.get("current", p.get("entry", 0)) * p.get("qty", 0) for p in open_positions)
            cash_pct = (portfolio_value - deployed) / portfolio_value
        if cash_pct < CONFIG["min_cash_reserve"]:
            return False, f"Cash reserve too low ({cash_pct*100:.1f}% < {CONFIG['min_cash_reserve']*100:.0f}% min). Preserve capital."

    # ── Layer 3: Market hours ─────────────────────────────────
    market_open  = time(9, 30)
    prime_start  = time(9, 32)
    close_buffer = time(15, 59)
    market_close = time(16, 0)
    pre_start    = time(4, 0)
    after_end    = time(20, 0)

    if now_time < pre_start or now_time > after_end:
        return False, "Overnight (8pm-4am) — no liquidity, monitoring only"
    if market_open <= now_time < prime_start:
        return False, "Opening auction settling — 2 minute buffer"
    if close_buffer <= now_time <= market_close:
        return False, "Final minute before close — MOC imbalance, avoid"

    return True, "OK"


# ── Simple can_trade() helpers (mockable in tests) ─────────────────────────

def _get_daily_pnl() -> float:
    """Return today's realized + unrealized P&L. Override in production."""
    return 0.0


def _get_portfolio_value() -> float:
    """Return current portfolio value for risk calculations. Override in tests."""
    return _session_start_value or 0.0


def _get_open_position_count() -> int:
    """Return current number of open positions."""
    return 0


def is_trading_day(date=None) -> bool:
    """Return True if the NYSE is open on *date* (defaults to today ET)."""
    if date is None:
        date = datetime.now(EST).date()
    cal = mcal.get_calendar("NYSE")
    days = cal.valid_days(start_date=date, end_date=date)
    return len(days) > 0


def _is_market_open() -> bool:
    """Return True if trading is currently permitted (pre-market to after-hours, trading days only)."""
    if not is_trading_day():
        return False
    now_est = datetime.now(EST).time()
    pre_start = time(4, 0)
    after_end = time(20, 0)
    return pre_start <= now_est <= after_end


def _get_correlation(symbol: str) -> float:
    """Return max correlation of *symbol* to any existing position (0.0 = none)."""
    return 0.0


def can_trade(symbol: str, config: dict) -> bool:
    """
    Simple risk gate: returns True if a new trade on *symbol* is permitted.
    """
    daily_pnl       = _get_daily_pnl()
    portfolio_value = _get_portfolio_value()
    max_loss_pct    = config.get("max_daily_loss_pct", CONFIG.get("max_daily_loss_pct", 0.05))
    if portfolio_value > 0 and daily_pnl <= -(portfolio_value * max_loss_pct):
        log.debug(f"can_trade blocked: daily P&L ${daily_pnl:,.2f} <= -{max_loss_pct:.0%} of ${portfolio_value:,.2f}")
        return False

    position_count = _get_open_position_count()
    max_pos = config.get("max_positions", CONFIG.get("max_positions", 10))
    if position_count >= max_pos:
        log.debug(f"can_trade blocked: {position_count} positions >= max {max_pos}")
        return False

    if not _is_market_open():
        log.debug("can_trade blocked: market closed")
        return False

    corr = _get_correlation(symbol)
    threshold = config.get("correlation_threshold", CONFIG.get("correlation_threshold", 0.75))
    if corr >= threshold:
        log.debug(f"can_trade blocked: correlation {corr:.2f} >= threshold {threshold:.2f}")
        return False

    return True


def get_session() -> str:
    """Return current trading session name."""
    if not is_trading_day():
        now_et = datetime.now(EST)
        return "WEEKEND" if now_et.weekday() >= 5 else "CLOSED"
    now_est  = datetime.now(EST).time()
    pre      = time(4, 0)
    open_    = time(9, 30)
    prime    = time(9, 45)
    lunch    = time(11, 30)
    after_pm = time(14, 0)
    close_b  = time(15, 55)
    close    = time(16, 0)
    after    = time(20, 0)

    if now_est < pre:          return "CLOSED"
    elif now_est < open_:      return "PRE_MARKET"
    elif now_est < prime:      return "OPEN_BUFFER"
    elif now_est < lunch:      return "PRIME_AM"
    elif now_est < after_pm:   return "LUNCH"
    elif now_est < close_b:    return "PRIME_PM"
    elif now_est < close:      return "CLOSE_BUFFER"
    elif now_est < after:      return "AFTER_HOURS"
    else:                      return "CLOSED"


def get_scan_interval() -> int:
    """Return appropriate scan interval in seconds for current session."""
    session = get_session()
    intervals = {
        "PRE_MARKET":   CONFIG["scan_interval_pre_market"]   * 60,
        "OPEN_BUFFER":  CONFIG["scan_interval_extended"]     * 60,
        "PRIME_AM":     CONFIG["scan_interval_prime"]        * 60,
        "LUNCH":        CONFIG["scan_interval_standard"]     * 60,
        "PRIME_PM":     CONFIG["scan_interval_prime"]        * 60,
        "CLOSE_BUFFER": CONFIG["scan_interval_extended"]     * 60,
        "AFTER_HOURS":  CONFIG["scan_interval_after_hours"]  * 60,
        "CLOSED":       CONFIG["scan_interval_overnight"]    * 60,
        "WEEKEND":      CONFIG["scan_interval_overnight"]    * 60,
    }
    return intervals.get(session, CONFIG["scan_interval_standard"] * 60)


# ══════════════════════════════════════════════════════════════════
# VIX-RANK ADAPTIVE KELLY FRACTION
# ══════════════════════════════════════════════════════════════════

def get_vix_rank(vix_override: Optional[float] = None) -> float:
    """
    Returns the percentile (0.0–1.0) of the current ^VIX reading within
    its trailing 252-day range. Caches the result for cache_ttl_seconds.
    Falls back to 0.5 (neutral) on any data error.
    """
    global _vix_rank_cache, _vix_rank_cache_ts

    ttl      = CONFIG["vix_kelly"]["cache_ttl_seconds"]
    lookback = CONFIG["vix_kelly"]["vix_lookback_days"]
    now      = datetime.now(timezone.utc)

    if (vix_override is None
            and _vix_rank_cache is not None
            and _vix_rank_cache_ts is not None
            and (now - _vix_rank_cache_ts).total_seconds() < ttl):
        return _vix_rank_cache

    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTE
        def _fetch():
            import yfinance as yf
            return yf.Ticker("^VIX").history(period=f"{lookback + 15}d")["Close"].dropna()
        with ThreadPoolExecutor(max_workers=1) as _pool:
            try:
                hist = _pool.submit(_fetch).result(timeout=10)
            except _FTE:
                log.warning("get_vix_rank: ^VIX fetch timed out (10s) — defaulting to 0.5")
                return 0.5
        if len(hist) < 20:
            log.warning("get_vix_rank: insufficient VIX history — defaulting to 0.5")
            return 0.5
        hist = hist.iloc[-lookback:]
        current = float(vix_override if vix_override is not None else hist.iloc[-1])
        rank    = float((hist < current).sum()) / len(hist)
        if vix_override is None:
            _vix_rank_cache    = rank
            _vix_rank_cache_ts = now
        log.debug(f"VIX rank: VIX={current:.2f} rank={rank:.3f} window={len(hist)}d")
        return rank
    except Exception as exc:
        log.warning(f"get_vix_rank: failed to fetch ^VIX — {exc}. Defaulting to 0.5")
        return 0.5


def get_kelly_fraction(vix_rank_override: Optional[float] = None) -> Tuple[float, float]:
    """
    Returns (kelly_fraction, vix_rank).
    Formula: kelly = base_kelly * (1 - vix_rank * max_reduction)
    """
    global _last_vix_rank, _last_kelly_fraction
    cfg      = CONFIG["vix_kelly"]
    vix_rank = get_vix_rank(vix_rank_override)
    kelly    = cfg["base_kelly"] * (1.0 - vix_rank * cfg["max_reduction"])
    kelly    = max(0.05, min(1.0, kelly))
    _last_vix_rank       = vix_rank
    _last_kelly_fraction = kelly
    return kelly, vix_rank


def get_drawdown_scalar(equity_override: Optional[float] = None) -> float:
    """
    Returns a [min_scalar, 1.0] multiplier that decays linearly as drawdown
    from the equity high-water mark increases.
    Returns 1.0 when HWM is not yet initialized or scaler is disabled.
    """
    cfg = CONFIG.get("drawdown_scaler", {})
    if not cfg.get("enabled", True):
        return 1.0

    hwm = _equity_high_water_mark
    if hwm is None or hwm <= 0:
        return 1.0

    equity = equity_override if equity_override is not None else _last_known_equity
    if equity is None:
        return 1.0

    drawdown   = max(0.0, (hwm - equity) / hwm)
    max_dd     = CONFIG.get("max_drawdown_alert", 0.25)
    min_scalar = cfg.get("min_scalar", 0.1)

    if max_dd <= 0:
        return 1.0

    t      = min(drawdown / max_dd, 1.0)
    scalar = 1.0 - t * (1.0 - min_scalar)
    return round(scalar, 6)


def get_sizing_state() -> dict:
    """Returns current VIX rank, Kelly fraction, and drawdown scalar for dashboard injection."""
    return {
        "vix_rank":        round(_last_vix_rank, 3),
        "kelly_fraction":  round(_last_kelly_fraction, 3),
        "drawdown_scalar": round(get_drawdown_scalar(), 6),
    }


def calculate_position_size(portfolio_value: float, price: float,
                             score: int, regime: dict,
                             atr: float = 0.0,
                             external_mult: float = 1.0) -> int:
    """
    VIX-rank adaptive Kelly position sizing with ATR volatility cap.
    Returns number of shares to buy.
    """
    external_mult = max(0.1, min(external_mult, 1.0))

    kelly_frac, vix_rank = get_kelly_fraction()

    base_risk = portfolio_value * CONFIG["risk_pct_per_trade"] * kelly_frac

    _sk_cfg   = CONFIG.get("signal_strength_kelly", {})
    _sk_floor = _sk_cfg.get("score_floor", 20)
    _sk_ceil  = _sk_cfg.get("score_ceil",  50)
    _sk_min   = _sk_cfg.get("min_mult",    0.5)
    _sk_max   = _sk_cfg.get("max_mult",    1.5)
    _sk_range = _sk_ceil - _sk_floor
    if _sk_range > 0:
        _sk_t = max(0.0, min(1.0, (score - _sk_floor) / _sk_range))
    else:
        _sk_t = 1.0
    conviction_mult = _sk_min + _sk_t * (_sk_max - _sk_min)

    regime_mult = regime.get("position_size_multiplier", 0.5)

    session = get_session()
    if session in ("PRE_MARKET", "AFTER_HOURS"):
        session_mult = 0.75
    else:
        session_mult = 1.0

    risk_amount = (base_risk * conviction_mult * regime_mult * session_mult
                   * _strategy_size_multiplier * external_mult)

    drawdown_scalar = get_drawdown_scalar()
    risk_amount     = risk_amount * drawdown_scalar

    if atr > 0:
        stop_dollars = atr * CONFIG["atr_stop_multiplier"]
        qty = max(1, int(risk_amount / stop_dollars))
    else:
        assumed_stop = price * CONFIG["assumed_stop_pct"]
        qty = max(1, int(risk_amount / assumed_stop))

    max_pos_qty = max(1, int(portfolio_value * CONFIG["max_single_position"] / price))
    qty = min(qty, max_pos_qty)

    atr_capped_qty: Optional[int] = None
    if CONFIG.get("atr_vol_cap_enabled") and atr > 0:
        atr_target     = portfolio_value * CONFIG["atr_vol_target_pct"]
        atr_capped_qty = max(1, int(atr_target / atr))
        if atr_capped_qty < qty:
            qty = atr_capped_qty

    order_value = qty * price
    hard_cap    = portfolio_value * 0.20
    if order_value > hard_cap and qty > 1:
        qty = max(1, int(hard_cap / price))
        log.warning(f"Position size hard cap triggered: {order_value:,.0f} > {hard_cap:,.0f}, reduced to {qty} shares")

    log.info(
        f"[sizing] vix_rank={vix_rank:.2f} kelly={kelly_frac:.3f} "
        f"conviction_mult={conviction_mult:.3f} score={score} "
        f"drawdown_scalar={drawdown_scalar:.3f} "
        f"atr_cap={atr_capped_qty if atr_capped_qty is not None else 'N/A'} "
        f"final_qty={qty}"
    )
    return qty


# ══════════════════════════════════════════════════════════════════
# FIX #4: Max drawdown from peak — circuit breaker
# ══════════════════════════════════════════════════════════════════

def update_equity_high_water_mark(current_equity: float) -> bool:
    """
    Call on each scan cycle with current portfolio value.
    Returns True only when the halt is NEWLY triggered (first breach).
    """
    global _equity_high_water_mark, _drawdown_halt, _last_known_equity

    _last_known_equity = current_equity

    if _equity_high_water_mark is None:
        _equity_high_water_mark = current_equity
        save_hwm_state(_equity_high_water_mark)
        return False

    if current_equity > _equity_high_water_mark:
        _equity_high_water_mark = current_equity
        save_hwm_state(_equity_high_water_mark)
        if _drawdown_halt:
            _drawdown_halt = False
            log.info(f"Drawdown halt cleared — equity recovered to new high: ${current_equity:,.2f}")
        return False

    if _equity_high_water_mark > 0:
        drawdown = (_equity_high_water_mark - current_equity) / _equity_high_water_mark
        max_dd = CONFIG.get("max_drawdown_alert", 0.25)

        if drawdown >= max_dd and not _drawdown_halt:
            _drawdown_halt = True
            log.warning(
                f"⛔ DRAWDOWN CIRCUIT BREAKER: {drawdown:.1%} drawdown from peak "
                f"${_equity_high_water_mark:,.2f} → ${current_equity:,.2f}. "
                f"Limit: {max_dd:.0%}. Flattening all positions."
            )
            return True

    return False


def check_drawdown() -> Tuple[bool, str]:
    """Returns (ok_to_trade, reason). Called from check_risk_conditions()."""
    if _drawdown_halt:
        drawdown = 0.0
        if _equity_high_water_mark and _equity_high_water_mark > 0 and _last_known_equity is not None:
            drawdown = (_equity_high_water_mark - _last_known_equity) / _equity_high_water_mark
        return False, (
            f"Drawdown circuit breaker active — "
            f"{drawdown:.1%} drawdown from peak ${_equity_high_water_mark:,.2f}. "
            f"No new trades until equity recovers."
        )
    return True, "OK"


def reset_drawdown_state(portfolio_value: float):
    """Reset drawdown tracking (e.g. at start of new session or manual override)."""
    global _equity_high_water_mark, _drawdown_halt
    _equity_high_water_mark = portfolio_value
    _drawdown_halt = False
    log.info(f"Drawdown state reset. High water mark: ${portfolio_value:,.2f}")


def init_equity_high_water_mark_from_history(equity_history: list):
    """
    Seed the in-memory HWM from two sources on bot startup:
      1. data/hwm_state.json — persisted all-time peak (truncation-immune)
      2. equity_history list — truncated last-2000 entries
    Takes the maximum of all sources.
    """
    global _equity_high_water_mark

    persisted_hwm = load_hwm_state()
    if persisted_hwm is not None:
        if _equity_high_water_mark is None or persisted_hwm > _equity_high_water_mark:
            _equity_high_water_mark = persisted_hwm
            log.info(f"HWM seeded from hwm_state.json: ${persisted_hwm:,.2f}")

    if not equity_history:
        return
    try:
        historical_peak = max(r["value"] for r in equity_history if "value" in r)
    except (ValueError, TypeError) as e:
        log.warning(f"init_equity_high_water_mark_from_history: could not parse history — {e}")
        return
    if _equity_high_water_mark is None or historical_peak > _equity_high_water_mark:
        _equity_high_water_mark = historical_peak
        log.info(f"HWM upgraded from equity history: ${historical_peak:,.2f}")


# ══════════════════════════════════════════════════════════════════
# FIX #1: Cross-instrument exposure check (stock + options same underlying)
# ══════════════════════════════════════════════════════════════════

def get_underlying_exposure(symbol: str, open_positions: list) -> dict:
    """Calculate total exposure to a single underlying across stocks AND options."""
    stock_value = 0.0
    option_value = 0.0

    for pos in open_positions:
        pos_sym = pos.get("symbol", "")
        if pos_sym != symbol:
            continue
        instrument = pos.get("instrument", "stock")
        qty = pos.get("qty", 0)
        if instrument == "option":
            delta = abs(pos.get("delta", 0.5))
            underlying = pos.get("underlying_price", pos.get("current", 0))
            option_value += qty * 100 * underlying * delta
        else:
            price = pos.get("current", pos.get("entry", 0))
            stock_value += abs(qty * price)

    return {
        "symbol": symbol,
        "stock_exposure": stock_value,
        "option_exposure": option_value,
        "total_exposure": stock_value + option_value,
    }


def check_combined_exposure(symbol: str, new_exposure_value: float,
                            open_positions: list, portfolio_value: float,
                            instrument: str = "stock") -> Tuple[bool, str]:
    """
    FIX #1 + #3: Check whether adding a new position would create
    excessive combined exposure to the same underlying.
    Returns (ok_to_trade, reason).
    """
    if portfolio_value <= 0:
        return True, "OK"

    max_single = CONFIG.get("max_single_position", 0.10)
    max_alloc = CONFIG.get("max_portfolio_allocation", 1.0)

    existing = get_underlying_exposure(symbol, open_positions)

    if existing["total_exposure"] > 0:
        combined = existing["total_exposure"] + new_exposure_value
        combined_pct = combined / portfolio_value

        has_stock = existing["stock_exposure"] > 0
        has_option = existing["option_exposure"] > 0
        adding_stock = instrument == "stock"
        adding_option = instrument == "option"
        cross_instrument = (has_stock and adding_option) or (has_option and adding_stock)

        if cross_instrument and combined_pct > max_single:
            return False, (
                f"Cross-instrument block: {symbol} already has "
                f"${existing['total_exposure']:,.0f} exposure "
                f"({'stock+option' if has_stock and has_option else 'stock' if has_stock else 'option'}). "
                f"Adding ${new_exposure_value:,.0f} would be {combined_pct:.1%} of portfolio "
                f"(limit: {max_single:.0%})"
            )

    total_deployed = 0.0
    for pos in open_positions:
        inst = pos.get("instrument", "stock")
        qty = pos.get("qty", 0)
        if inst == "option":
            premium = pos.get("entry_premium", pos.get("entry", 0))
            total_deployed += qty * premium * 100
        else:
            price = pos.get("current", pos.get("entry", 0))
            total_deployed += abs(qty * price)

    new_total = total_deployed + new_exposure_value
    alloc_pct = new_total / portfolio_value

    if alloc_pct > max_alloc:
        return False, (
            f"Portfolio allocation limit: ${total_deployed:,.0f} deployed + "
            f"${new_exposure_value:,.0f} new = {alloc_pct:.1%} "
            f"(limit: {max_alloc:.0%})"
        )

    return True, "OK"


# ══════════════════════════════════════════════════════════════════
# FIX #2: Sector concentration enforcement
# ══════════════════════════════════════════════════════════════════

_sector_monitor = None


def _get_sector_monitor():
    """Lazy-initialize the SectorMonitor from portfolio_optimizer."""
    global _sector_monitor
    if _sector_monitor is None:
        try:
            from portfolio_optimizer import SectorMonitor
            _sector_monitor = SectorMonitor()
            log.info("SectorMonitor initialized for sector concentration enforcement")
        except ImportError as e:
            log.warning(f"Could not import SectorMonitor: {e} — sector check disabled")
    return _sector_monitor


def check_sector_concentration(new_symbol: str, open_positions: list,
                                portfolio_value: float,
                                regime: str = "NORMAL") -> Tuple[bool, str]:
    """
    FIX #2: Check if adding new_symbol would breach sector concentration limits.
    Returns (ok_to_trade, reason).
    """
    monitor = _get_sector_monitor()
    if monitor is None:
        return True, "OK (sector monitor unavailable)"

    if portfolio_value <= 0 or not open_positions:
        return True, "OK"

    max_sector = CONFIG.get("max_sector_exposure", 0.50)

    try:
        portfolio_dict = {}
        for pos in open_positions:
            sym = pos.get("symbol", "")
            qty = pos.get("qty", 0)
            price = pos.get("current", pos.get("entry", 0))
            if sym and qty > 0 and price > 0:
                if sym in portfolio_dict:
                    existing_qty, existing_price = portfolio_dict[sym]
                    total_value = existing_qty * existing_price + qty * price
                    total_qty = existing_qty + qty
                    portfolio_dict[sym] = (total_qty, total_value / total_qty if total_qty else price)
                else:
                    portfolio_dict[sym] = (qty, price)

        new_sector = monitor.get_sector(new_symbol)
        sector_weights = monitor.calculate_sector_weights(portfolio_dict)
        current_sector_pct = sector_weights.get(new_sector, 0.0)

        # Single limit for all regimes; circuit breaker tighter.
        # Regime-label-based limits removed — Opus handles what to trade.
        if regime in ("PANIC", "EXTREME_STRESS"):
            limit = min(max_sector, 0.15)
        else:
            limit = min(max_sector, 0.30)

        if current_sector_pct >= limit:
            return False, (
                f"Sector concentration block: {new_sector} already at {current_sector_pct:.1%} "
                f"(limit: {limit:.0%} in {regime} regime). Cannot add {new_symbol}."
            )

        if current_sector_pct >= limit * 0.8:
            log.warning(
                f"Sector warning: {new_sector} at {current_sector_pct:.1%}, "
                f"approaching {limit:.0%} limit. Allowing {new_symbol} but watch closely."
            )

    except Exception as e:
        log.warning(f"Sector concentration check failed for {new_symbol}: {e} — allowing trade")
        return True, "OK (sector check error, allowing)"

    return True, "OK"


def check_correlation(new_symbol: str, open_positions: list) -> Tuple[bool, str]:
    """Basic correlation check — avoid highly correlated positions."""
    tech    = {"AAPL", "NVDA", "MSFT", "META", "GOOGL", "AMD", "INTC", "ORCL"}
    semis   = {"NVDA", "AMD", "MU", "INTC", "AMAT", "LRCX", "KLAC", "QCOM"}
    indices = {"SPY", "QQQ", "IWM", "DIA"}

    open_syms = {p["symbol"] for p in open_positions}

    if new_symbol in indices and len(open_syms & indices) >= 1:
        return False, f"Already hold a broad index ETF — {new_symbol} too correlated"

    if new_symbol in tech:
        tech_count = len(open_syms & tech)
        if tech_count >= 2:
            return False, f"Already hold {tech_count} tech stocks — too concentrated"

    if new_symbol in semis:
        semi_count = len(open_syms & semis)
        if semi_count >= 2:
            return False, f"Already hold {semi_count} semiconductor stocks — too concentrated"

    return True, "OK"


# ══════════════════════════════════════════════════════════════════
# INTRADAY ADAPTIVE STRATEGY
# ══════════════════════════════════════════════════════════════════

def get_consecutive_losses() -> int:
    """Return current consecutive loss count (for logging in bot.py)."""
    return _consecutive_losses


def get_strategy_mode() -> str:
    """Return current intraday strategy mode (NORMAL/DEFENSIVE/RECOVERY)."""
    return _current_strategy_mode


def get_pause_until() -> Optional[str]:
    """Return pause-until time as HH:MM string, or None if not paused."""
    if _pause_until and datetime.now(EST) < _pause_until:
        return _pause_until.strftime("%H:%M")
    return None


def set_session_opening_regime(regime_name: str) -> None:
    """
    Record the regime at session open.
    Idempotent — only captures once per trading day.
    """
    global _session_opening_regime, _session_regime_set
    if not _session_regime_set and regime_name:
        _session_opening_regime = regime_name
        _session_regime_set     = True
        log.info(f"Session opening regime recorded: {regime_name}")


_SIGNIFICANT_REGIME_CHANGES = {
    ("TRENDING_UP",   "TRENDING_DOWN"),
    ("TRENDING_UP",   "RELIEF_RALLY"),
    ("TRENDING_UP",   "CAPITULATION"),
    ("TRENDING_DOWN", "TRENDING_UP"),
    ("TRENDING_DOWN", "CAPITULATION"),
    ("RANGE_BOUND",   "CAPITULATION"),
    ("RELIEF_RALLY",  "CAPITULATION"),
}


def get_regime_changed(current_regime: str) -> bool:
    """Returns True if the market regime has changed significantly since session open."""
    if _session_opening_regime is None:
        return False
    return (_session_opening_regime, current_regime) in _SIGNIFICANT_REGIME_CHANGES


def get_intraday_strategy_mode(portfolio_value: float,
                                daily_pnl: float,
                                current_regime: str) -> dict:
    """
    Compute the current intraday strategy mode from PnL, loss streak, and regime change.
    Modes: NORMAL / DEFENSIVE / RECOVERY.
    """
    global _strategy_size_multiplier, _current_strategy_mode

    _MODE_PARAMS = {
        "NORMAL":    {"score_threshold_adj": 0,  "size_multiplier": 1.0, "max_new_trades": 6},  # Paper: raised from 3
        "DEFENSIVE": {"score_threshold_adj": 5,  "size_multiplier": 0.7, "max_new_trades": 4},  # Paper: raised from 2
        "RECOVERY":  {"score_threshold_adj": 10, "size_multiplier": 0.5, "max_new_trades": 2},  # Paper: raised from 1
    }

    daily_pnl_pct = (daily_pnl / portfolio_value) if portfolio_value > 0 else 0.0

    if daily_pnl_pct <= -CONFIG["strategy_recovery_loss_pct"]:
        mode = "RECOVERY"
    elif daily_pnl_pct <= -CONFIG["strategy_pivot_loss_pct"]:
        mode = "DEFENSIVE"
    else:
        mode = "NORMAL"

    if _consecutive_losses >= CONFIG["strategy_recovery_streak"]:
        if mode != "RECOVERY":
            mode = "RECOVERY"
            log.info(f"Strategy mode escalated to RECOVERY from consecutive losses ({_consecutive_losses})")
    elif _consecutive_losses >= CONFIG["strategy_defensive_streak"] and mode == "NORMAL":
        mode = "DEFENSIVE"
        log.info(f"Strategy mode escalated to DEFENSIVE from consecutive losses ({_consecutive_losses})")

    if mode == "DEFENSIVE":
        context = (
            f"STRATEGY MODE: DEFENSIVE — We have lost {abs(daily_pnl_pct * 100):.1f}% today "
            f"({_consecutive_losses} consecutive losses). "
            "Entry bar is ELEVATED. Only trade exceptional setups — no marginal trades. "
            "Reduce all recommended position sizes to 70% of normal. Max 2 new positions this scan."
        )
    elif mode == "RECOVERY":
        context = (
            f"STRATEGY MODE: RECOVERY — We have lost {abs(daily_pnl_pct * 100):.1f}% today "
            f"({_consecutive_losses} consecutive losses). "
            "We are in capital preservation mode. ONE new position maximum, and only if the setup is "
            "outstanding with high conviction. Position sizes at 50% of normal. "
            "If there is any doubt, the answer is NO TRADE. Cash is a valid and preferred position."
        )
    else:
        context = ""

    params = _MODE_PARAMS[mode]
    _strategy_size_multiplier = params["size_multiplier"]
    _current_strategy_mode = mode
    if mode != "NORMAL":
        log.info(f"Strategy mode: {mode} | PnL={daily_pnl_pct*100:+.2f}% | "
                 f"Streak={_consecutive_losses} | ScoreAdj=+{params['score_threshold_adj']} | "
                 f"SizeMult={params['size_multiplier']}x | MaxTrades={params['max_new_trades']}")

    return {
        "mode":                 mode,
        "score_threshold_adj":  params["score_threshold_adj"],
        "size_multiplier":      params["size_multiplier"],
        "max_new_trades":       params["max_new_trades"],
        "context":              context,
        "daily_pnl_pct":        daily_pnl_pct,
        "regime_changed":       get_regime_changed(current_regime),
    }


def check_thesis_validity(open_positions: list, current_regime: str) -> list:
    """
    Returns a list of {symbol, reason} dicts for open positions whose entry-regime
    thesis is invalidated by the current regime. Advisory only.
    """
    if not CONFIG.get("thesis_invalidation_regime_change", True):
        return []
    if not get_regime_changed(current_regime):
        return []
    if not open_positions:
        return []

    flagged = []
    for pos in open_positions:
        entry_regime = pos.get("regime") or _session_opening_regime
        if not entry_regime:
            continue
        if (entry_regime, current_regime) in _SIGNIFICANT_REGIME_CHANGES:
            reason = (
                f"Opened in {entry_regime} regime; market is now {current_regime}. "
                "Original thesis may no longer hold — reconsider."
            )
            flagged.append({"symbol": pos.get("symbol", "?"), "reason": reason})

    return flagged
