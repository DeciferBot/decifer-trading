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

    # ── MULTI-ACCOUNT AGGREGATION ──────────────────────────────
    # List of account IDs to include in the unified portfolio view.
    # Empty list (default) = auto-include all non-empty accounts above.
    # Set explicitly to restrict aggregation to specific accounts, e.g.:
    #   ["DUP481326", "U3059777"]
    "aggregate_accounts": [],

    # ── AI BRAIN ──────────────────────────────────────────────
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE"),
    "claude_model":      "claude-sonnet-4-6",   # Latest Sonnet
    "claude_max_tokens": 800,                    # Per agent call

    # ── IBKR RECONNECT & HEARTBEAT ────────────────────────────
    "reconnect_max_attempts":   10,     # Retry attempts before giving up
    "reconnect_max_wait_secs":  60,     # Cap on exponential backoff delay (secs)
    "reconnect_base_wait_secs": 1,      # Starting backoff delay (secs)
    "reconnect_alert_webhook":  "",     # Slack/Teams URL for reconnect-failed alert
    "heartbeat_interval_secs":  1200,   # 20 min heartbeat (reqCurrentTime)

    # ── RISK MANAGEMENT ───────────────────────────────────────
    # NOTE: Tuned for PAPER TRADING data generation — maximise trade volume
    # across regimes to build ML training dataset. Revert to conservative
    # values before switching to live. Original live values in comments.
    "risk_pct_per_trade":       0.03,   # 3% of portfolio per trade (live: 0.04)
    "risk_per_trade":           0.01,   # 1% per trade — used by position_size()
    "max_position_size":        0.30,   # Max fraction of account per position (30%)
    "max_daily_loss":           5000,   # Max daily loss in dollars before halting
    "correlation_threshold":    0.75,   # Block new trade if correlation > this
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
    # NOTE: Raised from 2→3 to filter low-conviction trades (roadmap #08).
    # 2/6 was rubber-stamping; 3/6 requires real consensus. (live: 4)
    "agents_required_to_agree": 3,

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

    # ── DIMENSION FLAGS ───────────────────────────────────────────
    # Enable / disable individual signal dimensions without code changes.
    # Set any flag to False to zero that dimension's score and remove its
    # direction vote. Useful when Signal Decay Monitor identifies a harmful
    # or noisy dimension — flip the flag, no deploy required.
    # All flags default to True (full pipeline, backward-compatible).
    "dimension_flags": {
        "trend":     True,   # Dim 1 — EMA alignment × ADX
        "momentum":  True,   # Dim 2 — MFI + RSI slope
        "squeeze":   True,   # Dim 3 — BB/Keltner compression
        "flow":      True,   # Dim 4 — VWAP + OBV
        "breakout":  True,   # Dim 5 — Donchian channel breach
        "mtf":       True,   # Dim 6 — Multi-timeframe agreement
        "news":      True,   # Dim 7 — Yahoo RSS + Claude sentiment
        "social":    True,   # Dim 8 — Reddit velocity + VADER
        "reversion": True,   # Dim 9 — Variance Ratio + OU half-life + z-score
    },

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

    # ── REGIME-AWARE SCORE THRESHOLDS ────────────────────────
    # Score thresholds are adjusted per market regime, relative to
    # min_score_to_trade (base). Offsets lower the bar in bear/choppy
    # regimes to capture more setups; floors prevent thresholds going
    # too low. PANIC blocks all entries (threshold set to 99).
    #
    # Effective thresholds (with default min_score_to_trade=18 for paper):
    #   BULL_TRENDING: 18  (base, no change)
    #   BEAR_TRENDING: 15  (max(15, 18-3))
    #   CHOPPY:        12  (max(12, 18-6))
    #   PANIC:         99  (block all)
    "regime_threshold_bear_offset":   -3,   # BEAR_TRENDING = base + offset
    "regime_threshold_choppy_offset": -6,   # CHOPPY = base + offset
    "regime_threshold_panic":         99,   # Effectively infinite — no trades in panic
    "regime_threshold_bear_min":      15,   # Floor for BEAR_TRENDING
    "regime_threshold_choppy_min":    12,   # Floor for CHOPPY

    # ── CANDLESTICK CONFIRMATION GATE ────────────────────────
    # When True, a BUY or SELL signal must have at least one confirming
    # candlestick pattern (candle_bull or candle_bear > 0) or it is
    # downgraded to HOLD. Candles still add bonus points regardless.
    # False = collect more data on paper; True = higher precision on live.
    "candle_required":  False,   # (live: True)

    # ── MULTI-TIMEFRAME ALIGNMENT GATE ──────────────────────
    # Hard filter that blocks entries when higher timeframes disagree
    # with the 5m signal direction. Fixes structural bullish bias by
    # preventing noisy intraday signals from triggering trades against
    # the daily/weekly trend.
    #
    # Modes:
    #   "hard"   — Block entry entirely if daily trend opposes signal
    #   "soft"   — Apply score penalty (mtf_penalty_points) instead of blocking
    #   "off"    — Disabled (current behaviour, just Dimension 6 scoring)
    #
    # NOTE: Set to "soft" for paper trading to still capture data while
    # penalising bad setups. Switch to "hard" for live trading.
    "mtf_gate_mode":            "soft",      # "hard" | "soft" | "off" (live: "hard")
    "mtf_penalty_points":       8,           # Score deduction when daily opposes 5m (soft mode)
    "mtf_require_weekly":       False,       # Also require weekly alignment? (stricter)
    "mtf_adx_min_for_gate":     20,          # Only enforce gate when daily ADX > this
    #                                           (weak trends don't reliably predict direction)

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
    "signals_log":  "data/signals_log.jsonl",

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

    # ── TELEGRAM KILL SWITCH ──────────────────────────────────────
    # Emergency stop accessible via Telegram (supplement to dashboard).
    # Required before live trading (Phase 4) — see phase_gate frozen_features.
    #
    # Setup:
    #   1. Create a bot via @BotFather — copy the token below (or set env var)
    #   2. Start a chat with your bot, then run:
    #        curl https://api.telegram.org/bot<TOKEN>/getUpdates
    #      to find your chat_id and add it to authorized_chat_ids
    #
    # Commands once running:
    #   /kill   — flatten all positions, cancel orders, halt bot
    #   /status — current bot state + open position count
    #   /resume — clear kill flag and resume trading
    "telegram": {
        "bot_token":           os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "authorized_chat_ids": [],   # e.g. [123456789] — integer chat IDs only
    },

    # ── PHASE GATE ────────────────────────────────────────────────
    # Enforces strict sequencing: Phase 4+ work (cloud, multi-user, Docker,
    # live accounts) is frozen until Phase 1 exit criteria are satisfied.
    #
    # Phases:
    #   1 — Paper trading validation (single account, core pipeline)
    #   2 — Bias removal & regime adaptation (roadmap A/B/C/D)
    #   3 — Signal validation & ML calibration (Alphalens, walk-forward)
    #   4 — Advanced data & execution (multi-account, live accounts, cloud)
    #   5 — Infrastructure (Docker, multi-user, hosted deployment)
    #
    # Alpha Validation Gate (must be cleared FIRST — before any of the below):
    #   - 50+ closed paper trades with positive average PnL (positive expectancy)
    #   - This gate blocks: new signal dimensions, infrastructure work, live trading
    #   - See LIVE_TRADING_GATE.md for the full criteria document
    #
    # Phase 1 exit criteria (ALL must be met before advancing to Phase 2):
    #   - 200+ closed paper trades logged to data/trades.json
    #   - Test suite ≥ 80% pass rate
    #   - 30+ consecutive paper trading days without critical bugs
    #   - Amit explicitly sets current_phase = 2 after reviewing results
    #
    # DO NOT change current_phase without meeting all exit criteria above.
    "phase_gate": {
        "current_phase": 1,

        # ── Alpha Validation Gate ──────────────────────────────────────────────
        # Hard checkpoint before any new signal dimension, infrastructure work,
        # or live trading gate. Signal model has no demonstrated alpha until this
        # is cleared. See LIVE_TRADING_GATE.md.
        "alpha_validation_gate": {
            "min_closed_trades":          50,   # Minimum closed paper trades required
            "require_positive_expectancy": True, # avg PnL/trade must be > 0
        },

        "phase1_exit_criteria": {
            "min_closed_trades":       200,   # Trades needed before ML/backtest are meaningful
            "min_test_pass_rate":      0.80,  # Fraction of pytest tests that must pass
            "min_paper_trading_days":  30,    # Consecutive days running without critical bugs
        },
        # Features frozen until the specified phase is reached.
        # Attempting to enable these in earlier phases triggers a PhaseGateViolation.
        "frozen_features": {
            # Phase 4: multi-account, live accounts, cloud infrastructure
            "live_account_trading":     4,   # Enabling live_1 / live_2 for order execution
            "multi_account_aggregation":4,   # aggregate_accounts with multiple live accounts
            "cloud_deployment":         4,   # Any hosted / cloud infra work
            # Phase 4 safety gate: Telegram kill switch MUST be configured before live trading.
            # phase_gate.validate() will block Phase 4 if bot_token or authorized_chat_ids is unset.
            "telegram_kill_switch":     4,   # Emergency stop via Telegram (gate for live trading)
            # Phase 5: infrastructure & multi-user
            "docker_deployment":        5,   # Dockerising the bot
            "multi_user_auth":          5,   # User accounts / auth layer
            "hosted_dashboard":         5,   # Public-facing dashboard
        },
    },
}
