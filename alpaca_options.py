# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpaca_options.py                         ║
# ║   Single responsibility: Alpaca options REST data adapter.   ║
# ║   Wraps Algo Trader Plus OPRA feed into canonical shapes     ║
# ║   used by options.py and options_scanner.py.                 ║
# ║                                                              ║
# ║   Exposed API:                                               ║
# ║     get_all_chains(symbol, min_dte, max_dte)                 ║
# ║       → list[{"calls", "puts", "expiry_str", "dte"}]         ║
# ║     get_chain(symbol, min_dte, max_dte)                      ║
# ║       → nearest expiry dict | None                           ║
# ║     get_snapshot_greeks(option_symbol)                       ║
# ║       → dict with live Greeks + IV + bid/ask | None          ║
# ║     get_underlying_price(symbol)                             ║
# ║       → float | None  (BAR_CACHE → REST)                     ║
# ║     build_option_symbol(symbol, expiry_ibkr, right, strike)  ║
# ║       → OCC symbol string                                    ║
# ║                                                              ║
# ║   No trading logic. No signals. Data only.                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta

import pandas as pd

from config import CONFIG

log = logging.getLogger("decifer.alpaca_options")

# ── Lazy client singleton ─────────────────────────────────────────────
_client_lock = threading.Lock()
_options_client = None


def _get_client():
    """Return a lazily-created OptionHistoricalDataClient, or None if keys missing."""
    global _options_client
    if _options_client is not None:
        return _options_client
    with _client_lock:
        if _options_client is not None:
            return _options_client
        api_key = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")
        if not api_key or not secret_key:
            log.debug("alpaca_options: ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
            return None
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
        except ImportError as exc:
            log.error(f"alpaca_options: alpaca-py import failed ({exc}) — run: pip3 install alpaca-py")
            return None
        try:
            _options_client = OptionHistoricalDataClient(api_key, secret_key)
            log.info("alpaca_options: OptionHistoricalDataClient initialised")
            return _options_client
        except Exception as exc:
            log.error(f"alpaca_options: client init failed — {type(exc).__name__}: {exc}")
            return None


# ── OCC option symbol helpers ─────────────────────────────────────────
# OCC format: [underlying][YYMMDD][C/P][8-digit price in 1/1000 dollars]
# e.g. AAPL240119C00150000 = AAPL, 2024-01-19, Call, $150.000
_OCC_RE = re.compile(r"^([A-Z ]{1,6})(\d{6})([CP])(\d{8})$")


def _parse_option_symbol(sym: str) -> tuple | None:
    """
    Parse an OCC option symbol.
    Returns (underlying_str, exp_date, opt_type_char, strike_float) or None.
    """
    m = _OCC_RE.match(sym.strip())
    if not m:
        return None
    underlying = m.group(1).strip()
    exp_date = datetime.strptime(m.group(2), "%y%m%d").date()
    opt_type = m.group(3)  # 'C' or 'P'
    strike = int(m.group(4)) / 1000.0
    return underlying, exp_date, opt_type, strike


def build_option_symbol(symbol: str, expiry_ibkr: str, right: str, strike: float) -> str:
    """
    Build an OCC option symbol.
    expiry_ibkr : 'YYYYMMDD'  (IBKR / Decifer internal format)
    right       : 'C' or 'P'
    strike      : dollars (float)
    Returns e.g. 'AAPL240119C00150000'
    """
    date_str = datetime.strptime(expiry_ibkr, "%Y%m%d").strftime("%y%m%d")
    price_str = f"{round(strike * 1000):08d}"
    return f"{symbol}{date_str}{right}{price_str}"


# ── Snapshot → canonical DataFrame ───────────────────────────────────


def _snapshots_to_df(snapshots: dict, opt_type: str) -> pd.DataFrame:
    """
    Convert an Alpaca OptionSnapshot dict → canonical chain DataFrame.
    opt_type: 'C' (calls) or 'P' (puts).

    Output columns match the yfinance option_chain schema so that
    existing _select_strike() / _analyse_symbol() logic works unchanged:
        strike, bid, ask, mid, spread_pct,
        volume, openInterest, impliedVolatility,
        delta, gamma, theta, vega,
        option_symbol (OCC — used by build/lookup callers)
    """
    rows = []
    for sym, snap in snapshots.items():
        parsed = _parse_option_symbol(sym)
        if parsed is None:
            continue
        _, _exp_date, s_type, strike = parsed
        if s_type != opt_type:
            continue

        # ── Quote ──────────────────────────────────────────────────
        bid = ask = 0.0
        if snap.latest_quote is not None:
            bid = float(snap.latest_quote.bid_price or 0)
            ask = float(snap.latest_quote.ask_price or 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        spread_pct = (ask - bid) / mid if mid > 0 else 1.0

        # ── Volume ─────────────────────────────────────────────────
        # Alpaca options snapshot doesn't expose daily volume.
        # Use bid_size + ask_size as a liquidity proxy — small quoted
        # sizes indicate nobody is trading this strike; large sizes
        # indicate active market-maker interest.
        volume = 0
        if snap.latest_quote is not None:
            volume = int((snap.latest_quote.bid_size or 0) + (snap.latest_quote.ask_size or 0))

        # ── OI and IV ──────────────────────────────────────────────
        # Alpaca snapshot also doesn't expose open interest — use the
        # same quoted-size proxy scaled up (OI is always >> single-day size).
        # The spread filter is the primary liquidity gate for Alpaca chains.
        oi = volume * 5
        iv = float(snap.implied_volatility or 0) if snap.implied_volatility else 0.0

        # ── Greeks ─────────────────────────────────────────────────
        delta = gamma = theta = vega = None
        if snap.greeks is not None:
            delta = snap.greeks.delta
            gamma = snap.greeks.gamma
            theta = snap.greeks.theta
            vega = snap.greeks.vega

        rows.append(
            {
                "strike": strike,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_pct": spread_pct,
                "volume": volume,
                "openInterest": oi,
                "impliedVolatility": iv,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "option_symbol": sym,
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    return df


# ── Public API ────────────────────────────────────────────────────────


def get_all_chains(symbol: str, min_dte: int, max_dte: int) -> list[dict]:
    """
    Fetch options chains for all expiries in [min_dte, max_dte] window.

    Returns a list sorted by DTE ascending, each entry:
        {"calls": DataFrame, "puts": DataFrame,
         "expiry_str": "YYYY-MM-DD", "dte": int}

    Returns [] if Alpaca is unavailable, keys missing, or chain is empty.
    """
    client = _get_client()
    if client is None:
        return []

    today = date.today()
    date_min = today + timedelta(days=min_dte)
    date_max = today + timedelta(days=max_dte)

    try:
        from alpaca.data.requests import OptionChainRequest

        request = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=date_min,
            expiration_date_lte=date_max,
        )
        snapshots = client.get_option_chain(request)
        if not snapshots:
            log.warning(
                f"alpaca_options.get_all_chains {symbol}: Alpaca returned empty chain "
                f"(DTE window {min_dte}-{max_dte}, dates {date_min}–{date_max})"
            )
            return []
    except Exception as exc:
        log.warning(f"alpaca_options.get_all_chains {symbol}: API call failed — {exc}")
        return []

    # Group snapshots by expiry date
    expiry_groups: dict[str, dict] = {}
    for sym, snap in snapshots.items():
        parsed = _parse_option_symbol(sym)
        if parsed is None:
            continue
        _, exp_date, _, _ = parsed
        exp_str = exp_date.strftime("%Y-%m-%d")
        if exp_str not in expiry_groups:
            expiry_groups[exp_str] = {}
        expiry_groups[exp_str][sym] = snap

    results = []
    for exp_str, exp_snaps in expiry_groups.items():
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_date - today).days
        calls_df = _snapshots_to_df(exp_snaps, "C")
        puts_df = _snapshots_to_df(exp_snaps, "P")
        if calls_df.empty and puts_df.empty:
            continue
        results.append(
            {
                "calls": calls_df,
                "puts": puts_df,
                "expiry_str": exp_str,
                "dte": dte,
            }
        )

    results.sort(key=lambda x: x["dte"])
    return results


def get_chain(symbol: str, min_dte: int, max_dte: int) -> dict | None:
    """
    Fetch the nearest valid expiry chain in [min_dte, max_dte] window.
    Returns {"calls": df, "puts": df, "expiry_str": str, "dte": int} or None.
    """
    chains = get_all_chains(symbol, min_dte, max_dte)
    return chains[0] if chains else None


def get_snapshot_greeks(option_symbol: str) -> dict | None:
    """
    Fetch real-time Greeks + IV + bid/ask for a specific option contract.
    option_symbol: OCC format (e.g. "AAPL240119C00150000")

    Returns dict with keys: delta, gamma, theta, vega, iv, bid, ask, mid, source
    or None if Alpaca unavailable or contract not found.
    """
    client = _get_client()
    if client is None:
        return None

    try:
        from alpaca.data.requests import OptionSnapshotRequest

        request = OptionSnapshotRequest(symbol_or_symbols=option_symbol)
        result = client.get_option_snapshot(request)
        if not result or option_symbol not in result:
            return None
        snap = result[option_symbol]
    except Exception as exc:
        log.debug(f"alpaca_options.get_snapshot_greeks {option_symbol}: {exc}")
        return None

    bid = ask = 0.0
    if snap.latest_quote is not None:
        bid = float(snap.latest_quote.bid_price or 0)
        ask = float(snap.latest_quote.ask_price or 0)
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else None

    greeks: dict = {}
    if snap.greeks is not None:
        if snap.greeks.delta is not None:
            greeks["delta"] = round(float(snap.greeks.delta), 4)
        if snap.greeks.gamma is not None:
            greeks["gamma"] = round(float(snap.greeks.gamma), 5)
        if snap.greeks.theta is not None:
            greeks["theta"] = round(float(snap.greeks.theta), 4)
        if snap.greeks.vega is not None:
            greeks["vega"] = round(float(snap.greeks.vega), 4)

    iv = float(snap.implied_volatility) if snap.implied_volatility else None

    return {
        **greeks,
        "iv": iv,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "source": "alpaca_live",
    }


def get_underlying_price(symbol: str) -> float | None:
    """
    Get the latest underlying equity price.
    Layer 1: BAR_CACHE (live Alpaca 1-min stream — already running).
    Layer 2: Alpaca REST latest bar.
    Returns None if both layers fail.
    """
    # Layer 1 — live stream cache (free, no extra call)
    try:
        from alpaca_stream import BAR_CACHE

        df = BAR_CACHE.get_5m(symbol)
        if df is not None and not df.empty:
            price = float(df["Close"].iloc[-1])
            if price > 0:
                return price
    except Exception:
        pass

    # Layer 2 — Alpaca REST latest bar
    client = _get_client()
    if client is None:
        return None
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestBarRequest

        api_key = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")
        stock_client = StockHistoricalDataClient(api_key, secret_key)
        req = StockLatestBarRequest(symbol_or_symbols=symbol)
        result = stock_client.get_stock_latest_bar(req)
        if result and symbol in result:
            return float(result[symbol].close)
    except Exception as exc:
        log.debug(f"alpaca_options.get_underlying_price REST {symbol}: {exc}")

    return None
