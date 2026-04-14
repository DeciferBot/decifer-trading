"""
Options Anomaly Detector
========================
Scans the options chain of M&A candidate tickers for unusual activity that
often precedes acquisition announcements:

  1. OTM call dominance  — OTM calls > 70% of total call volume
  2. Bullish skew        — call volume > 2× put volume (total chain)
  3. Near-term IV spike  — nearest expiry IV significantly elevated
  4. IV term compression — front-month IV / back-month IV > 1.20

Detection is run against the live chain (yfinance) so no historical storage
is needed for v1.  Daily snapshots are appended to
state/catalyst/options_snapshots.jsonl so IC can be measured over time.

Run standalone:  python -m signals.options_anomaly --tickers AAPL MSFT
Called from app: from signals.options_anomaly import run_anomaly_scan
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from config import CATALYST_DIR  # noqa: E402  chief-decifer/state/internal/catalyst/
SNAPSHOTS_FILE = CATALYST_DIR / "options_snapshots.jsonl"


# ── Options chain fetcher ─────────────────────────────────────────────────────

def _fetch_chain(ticker: str) -> dict | None:
    """
    Fetch options chain for the nearest two expiry dates.
    Returns dict {expiry: {calls: DataFrame, puts: DataFrame}} or None on error.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return None
        # Use nearest two expirations to compute IV term structure
        selected = expirations[:2]
        chain = {}
        for exp in selected:
            oc = t.option_chain(exp)
            chain[exp] = {"calls": oc.calls, "puts": oc.puts}
        return chain
    except Exception:
        return None


# ── Current price ─────────────────────────────────────────────────────────────

def _current_price(ticker: str) -> float | None:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        return getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    except Exception:
        return None


# ── Anomaly scorer ────────────────────────────────────────────────────────────

def _score_options(ticker: str, chain: dict, price: float | None) -> dict:
    """
    Compute options anomaly score (0–10) and flags for a single ticker.
    """
    expirations = list(chain.keys())
    if not expirations:
        return {"options_anomaly_score": 0, "options_anomaly_flags": []}

    front_exp = expirations[0]
    front_calls = chain[front_exp]["calls"]
    front_puts  = chain[front_exp]["puts"]

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
    if len(expirations) >= 2 and "impliedVolatility" in front_calls.columns:
        back_exp   = expirations[1]
        back_calls = chain[back_exp]["calls"].copy()
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

        price = _current_price(ticker)
        chain = _fetch_chain(ticker)

        if chain is None:
            result = {"options_anomaly_score": 0, "options_anomaly_flags": ["No options data"]}
        else:
            result = _score_options(ticker, chain, price)

        result["ticker"] = ticker
        result["scanned_at"] = datetime.utcnow().isoformat() + "Z"
        results[ticker] = result

        # Append snapshot for historical IC tracking
        snapshot = {"date": today, **result}
        with SNAPSHOTS_FILE.open("a") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")

        if verbose:
            score = result["options_anomaly_score"]
            flags = result.get("options_anomaly_flags", [])
            print(f"score={score}/10  {'; '.join(flags) or 'no anomaly'}")

        time.sleep(0.2)

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
