"""
Sprint 7A.3 — Factor Registry + Provider Capability Audit

Generates four static, API-free output files:
  data/reference/factor_registry.json
  data/reference/provider_capability_matrix.json
  data/reference/layer_factor_map.json
  data/reference/data_quality_report.json

No live API calls. No broker calls. No LLM. No .env inspection.
live_output_changed = false.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent
_REF_DIR = _HERE / "data" / "reference"

# ---------------------------------------------------------------------------
# Layer constants
# ---------------------------------------------------------------------------
L_REFERENCE = "Reference Data Layer"
L_ECONOMIC = "Economic Intelligence Layer"
L_FUNDAMENTALS = "Company Quality / Fundamentals Layer"
L_CATALYST = "Catalyst / Event Intelligence Layer"
L_MARKET = "Market Sensor / Technical Layer"
L_UNIVERSE = "Universe Builder"
L_TRADING_BOT = "Trading Bot / Entry Readiness"
L_EXECUTION = "Execution / Risk Layer"
L_ADVISORY = "Advisory / Observability"
L_BACKTEST = "Backtest / Research Only"

# ---------------------------------------------------------------------------
# Factor definitions
# Each factor: factor_id, factor_name, category, owning_layer, consuming_layers,
#   providers, primary_provider, fallback_provider, must_not_trigger_trade_directly,
#   production_runtime_allowed, offline_job_allowed, update_frequency, freshness_sla
# ---------------------------------------------------------------------------
_FACTORS = [
    # --- 1. REFERENCE DATA / SYMBOL IDENTITY ---
    {"factor_id": "symbol_identity", "factor_name": "Symbol / Ticker", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_MARKET, L_CATALYST, L_EXECUTION], "providers": ["fmp", "alpaca", "local_files"], "primary_provider": "fmp", "fallback_provider": "alpaca", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "static", "freshness_sla": "weekly"},
    {"factor_id": "company_name", "factor_name": "Company Name", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_ADVISORY], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "static", "freshness_sla": "weekly"},
    {"factor_id": "exchange", "factor_name": "Exchange", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_EXECUTION], "providers": ["fmp", "alpaca"], "primary_provider": "fmp", "fallback_provider": "alpaca", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "static", "freshness_sla": "weekly"},
    {"factor_id": "sector", "factor_name": "Sector", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_ECONOMIC, L_UNIVERSE, L_ADVISORY], "providers": ["fmp", "local_files"], "primary_provider": "local_files", "fallback_provider": "fmp", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "monthly", "freshness_sla": "monthly"},
    {"factor_id": "industry", "factor_name": "Industry / Sub-Industry", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_ECONOMIC, L_UNIVERSE], "providers": ["fmp", "local_files"], "primary_provider": "local_files", "fallback_provider": "fmp", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "monthly", "freshness_sla": "monthly"},
    {"factor_id": "market_cap", "factor_name": "Market Capitalisation", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_UNIVERSE, L_EXECUTION], "providers": ["fmp", "alpaca", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "shares_outstanding", "factor_name": "Shares Outstanding", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_FUNDAMENTALS], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "asset_type", "factor_name": "Asset Type (stock/ETF/ADR)", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_UNIVERSE, L_EXECUTION], "providers": ["fmp", "alpaca", "local_files"], "primary_provider": "local_files", "fallback_provider": "fmp", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "static", "freshness_sla": "weekly"},
    {"factor_id": "optionable_status", "factor_name": "Optionable Status", "category": "reference_symbol_identity", "owning_layer": L_REFERENCE, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 2. PRICE / OHLCV ---
    {"factor_id": "ohlcv_daily", "factor_name": "Daily OHLCV Bars", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE, L_BACKTEST], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "ohlcv_intraday", "factor_name": "Intraday OHLCV Bars", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_EXECUTION], "providers": ["alpaca", "alpha_vantage"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "intraday", "freshness_sla": "real_time"},
    {"factor_id": "latest_quote", "factor_name": "Latest Bid / Ask Quote", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_EXECUTION, L_TRADING_BOT], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "previous_close", "factor_name": "Previous Close", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "gap_percent", "factor_name": "Gap Percent vs Previous Close", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "intraday", "freshness_sla": "real_time"},
    {"factor_id": "week52_high_low", "factor_name": "52-Week High and Low", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE], "providers": ["alpaca", "yfinance", "fmp"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "adjusted_history", "factor_name": "Split-Adjusted Price History", "category": "price_ohlcv", "owning_layer": L_MARKET, "consuming_layers": [L_BACKTEST], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 3. TECHNICAL INDICATORS ---
    {"factor_id": "sma_20", "factor_name": "Simple Moving Average 20", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "alpha_vantage", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "sma_50", "factor_name": "Simple Moving Average 50", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "alpha_vantage", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "sma_200", "factor_name": "Simple Moving Average 200", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "alpha_vantage", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "ema_8", "factor_name": "Exponential Moving Average 8", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "rsi", "factor_name": "RSI (14-period)", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "alpha_vantage", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "macd", "factor_name": "MACD (12/26/9)", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "alpha_vantage", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "atr", "factor_name": "Average True Range", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_EXECUTION], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "vwap", "factor_name": "VWAP", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "intraday", "freshness_sla": "real_time"},
    {"factor_id": "relative_volume", "factor_name": "Relative Volume vs 20-Day Average", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "intraday", "freshness_sla": "real_time"},
    {"factor_id": "bollinger_bands", "factor_name": "Bollinger Bands", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT], "providers": ["alpaca", "alpha_vantage", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "alpha_vantage", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "distance_from_200dma", "factor_name": "Distance from 200-Day Moving Average (%)", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "sector_relative_strength", "factor_name": "Sector Relative Strength vs SPY", "category": "technical_indicators", "owning_layer": L_MARKET, "consuming_layers": [L_UNIVERSE, L_ECONOMIC], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 4. LIQUIDITY / MICROSTRUCTURE ---
    {"factor_id": "bid_ask_spread", "factor_name": "Bid / Ask Spread", "category": "liquidity_microstructure", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION, L_TRADING_BOT], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "spread_percent", "factor_name": "Spread as % of Price", "category": "liquidity_microstructure", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "average_dollar_volume", "factor_name": "Average Dollar Volume (20-Day)", "category": "liquidity_microstructure", "owning_layer": L_EXECUTION, "consuming_layers": [L_UNIVERSE, L_EXECUTION], "providers": ["alpaca", "fmp", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "marketability", "factor_name": "Marketability Score (liquidity proxy)", "category": "liquidity_microstructure", "owning_layer": L_EXECUTION, "consuming_layers": [L_UNIVERSE, L_EXECUTION], "providers": ["local_files"], "primary_provider": "local_files", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 5. OPTIONS DATA ---
    {"factor_id": "options_availability", "factor_name": "Options Availability Flag", "category": "options_data", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION, L_TRADING_BOT], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "option_chain", "factor_name": "Option Chain (strikes, expirations)", "category": "options_data", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "option_greeks", "factor_name": "Option Greeks (delta, gamma, theta, vega)", "category": "options_data", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "implied_volatility", "factor_name": "Implied Volatility", "category": "options_data", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION, L_TRADING_BOT], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "intraday", "freshness_sla": "real_time"},
    {"factor_id": "option_spread_quality", "factor_name": "Option Spread Quality (bid/ask ratio)", "category": "options_data", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "open_interest", "factor_name": "Open Interest", "category": "options_data", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["alpaca"], "primary_provider": "alpaca", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 6. FUNDAMENTALS ---
    {"factor_id": "revenue_growth", "factor_name": "Revenue Growth (YoY %)", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_CATALYST, L_UNIVERSE, L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "eps_growth", "factor_name": "EPS Growth (YoY %)", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_CATALYST, L_UNIVERSE], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "gross_margin", "factor_name": "Gross Margin", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "free_cash_flow", "factor_name": "Free Cash Flow", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_UNIVERSE, L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "valuation_multiples", "factor_name": "P/E, EV/EBITDA, P/S, P/FCF", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_UNIVERSE, L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "roe_roic", "factor_name": "ROE and ROIC", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "balance_sheet_strength", "factor_name": "Net Debt, Cash, Current Ratio", "category": "fundamentals", "owning_layer": L_FUNDAMENTALS, "consuming_layers": [L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    # --- 7. EARNINGS / EVENTS ---
    {"factor_id": "earnings_date", "factor_name": "Earnings Date", "category": "earnings_events", "owning_layer": L_CATALYST, "consuming_layers": [L_TRADING_BOT, L_UNIVERSE, L_ADVISORY], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "eps_actual_vs_estimate", "factor_name": "EPS Actual vs Estimate + Surprise %", "category": "earnings_events", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST, L_UNIVERSE], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "at_earnings"},
    {"factor_id": "revenue_surprise", "factor_name": "Revenue Surprise %", "category": "earnings_events", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "at_earnings"},
    {"factor_id": "guidance_revision", "factor_name": "Guidance Raise / Cut", "category": "earnings_events", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST, L_UNIVERSE], "providers": ["fmp"], "primary_provider": "fmp", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "at_earnings"},
    {"factor_id": "dividends_splits", "factor_name": "Dividends and Splits Calendar", "category": "earnings_events", "owning_layer": L_CATALYST, "consuming_layers": [L_EXECUTION], "providers": ["fmp", "alpaca", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 8. ANALYST ACTIONS ---
    {"factor_id": "analyst_rating", "factor_name": "Analyst Rating (Buy/Hold/Sell)", "category": "analyst_actions", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST, L_UNIVERSE], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "upgrade_downgrade", "factor_name": "Upgrade / Downgrade Event", "category": "analyst_actions", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "price_target_consensus", "factor_name": "Analyst Price Target Consensus", "category": "analyst_actions", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST, L_UNIVERSE], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "estimate_revision_direction", "factor_name": "Estimate Revision Trend", "category": "analyst_actions", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 9. NEWS ---
    {"factor_id": "structured_company_news", "factor_name": "Structured Company News (source-labelled)", "category": "news", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST, L_ADVISORY], "providers": ["fmp"], "primary_provider": "fmp", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "intraday", "freshness_sla": "within_15_minutes"},
    {"factor_id": "press_releases", "factor_name": "Press Releases", "category": "news", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST], "providers": ["fmp"], "primary_provider": "fmp", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "intraday", "freshness_sla": "within_15_minutes"},
    # --- 10. MACRO / ECONOMIC ---
    {"factor_id": "fed_funds_rate", "factor_name": "Fed Funds Rate", "category": "macro_economic", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC], "providers": ["alpha_vantage", "fmp", "yfinance"], "primary_provider": "alpha_vantage", "fallback_provider": "fmp", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "monthly", "freshness_sla": "monthly"},
    {"factor_id": "treasury_yields", "factor_name": "Treasury Yields (2Y, 5Y, 10Y, 30Y)", "category": "macro_economic", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC], "providers": ["alpha_vantage", "yfinance", "alpaca"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "vix", "factor_name": "VIX Volatility Index", "category": "macro_economic", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC, L_UNIVERSE], "providers": ["alpaca", "fmp", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "intraday", "freshness_sla": "real_time"},
    {"factor_id": "cpi_ppi", "factor_name": "CPI and PPI", "category": "macro_economic", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC], "providers": ["alpha_vantage", "fmp"], "primary_provider": "alpha_vantage", "fallback_provider": "fmp", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "monthly", "freshness_sla": "monthly"},
    {"factor_id": "sector_etf_performance", "factor_name": "Sector ETF Daily Performance", "category": "macro_economic", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC, L_UNIVERSE], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "oil_commodities", "factor_name": "Oil / Gold / Silver / Copper Prices", "category": "macro_economic", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC], "providers": ["alpha_vantage", "alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 11. SECTOR / THEME ---
    {"factor_id": "sector_etf_relative_strength", "factor_name": "Sector ETF Relative Strength vs SPY", "category": "sector_industry_theme", "owning_layer": L_ECONOMIC, "consuming_layers": [L_UNIVERSE, L_ADVISORY], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "theme_activation_evidence", "factor_name": "Theme Activation Evidence (local shadow)", "category": "sector_industry_theme", "owning_layer": L_ECONOMIC, "consuming_layers": [L_UNIVERSE, L_ADVISORY], "providers": ["local_files"], "primary_provider": "local_files", "fallback_provider": None, "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    {"factor_id": "breadth_indicators", "factor_name": "Market Breadth (advance/decline, % above MA)", "category": "sector_industry_theme", "owning_layer": L_ECONOMIC, "consuming_layers": [L_ECONOMIC, L_UNIVERSE], "providers": ["alpaca", "yfinance"], "primary_provider": "alpaca", "fallback_provider": "yfinance", "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 12. OWNERSHIP / SHORT INTEREST / FLOW ---
    {"factor_id": "short_interest", "factor_name": "Short Interest and Short Float %", "category": "ownership_short_flow", "owning_layer": L_MARKET, "consuming_layers": [L_UNIVERSE, L_TRADING_BOT], "providers": ["fmp"], "primary_provider": "fmp", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "bi_weekly", "freshness_sla": "bi_weekly"},
    {"factor_id": "institutional_ownership", "factor_name": "Institutional Ownership %", "category": "ownership_short_flow", "owning_layer": L_MARKET, "consuming_layers": [L_BACKTEST], "providers": ["fmp", "yfinance"], "primary_provider": "fmp", "fallback_provider": "yfinance", "production_runtime_allowed": False, "offline_job_allowed": True, "update_frequency": "quarterly", "freshness_sla": "quarterly"},
    {"factor_id": "insider_transactions", "factor_name": "Insider Transactions (Form 4)", "category": "ownership_short_flow", "owning_layer": L_CATALYST, "consuming_layers": [L_CATALYST, L_BACKTEST], "providers": ["fmp"], "primary_provider": "fmp", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": True, "update_frequency": "daily", "freshness_sla": "end_of_day"},
    # --- 13. RISK / PORTFOLIO / BROKER ---
    {"factor_id": "current_positions", "factor_name": "Current Positions", "category": "risk_portfolio_broker", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["ibkr"], "primary_provider": "ibkr", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "buying_power", "factor_name": "Buying Power / Cash Available", "category": "risk_portfolio_broker", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["ibkr"], "primary_provider": "ibkr", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "order_status", "factor_name": "Order Status and Fills", "category": "risk_portfolio_broker", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION], "providers": ["ibkr"], "primary_provider": "ibkr", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "portfolio_pnl", "factor_name": "Portfolio P&L (realised and unrealised)", "category": "risk_portfolio_broker", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION, L_ADVISORY], "providers": ["ibkr"], "primary_provider": "ibkr", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
    {"factor_id": "sector_exposure", "factor_name": "Sector / Symbol Portfolio Exposure", "category": "risk_portfolio_broker", "owning_layer": L_EXECUTION, "consuming_layers": [L_EXECUTION, L_ADVISORY], "providers": ["ibkr"], "primary_provider": "ibkr", "fallback_provider": None, "production_runtime_allowed": True, "offline_job_allowed": False, "update_frequency": "real_time", "freshness_sla": "real_time"},
]

# Inject must_not_trigger_trade_directly = True for every factor
for _f in _FACTORS:
    _f["must_not_trigger_trade_directly"] = True


# ---------------------------------------------------------------------------
# Provider capability matrix definitions
# ---------------------------------------------------------------------------
_PROVIDER_CAPS = [
    # --- ALPACA ---
    {"provider_name": "alpaca", "factor_category": "price_ohlcv", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Algo Trader Plus (active)", "official_source": True, "adjusted_data_supported": True, "realtime_supported": True, "historical_supported": True, "intraday_supported": True, "options_supported": True, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Options data requires Algo Trader Plus; no fundamentals", "recommended_layer": L_MARKET, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpaca", "factor_category": "technical_indicators", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Algo Trader Plus (active)", "official_source": True, "adjusted_data_supported": True, "realtime_supported": True, "historical_supported": True, "intraday_supported": True, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Technical indicators computed locally from OHLCV; not served as named endpoints by Alpaca", "recommended_layer": L_MARKET, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpaca", "factor_category": "liquidity_microstructure", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Algo Trader Plus (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": True, "historical_supported": False, "intraday_supported": True, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Latest quote only; no historical spread data", "recommended_layer": L_EXECUTION, "live_runtime_allowed": True, "offline_job_allowed": False, "must_not_import_in_live_bot": False},
    {"provider_name": "alpaca", "factor_category": "options_data", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Algo Trader Plus (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": True, "historical_supported": True, "intraday_supported": True, "options_supported": True, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Option chain read is confirmed working; Greek precision depends on plan tier", "recommended_layer": L_EXECUTION, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpaca", "factor_category": "macro_economic", "supported": True, "production_suitability": "secondary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Algo Trader Plus (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": True, "historical_supported": True, "intraday_supported": True, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": True, "known_limitations": "VIX, SPY, sector ETF prices available; macro indicators (CPI/PPI) not direct endpoints", "recommended_layer": L_ECONOMIC, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpaca", "factor_category": "risk_portfolio_broker", "supported": False, "production_suitability": "not_suitable", "requires_api_key": True, "requires_broker_connection": True, "requires_market_data_subscription": False, "rate_limit_known": False, "cost_or_plan_dependency": "IBKR execution account only", "official_source": False, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": False, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Portfolio/position/order state must come from IBKR, not Alpaca MCP", "recommended_layer": L_EXECUTION, "live_runtime_allowed": False, "offline_job_allowed": False, "must_not_import_in_live_bot": True},
    # --- FMP ---
    {"provider_name": "fmp", "factor_category": "reference_symbol_identity", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium (750 calls/min, active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": True, "analyst_supported": True, "news_supported": True, "macro_supported": True, "known_limitations": "v3 endpoints deprecated; use /stable/ endpoints only", "recommended_layer": L_REFERENCE, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "fmp", "factor_category": "fundamentals", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": True, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Quarterly cadence; not suitable for real-time signals", "recommended_layer": L_FUNDAMENTALS, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "fmp", "factor_category": "earnings_events", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Confirmed working for earnings calendar and EPS data", "recommended_layer": L_CATALYST, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "fmp", "factor_category": "analyst_actions", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": True, "news_supported": False, "macro_supported": False, "known_limitations": "Price target consensus confirmed; upgrades/downgrades may return empty for some symbols", "recommended_layer": L_CATALYST, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "fmp", "factor_category": "news", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": True, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": True, "macro_supported": False, "known_limitations": "Structured and source-labelled; confirmed working via /stable/news/stock", "recommended_layer": L_CATALYST, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "fmp", "factor_category": "ownership_short_flow", "supported": True, "production_suitability": "secondary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium (active)", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Short interest returned empty in test for some symbols; availability varies", "recommended_layer": L_MARKET, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    # --- ALPHA VANTAGE ---
    {"provider_name": "alpha_vantage", "factor_category": "price_ohlcv", "supported": True, "production_suitability": "fallback_only", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Free plan (25 req/day); premium upgrades available", "official_source": True, "adjusted_data_supported": True, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": True, "analyst_supported": False, "news_supported": False, "macro_supported": True, "known_limitations": "TIME_SERIES_DAILY_ADJUSTED is premium; free plan rate-limited to 25/day; not production-primary", "recommended_layer": L_MARKET, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpha_vantage", "factor_category": "technical_indicators", "supported": True, "production_suitability": "secondary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Free plan; rate-limited", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "RSI/MACD available but rate-limited on free plan; hit in testing", "recommended_layer": L_MARKET, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpha_vantage", "factor_category": "macro_economic", "supported": True, "production_suitability": "secondary_candidate", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Free plan; rate-limited", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": True, "known_limitations": "CPI/PPI/GDP/payrolls available; OVERVIEW requires premium; confirmed TIME_SERIES_DAILY working", "recommended_layer": L_ECONOMIC, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "alpha_vantage", "factor_category": "fundamentals", "supported": True, "production_suitability": "fallback_only", "requires_api_key": True, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "Premium required for OVERVIEW endpoint; free plan rate-limited", "official_source": True, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": True, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "OVERVIEW endpoint hit premium gate in testing; not suitable as primary", "recommended_layer": L_FUNDAMENTALS, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    # --- YFINANCE ---
    {"provider_name": "yfinance", "factor_category": "price_ohlcv", "supported": True, "production_suitability": "fallback_only", "requires_api_key": False, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": False, "cost_or_plan_dependency": "Free; unofficial; no SLA", "official_source": False, "adjusted_data_supported": True, "realtime_supported": False, "historical_supported": True, "intraday_supported": True, "options_supported": True, "fundamentals_supported": True, "analyst_supported": True, "news_supported": False, "macro_supported": False, "known_limitations": "Unofficial API; no uptime SLA; scraping risk; must not be production primary; confirmed OHLCV and info working", "recommended_layer": L_BACKTEST, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "yfinance", "factor_category": "fundamentals", "supported": True, "production_suitability": "research_only", "requires_api_key": False, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": False, "cost_or_plan_dependency": "Free; unofficial", "official_source": False, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": True, "intraday_supported": False, "options_supported": False, "fundamentals_supported": True, "analyst_supported": True, "news_supported": False, "macro_supported": False, "known_limitations": "research/fallback only; not production-primary", "recommended_layer": L_BACKTEST, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    # --- IBKR ---
    {"provider_name": "ibkr", "factor_category": "risk_portfolio_broker", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": False, "requires_broker_connection": True, "requires_market_data_subscription": True, "rate_limit_known": False, "cost_or_plan_dependency": "TWS Gateway + market data subscriptions required", "official_source": True, "adjusted_data_supported": False, "realtime_supported": True, "historical_supported": True, "intraday_supported": True, "options_supported": True, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Gateway unavailable in this environment (IBKR_HOST not configured); no account/order/position calls in this audit", "recommended_layer": L_EXECUTION, "live_runtime_allowed": True, "offline_job_allowed": False, "must_not_import_in_live_bot": False},
    {"provider_name": "ibkr", "factor_category": "price_ohlcv", "supported": True, "production_suitability": "secondary_candidate", "requires_api_key": False, "requires_broker_connection": True, "requires_market_data_subscription": True, "rate_limit_known": False, "cost_or_plan_dependency": "Market data subscriptions; gateway required", "official_source": True, "adjusted_data_supported": True, "realtime_supported": True, "historical_supported": True, "intraday_supported": True, "options_supported": True, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Historical data availability depends on subscriptions; gateway not available in this environment", "recommended_layer": L_MARKET, "live_runtime_allowed": True, "offline_job_allowed": False, "must_not_import_in_live_bot": False},
    # --- LOCAL FILES ---
    {"provider_name": "local_files", "factor_category": "reference_symbol_identity", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": False, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "None", "official_source": False, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": False, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Static bootstrap only; must be refreshed from provider periodically; sector/industry classification confirmed working via _SECTOR_MAP", "recommended_layer": L_REFERENCE, "live_runtime_allowed": True, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
    {"provider_name": "local_files", "factor_category": "sector_industry_theme", "supported": True, "production_suitability": "primary_candidate", "requires_api_key": False, "requires_broker_connection": False, "requires_market_data_subscription": False, "rate_limit_known": True, "cost_or_plan_dependency": "None", "official_source": False, "adjusted_data_supported": False, "realtime_supported": False, "historical_supported": False, "intraday_supported": False, "options_supported": False, "fundamentals_supported": False, "analyst_supported": False, "news_supported": False, "macro_supported": False, "known_limitations": "Advisory/shadow data only; theme_activation.json, thesis_store.json, economic_candidate_feed.json from local shadow pipeline", "recommended_layer": L_ECONOMIC, "live_runtime_allowed": False, "offline_job_allowed": True, "must_not_import_in_live_bot": False},
]


def _build_factor_registry() -> dict:
    categories = sorted({f["category"] for f in _FACTORS})
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factor_registry",
        "total_factors": len(_FACTORS),
        "categories": categories,
        "factors": _FACTORS,
        "live_output_changed": False,
        "llm_called": False,
        "live_api_called": False,
        "env_inspected": False,
    }


def _build_provider_capability_matrix() -> dict:
    providers = sorted({c["provider_name"] for c in _PROVIDER_CAPS})
    by_provider = {}
    for c in _PROVIDER_CAPS:
        p = c["provider_name"]
        by_provider.setdefault(p, []).append({k: v for k, v in c.items() if k != "provider_name"})

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factor_registry",
        "provider_count": len(providers),
        "providers": [
            {"provider_name": p, "capabilities": by_provider[p]}
            for p in providers
        ],
        "live_output_changed": False,
    }


def _build_layer_factor_map() -> dict:
    layer_order = [L_REFERENCE, L_ECONOMIC, L_FUNDAMENTALS, L_CATALYST,
                   L_MARKET, L_UNIVERSE, L_TRADING_BOT, L_EXECUTION,
                   L_ADVISORY, L_BACKTEST]
    layer_map = {l: [] for l in layer_order}
    for f in _FACTORS:
        layer = f["owning_layer"]
        if layer in layer_map:
            layer_map[layer].append(f["factor_id"])

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factor_registry",
        "layers": [
            {
                "layer_id": l.lower().replace(" ", "_").replace("/", "_"),
                "layer_name": l,
                "factor_ids": layer_map[l],
                "factor_count": len(layer_map[l]),
            }
            for l in layer_order
        ],
        "live_output_changed": False,
    }


def _build_data_quality_report() -> dict:
    categories = sorted({f["category"] for f in _FACTORS})

    # Determine coverage per category
    coverage = []
    production_ready = []
    partial = []
    unavailable = []

    cat_primary = {}
    for c in _PROVIDER_CAPS:
        if c["production_suitability"] in ("primary_candidate",):
            cat = c["factor_category"]
            cat_primary.setdefault(cat, []).append(c["provider_name"])

    cat_factors = {}
    for f in _FACTORS:
        cat_factors.setdefault(f["category"], []).append(f["factor_id"])

    for cat in categories:
        primaries = cat_primary.get(cat, [])
        factors_in_cat = cat_factors.get(cat, [])
        prod_runtime_factors = [f for f in _FACTORS if f["category"] == cat and f["production_runtime_allowed"]]

        if cat == "risk_portfolio_broker":
            status = "requires_broker_subscription"
            prod_ready = False
            partial.append(cat)
        elif len(primaries) > 0 and len(prod_runtime_factors) > 0:
            status = "covered"
            prod_ready = True
            production_ready.append(cat)
        elif len(primaries) > 0:
            status = "partially_covered"
            prod_ready = False
            partial.append(cat)
        else:
            status = "unavailable"
            prod_ready = False
            unavailable.append(cat)

        primary = primaries[0] if primaries else None
        fallback_sources = [c["provider_name"] for c in _PROVIDER_CAPS
                            if c["factor_category"] == cat and c["production_suitability"] in ("secondary_candidate", "fallback_only")]

        coverage.append({
            "category": cat,
            "coverage_status": status,
            "primary_provider": primary,
            "fallback_providers": fallback_sources,
            "production_ready": prod_ready,
            "factors_in_category": len(factors_in_cat),
            "production_runtime_factors": len(prod_runtime_factors),
        })

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factor_registry",
        "provider_summary": {
            "alpaca": {"status": "active", "suitability": "primary for market data, quotes, options", "plan": "Algo Trader Plus"},
            "fmp": {"status": "active", "suitability": "primary for fundamentals, events, analyst, news", "plan": "Premium (750 calls/min)"},
            "alpha_vantage": {"status": "active_limited", "suitability": "secondary/fallback; free plan rate-limited", "plan": "Free (25 req/day)"},
            "yfinance": {"status": "active_research_only", "suitability": "research/fallback only; unofficial", "plan": "Free"},
            "ibkr": {"status": "unavailable_gateway", "suitability": "primary for broker/execution layer; gateway not configured", "plan": "TWS required"},
            "local_files": {"status": "active", "suitability": "primary for reference/shadow layer", "plan": "None"},
        },
        "factor_coverage_summary": coverage,
        "missing_factor_summary": {
            "categories_unavailable": unavailable,
            "categories_partial": partial,
            "notes": "risk_portfolio_broker requires IBKR gateway; fundamentals offline-only; macro partially available",
        },
        "duplicate_factor_sources": [
            {"factor": "ohlcv_daily", "providers": ["alpaca", "alpha_vantage", "yfinance"], "resolution": "alpaca primary; others fallback"},
            {"factor": "fundamentals", "providers": ["fmp", "yfinance"], "resolution": "fmp primary; yfinance research fallback"},
        ],
        "recommended_primary_sources": {
            "market_data_ohlcv": "alpaca",
            "quotes_real_time": "alpaca",
            "options": "alpaca",
            "fundamentals": "fmp",
            "earnings_calendar": "fmp",
            "analyst_actions": "fmp",
            "structured_news": "fmp",
            "macro_indicators": "alpha_vantage_or_fmp",
            "broker_execution": "ibkr",
            "reference_classification": "local_files_then_fmp",
        },
        "recommended_fallback_sources": {
            "market_data_ohlcv": "yfinance",
            "fundamentals": "yfinance",
            "macro_indicators": "yfinance",
        },
        "production_blockers": [
            {"blocker": "IBKR gateway not configured", "affected_categories": ["risk_portfolio_broker"], "severity": "high", "resolution": "configure IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID for live deployment"},
            {"blocker": "Alpha Vantage free plan rate-limited (25/day)", "affected_categories": ["technical_indicators", "macro_economic"], "severity": "medium", "resolution": "upgrade plan or use alpaca/fmp as primary; alpha_vantage as fallback only"},
            {"blocker": "FMP /v3/ legacy endpoints deprecated", "affected_categories": ["reference_symbol_identity", "fundamentals"], "severity": "low", "resolution": "already using /stable/ endpoints; audit remaining callers"},
        ],
        "data_license_risks": [
            {"provider": "yfinance", "risk": "unofficial scraping API; ToS risk; no SLA", "recommendation": "research/backtest only; not production primary"},
            {"provider": "fmp", "risk": "paid subscription required; license terms apply to commercial use", "recommendation": "review FMP commercial license for production deployment"},
        ],
        "rate_limit_risks": [
            {"provider": "alpha_vantage", "risk": "25 requests/day on free plan; premium upgrade required for production", "current_status": "rate_limited_in_testing"},
            {"provider": "alpaca", "risk": "rate limits apply; monitor under high-frequency scan cycles", "current_status": "within_limits"},
        ],
        "cloud_runtime_implications": {
            "alpaca": "low — REST API, no gateway required",
            "fmp": "low — REST API, no gateway required",
            "alpha_vantage": "low — REST API; rate limit is a concern",
            "ibkr": "high — requires persistent TWS/IB Gateway connection; firewall/NAT handling required in cloud",
            "yfinance": "medium — unofficial; risk of blocking or breakage in headless/cloud environment",
        },
        "next_actions": [
            "Configure IBKR gateway for execution-layer testing",
            "Upgrade Alpha Vantage plan or confirm alpha_vantage is fallback only",
            "Audit all FMP callers to confirm /stable/ endpoint migration complete",
            "Add provider health-check job to daily intelligence pipeline",
            "Add yfinance guarded import to prevent accidental production use",
        ],
        "production_ready_categories": len(production_ready),
        "partial_categories": len(partial),
        "unavailable_categories": len(unavailable),
        "live_output_changed": False,
        "data_provider_api_called": False,   # static generator — no API calls of any kind
        "live_trading_api_called": False,
        "env_presence_checked": False,
        "env_values_logged": False,
        "secrets_exposed": False,
    }


def build(base_dir: str | None = None) -> None:
    ref_dir = _REF_DIR if base_dir is None else Path(base_dir) / "data" / "reference"
    os.makedirs(ref_dir, exist_ok=True)

    registry = _build_factor_registry()
    cap_matrix = _build_provider_capability_matrix()
    layer_map = _build_layer_factor_map()
    quality = _build_data_quality_report()

    for name, data in [
        ("factor_registry.json", registry),
        ("provider_capability_matrix.json", cap_matrix),
        ("layer_factor_map.json", layer_map),
        ("data_quality_report.json", quality),
    ]:
        path = ref_dir / name
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  wrote {path.relative_to(_HERE)}")

    print(f"\nFactor registry: {registry['total_factors']} factors across {len(registry['categories'])} categories")
    print(f"Provider capabilities: {cap_matrix['provider_count']} providers")
    print(f"Layer map: {len(layer_map['layers'])} layers")
    print(f"Data quality: {quality['production_ready_categories']} production-ready, {quality['partial_categories']} partial, {quality['unavailable_categories']} unavailable categories")
    print(f"Safety: data_provider_api_called=false, live_trading_api_called=false, live_output_changed=false")


if __name__ == "__main__":
    build()
