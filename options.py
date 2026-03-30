# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options.py                                 ║
# ║   Options chain analysis, IV rank, strike selection,         ║
# ║   Greeks via py_vollib (model) + IBKR (real-time)            ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import numpy as np
import warnings
from datetime import datetime, date, timedelta

import yfinance as yf

from config import CONFIG

log = logging.getLogger("decifer.options")

try:
    from py_vollib.black_scholes.greeks.analytical import (
        delta as _bs_delta,
        gamma as _bs_gamma,
        theta as _bs_theta,
        vega  as _bs_vega,
    )
    from py_vollib.black_scholes import black_scholes as _bs_price
    _VOLLIB_OK = True
except ImportError:
    _VOLLIB_OK = False
    log.warning("py_vollib not installed — Greeks will fall back to estimates. "
                "Run: pip install py_vollib")

# Risk-free rate used for Black-Scholes (approx Fed funds rate)
_RISK_FREE = 0.05


# ── Greeks ────────────────────────────────────────────────────────────

def calculate_greeks(flag: str, S: float, K: float,
                     dte: int, iv: float) -> dict:
    """
    Calculate Black-Scholes Greeks via py_vollib.
    flag: 'c' = call, 'p' = put
    S:    underlying price
    K:    strike price
    dte:  days to expiration
    iv:   implied volatility (decimal, e.g. 0.35 for 35%)
    Returns dict with delta, gamma, theta (per day), vega, model_price.
    Falls back to rough estimates if py_vollib is unavailable.
    """
    t = max(dte, 1) / 365.0
    iv = max(iv, 0.01)   # floor at 1% to avoid math errors

    if _VOLLIB_OK:
        try:
            return {
                "delta":       round(_bs_delta(flag, S, K, t, _RISK_FREE, iv), 4),
                "gamma":       round(_bs_gamma(flag, S, K, t, _RISK_FREE, iv), 5),
                "theta":       round(_bs_theta(flag, S, K, t, _RISK_FREE, iv), 4),
                "vega":        round(_bs_vega (flag, S, K, t, _RISK_FREE, iv), 4),
                "model_price": round(_bs_price(flag, S, K, t, _RISK_FREE, iv), 4),
            }
        except Exception as e:
            log.debug(f"py_vollib error ({flag} S={S} K={K} iv={iv}): {e}")

    # Rough fallback — linear delta approximation
    moneyness = S / K if flag == "c" else K / S
    est_delta = float(np.clip(0.5 + (moneyness - 1) * 2, 0.01, 0.99))
    if flag == "p":
        est_delta = -est_delta
    return {
        "delta":       round(est_delta, 4),
        "gamma":       None,
        "theta":       None,
        "vega":        None,
        "model_price": None,
    }


def get_ibkr_greeks(ib, symbol: str, expiry: str,
                    strike: float, right: str) -> dict | None:
    """
    Fetch real-time Greeks from IBKR for an options contract.
    expiry: YYYYMMDD string (IBKR format)
    right:  'C' or 'P'
    Returns dict with delta, gamma, theta, vega, iv — or None on failure.
    Cancels the market data subscription after reading.
    """
    if ib is None:
        return None
    try:
        from ib_async import Option as IBOption
        contract = IBOption(symbol, expiry, strike, right, exchange="SMART", currency="USD")
        ib.qualifyContracts(contract)
        # Request delayed data (type 3) as fallback if no live options subscription
        ib.reqMarketDataType(3)
        ticker = ib.reqMktData(contract, genericTickList="100", snapshot=False)
        ib.sleep(3)  # slightly longer to allow delayed data to arrive
        mg = ticker.modelGreeks
        ib.cancelMktData(contract)
        # Restore live data type for other requests
        ib.reqMarketDataType(1)
        if mg and mg.delta is not None:
            return {
                "delta": round(float(mg.delta), 4),
                "gamma": round(float(mg.gamma), 5) if mg.gamma else None,
                "theta": round(float(mg.theta), 4) if mg.theta else None,
                "vega":  round(float(mg.vega),  4) if mg.vega  else None,
                "iv":    round(float(mg.impliedVol), 4) if mg.impliedVol else None,
                "source": "ibkr_live",
            }
    except Exception as e:
        log.debug(f"IBKR Greeks failed for {symbol}: {e}")
    return None


# ── IV Rank ───────────────────────────────────────────────────────────

def get_iv_rank(symbol: str, current_iv: float) -> float | None:
    """
    Estimate IV Rank (0–100) using 52-week rolling realized volatility
    as a proxy for the IV range (true historical IV requires a paid feed).

    IV Rank < 30  → options cheap → good to buy
    IV Rank 30–60 → fair value
    IV Rank > 60  → options expensive → avoid buying
    """
    try:
        from signals import _safe_download
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hist = _safe_download(symbol, period="1y", interval="1d",
                                  progress=False, auto_adjust=True)
        if hist is None or len(hist) < 60:
            return None

        closes = hist["Close"].squeeze()
        rets   = closes.pct_change().dropna()
        # 30-day rolling annualised volatility
        rv = rets.rolling(30).std() * np.sqrt(252)
        rv = rv.dropna()
        if len(rv) < 10:
            return None

        rv_high = float(rv.max())
        rv_low  = float(rv.min())
        if rv_high <= rv_low:
            return None

        ivr = (current_iv - rv_low) / (rv_high - rv_low) * 100
        return round(float(np.clip(ivr, 0, 100)), 1)
    except Exception as e:
        log.debug(f"IV rank calc failed for {symbol}: {e}")
        return None


# ── Chain fetching ────────────────────────────────────────────────────

def _fetch_chain(symbol: str, expiry: str) -> tuple:
    """
    Fetch calls and puts for one expiry.
    Returns (calls_df, puts_df) — both filtered for basic liquidity.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        chain = yf.Ticker(symbol).option_chain(expiry)
    calls = chain.calls.copy()
    puts  = chain.puts.copy()

    for df in (calls, puts):
        df["mid"]        = (df["bid"] + df["ask"]) / 2
        df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"].replace(0, np.nan)

    # Basic liquidity filters — generous at this stage, tighter in select_contract
    min_vol = max(CONFIG.get("options_min_volume", 50) // 4, 5)
    min_oi  = max(CONFIG.get("options_min_oi", 200) // 4, 25)
    calls = calls[
        (calls["bid"] > 0.03) &
        (calls["volume"].fillna(0) > min_vol) &
        (calls["openInterest"].fillna(0) > min_oi)
    ]
    puts = puts[
        (puts["bid"] > 0.03) &
        (puts["volume"].fillna(0) > min_vol) &
        (puts["openInterest"].fillna(0) > min_oi)
    ]
    return calls, puts


# ── Contract selection ────────────────────────────────────────────────

def _select_strike(df, flag: str, S: float, dte: int,
                   target_delta: float, delta_range: float) -> dict | None:
    """
    From a filtered chain DataFrame, find the contract closest to
    target_delta with acceptable liquidity and spread.
    Adds computed Greeks to the result dict.
    """
    max_spread = CONFIG.get("options_max_spread_pct", 0.25)
    min_vol    = CONFIG.get("options_min_volume", 50)
    min_oi     = CONFIG.get("options_min_oi", 200)

    total_before = len(df)
    spread_ok = df["spread_pct"] < max_spread
    vol_ok    = df["volume"].fillna(0) >= min_vol
    oi_ok     = df["openInterest"].fillna(0) >= min_oi

    rows = df[spread_ok & vol_ok & oi_ok].copy()

    if rows.empty:
        n_spread = int((~spread_ok).sum())
        n_vol    = int((~vol_ok).sum())
        n_oi     = int((~oi_ok).sum())
        log.info(
            f"Options: {flag.upper()} chain liquidity filter killed all {total_before} strikes — "
            f"spread>{max_spread:.0%}: {n_spread} | vol<{min_vol}: {n_vol} | OI<{min_oi}: {n_oi}"
        )
        return None

    # Calculate delta for every remaining strike
    deltas = []
    for _, row in rows.iterrows():
        iv = float(row["impliedVolatility"])
        if iv <= 0 or iv > 5:
            iv = 0.30   # fallback if IV malformed
        g = calculate_greeks(flag, S, float(row["strike"]), dte, iv)
        deltas.append(abs(g["delta"]))
    rows["abs_delta"] = deltas
    rows["delta_dist"] = abs(rows["abs_delta"] - target_delta)

    # Must be within the allowed delta window
    in_window = rows[rows["delta_dist"] <= delta_range]
    if in_window.empty:
        delta_range_str = f"{target_delta - delta_range:.2f}-{target_delta + delta_range:.2f}"
        actual_deltas = rows["abs_delta"].tolist()
        closest = min(actual_deltas, key=lambda d: abs(d - target_delta))
        log.info(
            f"Options: {len(rows)} strikes passed liquidity but none in delta window "
            f"{delta_range_str} (target={target_delta:.2f}) — "
            f"closest delta={closest:.3f}, all deltas={[round(d,3) for d in sorted(actual_deltas)[:8]]}"
        )
        return None
    rows = in_window

    best = rows.sort_values("delta_dist").iloc[0]
    iv   = float(best["impliedVolatility"])
    if iv <= 0 or iv > 5:
        iv = 0.30

    greeks = calculate_greeks(flag, S, float(best["strike"]), dte, iv)
    return {
        "strike":      float(best["strike"]),
        "expiry_str":  "",            # filled in by caller
        "expiry_ibkr": "",            # YYYYMMDD, filled in by caller
        "dte":         dte,
        "right":       "C" if flag == "c" else "P",
        "mid":         round(float(best["mid"]), 4),
        "bid":         round(float(best["bid"]), 4),
        "ask":         round(float(best["ask"]), 4),
        "spread_pct":  round(float(best["spread_pct"]), 4),
        "volume":      int(best["volume"]),
        "open_interest": int(best["openInterest"]),
        "iv":          round(iv, 4),
        **greeks,
    }


# ── Main entry point ──────────────────────────────────────────────────

def find_best_contract(symbol: str,
                       direction: str,
                       portfolio_value: float,
                       ib=None,
                       regime: dict = None,
                       score: int = 0) -> dict | None:
    """
    Given a high-conviction signal, find the best options contract.

    direction: 'LONG' → buy call | 'SHORT' → buy put

    Returns a contract dict ready for execute_buy_option(), or None if:
      - No liquid chain in the DTE window
      - IVR too high (options overpriced)
      - No strike close enough to target delta
      - Spread too wide

    The returned dict includes:
      symbol, direction, strike, expiry_str, expiry_ibkr, dte, right,
      mid, bid, ask, spread_pct, volume, open_interest, iv, iv_rank,
      delta, gamma, theta, vega, model_price,
      contracts (position-sized), max_risk_dollars
    """
    flag       = "c" if direction == "LONG" else "p"
    min_dte    = CONFIG.get("options_min_dte",      7)
    max_dte    = CONFIG.get("options_max_dte",      21)
    max_ivr    = CONFIG.get("options_max_ivr",      50)
    t_delta    = CONFIG.get("options_target_delta", 0.40)
    d_range    = CONFIG.get("options_delta_range",  0.15)
    base_risk  = CONFIG.get("options_max_risk_pct", 0.01) * portfolio_value

    # ── Conviction scaling — mirror stock logic ──────────────────
    # Higher scores → larger options allocation, lower scores → smaller.
    high_conv = CONFIG.get("high_conviction_score", 38)
    if score >= high_conv:
        conviction_mult = 1.5     # High conviction → 1.5x base risk
    elif score >= 32:
        conviction_mult = 1.0     # Moderate conviction → base risk
    else:
        conviction_mult = 0.75    # Low conviction → 0.75x base risk

    max_risk = base_risk * conviction_mult
    log.info(f"Options sizing {symbol}: score={score} → conviction={conviction_mult}x → "
             f"max_risk=${max_risk:,.0f} (base=${base_risk:,.0f})")

    try:
        ticker = yf.Ticker(symbol)

        # Current underlying price
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hist = ticker.history(period="1d", interval="1m")
        if hist is None or hist.empty:
            log.info(f"Options: no price data for {symbol} — cannot evaluate")
            return None
        S = float(hist["Close"].iloc[-1])

        # Filter expiry dates to DTE window
        today     = date.today()
        all_exps  = ticker.options
        valid_exp = []
        for exp in all_exps:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte      = (exp_date - today).days
            if min_dte <= dte <= max_dte:
                valid_exp.append((exp, exp_date, dte))

        if not valid_exp:
            all_dtes = [(exp, (datetime.strptime(exp, "%Y-%m-%d").date() - today).days) for exp in all_exps[:6]]
            log.info(f"Options: no expiry in {min_dte}-{max_dte} DTE window for {symbol} — "
                     f"available: {all_dtes}")
            return None

        # Try each valid expiry, take the first good contract
        best_contract = None
        for exp_str, exp_date, dte in valid_exp:
            try:
                calls_df, puts_df = _fetch_chain(symbol, exp_str)
                df = calls_df if flag == "c" else puts_df
                if df.empty:
                    continue

                contract = _select_strike(df, flag, S, dte, t_delta, d_range)
                if contract is None:
                    continue

                contract["expiry_str"]  = exp_str
                contract["expiry_ibkr"] = exp_date.strftime("%Y%m%d")
                best_contract = contract
                break

            except Exception as e:
                log.info(f"Options chain error {symbol} {exp_str}: {e}")
                continue

        if best_contract is None:
            log.info(f"Options: no suitable contract found for {symbol} after checking "
                     f"{len(valid_exp)} expiries — all filtered out by liquidity/delta")
            return None

        # IV Rank check — bail if options too expensive
        iv_rank = get_iv_rank(symbol, best_contract["iv"])
        best_contract["iv_rank"] = iv_rank
        if iv_rank is not None and iv_rank > max_ivr:
            log.info(f"Options: {symbol} IVR={iv_rank:.0f} > {max_ivr} — too expensive, skipping")
            return None

        # Upgrade to IBKR real-time Greeks if bot is connected
        ibkr_greeks = get_ibkr_greeks(
            ib, symbol,
            best_contract["expiry_ibkr"],
            best_contract["strike"],
            best_contract["right"],
        )
        if ibkr_greeks:
            best_contract.update(ibkr_greeks)
            log.debug(f"Options: used IBKR live Greeks for {symbol}")

        # Position sizing: max_risk / (mid × 100 shares per contract)
        premium_per_contract = best_contract["mid"] * 100
        contracts = max(1, int(max_risk // premium_per_contract))
        best_contract["contracts"]        = contracts
        best_contract["max_risk_dollars"] = round(contracts * premium_per_contract, 2)
        best_contract["symbol"]           = symbol
        best_contract["direction"]        = direction
        best_contract["underlying_price"] = round(S, 4)

        log.info(
            f"Options setup: {symbol} {best_contract['right']} "
            f"${best_contract['strike']:.0f} exp={best_contract['expiry_str']} "
            f"({dte} DTE) mid=${best_contract['mid']:.2f} "
            f"delta={best_contract['delta']:.3f} "
            f"IVR={iv_rank:.0f}% "
            f"contracts={contracts} risk=${best_contract['max_risk_dollars']:.0f}"
        )
        return best_contract

    except Exception as e:
        log.error(f"Options find_best_contract error {symbol}: {e}")
        return None


# ── Open position monitoring ──────────────────────────────────────────

def check_options_exits(open_options: dict, ib=None) -> list[str]:
    """
    Check all open options positions for exit conditions.
    Returns list of symbols to exit.

    Exit when ANY of:
      - P&L >= options_profit_target  (e.g. +75% on premium)
      - P&L <= -options_stop_loss     (e.g. -50% on premium)
      - DTE <= options_exit_dte        (e.g. ≤2 days, gamma risk)
    """
    profit_target = CONFIG.get("options_profit_target", 0.75)
    stop_loss     = CONFIG.get("options_stop_loss",     0.50)
    exit_dte      = CONFIG.get("options_exit_dte",      2)
    to_exit       = []
    today         = date.today()

    for sym, pos in open_options.items():
        if pos.get("instrument") != "option":
            continue

        # DTE check
        try:
            exp_date = datetime.strptime(pos["expiry_str"], "%Y-%m-%d").date()
            dte_remaining = (exp_date - today).days
            if dte_remaining <= exit_dte:
                log.info(f"Options exit: {sym} — {dte_remaining} DTE remaining (gamma risk)")
                to_exit.append(sym)
                continue
        except Exception:
            pass

        # P&L check — use current premium vs entry
        entry_premium = pos.get("entry_premium", 0)
        curr_premium  = pos.get("current_premium")

        # Try to get current premium from IBKR if available
        if ib and curr_premium is None:
            try:
                from ib_async import Option as IBOption
                contract = IBOption(
                    sym, pos["expiry_ibkr"],
                    pos["strike"], pos["right"],
                    exchange="SMART", currency="USD",
                )
                ib.qualifyContracts(contract)
                ticker = ib.reqMktData(contract, snapshot=True)
                ib.sleep(1)
                import math as _om
                _tbid = ticker.bid
                _task = ticker.ask
                _tlst = ticker.last
                _bid_ok = _tbid is not None and not _om.isnan(_tbid) and _tbid > 0
                _ask_ok = _task is not None and not _om.isnan(_task) and _task > 0
                if _bid_ok and _ask_ok:
                    mid = (_tbid + _task) / 2
                elif _tlst is not None and not _om.isnan(_tlst) and _tlst > 0:
                    mid = _tlst
                else:
                    mid = None
                ib.cancelMktData(contract)
                if mid and mid > 0:
                    curr_premium = float(mid)
                    pos["current_premium"] = curr_premium
            except Exception:
                pass

        # yfinance fallback for current premium
        if curr_premium is None:
            try:
                exp_str = pos.get("expiry_str", "")
                strike  = pos.get("strike", 0)
                right   = pos.get("right", "C")
                if exp_str:
                    chain = yf.Ticker(sym).option_chain(exp_str)
                    df    = chain.calls if right == "C" else chain.puts
                    row   = df[df["strike"] == strike]
                    if not row.empty:
                        curr_premium = float((row.iloc[0]["bid"] + row.iloc[0]["ask"]) / 2)
                        pos["current_premium"] = curr_premium
            except Exception:
                pass

        if curr_premium and entry_premium > 0:
            pnl_pct = (curr_premium - entry_premium) / entry_premium
            if pnl_pct >= profit_target:
                log.info(f"Options exit: {sym} — profit target hit ({pnl_pct:+.0%})")
                to_exit.append(sym)
            elif pnl_pct <= -stop_loss:
                log.info(f"Options exit: {sym} — stop loss hit ({pnl_pct:+.0%})")
                to_exit.append(sym)

    return to_exit
