# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  risk.py                                    ║
# ║   Five-layer risk management — hardcoded, agent-proof        ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from datetime import datetime, time
import pytz
from config import CONFIG

log = logging.getLogger("decifer.risk")

EST = pytz.timezone("America/New_York")

# ── State tracking ─────────────────────────────────────────────
_consecutive_losses  = 0
_pause_until         = None
_daily_loss_hit      = False
_session_start_value = None

# ── Drawdown-from-peak tracking ────────────────────────────────
_equity_high_water_mark = None
_drawdown_halt          = False
_last_known_equity      = None


def reset_daily_state(portfolio_value: float):
    """Call at the start of each trading day."""
    global _consecutive_losses, _pause_until, _daily_loss_hit, _session_start_value
    global _equity_high_water_mark
    _consecutive_losses  = 0
    _pause_until         = None
    _daily_loss_hit      = False
    _session_start_value = portfolio_value
    # Initialize HWM if not yet set (don't reset to current — preserve peak across days)
    if _equity_high_water_mark is None:
        _equity_high_water_mark = portfolio_value
    log.info(f"Daily risk state reset. Session start value: ${portfolio_value:,.2f} | HWM: ${_equity_high_water_mark:,.2f}")


def record_loss(source: str = "bot"):
    """
    Record a losing trade — may trigger consecutive loss pause.
    source: "bot" = from bot-initiated trade, "external" = from stop loss / TP hit
    External closes do NOT extend the pause — they're not new bad decisions.
    """
    global _consecutive_losses, _pause_until
    # External closes (stop losses / take profits hitting) should NOT extend the pause.
    # Only fresh bot-initiated trades that lose should count as consecutive losses.
    if source == "external" and _pause_until is not None:
        log.info(f"External close loss recorded — not extending existing pause")
        return
    _consecutive_losses += 1
    if _consecutive_losses >= CONFIG["consecutive_loss_pause"]:
        from datetime import timedelta
        # Cap pause at 2 hours — never extend beyond that
        if _pause_until is None or datetime.now(EST) >= _pause_until:
            _pause_until = datetime.now(EST) + timedelta(hours=2)
            log.warning(f"⚠️  {_consecutive_losses} consecutive losses — pausing until {_pause_until.strftime('%H:%M')}")
        else:
            log.info(f"Loss #{_consecutive_losses} — pause already active until {_pause_until.strftime('%H:%M')}, not extending")


def record_win():
    """Reset consecutive loss counter on a win."""
    global _consecutive_losses
    _consecutive_losses = 0


def _get_ibkr_cash(ib, account: str) -> float | None:
    """
    Get actual cash from IBKR account.
    Uses TotalCashValue — the real USD cash in the account.
    This is the source of truth for a margin account, NOT entry_price * qty.
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


def check_risk_conditions(portfolio_value: float, daily_pnl: float, regime: dict,
              open_positions: list = None, ib=None) -> tuple[bool, str]:
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
    if regime.get("regime") == "PANIC":
        return False, "PANIC regime — VIX too high or spiking. No new trades."

    # ── Layer 3: Daily loss limit ─────────────────────────────
    daily_loss_limit = portfolio_value * CONFIG["daily_loss_limit"]
    if portfolio_value > 0 and daily_pnl < 0 and daily_pnl <= -daily_loss_limit:
        _daily_loss_hit = True
        return False, f"Daily loss limit hit (${daily_pnl:,.2f}). Bot halted for today."

    # ── Layer 3: Consecutive loss pause ──────────────────────
    if _pause_until and datetime.now(EST) < _pause_until:
        return False, f"Consecutive loss pause active until {_pause_until.strftime('%H:%M')} EST"
    elif _pause_until and datetime.now(EST) >= _pause_until:
        _pause_until = None  # Pause expired

    # ── Layer 4: Min cash reserve ────────────────────────────
    # Use IBKR's actual TotalCashValue — this is the source of truth for a
    # margin account. The old entry*qty calculation was wrong because it
    # treated the account as 100% cash (no margin), making the bot think
    # it had almost no cash when IBKR reported plenty.
    if open_positions and portfolio_value > 0:
        ibkr_cash = _get_ibkr_cash(ib, CONFIG.get("active_account", ""))
        if ibkr_cash is not None:
            cash_pct = ibkr_cash / portfolio_value
        else:
            # Fallback: use current market value of positions (not entry cost)
            deployed = sum(p.get("current", p.get("entry", 0)) * p.get("qty", 0) for p in open_positions)
            cash_pct = (portfolio_value - deployed) / portfolio_value
        if cash_pct < CONFIG["min_cash_reserve"]:
            return False, f"Cash reserve too low ({cash_pct*100:.1f}% < {CONFIG['min_cash_reserve']*100:.0f}% min). Preserve capital."

    # ── Layer 3: Market hours ─────────────────────────────────
    # Extended hours trading enabled — pre-market (4am) through after-hours (8pm)
    # Only block the dead overnight period and the most dangerous micro-windows
    market_open  = time(9, 30)
    prime_start  = time(9, 32)   # Just 2 min buffer after open bell
    close_buffer = time(15, 59)  # Last 1 min only — let us trade into close
    market_close = time(16, 0)
    pre_start    = time(4, 0)    # Pre-market opens at 4am EST
    after_end    = time(20, 0)   # After-hours closes at 8pm EST

    # Dead overnight (8pm-4am) — no liquidity, no trades
    if now_time < pre_start or now_time > after_end:
        return False, "Overnight (8pm-4am) — no liquidity, monitoring only"

    # First 2 min after open bell — let the opening auction settle
    if market_open <= now_time < prime_start:
        return False, "Opening auction settling — 2 minute buffer"

    # Last 1 min before close — avoid MOC imbalance games
    if close_buffer <= now_time <= market_close:
        return False, "Final minute before close — MOC imbalance, avoid"

    return True, "OK"


# ── Simple can_trade() helpers (mockable in tests) ─────────────────────────

def _get_daily_pnl() -> float:
    """Return today's realized + unrealized P&L. Override in production."""
    return 0.0


def _get_open_position_count() -> int:
    """Return current number of open positions."""
    return 0


def _is_market_open() -> bool:
    """Return True if trading is currently permitted (pre-market to after-hours)."""
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

    Checks (in order):
      1. Daily P&L has not hit the max_daily_loss dollar limit.
      2. Open position count is below max_positions.
      3. Market is currently open (4 am – 8 pm EST).
      4. Correlation to existing positions is below correlation_threshold.

    All checks use thin private helpers (_get_daily_pnl, etc.) so tests can
    mock them via patch.object without importing live infrastructure.
    """
    daily_pnl = _get_daily_pnl()
    max_loss = config.get("max_daily_loss", CONFIG.get("max_daily_loss", 5000))
    if daily_pnl <= -abs(max_loss):
        log.debug(f"can_trade blocked: daily P&L ${daily_pnl:,.2f} <= -${abs(max_loss):,.2f}")
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


def position_size(account_value: float, entry_price: float,
                  stop_price: float, config: dict = None) -> int:
    """
    Fixed-risk position sizer.

    Shares = (account_value × risk_per_trade) / |entry_price - stop_price|

    Capped at max_position_size × account_value / entry_price.
    Returns 0 when entry_price == stop_price (zero stop distance).
    """
    if config is None:
        config = CONFIG

    stop_distance = abs(entry_price - stop_price)
    if stop_distance == 0:
        return 0

    risk_pct = config.get("risk_per_trade", CONFIG.get("risk_per_trade", 0.01))
    risk_amount = account_value * risk_pct
    shares = int(risk_amount / stop_distance)

    max_pos_pct = config.get("max_position_size", CONFIG.get("max_position_size", 0.10))
    if entry_price > 0:
        max_shares = int((account_value * max_pos_pct) / entry_price)
        shares = min(shares, max_shares)

    return max(0, shares)


def get_session() -> str:
    """Return current trading session name."""
    now_est  = datetime.now(EST).time()
    pre      = time(4, 0)
    open_    = time(9, 30)
    prime    = time(9, 45)
    lunch    = time(11, 30)
    after_pm = time(14, 0)
    close_b  = time(15, 55)
    close    = time(16, 0)
    after    = time(20, 0)

    if now_est < pre:          return "OVERNIGHT"
    elif now_est < open_:      return "PRE_MARKET"
    elif now_est < prime:      return "OPEN_BUFFER"
    elif now_est < lunch:      return "PRIME_AM"
    elif now_est < after_pm:   return "LUNCH"
    elif now_est < close_b:    return "PRIME_PM"
    elif now_est < close:      return "CLOSE_BUFFER"
    elif now_est < after:      return "AFTER_HOURS"
    else:                      return "OVERNIGHT"


def get_scan_interval() -> int:
    """Return appropriate scan interval in seconds for current session."""
    session = get_session()
    intervals = {
        "PRE_MARKET":   CONFIG["scan_interval_extended"] * 60,
        "OPEN_BUFFER":  CONFIG["scan_interval_extended"] * 60,
        "PRIME_AM":     CONFIG["scan_interval_prime"]    * 60,
        "LUNCH":        CONFIG["scan_interval_standard"] * 60,
        "PRIME_PM":     CONFIG["scan_interval_prime"]    * 60,
        "CLOSE_BUFFER": CONFIG["scan_interval_extended"] * 60,
        "AFTER_HOURS":  CONFIG["scan_interval_extended"] * 60,
        "OVERNIGHT":    CONFIG["scan_interval_overnight"] * 60,
    }
    return intervals.get(session, CONFIG["scan_interval_standard"] * 60)


def calculate_position_size(portfolio_value: float, price: float,
                             score: int, regime: dict) -> int:
    """
    Kelly-inspired position sizing.
    Returns number of shares to buy.
    """
    # Base risk amount
    base_risk = portfolio_value * CONFIG["risk_pct_per_trade"]

    # Conviction multiplier based on score
    if score >= CONFIG["high_conviction_score"]:
        conviction_mult = 1.5
    elif score >= 32:
        conviction_mult = 1.0
    else:
        conviction_mult = 0.75

    # Regime multiplier
    regime_mult = regime.get("position_size_multiplier", 0.5)

    # Extended hours reduction
    session = get_session()
    if session in ("PRE_MARKET", "AFTER_HOURS"):
        session_mult = 0.75   # Slight reduction for lower liquidity, not a penalty
    else:
        session_mult = 1.0

    # Final risk amount
    risk_amount = base_risk * conviction_mult * regime_mult * session_mult

    # Max position size check
    max_position_value = portfolio_value * CONFIG["max_single_position"]
    position_value = min(risk_amount / 0.02, max_position_value)  # Assume 2% stop

    qty = max(1, int(position_value / price))

    # ── HARD SAFETY CAP — catch any remaining edge cases ──
    # If computed qty × price > 20% of portfolio, something is wrong (likely bad price data)
    order_value = qty * price
    hard_cap = portfolio_value * 0.20
    if order_value > hard_cap and qty > 1:
        qty = max(1, int(hard_cap / price))
        log.warning(f"Position size hard cap triggered: {order_value:,.0f} > {hard_cap:,.0f}, reduced to {qty} shares")

    return qty


def calculate_stops(price: float, atr: float, direction: str) -> tuple[float, float]:
    """
    Calculate stop loss and first take profit using ATR.
    Returns (stop_loss, take_profit_1).
    """
    sl_distance = atr * CONFIG["atr_stop_multiplier"]
    tp_distance = sl_distance * CONFIG["min_reward_risk_ratio"] * 1.5

    if direction == "LONG":
        sl = round(price - sl_distance, 2)
        tp = round(price + tp_distance, 2)
    else:  # SHORT
        sl = round(price + sl_distance, 2)
        tp = round(price - tp_distance, 2)

    return sl, tp


def check_correlation(new_symbol: str, open_positions: list) -> tuple[bool, str]:
    """
    Basic correlation check — avoid highly correlated positions.
    Returns (ok_to_add, reason).
    """
    # Sector mapping for basic correlation check
    tech    = {"AAPL", "NVDA", "MSFT", "META", "GOOGL", "AMD", "INTC", "ORCL"}
    semis   = {"NVDA", "AMD", "MU", "INTC", "AMAT", "LRCX", "KLAC", "QCOM"}
    indices = {"SPY", "QQQ", "IWM", "DIA"}

    open_syms = {p["symbol"] for p in open_positions}

    # Don't hold both SPY and QQQ (highly correlated)
    if new_symbol in indices and len(open_syms & indices) >= 1:
        return False, f"Already hold a broad index ETF — {new_symbol} too correlated"

    # Don't hold more than 2 in same tech cluster
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
# FIX #1: Cross-instrument exposure check (stock + options same underlying)
# ══════════════════════════════════════════════════════════════════

def get_underlying_exposure(symbol: str, open_positions: list) -> dict:
    """
    Calculate total exposure to a single underlying across stocks AND options.
    Returns dict with exposure details.
    """
    stock_value = 0.0
    option_value = 0.0

    for pos in open_positions:
        pos_sym = pos.get("symbol", "")
        if pos_sym != symbol:
            continue

        instrument = pos.get("instrument", "stock")
        qty = pos.get("qty", 0)

        if instrument == "option":
            # Options exposure = contracts × 100 × underlying price (delta-adjusted)
            delta = abs(pos.get("delta", 0.5))
            underlying = pos.get("underlying_price", pos.get("current", 0))
            option_value += qty * 100 * underlying * delta
        else:
            # Stock exposure = shares × current price
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
                            instrument: str = "stock") -> tuple[bool, str]:
    """
    FIX #1 + #3: Check whether adding a new position would create
    excessive combined exposure to the same underlying.

    Blocks if:
    - Already have the same underlying in the OTHER instrument type
      AND combined exposure would exceed max_single_position
    - Total deployed capital + new position exceeds max_portfolio_allocation

    Returns (ok_to_trade, reason).
    """
    if portfolio_value <= 0:
        return True, "OK"

    max_single = CONFIG.get("max_single_position", 0.10)
    max_alloc = CONFIG.get("max_portfolio_allocation", 1.0)

    # ── Check same-underlying cross-instrument concentration ──────
    existing = get_underlying_exposure(symbol, open_positions)

    if existing["total_exposure"] > 0:
        combined = existing["total_exposure"] + new_exposure_value
        combined_pct = combined / portfolio_value

        # If we already have this underlying in the OTHER instrument,
        # check combined doesn't exceed single-position limit
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

    # ── Check total portfolio deployment ──────────────────────────
    total_deployed = 0.0
    for pos in open_positions:
        inst = pos.get("instrument", "stock")
        qty = pos.get("qty", 0)
        if inst == "option":
            # Option cost = premium × contracts × 100
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
# FIX #4: Max drawdown from peak — circuit breaker
# ══════════════════════════════════════════════════════════════════

def update_equity_high_water_mark(current_equity: float):
    """
    Call on each scan cycle with current portfolio value.
    Tracks the peak and triggers halt if drawdown exceeds limit.
    """
    global _equity_high_water_mark, _drawdown_halt, _last_known_equity

    _last_known_equity = current_equity

    if _equity_high_water_mark is None:
        _equity_high_water_mark = current_equity
        return

    # Update high water mark
    if current_equity > _equity_high_water_mark:
        _equity_high_water_mark = current_equity
        # If we were halted due to drawdown but recovered, clear the halt
        if _drawdown_halt:
            _drawdown_halt = False
            log.info(f"Drawdown halt cleared — equity recovered to new high: ${current_equity:,.2f}")

    # Check drawdown from peak
    if _equity_high_water_mark > 0:
        drawdown = (_equity_high_water_mark - current_equity) / _equity_high_water_mark
        max_dd = CONFIG.get("max_drawdown_alert", 0.25)

        if drawdown >= max_dd and not _drawdown_halt:
            _drawdown_halt = True
            log.warning(
                f"⛔ DRAWDOWN CIRCUIT BREAKER: {drawdown:.1%} drawdown from peak "
                f"${_equity_high_water_mark:,.2f} → ${current_equity:,.2f}. "
                f"Limit: {max_dd:.0%}. New trades halted."
            )


def check_drawdown() -> tuple[bool, str]:
    """
    Returns (ok_to_trade, reason). Called from can_trade().
    """
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


# ══════════════════════════════════════════════════════════════════
# FIX #2: Sector concentration enforcement
# ══════════════════════════════════════════════════════════════════

# Lazy-init sector monitor to avoid circular imports / heavy init at module load
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
                                regime: str = "NORMAL") -> tuple[bool, str]:
    """
    FIX #2: Check if adding new_symbol would breach sector concentration limits.
    Uses SectorMonitor from portfolio_optimizer.py (was built but never wired in).

    Returns (ok_to_trade, reason).
    """
    monitor = _get_sector_monitor()
    if monitor is None:
        return True, "OK (sector monitor unavailable)"

    if portfolio_value <= 0 or not open_positions:
        return True, "OK"

    max_sector = CONFIG.get("max_sector_exposure", 0.50)

    try:
        # Build portfolio dict: symbol → (qty, price)
        portfolio_dict = {}
        for pos in open_positions:
            sym = pos.get("symbol", "")
            qty = pos.get("qty", 0)
            price = pos.get("current", pos.get("entry", 0))
            if sym and qty > 0 and price > 0:
                if sym in portfolio_dict:
                    # Accumulate if same symbol appears multiple times (stock + option)
                    existing_qty, existing_price = portfolio_dict[sym]
                    # Use value-weighted combination
                    total_value = existing_qty * existing_price + qty * price
                    total_qty = existing_qty + qty
                    portfolio_dict[sym] = (total_qty, total_value / total_qty if total_qty else price)
                else:
                    portfolio_dict[sym] = (qty, price)

        # Get sector of new symbol
        new_sector = monitor.get_sector(new_symbol)

        # Calculate current sector weights
        sector_weights = monitor.calculate_sector_weights(portfolio_dict)

        current_sector_pct = sector_weights.get(new_sector, 0.0)

        # Use regime-aware limits from SectorMonitor
        regime_limits = {
            "NORMAL": min(max_sector, 0.30),
            "CHOPPY": min(max_sector, 0.20),
            "PANIC":  min(max_sector, 0.15),
        }
        limit = regime_limits.get(regime, max_sector)

        if current_sector_pct >= limit:
            return False, (
                f"Sector concentration block: {new_sector} already at {current_sector_pct:.1%} "
                f"(limit: {limit:.0%} in {regime} regime). "
                f"Cannot add {new_symbol}."
            )

        # Warn if approaching limit (>80%)
        if current_sector_pct >= limit * 0.8:
            log.warning(
                f"Sector warning: {new_sector} at {current_sector_pct:.1%}, "
                f"approaching {limit:.0%} limit. Allowing {new_symbol} but watch closely."
            )

    except Exception as e:
        log.warning(f"Sector concentration check failed for {new_symbol}: {e} — allowing trade")
        return True, "OK (sector check error, allowing)"

    return True, "OK"
