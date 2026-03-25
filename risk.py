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


def reset_daily_state(portfolio_value: float):
    """Call at the start of each trading day."""
    global _consecutive_losses, _pause_until, _daily_loss_hit, _session_start_value
    _consecutive_losses  = 0
    _pause_until         = None
    _daily_loss_hit      = False
    _session_start_value = portfolio_value
    log.info(f"Daily risk state reset. Session start value: ${portfolio_value:,.2f}")


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


def can_trade(portfolio_value: float, daily_pnl: float, regime: dict,
              open_positions: list = None) -> tuple[bool, str]:
    """
    Master check — can we take new trades right now?
    Returns (bool, reason_if_not).
    This is hardcoded. No agent can override this.
    """
    global _daily_loss_hit, _pause_until

    now_est = datetime.now(EST)
    now_time = now_est.time()

    # ── Layer 5: Panic regime ─────────────────────────────────
    if regime.get("regime") == "PANIC":
        return False, "PANIC regime — VIX too high or spiking. No new trades."

    # ── Layer 3: Daily loss limit ─────────────────────────────
    daily_loss_limit = portfolio_value * CONFIG["daily_loss_limit"]
    if daily_pnl <= -daily_loss_limit:
        _daily_loss_hit = True
        return False, f"Daily loss limit hit (${daily_pnl:,.2f}). Bot halted for today."

    # ── Layer 3: Consecutive loss pause ──────────────────────
    if _pause_until and datetime.now(EST) < _pause_until:
        return False, f"Consecutive loss pause active until {_pause_until.strftime('%H:%M')} EST"
    elif _pause_until and datetime.now(EST) >= _pause_until:
        _pause_until = None  # Pause expired

    # ── Layer 4: Min cash reserve ────────────────────────────
    if open_positions:
        invested = sum(p.get("entry", 0) * p.get("qty", 0) for p in open_positions)
        cash_pct  = (portfolio_value - invested) / portfolio_value if portfolio_value > 0 else 1.0
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
