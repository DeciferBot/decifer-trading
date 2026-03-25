# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_scanner.py                         ║
# ║   Proactive options opportunity scanner via yfinance         ║
# ║                                                              ║
# ║   Scans for:                                                 ║
# ║   • Unusual options volume (smart money activity)            ║
# ║   • IV rank sweeps (cheap options windows)                   ║
# ║   • Earnings catalyst plays (3–21 DTE)                       ║
# ║   • Call/put skew (directional flow bias)                    ║
# ║   • Max pain levels (OI concentration)                       ║
# ║                                                              ║
# ║   Output feeds directly into the 6-agent pipeline alongside  ║
# ║   stock signals — agents can recommend options as instrument  ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import warnings
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import yfinance as yf

from config import CONFIG

log = logging.getLogger("decifer.options_scanner")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# ── Highly optionable universe ─────────────────────────────────────────
# Liquid names with reliable options data on yfinance.
# Scanned every cycle in addition to top TV screener hits.
OPTIONABLE_UNIVERSE = [
    # Mega-cap tech (tightest spreads, highest options liquidity)
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD",
    # High-beta / momentum (high IV, active options flow)
    "PLTR", "MSTR", "CRWD", "DDOG", "SNOW", "SHOP", "COIN", "HOOD",
    "SMCI", "NFLX", "UBER", "CRM", "ORCL",
    # Semiconductors
    "MU", "INTC", "QCOM", "AMAT",
    # ETFs (deepest options liquidity of all)
    "SPY", "QQQ", "IWM", "XLK", "XLF", "GLD",
    # Active momentum names from the Decifer watchlist
    "HIMS", "OSCR", "ASTS", "ALAB", "NBIS", "IBIT",
]

# ── Scanner thresholds ─────────────────────────────────────────────────
_SCAN_MIN_DTE      = 5     # Wider than trading window — catches early catalyst setups
_SCAN_MAX_DTE      = 45
_UNUSUAL_VOL_RATIO = 0.25  # volume / OI > 25% = unusual (significant same-day activity)
_MIN_TOTAL_VOL     = 200   # Skip symbols with < 200 total options contracts traded
_MIN_OPTIONS_SCORE = 12    # Must score at least 12 / 30 to be returned to agents
_MAX_RESULTS       = 15    # Top N signals returned per cycle


# ═══════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _get_nearest_expiry(ticker_obj) -> tuple[str | None, int | None]:
    """
    Find the nearest expiry in the _SCAN_MIN_DTE to _SCAN_MAX_DTE window.
    Returns (expiry_str "YYYY-MM-DD", dte_int) or (None, None).
    """
    today = date.today()
    try:
        exps = ticker_obj.options
        if not exps:
            return None, None
    except Exception:
        return None, None

    for exp in exps:
        try:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if _SCAN_MIN_DTE <= dte <= _SCAN_MAX_DTE:
                return exp, dte
        except Exception:
            continue

    return None, None


def _get_earnings_days(ticker_obj) -> int | None:
    """
    Return days until next earnings announcement, or None.
    Handles the various shapes yfinance.calendar returns.
    """
    try:
        cal = ticker_obj.calendar
        if cal is None:
            return None

        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.columns:
                ed = cal["Earnings Date"].iloc[0]
            elif "Earnings Date" in cal.index:
                ed = cal.T["Earnings Date"].iloc[0]

        if ed is None:
            return None

        # Handle list / series
        if isinstance(ed, (list, pd.Series)):
            ed = ed[0] if len(ed) > 0 else None
        if ed is None:
            return None

        # Normalise to date
        if hasattr(ed, "date"):
            ed = ed.date()
        elif isinstance(ed, str):
            ed = datetime.strptime(ed[:10], "%Y-%m-%d").date()

        days = (ed - date.today()).days
        return int(days) if 0 <= days <= 60 else None

    except Exception:
        return None


def _compute_max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> float | None:
    """
    Max pain = the strike where total dollar value of expiring options is minimised.
    Market makers are naturally hedged to pin price near max pain at expiry.
    Vectorized implementation for performance.
    """
    try:
        strikes = sorted(set(
            calls["strike"].dropna().tolist() + puts["strike"].dropna().tolist()
        ))
        if len(strikes) < 3:
            return None

        test_prices = np.array(strikes)

        # Vectorized: for each test_price, sum OI * max(0, test_price - strike) for calls
        call_strikes = calls["strike"].dropna().values
        call_oi = calls["openInterest"].dropna().values.astype(float)
        put_strikes = puts["strike"].dropna().values
        put_oi = puts["openInterest"].dropna().values.astype(float)

        total_pain = np.zeros(len(test_prices))
        for i, tp in enumerate(test_prices):
            call_val = np.sum(call_oi * np.maximum(0.0, tp - call_strikes))
            put_val  = np.sum(put_oi * np.maximum(0.0, put_strikes - tp))
            total_pain[i] = call_val + put_val

        return float(test_prices[np.argmin(total_pain)])

    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
# SINGLE-SYMBOL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

def _analyse_symbol(symbol: str, regime: dict = None) -> dict | None:
    """
    Run full options analysis for one symbol.
    Returns an options signal dict or None if nothing notable.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ticker = yf.Ticker(symbol)

        # ── Current underlying price ──────────────────────────────────
        hist = ticker.history(period="2d", interval="1d")
        if hist is None or hist.empty:
            return None
        S = float(hist["Close"].iloc[-1])
        if S <= 0:
            return None

        # ── Find nearest expiry in scan window ────────────────────────
        exp_str, dte = _get_nearest_expiry(ticker)
        if exp_str is None:
            return None

        # ── Fetch options chain ───────────────────────────────────────
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            chain = ticker.option_chain(exp_str)

        calls = chain.calls.copy()
        puts  = chain.puts.copy()

        if calls.empty and puts.empty:
            return None

        # ── Volume & OI totals ────────────────────────────────────────
        call_vol = float(calls["volume"].fillna(0).sum())
        put_vol  = float(puts["volume"].fillna(0).sum())
        call_oi  = float(calls["openInterest"].fillna(0).sum())
        put_oi   = float(puts["openInterest"].fillna(0).sum())
        total_vol = call_vol + put_vol

        if total_vol < _MIN_TOTAL_VOL:
            return None   # Not enough activity to be meaningful

        # ── Call/Put ratio (by volume) ────────────────────────────────
        cp_ratio = round(call_vol / put_vol, 2) if put_vol > 10 else 10.0
        pc_ratio = round(put_vol / call_vol, 2) if call_vol > 10 else 10.0

        # ── Unusual volume detection ──────────────────────────────────
        # "Unusual" = today's volume is > 25% of total open interest
        unusual_calls = (call_oi > 0) and (call_vol / call_oi >= _UNUSUAL_VOL_RATIO)
        unusual_puts  = (put_oi  > 0) and (put_vol  / put_oi  >= _UNUSUAL_VOL_RATIO)

        # ── Dominant strike (most active contract today) ──────────────
        all_c = pd.concat([
            calls.assign(opt_type="call"),
            puts.assign(opt_type="put")
        ], ignore_index=True)
        all_c["volume"] = all_c["volume"].fillna(0)
        dom_row    = all_c.loc[all_c["volume"].idxmax()]
        dom_strike = float(dom_row["strike"])
        dom_type   = str(dom_row["opt_type"])
        # Safe scalar extraction — impliedVolatility can be a Series after concat
        try:
            _raw_iv = dom_row["impliedVolatility"]
            dom_iv  = float(_raw_iv.iloc[0] if hasattr(_raw_iv, "iloc") else _raw_iv)
        except Exception:
            dom_iv = 0.30
        if not (0 < dom_iv < 5):
            dom_iv = 0.30

        # ── IV Rank (uses options.py proxy) ───────────────────────────
        from options import get_iv_rank
        iv_rank = get_iv_rank(symbol, dom_iv)

        # ── Earnings catalyst ─────────────────────────────────────────
        earnings_days = _get_earnings_days(ticker)

        # ── Max pain ──────────────────────────────────────────────────
        max_pain = _compute_max_pain(calls, puts)

        # ══════════════════════════════════════════════════════════════
        # SCORING  (0 – 30)
        # ══════════════════════════════════════════════════════════════
        score   = 0
        reasons = []

        # 1. Unusual volume  (0–10)
        # Both sides unusual: award based on which side dominates by magnitude
        if unusual_calls and unusual_puts:
            if cp_ratio >= 1.5:          # Call-dominated both-unusual
                score += 9
                reasons.append(
                    f"unusual vol both sides, CALL-led — C/P={cp_ratio:.1f}x "
                    f"(call {call_vol/call_oi*100:.0f}%, put {put_vol/put_oi*100:.0f}% of OI)"
                )
            elif pc_ratio >= 1.5:        # Put-dominated both-unusual
                score += 9
                reasons.append(
                    f"unusual vol both sides, PUT-led — C/P={cp_ratio:.1f}x "
                    f"(put {put_vol/put_oi*100:.0f}%, call {call_vol/call_oi*100:.0f}% of OI)"
                )
            else:                        # Balanced — event/uncertainty hedging
                score += 7
                reasons.append(
                    f"unusual vol both sides balanced — C/P={cp_ratio:.1f}x "
                    f"(likely event/catalyst hedging)"
                )
        elif unusual_calls:
            score += 10
            reasons.append(
                f"unusual CALL volume — {call_vol/call_oi*100:.0f}% of call OI "
                f"({int(call_vol):,} contracts)"
            )
        elif unusual_puts:
            score += 9
            reasons.append(
                f"unusual PUT volume — {put_vol/put_oi*100:.0f}% of put OI "
                f"({int(put_vol):,} contracts)"
            )

        # 2. IV rank  (0–8)
        if iv_rank is not None:
            if iv_rank < 20:
                score += 8
                reasons.append(f"IVR={iv_rank:.0f}% — very cheap options")
            elif iv_rank < 35:
                score += 5
                reasons.append(f"IVR={iv_rank:.0f}% — options fairly cheap")
            # IVR >= 35 gets no bonus — expensive options are a risk

        # 3. Directional flow skew  (0–5)
        if cp_ratio >= 3.0:
            score += 5
            reasons.append(f"heavy CALL skew ({cp_ratio:.1f}x calls vs puts)")
        elif pc_ratio >= 3.0:
            score += 5
            reasons.append(f"heavy PUT skew ({pc_ratio:.1f}x puts vs calls)")
        elif cp_ratio >= 2.0:
            score += 3
            reasons.append(f"call-leaning flow ({cp_ratio:.1f}x)")
        elif pc_ratio >= 2.0:
            score += 3
            reasons.append(f"put-leaning flow ({pc_ratio:.1f}x)")

        # 4. Earnings catalyst  (0–7)
        if earnings_days is not None:
            if 3 <= earnings_days <= 10:
                score += 7
                reasons.append(f"EARNINGS in {earnings_days}d — prime catalyst window")
            elif earnings_days <= 21:
                score += 4
                reasons.append(f"earnings in {earnings_days}d")
            elif earnings_days <= 45:
                score += 2
                reasons.append(f"earnings in {earnings_days}d")

        # Below minimum threshold — not noteworthy
        if score < _MIN_OPTIONS_SCORE:
            return None

        # ── Direction signal ──────────────────────────────────────────
        if earnings_days is not None and earnings_days <= 10:
            signal = "EARNINGS_PLAY"
        elif unusual_calls and unusual_puts and cp_ratio >= 1.5:
            signal = "CALL_BUYER"
        elif unusual_calls and unusual_puts and pc_ratio >= 1.5:
            signal = "PUT_BUYER"
        elif unusual_calls and cp_ratio >= 1.5:
            signal = "CALL_BUYER"
        elif unusual_puts and pc_ratio >= 1.5:
            signal = "PUT_BUYER"
        elif cp_ratio >= 2.5:
            signal = "CALL_BUYER"
        elif pc_ratio >= 2.5:
            signal = "PUT_BUYER"
        else:
            signal = "MIXED_FLOW"

        reasoning = (
            f"{symbol} @ ${S:.2f} | {' | '.join(reasons)} | "
            f"dominant: ${dom_strike:.0f} {dom_type.upper()} | "
            f"{dte} DTE ({exp_str})"
            + (f" | max_pain=${max_pain:.0f}" if max_pain else "")
        )

        return {
            "symbol":          symbol,
            "price":           round(S, 2),
            "options_score":   score,
            "signal":          signal,
            "call_vol":        int(call_vol),
            "put_vol":         int(put_vol),
            "call_oi":         int(call_oi),
            "put_oi":          int(put_oi),
            "cp_ratio":        cp_ratio,
            "unusual_calls":   unusual_calls,
            "unusual_puts":    unusual_puts,
            "iv_rank":         iv_rank,
            "dom_strike":      dom_strike,
            "dom_type":        dom_type,
            "dom_iv":          round(dom_iv, 3),
            "earnings_days":   earnings_days,
            "max_pain":        max_pain,
            "expiry":          exp_str,
            "dte":             dte,
            "reasoning":       reasoning,
        }

    except Exception as e:
        log.debug(f"Options scan error {symbol}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def scan_options_universe(extra_symbols: list = None,
                          regime: dict = None) -> list[dict]:
    """
    Scan the optionable universe for notable options setups.

    extra_symbols:  high-scoring symbols from the TradingView stock scanner —
                    these are appended to OPTIONABLE_UNIVERSE so the scanner
                    automatically considers anything the stock side is excited about
    regime:         current market regime (affects which signals are surfaced)

    Returns up to _MAX_RESULTS dicts, sorted by options_score descending.
    """
    symbols     = list(set(OPTIONABLE_UNIVERSE + (extra_symbols or [])))
    regime_name = (regime or {}).get("regime", "UNKNOWN")

    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_analyse_symbol, sym, regime): sym for sym in symbols}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                result = None
            if result is None:
                continue

            # Regime-aware filtering
            # In a BULL regime, suppress weak put signals (noise > signal)
            if regime_name == "BULL_TRENDING" and result["signal"] == "PUT_BUYER":
                if result["options_score"] < 20:
                    continue
            # In PANIC, ignore call-buyer signals — only hedging/put flow matters
            if regime_name == "PANIC" and result["signal"] == "CALL_BUYER":
                continue

            results.append(result)

    results.sort(key=lambda x: x["options_score"], reverse=True)
    top = results[:_MAX_RESULTS]

    log.info(
        f"Options scan: {len(top)} notable setups from {len(symbols)} symbols "
        f"(regime={regime_name})"
    )
    for r in top[:5]:
        log.info(f"  [{r['options_score']:>2}/30] {r['signal']:<14} {r['reasoning'][:90]}")

    return top
