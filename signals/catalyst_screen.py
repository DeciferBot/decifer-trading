"""
M&A Target Fundamental Screen
==============================
Screens a broad stock universe for companies that exhibit classic acquisition-
target characteristics: low EV/Revenue, net-cash balance sheet, meaningful
revenue growth, and a market cap in the "acquisition sweet spot".

Run standalone:  python -m signals.catalyst_screen
Called from app: from signals.catalyst_screen import run_screen

Output: state/catalyst/candidates_YYYY-MM-DD.json
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
# Use the config's sacred state path so output lands where the dashboard reads.
from config import CATALYST_DIR  # noqa: E402  chief-decifer/state/internal/catalyst/

# ── Defaults (overridden by config.CATALYST_THRESHOLDS if available) ──────────

_DEFAULT_THRESHOLDS = {
    "ev_revenue_max":     3.0,    # EV/Revenue must be below this
    "revenue_growth_min": 0.10,   # YoY revenue growth >= 10%
    "market_cap_min":     1e9,    # $1 B minimum
    "market_cap_max":     50e9,   # $50 B maximum
    "target_sectors": [
        "Healthcare",
        "Technology",
        "Industrials",
        "Communication Services",
        "Consumer Discretionary",
    ],
}


def _thresholds() -> dict:
    try:
        from config import CATALYST_THRESHOLDS
        merged = {**_DEFAULT_THRESHOLDS, **CATALYST_THRESHOLDS}
        return merged
    except (ImportError, AttributeError):
        return _DEFAULT_THRESHOLDS


# ── Universe loader ───────────────────────────────────────────────────────────

def _load_sp500_tickers() -> list[str]:
    """
    Fetch S&P 500 tickers from Wikipedia.  Falls back to an empty list on error.
    Requires pandas (already a project dependency).
    """
    try:
        import io
        import urllib.request
        import pandas as pd
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        # Wikipedia requires a browser-like User-Agent; pandas read_html uses a
        # default that gets 403'd.  Fetch with urllib and pass the HTML string.
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DeciferBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8")
        tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        return tickers
    except Exception as exc:
        print(f"  [catalyst_screen] WARNING: Could not fetch S&P 500 list: {exc}")
        return []


def _load_watchlist() -> list[str]:
    """
    Check for a manually maintained watchlist at state/catalyst/watchlist.json.
    Format: {"tickers": ["AAPL", "MSFT", ...]}
    """
    wl = CATALYST_DIR / "watchlist.json"
    if wl.exists():
        try:
            data = json.loads(wl.read_text())
            return data.get("tickers", [])
        except Exception:
            pass
    return []


# ── Fundamental fetcher ───────────────────────────────────────────────────────

def _fetch_info(ticker: str) -> dict | None:
    """
    Fetch yfinance Ticker.info.  Returns None on any error or empty response.

    yfinance quirks handled:
    - Returns {} or {"trailingPegRatio": None} for unknown/delisted tickers
    - Raises HTTPError 404 for invalid symbols
    - Returns a dict missing price fields for ETFs / non-equity instruments
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info

        # yfinance returns a near-empty dict (1–3 keys) for unknown tickers
        if not info or len(info) < 5:
            return None

        # Must have a valid price field — rules out delisted and ETF-only symbols
        price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
        if not price:
            return None

        return info
    except Exception:
        return None


# ── Scorer ───────────────────────────────────────────────────────────────────

def _score_ticker(ticker: str, info: dict, thr: dict) -> dict | None:
    """
    Score a ticker against M&A target criteria.
    Returns a candidate dict if score >= 2 (out of 5), else None.
    """
    sector        = info.get("sector", "")
    market_cap    = info.get("marketCap") or 0
    ev_revenue    = info.get("enterpriseToRevenue")
    revenue_growth = info.get("revenueGrowth")   # decimal, e.g. 0.18 = 18%
    total_cash    = info.get("totalCash") or 0
    total_debt    = info.get("totalDebt") or 0
    short_name    = info.get("shortName", ticker)

    score  = 0
    flags  = []

    # ── Criterion 1: sector ───────────────────────────────────────────────────
    if sector in thr["target_sectors"]:
        score += 1
        flags.append(f"Sector: {sector}")

    # ── Criterion 2: market cap sweet spot ───────────────────────────────────
    if thr["market_cap_min"] <= market_cap <= thr["market_cap_max"]:
        score += 1
        cap_b = market_cap / 1e9
        flags.append(f"Market cap ${cap_b:.1f}B (sweet spot)")

    # ── Criterion 3: low EV/Revenue ───────────────────────────────────────────
    if ev_revenue is not None and 0 < ev_revenue <= thr["ev_revenue_max"]:
        score += 1
        flags.append(f"EV/Revenue {ev_revenue:.2f}x (≤{thr['ev_revenue_max']}x)")

    # ── Criterion 4: net cash positive ───────────────────────────────────────
    if total_cash > total_debt:
        net_cash_m = (total_cash - total_debt) / 1e6
        score += 1
        flags.append(f"Net cash ${net_cash_m:.0f}M")

    # ── Criterion 5: revenue growth ───────────────────────────────────────────
    if revenue_growth is not None and revenue_growth >= thr["revenue_growth_min"]:
        score += 1
        flags.append(f"Revenue growth {revenue_growth*100:.0f}% YoY")

    if score < 2:
        return None

    return {
        "ticker":           ticker,
        "name":             short_name,
        "sector":           sector,
        "market_cap":       market_cap,
        "ev_revenue":       ev_revenue,
        "revenue_growth":   revenue_growth,
        "net_cash":         total_cash - total_debt,
        "fundamental_score": score,          # 0–5
        "fundamental_score_max": 5,
        "flags":            flags,
        "screened_at":      datetime.utcnow().isoformat() + "Z",
        "options_anomaly_score": 0,          # filled in by options_anomaly.py
        "options_anomaly_flags": [],
        "edgar_score":      0,               # filled in by edgar_monitor.py
        "edgar_events":     [],
        "sentiment_score":  0.0,             # filled in by sentiment_scorer.py
        "sentiment_claude": None,
        "sentiment_finbert": None,
        "sentiment_flags":  [],
        # composite score (0–10); updated after all four signal tiers run
        # F:35% + O:35% + E:15% + S:15%
        "catalyst_score":   round(score / 5 * 10 * 0.35, 1),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def run_screen(tickers: list[str] | None = None, verbose: bool = False, force: bool = False) -> list[dict]:
    """
    Run the fundamental M&A target screen.

    Parameters
    ----------
    tickers : list of ticker strings, or None to auto-load S&P 500 + watchlist.
    verbose : print progress to stdout.
    force   : re-run even if today's candidates file already exists.

    Returns
    -------
    List of candidate dicts sorted by fundamental_score descending.
    """
    CATALYST_DIR.mkdir(parents=True, exist_ok=True)
    thr = _thresholds()

    # ── Skip if today's file already exists (avoid re-scraping 500 tickers) ──
    today = datetime.utcnow().strftime("%Y-%m-%d")
    out_path = CATALYST_DIR / f"candidates_{today}.json"
    if not force and out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            candidates = existing.get("candidates", [])
            if candidates:
                if verbose:
                    print(f"  [catalyst_screen] Today's file already exists ({len(candidates)} candidates) — skipping re-scan. Pass force=True to override.")
                return candidates
        except Exception:
            pass  # corrupt file — fall through and re-run

    if tickers is None:
        sp500    = _load_sp500_tickers()
        watchlist = _load_watchlist()
        tickers  = list(dict.fromkeys(sp500 + watchlist))  # deduplicate, preserve order

    if not tickers:
        print("  [catalyst_screen] No tickers to scan.", file=sys.stderr)
        return []

    if verbose:
        print(f"  [catalyst_screen] Scanning {len(tickers)} tickers …")

    candidates = []
    for i, ticker in enumerate(tickers):
        if verbose and i % 50 == 0 and i > 0:
            print(f"  [catalyst_screen]   {i}/{len(tickers)} scanned, {len(candidates)} candidates so far")

        info = _fetch_info(ticker)
        if info is None:
            continue

        candidate = _score_ticker(ticker, info, thr)
        if candidate:
            candidates.append(candidate)

        # Polite throttle — yfinance is a free service
        time.sleep(0.15)

    candidates.sort(key=lambda c: c["fundamental_score"], reverse=True)

    # Persist
    payload = {
        "_schema_version": 1,
        "date":        today,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tickers_scanned": len(tickers),
        "candidates":  candidates,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    if verbose:
        print(f"  [catalyst_screen] Done — {len(candidates)} candidates → {out_path.name}")

    return candidates


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="M&A Target Fundamental Screen")
    parser.add_argument("--tickers", nargs="*", help="Override ticker list")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    results = run_screen(tickers=args.tickers, verbose=True)
    print(f"\nTop 10 candidates:")
    for c in results[:10]:
        print(f"  {c['ticker']:6s}  score={c['fundamental_score']}/5  {', '.join(c['flags'])}")
