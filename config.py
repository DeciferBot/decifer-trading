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
    "active_account":   os.environ.get("IBKR_ACTIVE_ACCOUNT", ""),

    # ── ACCOUNT REGISTRY ──────────────────────────────────────
    "accounts": {
        "paper":  os.environ.get("IBKR_PAPER_ACCOUNT", ""),
        "live_1": os.environ.get("IBKR_LIVE_1_ACCOUNT", ""),
        "live_2": os.environ.get("IBKR_LIVE_2_ACCOUNT", ""),
    },

    # ── AI BRAIN ──────────────────────────────────────────────
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE"),
    "claude_model":      "claude-sonnet-4-6",   # Latest Sonnet
    "claude_max_tokens": 800,                    # Per agent call

    # ── RISK MANAGEMENT ───────────────────────────────────────
    # NOTE: Tuned for PAPER TRADING data generation — maximise trade volume
    # across regimes to build ML training dataset. Revert to conservative
    # values before switching to live. Original live values in comments.
    "risk_pct_per_trade":       0.03,   # 3% of portfolio per trade (live: 0.04)
    "max_positions":            20,     # More concurrent positions = more data (live: 12)
    "daily_loss_limit":         0.10,   # 10% daily — paper can absorb more (live: 0.06)
    "max_drawdown_alert":       0.25,   # 25% drawdown alert (live: 0.15)
    "min_cash_reserve":         0.05,   # 5% cash reserve (live: 0.10)
    "max_single_position":      0.10,   # 10% per position with more positions (live: 0.15)
    "max_sector_exposure":      0.50,   # 50% sector — allow concentration (live: 0.40)
    "consecutive_loss_pause":   8,      # More tolerance for losing streaks (live: 5)
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
    # NOTE: Set to 2 for paper trading to maximise trade data generation (live: 4)
    "agents_required_to_agree": 2,

    # ── SCANNING ──────────────────────────────────────────────
    # NOTE: Faster scans for paper trading data generation (live values in comments)
    "scan_interval_prime":      3,      # Minutes — 9:32am-11:30am, 2pm-3:59pm (live: 5)
    "scan_interval_standard":   5,      # Minutes — 11:30am-2pm (lunch lull) (live: 10)
    "scan_interval_extended":   5,      # Minutes — pre-market 4am-9:30am & after-hours 4pm-8pm (live: 7)
    "scan_interval_overnight":  30,     # Minutes — overnight 8pm-4am (monitoring only) (live: 60)

    # ── SCORING THRESHOLD ─────────────────────────────────────
    # NOTE: Lowered for paper trading to capture more setups for ML training (live values in comments)
    "min_score_to_trade":       18,     # Out of 50 — lower = more trades for training (live: 28)
    "high_conviction_score":    30,     # Above this = 1.5x position size (live: 38)

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
    "options_delta_range":    0.35,    # Acceptable window either side of target (0.15-0.85)
    #                                     Wider range needed for biotechs/small-caps
    #                                     with $5 strike spacing where ATM doesn't exist
    "options_min_dte":        5,       # Minimum days to expiry
    "options_max_dte":        45,      # Maximum days to expiry

    # Liquidity filters
    "options_min_volume":     25,      # Minimum contracts traded today (was 50, relaxed for mid-cap)
    "options_min_oi":         100,     # Minimum open interest (was 200, relaxed for mid-cap)
    "options_max_spread_pct": 0.35,    # Max bid-ask spread as % of mid price (was 0.25, too tight for small/mid-cap)

    # Position sizing
    "options_max_risk_pct":   0.025,   # Max 2.5% of portfolio per options trade
    #                                     (premium paid = max loss if goes to zero)

    # Exit rules
    "options_profit_target":  1.00,    # Take profit at 100% premium gain
    "options_stop_loss":      0.50,    # Stop loss at 50% premium loss
    "options_exit_dte":       2,       # Hard exit at this many DTE (gamma risk)

    # ── NEWS SENTINEL (real-time news trigger) ───────────────
    # Runs independently of the scan loop. Polls news every N seconds
    # and fires a 3-agent mini pipeline when material news is detected.
    "sentinel_enabled":             True,     # Master switch for News Sentinel
    "sentinel_poll_seconds":        45,       # Seconds between news polls (30-60 recommended)
    "sentinel_cooldown_minutes":    10,       # Don't re-trigger same symbol within N minutes
    "sentinel_batch_size":          10,       # Symbols per poll cycle (rotates through universe)
    "sentinel_max_symbols":         80,       # Max symbols in sentinel universe
    "sentinel_keyword_threshold":   3,        # Minimum |keyword_score| to consider material
    "sentinel_claude_confidence":   7,        # Min Claude confidence to auto-upgrade urgency
    "sentinel_min_confidence":      5,        # Min final decision confidence to execute trade
    "sentinel_use_ibkr":            True,     # Use IBKR news API as a source
    "sentinel_use_finviz":          True,     # Use Finviz news scraping as a source
    "sentinel_risk_multiplier":     0.75,     # Position size multiplier for sentinel trades (smaller = safer)
    "sentinel_max_trades_per_hour": 3,        # Max sentinel trades per hour (rate limit)

    # ── ML ENGINE (scikit-learn learning loop) ─────────────────────
    # Learns from trade history to identify winning patterns and enhance signals.
    # Requires: scikit-learn, joblib (pip install scikit-learn joblib)
    "ml_enabled":                True,        # Master switch: enable ML enhancements
    "ml_min_trades":             50,          # Minimum trades before ML kicks in
    "ml_retrain_interval":       168,         # Hours between automatic retraining (1 week)
    "ml_confidence_weight":      0.3,         # Weight of ML adjustment: 0.3 = 30% of change
    "ml_models_dir":             "data/models",  # Where to persist trained models
}
