# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  position_sizing.py                         ║
# ║   Pure position-sizing and stop utilities                    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Pure, stateless sizing and stop functions extracted from risk.py.

These three functions have no shared module state and do not depend on
any risk.py globals, so they can live here cleanly without worrying
about the sys.modules.pop("risk") test pattern.

The main VIX-rank adaptive stack (calculate_position_size, get_vix_rank,
get_kelly_fraction, get_drawdown_scalar) remains in risk.py because those
functions read/write risk.py globals that tests access via risk.* attributes.
"""

from __future__ import annotations

import logging

from config import CONFIG

log = logging.getLogger("decifer.risk")


def calculate_stops(price: float, atr: float, direction: str) -> tuple[float, float]:
    """
    Calculate stop loss and first take profit using ATR.
    Returns (stop_loss, take_profit_1).
    """
    # Guard against near-zero ATR (yfinance data contamination or flat tape).
    # 0.3% of price is the minimum viable stop distance for any liquid US equity.
    min_atr = price * 0.003
    if atr < min_atr:
        log.warning(
            f"calculate_stops: ATR {atr:.4f} below floor {min_atr:.4f} "
            f"(0.3%% of {price}) — using floor to prevent hair-trigger stop"
        )
        atr = min_atr
    sl_distance = atr * CONFIG["atr_stop_multiplier"]
    tp_distance = sl_distance * CONFIG["min_reward_risk_ratio"] * 1.5

    if direction == "LONG":
        sl = round(price - sl_distance, 2)
        tp = round(price + tp_distance, 2)
    else:  # SHORT
        sl = round(price + sl_distance, 2)
        tp = round(price - tp_distance, 2)

    return sl, tp


def position_size(account_value: float, entry_price: float, stop_price: float, config: dict | None = None) -> int:
    """
    Fixed-risk position sizer.

    Shares = (account_value × risk_per_trade) / |entry_price - stop_price|

    Capped at max_position_size × account_value / entry_price.
    Returns 0 when entry_price == stop_price (zero stop distance).

    Applies a macro-event size multiplier (default 0.5) when a FOMC, CPI,
    or NFP event is within 24 hours — reducing exposure before high-vol events.
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

    # Macro-event gate: halve size within 24h of FOMC / CPI / NFP
    try:
        from macro_calendar import get_macro_size_multiplier

        macro_mult = get_macro_size_multiplier()
        if macro_mult < 1.0:
            shares = int(shares * macro_mult)
    except Exception:
        pass  # fail-open: never block sizing on calendar errors

    return max(0, shares)


def get_short_size_multiplier() -> float:
    """
    Return a position-size multiplier for SHORT entries based on IC quality.

    When short IC is unproven (< 0.03), reduce position size to 0.60 of normal.
    This limits capital at risk while still allowing short trades through.
    Returns 1.0 when IC is proven or unavailable (fail-open).

    Called from execute_short() or anywhere sizing SHORT positions.
    The multiplier should be passed as external_mult to calculate_position_size().
    """
    try:
        from ic_calculator import get_short_quality_score

        short_quality = get_short_quality_score()
        if short_quality < 0.03:
            log.info(f"[sizing] Short IC unproven (quality={short_quality:.3f}) → 0.60x size mult")
            return 0.60
        return 1.0
    except Exception:
        return 1.0  # fail-open: don't block shorts on IC calculator errors
