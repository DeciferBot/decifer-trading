#!/usr/bin/env python3
"""
verify_shadow_market_data.py — Shadow comparison: intelligence API vs mobile's direct FMP.

Calls the three new intelligence API market-data endpoints and compares their output
against FMP directly (the same calls the mobile app makes today). Prints a diff
report so we can validate data quality before wiring mobile to the intelligence API.

Usage:
    # Against local dev server (default):
    python3 scripts/verify_shadow_market_data.py

    # Against live DigitalOcean:
    python3 scripts/verify_shadow_market_data.py --intel-url https://intelligence.decifertrading.com

    # Verbose (show all symbols):
    python3 scripts/verify_shadow_market_data.py --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

_FMP_BASE = "https://financialmodelingprep.com/stable"


def _fmp_key() -> str:
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        # Try loading from .env in repo root
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            for line in open(env_path).readlines():
                if line.startswith("FMP_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key


def _get(url: str, label: str) -> dict | None:
    try:
        req = Request(url, headers={"User-Agent": "Decifer/shadow-verify"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except URLError as exc:
        print(f"  ✗ {label}: network error — {exc}")
        return None
    except Exception as exc:
        print(f"  ✗ {label}: {exc}")
        return None


def _fmp_movers(key: str) -> tuple[list, list]:
    g = _get(f"{_FMP_BASE}/biggest-gainers?apikey={key}", "FMP gainers")
    l_ = _get(f"{_FMP_BASE}/biggest-losers?apikey={key}", "FMP losers")

    def to_m(r: dict) -> dict:
        pct = r.get("changesPercentage", 0)
        if isinstance(pct, str):
            pct = float(pct.replace("%", ""))
        return {"symbol": r["symbol"], "changePct": round(float(pct), 2),
                "price": float(r.get("price", 0))}

    gainers = [to_m(r) for r in (g or []) if float(r.get("price", 0)) >= 5][:5]
    losers  = [to_m(r) for r in (l_ or []) if float(r.get("price", 0)) >= 5][:5]
    return gainers, losers


def _compare_movers(intel_url: str, key: str, verbose: bool) -> bool:
    print("\n── MOVERS ──────────────────────────────────────────")
    intel = _get(f"{intel_url}/api/market-data/movers", "Intelligence API movers")
    fmp_g, fmp_l = _fmp_movers(key)

    if intel is None:
        print("  ✗ Intelligence API returned no data")
        return False

    ig = intel.get("gainers", [])
    il = intel.get("losers", [])

    intel_syms_g = {m["symbol"] for m in ig}
    fmp_syms_g   = {m["symbol"] for m in fmp_g}
    intel_syms_l = {m["symbol"] for m in il}
    fmp_syms_l   = {m["symbol"] for m in fmp_l}

    print(f"  Gainers — Intel: {sorted(intel_syms_g)} | FMP: {sorted(fmp_syms_g)}")
    print(f"  Losers  — Intel: {sorted(intel_syms_l)} | FMP: {sorted(fmp_syms_l)}")

    match_g = intel_syms_g == fmp_syms_g
    match_l = intel_syms_l == fmp_syms_l
    if match_g and match_l:
        print("  ✓ Symbol sets match exactly")
    else:
        if not match_g:
            print(f"  ~ Gainers differ — only in Intel: {intel_syms_g - fmp_syms_g} | only in FMP: {fmp_syms_g - intel_syms_g}")
        if not match_l:
            print(f"  ~ Losers differ  — only in Intel: {intel_syms_l - fmp_syms_l} | only in FMP: {fmp_syms_l - intel_syms_l}")
        print("  (Differences expected: Intel may have a 5-min cached snapshot vs FMP real-time)")

    if verbose:
        print(f"  Intel source field: {intel.get('source')}, ts: {intel.get('ts')}")
    return True


def _compare_news(intel_url: str, key: str, verbose: bool) -> bool:
    print("\n── NEWS ────────────────────────────────────────────")
    intel = _get(f"{intel_url}/api/market-data/news", "Intelligence API news")
    fmp_stock   = _get(f"{_FMP_BASE}/news/stock-latest?limit=20&apikey={key}", "FMP stock news")
    fmp_general = _get(f"{_FMP_BASE}/news/general-latest?limit=10&apikey={key}", "FMP general news")

    if intel is None:
        print("  ✗ Intelligence API returned no data")
        return False

    intel_news = intel.get("news", [])
    fmp_titles = {
        (n.get("title") or "")[:60].lower()
        for n in (fmp_stock or []) + (fmp_general or [])
        if n.get("title")
    }
    intel_titles = {item["title"][:60].lower() for item in intel_news}

    overlap = len(intel_titles & fmp_titles)
    print(f"  Intel items: {len(intel_news)} | FMP raw items: {len(fmp_titles)}")
    print(f"  Title overlap: {overlap}/{len(intel_news)} intel items found in FMP raw feed")

    if overlap >= len(intel_news) * 0.8:
        print("  ✓ Strong overlap (≥80%) — Intel news matches FMP source")
    else:
        print("  ~ Overlap below 80% — check caching or dedup logic")

    if verbose and intel_news:
        print("  Top 3 Intel headlines:")
        for item in intel_news[:3]:
            print(f"    [{item.get('minutesAgo', '?')}m] {item['title'][:80]}")
    return True


def _compare_tape(intel_url: str, key: str, verbose: bool) -> bool:
    print("\n── TAPE ────────────────────────────────────────────")
    intel = _get(f"{intel_url}/api/market-data/tape", "Intelligence API tape")
    fmp_etf = _get(f"{_FMP_BASE}/batch-quote-short?symbols=SPY,QQQ,IWM,TLT,GLD,USO,UUP&apikey={key}", "FMP ETF tape")
    fmp_vix = _get(f"{_FMP_BASE}/quote/%5EVIX?apikey={key}", "FMP VIX")

    if intel is None:
        print("  ✗ Intelligence API returned no data")
        return False

    intel_tape = {e["sym"]: e for e in intel.get("tape", [])}
    fmp_lookup = {r["symbol"]: r for r in (fmp_etf or [])} if fmp_etf else {}

    mismatches = []
    for sym in ["SPY", "QQQ", "IWM"]:
        i_pct = intel_tape.get(sym, {}).get("changePct")
        fmp_r = fmp_lookup.get(sym, {})
        prev = (fmp_r.get("price", 0) or 0) - (fmp_r.get("change", 0) or 0)
        f_pct = round((fmp_r["change"] / prev) * 100, 2) if prev and fmp_r.get("change") is not None else None
        diff = abs((i_pct or 0) - (f_pct or 0))
        status = "✓" if diff < 0.1 else "~"
        print(f"  {status} {sym}: Intel {i_pct:+.2f}% | FMP {f_pct:+.2f}%" if i_pct is not None and f_pct is not None else f"  ? {sym}: Intel={i_pct} FMP={f_pct}")
        if diff >= 0.1:
            mismatches.append(sym)

    if not mismatches:
        print("  ✓ Tape values match within 0.1% (accounting for cache lag)")
    else:
        print(f"  ~ Mismatches on {mismatches} — may be cache age or rounding")

    if verbose:
        vix = intel_tape.get("VIX", {})
        print(f"  VIX: {vix.get('level')} ({vix.get('changePct'):+.2f}%)" if vix.get("changePct") is not None else f"  VIX: {vix.get('level')}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow verify: intelligence API vs FMP direct")
    parser.add_argument("--intel-url", default="http://localhost:8000",
                        help="Base URL of intelligence API (default: http://localhost:8000)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    key = _fmp_key()
    if not key:
        print("ERROR: FMP_API_KEY not set — cannot compare against FMP directly")
        sys.exit(1)

    print(f"Shadow market data verify — Intel: {args.intel_url}")
    print(f"FMP key: {'set' if key else 'missing'}")

    ok_movers = _compare_movers(args.intel_url, key, args.verbose)
    ok_news   = _compare_news(args.intel_url, key, args.verbose)
    ok_tape   = _compare_tape(args.intel_url, key, args.verbose)

    print("\n── SUMMARY ─────────────────────────────────────────")
    if all([ok_movers, ok_news, ok_tape]):
        print("  ✓ All three market-data endpoints reachable and returning data")
        print("  Ready to wire mobile app when diff above looks acceptable.")
    else:
        print("  ✗ One or more endpoints failed — check intelligence API deployment")
        sys.exit(1)


if __name__ == "__main__":
    main()
