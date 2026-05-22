# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_scanner.py                         ║
# ║   Proactive options opportunity scanner                      ║
# ║                                                              ║
# ║   Scans for:                                                 ║
# ║   • Unusual options volume (real volume expansion signal)    ║
# ║   • IV rank sweeps (cheap options windows)                   ║
# ║   • Earnings catalyst plays (3–21 DTE)                       ║
# ║   • Call/put skew (directional flow bias)                    ║
# ║                                                              ║
# ║   Volume data source: Alpaca OPRA dailyBar.v (real volume)   ║
# ║   No yfinance. No synthetic OI. No bid_size proxy.           ║
# ║                                                              ║
# ║   Output feeds into Apex Single-Synthesizer scan cycles      ║
# ║   alongside stock signals — Apex selects instrument type     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import numpy as np
import pandas as pd

from options_provider import (
    MIN_DAY_OVER_DAY_RATIO,
    MIN_SIDE_TRADE_COUNT,
    MIN_SIDE_VOLUME,
    PREV_VOLUME_FLOOR,
    get_options_flow_data,
)

try:
    from options import get_iv_rank  # module-level so tests can patch options_scanner.get_iv_rank
except (ImportError, Exception):

    def get_iv_rank(symbol, iv=None):  # type: ignore
        return None


log = logging.getLogger("decifer.options_scanner")

# ── Highly optionable universe ─────────────────────────────────────────
# Liquid names with reliable options data on Alpaca OPRA.
# Scanned every cycle in addition to top stock scanner hits.
OPTIONABLE_UNIVERSE = [
    # Mega-cap tech (tightest spreads, highest options liquidity)
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    # High-beta / momentum (high IV, active options flow)
    "PLTR",
    "MSTR",
    "CRWD",
    "DDOG",
    "SNOW",
    "SHOP",
    "COIN",
    "HOOD",
    "SMCI",
    "NFLX",
    "UBER",
    "CRM",
    "ORCL",
    # Semiconductors
    "MU",
    "INTC",
    "QCOM",
    "AMAT",
    # ETFs (deepest options liquidity of all)
    "SPY",
    "QQQ",
    "IWM",
    "XLK",
    "XLF",
    "GLD",
    # Active momentum names from the Decifer watchlist
    "HIMS",
    "OSCR",
    "ASTS",
    "ALAB",
    "NBIS",
    "IBIT",
]

# ── Scanner thresholds ─────────────────────────────────────────────────
_SCAN_MIN_DTE = 5   # Wider than trading window — catches early catalyst setups
_SCAN_MAX_DTE = 45
_MIN_OPTIONS_SCORE = 12  # Must score at least 12 / 30 to be returned to Apex
_MAX_RESULTS = 15        # Top N signals returned per cycle


# ═══════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _get_earnings_days_fmp(symbol: str) -> int | None:
    """
    Return days until next earnings announcement via FMP, or None.

    Uses FMP earning-calendar endpoint (stable API, returns structured dates).
    Returns None on any failure — earnings data is advisory only.
    """
    try:
        from fmp_client import get_earnings_calendar
        items = get_earnings_calendar(symbols=[symbol], days_ahead=60)
        for item in items:
            if item.get("symbol", "").upper() == symbol.upper():
                raw_date = item.get("date", "")
                if not raw_date:
                    continue
                d = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
                days = (d - date.today()).days
                if 0 <= days <= 60:
                    return int(days)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════
# SINGLE-SYMBOL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════


def _analyse_symbol(symbol: str, regime: dict | None = None) -> dict | None:
    """
    Run full options analysis for one symbol.
    Returns an options signal dict or None if nothing notable.

    Volume metrics come from options_provider.get_options_flow_data() which
    reads Alpaca dailyBar.v — real traded contracts, no synthetic proxies.
    """
    try:
        # ── Price + contract chain: Alpaca OPRA ───────────────────
        S = calls = puts = exp_str = dte = None

        try:
            from alpaca_options import (
                get_chain as _alpaca_chain,
            )
            from alpaca_options import (
                get_underlying_price as _alpaca_price,
            )

            _s = _alpaca_price(symbol)
            _chain = _alpaca_chain(symbol, _SCAN_MIN_DTE, _SCAN_MAX_DTE)
            if _s and _s > 0 and _chain:
                S = _s
                calls = _chain["calls"]
                puts = _chain["puts"]
                exp_str = _chain["expiry_str"]
                dte = _chain["dte"]
        except Exception:
            pass

        if S is None or calls is None:
            return None

        # ── Earnings calendar: FMP ────────────────────────────────
        earnings_days = _get_earnings_days_fmp(symbol)

        if calls.empty and puts.empty:
            return None

        # ── Options flow data: real volume from Alpaca dailyBar ───
        flow_data = get_options_flow_data(symbol, _SCAN_MIN_DTE, _SCAN_MAX_DTE)

        if flow_data is None or not flow_data.flow_metrics_available:
            unusual_calls = False
            unusual_puts = False
            unusual_eval_reason = "missing_approved_options_flow_provider"
            provider_status = "NULL"
            call_vol = put_vol = call_tc = put_tc = call_prev = put_prev = 0.0
        else:
            call_vol = flow_data.call_volume
            put_vol = flow_data.put_volume
            call_tc = flow_data.call_trade_count
            put_tc = flow_data.put_trade_count
            call_prev = flow_data.call_prev_volume
            put_prev = flow_data.put_prev_volume
            provider_status = flow_data.provider_status

            # Volume expansion detection:
            #   1. Side must exceed MIN_SIDE_VOLUME (absolute floor)
            #   2. Trade count must exceed MIN_SIDE_TRADE_COUNT (quality gate)
            #   3. Today / prev ratio >= MIN_DAY_OVER_DAY_RATIO (expansion gate)
            #      prev is floored at PREV_VOLUME_FLOOR to avoid tiny denominators
            unusual_calls = (
                call_vol >= MIN_SIDE_VOLUME
                and call_tc >= MIN_SIDE_TRADE_COUNT
                and call_vol / max(call_prev, PREV_VOLUME_FLOOR) >= MIN_DAY_OVER_DAY_RATIO
            )
            unusual_puts = (
                put_vol >= MIN_SIDE_VOLUME
                and put_tc >= MIN_SIDE_TRADE_COUNT
                and put_vol / max(put_prev, PREV_VOLUME_FLOOR) >= MIN_DAY_OVER_DAY_RATIO
            )
            unusual_eval_reason = (
                f"vol_expansion: calls={int(call_vol)} prev={int(call_prev)} "
                f"puts={int(put_vol)} prev={int(put_prev)}"
            )

        # ── Call/Put volume ratio ─────────────────────────────────
        # Uses flow_data volumes for directional skew assessment
        total_vol = call_vol + put_vol
        if total_vol < MIN_SIDE_VOLUME:
            return None  # Not enough activity to be meaningful

        cp_ratio = round(call_vol / put_vol, 2) if put_vol > 10 else 10.0
        pc_ratio = round(put_vol / call_vol, 2) if call_vol > 10 else 10.0

        # ── Dominant strike (most active contract today by volume) ─
        all_c = pd.concat([calls.assign(opt_type="call"), puts.assign(opt_type="put")], ignore_index=True)
        all_c["volume"] = all_c["volume"].fillna(0)
        dom_row = all_c.loc[all_c["volume"].idxmax()]
        dom_strike = float(dom_row["strike"])
        dom_type = str(dom_row["opt_type"])
        # Safe scalar extraction — impliedVolatility can be a Series after concat
        try:
            _raw_iv = dom_row["impliedVolatility"]
            dom_iv = float(_raw_iv.iloc[0] if hasattr(_raw_iv, "iloc") else _raw_iv)
        except Exception:
            dom_iv = 0.30
        if not (0 < dom_iv < 5):
            dom_iv = 0.30

        # ── IV Rank (uses options.py proxy) ───────────────────────
        iv_rank = get_iv_rank(symbol, dom_iv)

        # ══════════════════════════════════════════════════════════════
        # SCORING  (0 – 30)
        # ══════════════════════════════════════════════════════════════
        score = 0
        reasons: list[str] = []

        # 1. Unusual volume  (0–10)
        # Evaluated using volume expansion (today vs prev day) not OI ratio.
        # Both sides unusual: award based on which side dominates by magnitude.
        if unusual_calls and unusual_puts:
            if cp_ratio >= 1.5:  # Call-dominated both-unusual
                score += 9
                reasons.append(
                    f"unusual vol both sides, CALL-led — C/P={cp_ratio:.1f}x "
                    f"(calls={int(call_vol)} {call_vol/max(call_prev,PREV_VOLUME_FLOOR):.1f}x prev, "
                    f"puts={int(put_vol)} {put_vol/max(put_prev,PREV_VOLUME_FLOOR):.1f}x prev)"
                )
            elif pc_ratio >= 1.5:  # Put-dominated both-unusual
                score += 9
                reasons.append(
                    f"unusual vol both sides, PUT-led — C/P={cp_ratio:.1f}x "
                    f"(puts={int(put_vol)} {put_vol/max(put_prev,PREV_VOLUME_FLOOR):.1f}x prev, "
                    f"calls={int(call_vol)} {call_vol/max(call_prev,PREV_VOLUME_FLOOR):.1f}x prev)"
                )
            else:  # Balanced — event/uncertainty hedging
                score += 7
                reasons.append(f"unusual vol both sides balanced — C/P={cp_ratio:.1f}x (likely event/catalyst hedging)")
        elif unusual_calls:
            score += 10
            ratio_str = f"{call_vol/max(call_prev,PREV_VOLUME_FLOOR):.1f}x prev"
            reasons.append(f"unusual CALL volume — {int(call_vol):,} contracts ({ratio_str})")
        elif unusual_puts:
            score += 9
            ratio_str = f"{put_vol/max(put_prev,PREV_VOLUME_FLOOR):.1f}x prev"
            reasons.append(f"unusual PUT volume — {int(put_vol):,} contracts ({ratio_str})")

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

        # ── Direction signal (Part 8) ─────────────────────────────
        # EARNINGS_PLAY and MIXED_FLOW are advisory signals only — they are
        # never executed deterministically (options_entries blocks them).
        # CALL_BUYER and PUT_BUYER require confirmed unusual flow on the
        # correct side AND directional skew.
        if earnings_days is not None and earnings_days <= 10:
            signal = "EARNINGS_PLAY"
        elif unusual_calls and unusual_puts and call_vol >= 1.5 * max(put_vol, 1):
            signal = "CALL_BUYER"
        elif unusual_calls and unusual_puts and put_vol >= 1.5 * max(call_vol, 1):
            signal = "PUT_BUYER"
        elif unusual_calls and call_vol >= 1.5 * max(put_vol, 1):
            signal = "CALL_BUYER"
        elif unusual_puts and put_vol >= 1.5 * max(call_vol, 1):
            signal = "PUT_BUYER"
        else:
            signal = "MIXED_FLOW"

        reasoning = (
            f"{symbol} @ ${S:.2f} | {' | '.join(reasons)} | "
            f"dominant: ${dom_strike:.0f} {dom_type.upper()} | "
            f"{dte} DTE ({exp_str})"
        )

        return {
            "symbol": symbol,
            "price": round(S, 2),
            "options_score": score,
            "signal": signal,
            "provider": flow_data.provider if flow_data else "null",
            "provider_status": provider_status,
            "flow_definition": flow_data.flow_definition if flow_data else "NONE",
            "call_volume": int(call_vol),
            "call_volume_source": flow_data.call_volume_source if flow_data else "unavailable",
            "call_open_interest": flow_data.call_open_interest if flow_data else None,
            "call_open_interest_source": flow_data.call_open_interest_source if flow_data else "unavailable",
            "call_prev_volume": int(call_prev),
            "call_prev_volume_source": flow_data.call_prev_volume_source if flow_data else "unavailable",
            "put_volume": int(put_vol),
            "put_volume_source": flow_data.put_volume_source if flow_data else "unavailable",
            "put_open_interest": flow_data.put_open_interest if flow_data else None,
            "put_open_interest_source": flow_data.put_open_interest_source if flow_data else "unavailable",
            "put_prev_volume": int(put_prev),
            "put_prev_volume_source": flow_data.put_prev_volume_source if flow_data else "unavailable",
            "unusual_calls": unusual_calls,
            "unusual_puts": unusual_puts,
            "unusual_eval_reason": unusual_eval_reason,
            # Contract selection fields — populated later by find_best_contract in entries
            "selected_contract_symbol": None,
            "selected_contract_volume": None,
            "selected_contract_spread_pct": None,
            "selected_contract_delta": None,
            "selected_contract_dte": None,
            "iv_rank": iv_rank,
            "dom_strike": dom_strike,
            "dom_type": dom_type,
            "dom_iv": round(dom_iv, 3),
            "earnings_days": earnings_days,
            "expiry": exp_str,
            "dte": dte,
            "reasoning": reasoning,
            # Expression router fields — populated by expression_router when evaluated in entries
            "expression_route": None,
            "expression_reason": None,
            "entry_skip_reason": None,
        }

    except Exception as e:
        log.debug(f"Options scan error {symbol}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════


def scan_options_universe(extra_symbols: list | None = None, regime: dict | None = None) -> list[dict]:
    """
    Scan the optionable universe for notable options setups.

    extra_symbols:  high-scoring symbols from the stock scanner —
                    these are appended to OPTIONABLE_UNIVERSE so the scanner
                    automatically considers anything the stock side is excited about
    regime:         current market regime (affects which signals are surfaced)

    Returns up to _MAX_RESULTS dicts, sorted by options_score descending.
    """
    symbols = list(set(OPTIONABLE_UNIVERSE + (extra_symbols or [])))
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
            if regime_name == "TRENDING_UP" and result["signal"] == "PUT_BUYER" and result["options_score"] < 20:
                continue
            # In PANIC, ignore call-buyer signals — only hedging/put flow matters
            if regime_name == "CAPITULATION" and result["signal"] == "CALL_BUYER":
                continue

            results.append(result)

    results.sort(key=lambda x: x["options_score"], reverse=True)
    top = results[:_MAX_RESULTS]

    log.info(f"Options scan: {len(top)} notable setups from {len(symbols)} symbols (regime={regime_name})")
    for r in top[:5]:
        log.info(f"  [{r['options_score']:>2}/30] {r['signal']:<14} {r.get('reasoning', '')[:90]}")

    return top
