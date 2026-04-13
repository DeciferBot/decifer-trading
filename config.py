# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  config.py                                  ║
# ║   All settings in one place. This is the only file you       ║
# ║   need to edit to change bot behaviour.                      ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)
except ImportError:
    pass  # python-dotenv not installed — fall back to shell environment

CONFIG = {

    # ── IBKR CONNECTION ────────────────────────────────────────
    "ibkr_host":        "127.0.0.1",
    "ibkr_port":        7496,           # 7496 = TWS/Gateway
    "ibkr_client_id":   10,
    "active_account":   os.environ.get("IBKR_ACTIVE_ACCOUNT", ""),

    # ── ACCOUNT REGISTRY ──────────────────────────────────────
    "accounts": {
        "paper":  os.environ.get("IBKR_PAPER_ACCOUNT", "DUP481326"),
        "live_1": os.environ.get("IBKR_LIVE_1_ACCOUNT", ""),
        "live_2": os.environ.get("IBKR_LIVE_2_ACCOUNT", ""),
    },

    # ── MULTI-ACCOUNT AGGREGATION ──────────────────────────────
    # List of account IDs to include in the unified portfolio view.
    # Empty list (default) = auto-include all non-empty accounts above.
    # Set explicitly to restrict aggregation to specific accounts, e.g.:
    #   ["DUP481326", "U3059777"]
    "aggregate_accounts": [],

    # ── ALPHA VANTAGE (free tier: 25 calls/day) ──────────────
    # Used for: earnings calendar (primary source) + news sentiment (Tier 3 enrichment).
    # Sign up at https://www.alphavantage.co/support/#api-key (free, no credit card).
    # Key stored in ~/.bash_profile as ALPHA_VANTAGE_KEY — never commit the value.
    # If key is absent, AV calls are silently skipped and fallbacks take over.
    "alpha_vantage_key":        os.environ.get("ALPHA_VANTAGE_KEY", ""),
    "alpha_vantage_daily_limit": 25,   # Free tier cap. Upgrade plan → increase this.

    # ── ALPACA MARKETS (Algo Trader Plus — $99/mo) ────────────
    # Paper trading base URL: https://paper-api.alpaca.markets
    # Keys stored in ~/.bash_profile — never commit values here.
    "alpaca_api_key":           os.environ.get("ALPACA_API_KEY", ""),
    "alpaca_secret_key":        os.environ.get("ALPACA_SECRET_KEY", ""),
    "alpaca_base_url":          os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    # Stream switches — disable individually without touching keys
    "alpaca_news_enabled":      True,   # AlpacaNewsStream (Benzinga push feed)
    # Order protection gates sourced from the live Alpaca stream
    "max_spread_pct":           0.003,  # 0.30% bid-ask spread cap (skip wider markets)

    # ── IBKR HISTORICAL DATA PACING ───────────────────────────
    # IBKR enforces a soft limit of 60 reqHistoricalData requests per 10 minutes.
    # We stay under 55 to leave headroom. Throttle applied in fetch_ibkr_historical().
    "ibkr_hist_pacing_per_10min": 55,

    # ── IBKR FLEX WEB SERVICE (equity history backfill) ───────
    # Used automatically at bot startup to recover/extend equity_history.json
    # with accurate IBKR daily NAV when history is < 30 days deep.
    # One-time setup (5 min):
    #   1. IBKR Client Portal → Reports & Statements → Flex Queries → Create Query
    #      • Section: Account Information → Net Asset Value
    #      • Date range: all available, Period: Daily, Format: XML
    #   2. Note the Query ID shown on the query card.
    #   3. Same menu → "Flex Web Service" → Generate Token (valid 1 year).
    #   4. Add to .env:  IBKR_FLEX_TOKEN=<token>  IBKR_FLEX_QUERY_ID=<id>
    # Leave blank to skip (bot falls back to trade-based reconstruction).
    "ibkr_flex_token":    os.environ.get("IBKR_FLEX_TOKEN", ""),
    "ibkr_flex_query_id": os.environ.get("IBKR_FLEX_QUERY_ID", ""),

    # ── FRED (Federal Reserve Economic Data) — free, unlimited ──
    # Economic release calendar + macro indicator snapshots (CPI, unemployment, etc.)
    # Sign up: https://fred.stlouisfed.org/docs/api/api_key.html (free, instant)
    "fred_api_key":             os.environ.get("FRED_API_KEY", ""),

    # ── FINANCIAL MODELING PREP (free tier: 250 calls/day) ───
    # Used for: overnight research — economic calendar, earnings with estimates,
    # analyst upgrades/downgrades. Much richer than the hardcoded macro_calendar.
    # Sign up at https://financialmodelingprep.com/register (free, no credit card).
    # Set env var: FMP_API_KEY
    "fmp_api_key":              os.environ.get("FMP_API_KEY", ""),

    # ── FINNHUB (free tier: 60 calls/min) ────────────────────
    # Sign up at https://finnhub.io — free API key, no credit card required.
    # Free tier covers: stock quotes + company news articles (/company-news).
    # Used for: supplementing Yahoo RSS with a second news feed per symbol.
    # Set to "" (or omit FINNHUB_API_KEY env var) to disable all Finnhub calls.
    "finnhub_api_key":          os.environ.get("FINNHUB_API_KEY", ""),
    "use_finnhub":              True,   # Master switch; also gated by finnhub_api_key

    # ── AI BRAIN ──────────────────────────────────────────────
    "anthropic_api_key":        os.environ.get("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE"),
    "claude_model":             "claude-sonnet-4-6",       # Sonnet — number crunching, structured data
    "claude_max_tokens":        800,                       # Default token cap
    # Alpha agents use Opus — regime/sentiment judgment and nuanced trade decisions.
    "claude_model_alpha":       "claude-opus-4-6",         # Opus for alpha-generating agents
    "claude_max_tokens_alpha":  4096,                      # Unconstrained — let Opus reason fully
    # Haiku — text generation for voice alerts, trade cards, speech.
    "claude_model_haiku":       "claude-haiku-4-5-20251001",

    # ── INTELLIGENCE LAYER ────────────────────────────────────
    # Classifies every signal with trade_type (SCALP/SWING/HOLD/AVOID) and
    # evidence-based conviction before dispatch. Always fires — Tier 2
    # evidence fallback used when Opus is unavailable.
    "use_intelligence_layer":        True,
    "intelligence_model":            "claude-opus-4-6",
    "intelligence_max_tokens":       1024,
    "intelligence_cache_minutes":    30,    # session context cache window
    "intelligence_pattern_lookback": 20,    # similar patterns shown to Opus per call
    "intelligence_news_mode":        "full_on_open_headlines_thereafter",

    # ── TRADE ADVISOR (execution layer) ──────────────────────
    # Opus decides PT, SL, position size multiplier, and instrument
    # per signal. Separate from the intelligence layer.
    # Falls back to ATR formula if the API call fails.
    "use_llm_advisor":          True,
    "llm_advisor_model":        "claude-opus-4-6",
    "llm_advisor_max_tokens":   512,
    "llm_advisor_history":      15,   # last N completed decisions passed to Opus as learning context

    # ── IBKR RECONNECT & HEARTBEAT ────────────────────────────
    "reconnect_max_attempts":   10,     # Retry attempts before giving up
    "reconnect_max_wait_secs":  60,     # Cap on exponential backoff delay (secs)
    "reconnect_base_wait_secs": 1,      # Starting backoff delay (secs)
    "reconnect_alert_webhook":  "",     # Slack/Teams URL for reconnect-failed alert
    "heartbeat_interval_secs":  1200,   # 20 min heartbeat (reqCurrentTime)

    # ── RISK MANAGEMENT ───────────────────────────────────────
    "risk_pct_per_trade":       0.005,  # 0.5% of portfolio AT RISK per trade.
                                        # With ATR stop sizing, this yields ~4-6% position
                                        # size at neutral conditions, capped at max_single_position.
    "assumed_stop_pct":         0.04,   # 4% fallback stop when ATR is unavailable.
                                        # Primary path uses atr × atr_stop_multiplier instead.
    "risk_per_trade":           0.01,   # 1% per trade — used by legacy position_size()
    "max_position_size":        0.30,   # Max fraction of account per position (30%)
    "max_daily_loss_pct":       0.05,   # 5% max daily loss before halting
    "correlation_threshold":    0.75,   # Block new trade if correlation > this
    "max_positions":            15,     # 15 × 6% = 90% deployed; 10% cash floor stops at ~14
    "daily_loss_limit":         0.10,   # 10% daily loss limit
    "max_drawdown_alert":       0.25,   # 25% drawdown alert
    "min_cash_reserve":         0.10,   # 10% cash floor — hard stop on new entries
    "max_single_position":      0.06,   # 6% per position — keeps 14 positions before hitting floor
    "max_sector_exposure":      0.40,   # 40% sector cap
    "consecutive_loss_pause":   999,    # Paper learning mode: effectively disabled (live: 5)
    "reentry_cooldown_minutes": 30,     # Block re-entry after close (lifecycle gate)

    # ── MACRO EVENT GATE ──────────────────────────────────────────
    # Halve position sizing within 24 hours of FOMC, CPI, or NFP.
    # Controlled by macro_calendar.get_macro_size_multiplier().
    "macro_event_size_mult":    0.5,    # Multiplier applied when event is within window
    "macro_event_hours_window": 24.0,   # Hours before event to apply the multiplier

    # ── INTRADAY ADAPTIVE STRATEGY ────────────────────────────────
    # When the day is going badly, the bot shifts posture rather than trading normally
    # until a hard circuit breaker fires. Three modes: NORMAL → DEFENSIVE → RECOVERY.
    "strategy_pivot_loss_pct":           0.050,  # -5.0% daily PnL → DEFENSIVE mode (paper: raised from 1.5%)
    "strategy_recovery_loss_pct":        0.100,  # -10.0% daily PnL → RECOVERY mode (paper: raised from 3.0%)
    "strategy_defensive_streak":         10,      # Paper: raised from 3 — don't tighten on paper losses
    "strategy_recovery_streak":          20,      # Paper: raised from 6 — don't tighten on paper losses
    "thesis_invalidation_regime_change": True,    # Re-evaluate open positions on significant regime shift

    "max_portfolio_allocation": 1.0,    # 1.0 = full account, 0.2 = 20% of account
    "starting_capital":         1_000_000,  # Starting portfolio value for P&L tracking — update to match real account before going live

    # ── PDT RULE (Pattern Day Trader) ─────────────────────────
    # Applies when portfolio_value < pdt_threshold AND live account.
    # Paper accounts are exempt (IBKR paper does not enforce the SEC PDT rule).
    "pdt": {
        "enabled":       True,
        "threshold":     25_000,   # USD — below this, PDT rule is active
        "max_day_trades": 3,       # Max day trades per rolling 5 trading days
    },

    # ── TAKE PROFIT / STOP LOSS ───────────────────────────────
    "atr_stop_multiplier":      1.0,    # Stop = entry - (1.0 × ATR)  — tighter, cut losses faster
    "atr_trail_multiplier":     1.5,    # Trailing stop = 1.5 × ATR from high-water mark
    "trailing_stop_enabled":    True,   # Slide stop up as price advances (ATR-based)
    "partial_exit_1_pct":       0.02,   # Sell 33% at +2%  — take profit faster
    "partial_exit_2_pct":       0.04,   # Sell 33% at +4%, trail rest
    "min_reward_risk_ratio":    1.5,    # Minimum R:R to enter trade
    "gap_protection_pct":       0.03,   # Exit if opens 3% against position

    # ── DISAGREEMENT PROTOCOL ─────────────────────────────────
    # How many of 6 agents must agree before a trade is taken
    # 4 = conservative (default), 3 = standard, 2 = aggressive
    # NOTE: Raised from 2→3 to filter low-conviction trades (roadmap #08).
    # 2/6 was rubber-stamping; 3/6 requires real consensus. (live: 4)
    "agents_required_to_agree": 2,

    # ── MOMENTUM SENTINEL ──────────────────────────────────────
    # Background thread monitoring SPY 1m bars via BAR_CACHE (live Alpaca stream).
    # When SPY moves fast, immediately bypasses the scan scheduler and triggers a scan.
    # Follows same pattern as News/Catalyst sentinels.
    "momentum_sentinel_enabled":    True,
    "momentum_sentinel_fast_pct":   0.3,   # SPY moves > 0.3% in last 3 bars (~3 min) → fire
    "momentum_sentinel_slow_pct":   0.6,   # SPY moves > 0.6% in last 10 bars (~10 min) → fire
    "momentum_sentinel_cooldown_m": 15,    # Minutes between triggers (avoid chasing chop)
    "momentum_sentinel_poll_s":     10,    # Seconds between BAR_CACHE checks

    # ── SCANNING ──────────────────────────────────────────────
    # NOTE: Faster scans for paper trading data generation (live values in comments)
    "scan_interval_prime":        3,      # Minutes — PRIME_AM/PM (live: 5)
    "scan_interval_standard":    5,      # Minutes — LUNCH lull (live: 10)
    "scan_interval_extended":    5,      # Minutes — OPEN_BUFFER / CLOSE_BUFFER (live: 7)
    "scan_interval_pre_market":  15,     # Minutes — PRE_MARKET 4am–9:30am (reduced activity)
    "scan_interval_after_hours": 15,     # Minutes — AFTER_HOURS 4pm–8pm (reduced activity)
    "scan_interval_overnight":   60,     # Minutes — OVERNIGHT 8pm–4am (sentinel only)

    # ── SCORING THRESHOLD ─────────────────────────────────────
    # NOTE: Lowered for paper trading to capture more setups for ML training (live values in comments)
    "min_score_to_trade":       14,     # Out of 50 — lower = more trades for training (live: 28)
    "high_conviction_score":    36,     # Above this = 1.5x position size — narrower band, fewer bloated positions

    # ── DIMENSION FLAGS ───────────────────────────────────────────
    # Enable / disable individual signal dimensions without code changes.
    # Set any flag to False to zero that dimension's score and remove its
    # direction vote. Useful when Signal Decay Monitor identifies a harmful
    # or noisy dimension — flip the flag, no deploy required.
    # IC auto-disable (ic_calculator.py) will write overrides to
    # data/settings_override.json which is merged into dimension_flags at startup.
    "dimension_flags": {
        "directional":    True,   # Dim 1  — EMA alignment × ADX + timeframe vote (merged TREND+MTF)
        "momentum":       True,   # Dim 2  — MFI + RSI slope
        "squeeze":        True,   # Dim 3  — BB/Keltner compression
        "flow":           True,   # Dim 4  — VWAP + OBV
        "breakout":       True,   # Dim 5  — Donchian channel breach
        "pead":           True,   # Dim 6  — Post-Earnings Announcement Drift
        "news":           True,   # Dim 7  — Yahoo RSS + Claude sentiment
        "short_squeeze":  True,   # Dim 8  — Short float + volume surge + price vs resistance
        "reversion":      True,   # Dim 9  — Variance Ratio + OU half-life + z-score
        "overnight_drift":True,   # Dim 10 — 90-day close-to-open drift statistics
        "social":         True,   # Reddit velocity + VADER — IC auto-disable if harmful
        "iv_skew":        True,   # Dim 11 — OTM put / ATM call IV skew (Alpaca, enabled)
    },

    # ── IV SKEW (Alpaca options chain) ────────────────────────────
    # Requires Algo Trader Plus ($99/mo) for OPRA real-time options data.
    # Keys: alpaca_api_key / alpaca_secret_key (already set above).
    # Enable via dimension_flags["iv_skew"] = True once IC proves predictive value.
    # Wu & Tian (2024, Management Science): high put-call skew predicts negative
    # next-period returns — reflects both structural risk and informed order flow.
    "iv_skew": {
        "dte_min":         7,      # Earliest expiry to consider (days out)
        "dte_max":         60,     # Latest expiry to consider
        "target_dte":      30,     # Preferred expiry (closest to this wins)
        "otm_put_delta":  -0.25,   # Target delta for OTM put selection
        "atm_call_delta":  0.50,   # Target delta for ATM call selection
        # Scoring thresholds (raw skew = otm_put_IV - atm_call_IV)
        "skew_bearish_hi":  0.15,  # > 0.15 → score 10, bearish
        "skew_bearish_mid": 0.10,  # > 0.10 → score 7,  bearish
        "skew_bearish_lo":  0.05,  # > 0.05 → score 4,  bearish
        "skew_bullish_lo": -0.03,  # < -0.03 → score 3, bullish (complacency)
        # [neutral band: -0.03 to 0.05 → score 0]
    },

    # ── IC CALCULATOR ────────────────────────────────────────────
    # Controls the rolling IC-weighted signal composite (ic_calculator.py).
    # Phase 1 (current): ic_min_threshold = 0.0 — any positive IC gets weight.
    # Phase 2 (needs 100+ trades): raise ic_min_threshold to 0.03 to suppress noise.
    "ic_calculator": {
        "rolling_window":    60,    # Number of trading *dates* in rolling IC window
        "min_valid_records": 10,    # Legacy — kept for backward compat
        "min_valid_dates":   3,     # Min trading dates with IC before weights diverge from equal
        "forward_horizon_days": 1,  # Forward return horizon (trading days). 1 = next close.
                                    #   Phase 1: 1 day (fast bootstrap, noisier IC)
                                    #   Phase 2: raise to 5 once 60+ dates accumulated
        "ic_min_threshold":  0.0,   # Noise floor — dimensions below this get zero weight
                                    #   Phase 1: 0.0 (any positive IC passes)
                                    #   Phase 2: raise to 0.03 once 100+ trades available
        "max_single_weight": 0.40,  # HHI cap — no dimension may exceed this share of total weight

        # IC auto-disable: if a dimension's IC falls below the threshold for N
        # consecutive weekly updates, it is automatically disabled via
        # data/settings_override.json. Re-enabled when IC recovers above re-enable
        # threshold for M consecutive weeks.
        "auto_disable_threshold":  -0.02,  # IC below this triggers disable countdown
        "auto_disable_weeks":       3,     # Consecutive weeks before disable fires
        "auto_enable_threshold":    0.01,  # IC above this triggers re-enable countdown
        "auto_enable_weeks":        2,     # Consecutive weeks before re-enable fires

        # ── EDGE GATE (deployment throttle based on system-level IC health) ──
        # get_system_ic_health() returns mean positive IC across all active
        # dimensions. When this falls below the warn/off thresholds, the score
        # bar is raised systemwide — fewer trades, higher quality only.
        # This stacks on top of strategy_mode score_threshold_adj.
        # ── PAPER LEARNING MODE ───────────────────────────────────────
        # force_equal_weights: True → ignore IC weights, score all 12 dimensions equally.
        # Fixes the cold-start trap: dimensions with IC=0 (no data) get zero weight →
        # never generate trades → never build IC → permanently stuck at 0.
        # Enable in paper mode. Disable once all dimensions have ≥20 trades of IC data.
        "force_equal_weights":      True,

        "edge_gate_enabled":        False, # Paper learning mode: gate prevents data accumulation.
                                           # Circular: low IC → gate raises bar → fewer trades → lower IC.
                                           # Re-enable when system has proven IC > 0.02 across dims.
        "edge_gate_warn_threshold": 0.02,  # mean IC below this → degraded, raise bar +5
        "edge_gate_warn_adj":       5,     # score points added in degraded state
        "edge_gate_off_threshold":  0.005, # mean IC below this → broken, raise bar +12
        "edge_gate_off_adj":        12,    # score points added in broken state
    },

    # ── OVERNIGHT DRIFT CACHE ────────────────────────────────────
    "overnight_cache_path": "data/overnight_cache.json",

    # ── SMALL / MICRO CAP UNIVERSE ───────────────────────────────
    # Supplemental universe track for market caps $50M–$2B.
    # Smaller companies are less efficiently priced — fewer institutional
    # participants means exploitable anomalies persist longer.
    # ── SECTOR ROTATION ───────────────────────────────────────────
    "sector_rotation_enabled":    True,   # Score SPDR sector ETFs vs SPY; add leaders to universe

    # ── SYMPATHY SCANNER ──────────────────────────────────────────
    "sympathy_scanner_enabled":   True,   # Add sector peers when a leader has earnings within 48h

    # ── FX TRADING ────────────────────────────────────────────────
    # Disabled by default — enable after paper validation (2+ weeks).
    # IBKR Forex contract support already present in orders_contracts.py.
    "fx_enabled":    True,               # Master switch for FX scanning + trading
    "fx_pairs":      ["EURUSD", "GBPUSD", "USDJPY"],  # Active pairs
    "fx_min_score":  20,                 # Min composite score to generate FX signal (0-50)
    "fx_min_lot_size": 25000,            # Minimum lot size for FX orders (units of base currency) — IBKR IDEALPRO minimum

    # ── CROSS-ASSET REGIME SIGNALS ────────────────────────────────
    # DXY (dollar) and HYG/LQD (credit spread) as early risk-off indicators.
    # credit_stress overrides momentum regime router → mean_reversion.
    "cross_asset_regime_enabled": True,
    "credit_stress_threshold":    0.4,   # % spread between HYG/LQD 3d returns that triggers stress flag

    "small_cap_enabled":      True,
    "small_cap_min_score":    22,   # Slightly higher threshold (wider spreads, more risk)
    "small_cap_max_position": 0.05, # 5% max per position (vs 10% for large cap)

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

    # ── REGIME SIGNAL ROUTER ──────────────────────────────────
    # Two-state VIX router: upweights momentum dims (TREND, MOMENTUM, SQUEEZE,
    # FLOW, BREAKOUT, MTF) in low-vol markets, upweights REVERSION in high-vol.
    # Layered as a multiplier — set regime_routing_enabled: False for equal-weight
    # A/B baseline without code changes.
    "regime_routing_enabled":       True,   # A/B flag: False = equal weights
    "regime_router_vix_threshold":  25,     # VIX < 25 → "momentum"; >= 25 → "mean_reversion"
    # Raised 20→25: VIX 20-25 is "mild/transitional", not true fear. Momentum scoring
    # should stay active in this band so relief rallies and directional days score correctly.
    "regime_router_momentum_mult":  1.3,    # Momentum dim multiplier in momentum regime
    "regime_router_reversion_mult": 0.7,    # Reversion dim multiplier in momentum regime
    #                                         (roles invert in mean_reversion regime)
    # Intraday SPY move override — two triggers, either one switches the router to momentum:
    #   open-to-now: SPY up/down > 1.5% from today's open (sustained trend)
    #   2-bar ROC:   SPY moved > 0.4% in last 2 hourly bars (fast acceleration ~30-60 min)
    "regime_router_rally_override_pct": 1.5,
    "regime_router_roc_override_pct":   0.4,

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
    # Raised vix_bull_max 15→20: with the 200d daily MA as trend filter (more
    # stable than 20h EMA), VIX < 20 in an uptrend is a genuine bull regime.
    # Lowered vix_choppy_max 25→20: VIX > 20 while both SPY and QQQ are below
    # their 200d MA is a bear market, not merely choppy.
    "vix_bull_max":             20,     # VIX below this + above 200d MA = TRENDING_UP
    "vix_choppy_max":           20,     # VIX above this + below 200d MA = TRENDING_DOWN
    "vix_panic_min":            35,     # VIX above = panic — no trades
    "vix_spike_pct":            0.20,   # 20% VIX spike in 1 hour = exit all

    # ── ATR VOLATILITY-TARGETING POSITION SIZE CAP ────────────
    # Limits qty so that a 1-ATR adverse move costs at most atr_vol_target_pct
    # of portfolio. Applied after Kelly sizing — more conservative wins.
    "atr_vol_cap_enabled":      True,
    "atr_vol_target_pct":       0.01,   # 1 ATR move = 1% of portfolio value
                                         # atr_capped_qty = (portfolio * 0.01) / atr

    # ── VIX-RANK ADAPTIVE KELLY FRACTION ──────────────────────
    # kelly_fraction = base_kelly * (1 - vix_rank * max_reduction)
    # low VIX rank (calm) → fraction near base_kelly (0.50)
    # high VIX rank (panic) → fraction near base_kelly*(1-max_reduction) (0.10)
    "vix_kelly": {
        "base_kelly":        0.50,   # Kelly fraction at 0th VIX percentile
        "max_reduction":     0.80,   # Scalar applied at 100th percentile
                                      # rank=1.0 → 0.50*(1-0.80)=0.10
        "vix_lookback_days": 252,    # Trailing window for percentile calc
        "cache_ttl_seconds": 3600,   # Re-fetch VIX data at most once per hour
    },

    # ── SIGNAL-STRENGTH-PROPORTIONAL KELLY MULTIPLIER ────────────
    # Replaces the discrete 3-tier conviction_mult with continuous linear scaling.
    # Formula: t = clamp((score - score_floor) / (score_ceil - score_floor), 0, 1)
    #          conviction_mult = min_mult + t * (max_mult - min_mult)
    # Calibration: score 20→50 maps 0.5×→1.5×. Old 0.75× tier ≈ score 27.5.
    "signal_strength_kelly": {
        "score_floor": 20,   # Raw score (0–50) at which min_mult applies
        "score_ceil":  50,   # Raw score at which max_mult applies
        "min_mult":    0.5,
        "max_mult":    1.5,
    },

    # ── DRAWDOWN-PROPORTIONAL POSITION SCALER ────────────────────
    # Smoothly reduces position size as equity draws down from HWM.
    # Linear: 1.0 at 0% drawdown → min_scalar at max_drawdown_alert.
    # Returns 1.0 when HWM is not yet initialized.
    "drawdown_scaler": {
        "enabled":    True,
        "min_scalar": 0.1,
    },

    # ── MARKET BREADTH REGIME INPUT ───────────────────────────────
    # % of S&P 500 stocks above their 200-day MA (^MMTH from Yahoo Finance).
    # Used as a third factor in scanner.get_market_regime() alongside VIX
    # level and the 200-day MA trend. Breadth confirms or contradicts the
    # price-based signal: a SPY above its 200d MA with weak breadth (<50%)
    # is a narrow-leader rally, not a genuine bull regime.
    "breadth_regime": {
        "enabled":          True,
        "ticker":           "^MMTH",    # % of S&P 500 stocks above 200d MA
        "bull_min":         55.0,       # > 55% → breadth confirms BULL
        "bear_max":         40.0,       # < 40% → breadth confirms BEAR
        "cache_ttl_seconds": 3600,
    },

    # ── HURST DFA REGIME SIGNAL ───────────────────────────────────
    # Second input to the Layer 2 signal router (alongside VIX).
    # Uses DFA-1 Hurst of SPY daily closes over a 63-day window.
    # Enabled: consensus rule requires VIX+Hurst (and optionally HMM) to
    # agree before multipliers fire — prevents false tilts in transitional
    # regimes where VIX alone would be ambiguous.
    #
    # Consensus rule (3-way with HMM): majority (>50%) of participating
    # signals required. Hurst "neutral" dilutes but does not veto.
    "hurst_regime": {
        "enabled":              True,    # Live — validated DFA implementation
        "trending_threshold":   0.55,    # H > this → "trending" (momentum edge)
        "reverting_threshold":  0.45,    # H < this → "reverting" (reversion edge)
        "lookback_days":        63,      # ~1 quarter of daily closes
        "cache_ttl_seconds":    3600,    # Re-fetch SPY daily at most once per hour
    },

    # ── HMM REGIME SIGNAL ─────────────────────────────────────────
    # Third input to the Layer 2 signal router. 2-state Gaussian Hidden
    # Markov Model on SPY daily log returns, fitted via Baum-Welch EM and
    # decoded via Viterbi. Pure numpy — no external ML dependencies.
    #
    # State 0 (bear): lower mean return, higher vol → mean_reversion vote
    # State 1 (bull): higher mean return, lower vol → momentum vote
    #
    # Academic basis: Hamilton (1989) Markov regime switching. The HMM
    # is the canonical latent-state approach for financial regime detection.
    # Unlike VIX (implied vol) and Hurst (serial correlation), the HMM
    # directly models the return distribution — genuinely orthogonal signal.
    "hmm_regime": {
        "enabled":          True,
        "lookback_days":    252,        # 1 year of daily returns
        "cache_ttl_seconds": 3600,      # Re-fit at most once per hour
    },

    # ── REGIME DETECTOR LOCK ──────────────────────────────────
    # Committed approach: "vix_proxy" (scanner.get_market_regime + signals.get_market_regime_vix)
    # DO NOT change to "ml_random_forest" or "hmm" without IC Phase 2 gate review.
    # Gate: closed_trades >= 100. See DECISIONS.md Action #9.
    "regime_detector":          "vix_proxy",

    # Canonical regime state names produced by the VIX-proxy detector.
    # Any function that produces or consumes regime strings must use only these values.
    # "UNKNOWN" is the safe fallback when data is unavailable.
    "regime_states":            ("TRENDING_UP", "TRENDING_DOWN", "RELIEF_RALLY", "RANGE_BOUND", "CAPITULATION", "UNKNOWN"),

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
    "options_enabled":        True,    # Re-enabled 2026-04-11 — ask-based sizing fix validated; sizing now uses ask price as primary (not stale mid)

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
    "options_profit_target":  0.75,    # Take profit at 75% premium gain
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
    #   - 100+ closed paper trades logged to data/trades.json
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

        # ── IC + Walk-Forward Validation Gate ─────────────────────────────────
        # Hard gate before Phase 4 / live trading. All three sub-gates must pass:
        #   1. Sample gate  — enough signal records with resolved forward returns
        #   2. IC gate      — composite IC is meaningfully predictive
        #   3. Sharpe gate  — out-of-sample walk-forward Sharpe > threshold
        #
        # Run: python ic_validator.py  to evaluate and persist the result to
        # data/ic_validation_result.json.  phase_gate.validate() reads that file.
        "ic_validation_gate": {
            "min_valid_records":      50,    # records with resolved 5-day forward returns
            "min_mean_positive_ic":   0.05,  # mean IC of positive-IC dimensions
            "min_positive_dims":      5,     # at least N dimensions with IC > 0
            "min_walkforward_sharpe": 0.8,   # out-of-sample Sharpe threshold
        },

        "phase1_exit_criteria": {
            "min_closed_trades":       100,   # Trades needed before ML/backtest are meaningful
            "min_test_pass_rate":      0.80,  # Fraction of pytest tests that must pass
            "min_paper_trading_days":  30,    # Consecutive days running without critical bugs
        },
        # Features frozen until the specified phase is reached.
        # Attempting to enable these in earlier phases triggers a PhaseGateViolation.
        "frozen_features": {
            # Phase 4: multi-account, live accounts, cloud infrastructure
            "live_account_trading":      4,   # Enabling live_1 / live_2 for order execution
            "multi_account_aggregation": 4,   # aggregate_accounts with multiple live accounts
            "cloud_deployment":          4,   # Any hosted / cloud infra work
            # Phase 4 safety gate: Telegram kill switch MUST be configured before live trading.
            # phase_gate.validate() will block Phase 4 if bot_token or authorized_chat_ids is unset.
            "telegram_kill_switch":      4,   # Emergency stop via Telegram (gate for live trading)
            # Phase 4 signal gate: IC + walk-forward validation must pass before live trading.
            # ic_validator.validate_and_persist() writes data/ic_validation_result.json.
            # phase_gate.validate() reads that file when live accounts are active.
            "ic_walkforward_validation": 4,   # IC quality + out-of-sample Sharpe > 0.8
            # Phase 5: infrastructure & multi-user
            "docker_deployment":         5,   # Dockerising the bot
            "multi_user_auth":           5,   # User accounts / auth layer
            "hosted_dashboard":          5,   # Public-facing dashboard
        },
    },

    # ── CATALYST SENTINEL (real-time M&A / acquisition monitor) ──────────────
    # Two daemon threads alongside the News Sentinel:
    #   1. News thread  — polls Yahoo RSS for M&A keywords (60s interval)
    #   2. EDGAR thread — polls SEC RSS for 13D/13G/Form 4 filings (10 min)
    # Fires handle_catalyst_trigger() immediately on detection.
    "catalyst_sentinel_enabled":      True,
    "catalyst_news_poll_seconds":     60,    # News polling interval (seconds)
    "catalyst_edgar_poll_seconds":    600,   # EDGAR polling interval (10 minutes)
    "catalyst_cooldown_minutes":      60,    # Re-trigger cooldown per symbol (minutes)
    "catalyst_min_confidence":        5,     # Min agent confidence to execute trade
    "catalyst_max_trades_per_day":    2,     # Hard cap: catalyst-driven trades per trading day
    "catalyst_risk_multiplier":       0.50,  # Size multiplier vs normal sentinel (0.5 = ~1.5% portfolio)

    # ── SETTINGS OVERRIDE (written by IC auto-disable) ───────────────────────
    # data/settings_override.json may contain {"dimension_flags": {"dim": false}}
    # entries written by ic_calculator._check_ic_auto_disable(). These are merged
    # into dimension_flags below. Only dimension_flags may be overridden this way.
    "settings_override_path": "data/settings_override.json",

    # ── FILL WATCHER ──────────────────────────────────────────────
    # Background thread that monitors an unfilled limit order after placement
    # and adjusts the price in small steps to chase a fill.  Cancels the order
    # if the price ceiling is hit or max attempts are exhausted — never chases
    # a stale signal indefinitely.
    #
    # Total max order life = initial_wait_secs + max_attempts × interval_secs
    # Defaults: 30 + 3×20 = 90 seconds before cancel.
    "fill_watcher": {
        "enabled":           True,   # Master switch — set False to disable without removing code
        "initial_wait_secs": 30,     # Seconds to wait before the first price adjustment
        "max_attempts":      3,      # Max number of adjustments before cancelling
        "interval_secs":     20,     # Seconds between each adjustment attempt
        "step_pct":          0.002,  # Price chase step: 0.2% per attempt (e.g. $0.20 on a $100 stock)
        "max_chase_pct":     0.01,   # Hard ceiling: never pay more than 1% above the original limit
        "orphan_timeout_mins": 5,    # Hard cancel watcherless PENDING orders after this many minutes
    },

    # ── EXECUTION AGENT ───────────────────────────────────────────────────────
    # Decides HOW to execute a trade (order type, aggression, fill watcher params).
    # Now deterministic — same rules encoded in Python, not LLM.
    # Falls back to static fill_watcher config on any exception.
    "execution_agent": {
        "enabled":           True,   # Master switch — False = always use static fill_watcher config
        "fallback_on_error": True,   # Never block a trade on any exception
    },

    # ── PORTFOLIO MANAGER ─────────────────────────────────────────────────────
    # LLM agent that reviews open positions for thesis drift.
    # Fires: (1) pre-market once per day, (2) on any of 6 event triggers.
    # Does NOT run every scan cycle — only when information has actually changed.
    "portfolio_manager": {
        "enabled":                  True,
        "score_collapse_threshold": 15,     # pts drop from entry_score → trigger review
        "news_hit_threshold":       3,      # |keyword_score| on held symbol → trigger
        "cascade_stop_count":       2,      # stops hit this session → trigger
        "drawdown_trigger_pct":    -0.015,  # daily_pnl / portfolio_value → trigger
        "earnings_lookahead_hours": 48,     # flag earnings within this window
        "max_tokens":               600,
    },
}

# ── SETTINGS OVERRIDE MERGE ───────────────────────────────────────────────────
# Load data/settings_override.json (written by IC auto-disable) and merge only
# the dimension_flags section into CONFIG. Runs once at import time. The running
# bot picks up changes on the next scan cycle when config.py is re-evaluated.
def _apply_settings_override() -> None:
    import json, os
    path = CONFIG.get("settings_override_path", "data/settings_override.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            override = json.load(f)
        dim_overrides = override.get("dimension_flags", {})
        if not isinstance(dim_overrides, dict):
            return
        for dim, enabled in dim_overrides.items():
            if dim in CONFIG["dimension_flags"] and isinstance(enabled, bool):
                CONFIG["dimension_flags"][dim] = enabled
    except Exception:
        pass  # Never let a corrupt override file crash the bot

_apply_settings_override()
