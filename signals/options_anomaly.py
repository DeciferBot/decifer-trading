"""
Options Anomaly Detector
========================
Scans the options chain of M&A candidate tickers for unusual activity that
often precedes acquisition announcements:

  1. OTM call dominance  — OTM calls > 70% of total call volume
  2. Bullish skew        — call volume > 2× put volume (total chain)
  3. Near-term IV spike  — nearest expiry IV significantly elevated
  4. IV term compression — front-month IV / back-month IV > 1.20

Data source priority:
  1. Alpaca OPRA (paid, real-time) — used when client is available
  2. yfinance (free, 15-20min delayed) — fallback only

NOTE: Alpaca snapshot "volume" is bid_size + ask_size (quoted liquidity),
not daily trade volume. It's real-time and accurate for OTM interest
detection; semantically different from yfinance daily volume but more
timely.

Run standalone:  python -m signals.options_anomaly --tickers AAPL MSFT
Called from app: from signals.options_anomaly import run_anomaly_scan
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from config import CATALYST_DIR  # noqa: E402  chief-decifer/state/internal/catalyst/

log = logging.getLogger("decifer.options_anomaly")

SNAPSHOTS_FILE = CATALYST_DIR / "options_snapshots.jsonl"


# ── Options chain fetcher ─────────────────────────────────────────────────────

def _fetch_chain(ticker: str) -> list[dict] | None:
    """
    Fetch options chain for the nearest two expiry dates.
    Returns list of dicts: [{"calls": df, "puts": df, "expiry_str": str, "dte": int}, ...]
    Tries Alpaca OPRA first; falls back to yfinance.
    """
    # Primary: Alpaca OPRA (real-time)
    try:
        from alpaca_options import get_all_chains
        chains = get_all_chains(ticker, min_dte=0, max_dte=60)
        if chains:
            return chains[:2]
    except Exception as exc:
        log.debug(f"options_anomaly: Alpaca chain fetch failed for {ticker} — {exc}")

    # Fallback: yfinance (15-20min delayed)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return None
        selected = expirations[:2]
        result = []
        for exp in selected:
            oc = t.option_chain(exp)
            result.append({
                "calls": oc.calls,
                "puts": oc.puts,
                "expiry_str": exp,
                "dte": None,
            })
        return result if result else None
    except Exception:
        return None


# ── Current price ─────────────────────────────────────────────────────────────

def _current_price(ticker: str) -> float | None:
    # Primary: Alpaca (real-time)
    try:
        from alpaca_options import get_underlying_price
        price = get_underlying_price(ticker)
        if price and price > 0:
            return price
    except Exception:
        pass

    # Fallback: yfinance
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        return getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    except Exception:
        return None


# ── Anomaly scorer ────────────────────────────────────────────────────────────

def _score_options(ticker: str, chains: list[dict], price: float | None) -> dict:
    """
    Compute options anomaly score (0–10) and flags for a single ticker.
    chains: list of {"calls": df, "puts": df, "expiry_str": str, "dte": int}
    """
    if not chains:
        return {"options_anomaly_score": 0, "options_anomaly_flags": []}

    front = chains[0]
    front_calls = front["calls"]
    front_puts  = front["puts"]
    front_exp   = front["expiry_str"]

    flags  = []
    points = 0  # max 4 → normalised to 0–10

    # ── Guard: need volume columns ────────────────────────────────────────────
    if "volume" not in front_calls.columns or "volume" not in front_puts.columns:
        return {"options_anomaly_score": 0, "options_anomaly_flags": ["No volume data"]}

    # ── Fill NaN volumes with 0 ───────────────────────────────────────────────
    front_calls = front_calls.copy()
    front_puts  = front_puts.copy()
    front_calls["volume"] = front_calls["volume"].fillna(0)
    front_puts["volume"]  = front_puts["volume"].fillna(0)

    total_call_vol = front_calls["volume"].sum()
    total_put_vol  = front_puts["volume"].sum()

    # ── Signal 1: OTM call dominance ─────────────────────────────────────────
    if price and total_call_vol > 0:
        otm_calls = front_calls[front_calls["strike"] > price]
        otm_call_vol = otm_calls["volume"].sum()
        otm_pct = otm_call_vol / total_call_vol if total_call_vol > 0 else 0
        if otm_pct >= 0.70:
            points += 2  # strongest signal — weight 2
            flags.append(f"OTM calls {otm_pct*100:.0f}% of call volume (≥70%)")
        elif otm_pct >= 0.55:
            points += 1
            flags.append(f"OTM calls elevated ({otm_pct*100:.0f}%)")

    # ── Signal 2: Bullish put/call skew ───────────────────────────────────────
    if total_put_vol > 0 and total_call_vol > 0:
        pc_ratio = total_call_vol / total_put_vol
        if pc_ratio >= 2.0:
            points += 1
            flags.append(f"Call/put ratio {pc_ratio:.1f}x (≥2.0)")

    # ── Signal 3: IV term structure compression ───────────────────────────────
    if len(chains) >= 2 and "impliedVolatility" in front_calls.columns:
        back_calls = chains[1]["calls"].copy()
        back_calls["impliedVolatility"] = back_calls["impliedVolatility"].fillna(0)

        front_iv = front_calls["impliedVolatility"].median()
        back_iv  = back_calls["impliedVolatility"].median()

        if back_iv > 0:
            iv_ratio = front_iv / back_iv
            if iv_ratio >= 1.20:
                points += 1
                flags.append(f"IV term compression {iv_ratio:.2f}x front/back (≥1.20)")

    # Normalise to 0–10 (max raw points = 4)
    score = min(10, round(points / 4 * 10))

    return {
        "options_anomaly_score": score,
        "options_anomaly_flags": flags,
        "options_detail": {
            "total_call_vol": int(total_call_vol),
            "total_put_vol":  int(total_put_vol),
            "front_expiry":   front_exp,
        },
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def run_anomaly_scan(
    tickers: list[str],
    verbose: bool = False,
) -> dict[str, dict]:
    """
    Scan options for each ticker in tickers.

    Returns
    -------
    Dict mapping ticker → anomaly result dict
    (keys: options_anomaly_score, options_anomaly_flags, options_detail)
    """
    CATALYST_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    today = datetime.utcnow().strftime("%Y-%m-%d")

    for i, ticker in enumerate(tickers):
        if verbose:
            print(f"  [options_anomaly] {ticker} ({i+1}/{len(tickers)}) …", end=" ", flush=True)

        price  = _current_price(ticker)
        chains = _fetch_chain(ticker)

        if chains is None:
            result = {"options_anomaly_score": 0, "options_anomaly_flags": ["No options data"]}
        else:
            result = _score_options(ticker, chains, price)

        result["ticker"]     = ticker
        result["scanned_at"] = datetime.utcnow().isoformat() + "Z"
        results[ticker]      = result

        # Append snapshot for historical IC tracking
        snapshot = {"date": today, **result}
        with SNAPSHOTS_FILE.open("a") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")

        if verbose:
            score = result["options_anomaly_score"]
            flags = result.get("options_anomaly_flags", [])
            print(f"score={score}/10  {'; '.join(flags) or 'no anomaly'}")

    return results


# ── Merge results into candidates file ───────────────────────────────────────

def merge_into_candidates(scan_results: dict[str, dict]) -> int:
    """
    Update today's candidates file with options anomaly scores.
    Returns number of candidates updated.
    """
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    cand_file = CATALYST_DIR / f"candidates_{today}.json"
    if not cand_file.exists():
        return 0

    payload = json.loads(cand_file.read_text())
    updated = 0
    for candidate in payload.get("candidates", []):
        ticker = candidate["ticker"]
        if ticker in scan_results:
            res = scan_results[ticker]
            candidate["options_anomaly_score"] = res.get("options_anomaly_score", 0)
            candidate["options_anomaly_flags"] = res.get("options_anomaly_flags", [])
            # Recompute composite catalyst_score (F:35% + O:35% + E:15% + S:15%)
            f_score = candidate.get("fundamental_score", 0)
            o_score = candidate["options_anomaly_score"]
            e_score = candidate.get("edgar_score", 0)
            s_score = candidate.get("sentiment_score", 0.0)
            candidate["catalyst_score"] = round(
                0.35 * (f_score / 5 * 10) +
                0.35 * o_score +
                0.15 * e_score +
                0.15 * s_score,
                1,
            )
            updated += 1

    cand_file.write_text(json.dumps(payload, indent=2, default=str))
    return updated


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Options Anomaly Detector")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    results = run_anomaly_scan(args.tickers, verbose=True)
    print(f"\nSummary: scanned {len(results)} tickers")
    for t, r in sorted(results.items(), key=lambda x: -x[1]["options_anomaly_score"]):
        if r["options_anomaly_score"] > 0:
            print(f"  {t:6s}  {r['options_anomaly_score']}/10  {r['options_anomaly_flags']}")
