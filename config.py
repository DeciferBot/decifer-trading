# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  config.py                                  ║
# ║   All settings in one place. This is the only file you       ║
# ║   need to edit to change bot behaviour.                      ║
# ╚══════════════════════════════════════════════════════════════╝

import os

CONFIG = {

    # ── IBKR CONNECTION ────────────────────────────────────────
    "ibkr_host":        "127.0.0.1",
    "ibkr_port":        7496,           # 7496 = TWS/Gateway
    "ibkr_client_id":   10,
    "active_account":   "DUP481326",    # PAPER — change to U3059777 or U24093086 for live

    # ── ACCOUNT REGISTRY ──────────────────────────────────────
    "accounts": {
        "paper":  "DUP481326",
        "live_1": "U3059777",
        "live_2": "U24093086",
    },

    # ── AI BRAIN ──────────────────────────────────────────────
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE"),
    "claude_model":      "claude-sonnet-4-6",   # Latest Sonnet
    "claude_max_tokens": 800,                    # Per agent call

    # ── RISK MANAGEMENT ───────────────────────────────────────
    "risk_pct_per_trade":       0.04,   # 4% of portfolio per trade
    "max_positions":            12,     # Max simultaneous positions
    "daily_loss_limit":         0.06,   # 6% daily stop — bot halts for day
    "max_drawdown_alert":       0.15,   # 15% drawdown — alert + pause
    "min_cash_reserve":         0.10,   # 10% always in cash
    "max_single_position":      0.15,   # 15% max in any one position
    "max_sector_exposure":      0.40,   # 40% max in any one sector
    "consecutive_loss_pause":   5,      # Losses before 2-hour pause
    "max_portfolio_allocation": 1.0,    # 1.0 = full account, 0.2 = 20% of account
    "starting_capital":         1_000_000,  # Starting portfolio value for P&L tracking

    # ── TAKE PROFIT / STOP LOSS ───────────────────────────────
    "atr_stop_multiplier":      1.5,    # Stop = entry - (1.5 × ATR)
    "atr_trail_multiplier":     2.0,    # Trailing stop = 2 × ATR from high
    "partial_exit_1_pct":       0.04,   # Sell 33% at +4%
    "partial_exit_2_pct":       0.08,   # Sell 33% at +8%, trail rest
    "min_reward_risk_ratio":    1.5,    # Minimum R:R to enter trade
    "gap_protection_pct":       0.03,   # Exit if opens 3% against position

    # ── DISAGREEMENT PROTOCOL ─────────────────────────────────
    # How many of 6 agents must agree before a trade is taken
    # 4 = conservative (default), 3 = standard, 2 = aggressive
    "agents_required_to_agree": 3,

    # ── SCANNING ──────────────────────────────────────────────
    "scan_interval_prime":      5,      # Minutes — 9:32am-11:30am, 2pm-3:59pm
    "scan_interval_standard":   10,     # Minutes — 11:30am-2pm (lunch lull)
    "scan_interval_extended":   7,      # Minutes — pre-market 4am-9:30am & after-hours 4pm-8pm
    "scan_interval_overnight":  60,     # Minutes — overnight 8pm-4am (monitoring only)

    # ── SCORING THRESHOLD ─────────────────────────────────────
    "min_score_to_trade":       28,     # Out of 50 — below this = skip
    "high_conviction_score":    38,     # Above this = 1.5x position size

    # ── MARKET HOURS (EST) ────────────────────────────────────
    "pre_market_start":         "04:00",
    "market_open":              "09:30",
    "prime_start":              "09:45",    # Avoid first 15 min
    "lunch_start":              "11:30",
    "afternoon_start":          "14:00",
    "close_buffer":             "15:55",    # Avoid last 5 min
    "market_close":             "16:00",
    "after_hours_end":          "20:00",

    # ── INDICATORS ────────────────────────────────────────────
    # Trend
    "ema_fast":                 9,
    "ema_slow":                 21,
    "ema_trend":                50,
    # Momentum
    "rsi_period":               14,
    # Timing
    "macd_fast":                12,
    "macd_slow":                26,
    "macd_signal":              9,
    # Volatility
    "atr_period":               14,
    "volume_surge_multiplier":  1.5,    # Volume must be 1.5x average
    # Squeeze detection (BB inside Keltner = coiled spring)
    "keltner_period":           20,     # Keltner EMA period
    "keltner_atr_period":       10,     # Keltner ATR period
    "keltner_multiplier":       1.5,    # Keltner ATR multiplier
    # Breakout detection
    "donchian_period":          20,     # Donchian channel lookback

    # ── DASHBOARD ─────────────────────────────────────────────
    "dashboard_port":           8080,

    # ── VIX REGIME THRESHOLDS ─────────────────────────────────
    "vix_bull_max":             15,     # VIX below = bull trending
    "vix_choppy_max":           25,     # VIX 15-25 = choppy
    "vix_panic_min":            35,     # VIX above = panic — no trades
    "vix_spike_pct":            0.20,   # 20% VIX spike in 1 hour = exit all

    # ── INVERSE ETFs FOR SHORT EXPOSURE ───────────────────────
    "inverse_etfs": {
        "market_short":  "SPXS",   # 3x inverse S&P 500
        "tech_short":    "SQQQ",   # 3x inverse Nasdaq
        "vix_long":      "UVXY",   # VIX spike play
    },

    # ── LOGGING ───────────────────────────────────────────────
    "log_file":     "logs/decifer.log",
    "trade_log":    "data/trades.json",
    "order_log":    "data/orders.json",

    # ── OPTIONS TRADING ───────────────────────────────────────
    # Set options_enabled to True to activate options trading.
    # When enabled, high-conviction stock signals (score >= options_min_score)
    # are evaluated for an options trade instead of (or alongside) the stock.
    "options_enabled":        True,    # OPTIONS ACTIVE

    # Entry filters
    "options_min_score":      35,      # Minimum stock score to consider options
    "options_max_ivr":        65,      # Max IV Rank — expanded range
    #                                     IVR < 30 = cheap (ideal), 30-50 = fair,
    #                                     50-65 = acceptable for high conviction

    # Contract selection
    "options_target_delta":   0.50,    # Target delta (ATM for max leverage)
    "options_delta_range":    0.20,    # Acceptable window either side of target
    "options_min_dte":        5,       # Minimum days to expiry
    "options_max_dte":        45,      # Maximum days to expiry

    # Liquidity filters
    "options_min_volume":     50,      # Minimum contracts traded today
    "options_min_oi":         200,     # Minimum open interest
    "options_max_spread_pct": 0.25,    # Max bid-ask spread as % of mid price

    # Position sizing
    "options_max_risk_pct":   0.025,   # Max 2.5% of portfolio per options trade
    #                                     (premium paid = max loss if goes to zero)

    # Exit rules
    "options_profit_target":  1.00,    # Take profit at 100% premium gain
    "options_stop_loss":      0.50,    # Stop loss at 50% premium loss
    "options_exit_dte":       2,       # Hard exit at this many DTE (gamma risk)
}
