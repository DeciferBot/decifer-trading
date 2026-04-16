#!/usr/bin/env python3
"""
analyze_entry_timing.py
-----------------------
Measures how close each LONG trade entry was to the high-of-day at entry time.

For each closed LONG trade:
  1. Fetch 1-minute Alpaca bars for that symbol on the entry date
  2. Compute day_high_at_entry = max(High) from market open to entry_time
  3. Compute pct_from_hod = (entry_price - day_high_at_entry) / day_high_at_entry * 100
     (negative = entered below HOD, close to 0 = entered near HOD)

Outputs:
  - Summary table by hour bucket
  - Win/loss breakdown by HOD proximity bucket
  - List of worst "chasing" trades

Usage:
  cd "/Users/amitchopra/Desktop/decifer trading"
  python3 scripts/analyze_entry_timing.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

# ── project root on path ─────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import pandas as pd

# ── Load trades ───────────────────────────────────────────────────────────────

TRADES_PATH = os.path.join(ROOT, "data", "trades.json")

with open(TRADES_PATH) as f:
    raw = json.load(f)

all_trades = raw if isinstance(raw, list) else raw.get("trades", [])

# Keep only closed LONG equity trades (skip options — they have no HOD comparison)
def is_equity(sym: str) -> bool:
    """Simple heuristic: options symbols contain spaces or 'P'/'C' after date-like string."""
    return " " not in sym and len(sym) <= 6

closed_longs = [
    t for t in all_trades
    if t.get("action") == "CLOSE"
    and t.get("direction") == "LONG"
    and t.get("entry_price")
    and t.get("entry_time")
    and is_equity(t.get("symbol", ""))
]

print(f"Closed LONG equity trades to analyse: {len(closed_longs)}")

# ── Alpaca bar fetcher (intraday) ──────────────────────────────────────────────

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

API_KEY = os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

if not API_KEY or not SECRET_KEY:
    print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set. Check .env")
    sys.exit(1)

client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

_bar_cache: dict[tuple[str, str], pd.DataFrame | None] = {}

def fetch_intraday_bars(symbol: str, date_str: str) -> pd.DataFrame | None:
    """Fetch 1-minute bars for symbol on date_str (YYYY-MM-DD). Cached."""
    key = (symbol, date_str)
    if key in _bar_cache:
        return _bar_cache[key]

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start = dt.replace(hour=9, minute=29, tzinfo=UTC)
        end   = dt.replace(hour=20, minute=0,  tzinfo=UTC)

        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed="sip",
            adjustment="split",
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            _bar_cache[key] = None
            return None

        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)

        df.index = pd.to_datetime(df.index, utc=True)
        _bar_cache[key] = df
        time.sleep(0.05)   # gentle rate-limit
        return df
    except Exception as exc:
        print(f"  [WARN] fetch_intraday_bars({symbol}, {date_str}): {exc}")
        _bar_cache[key] = None
        return None


# ── Per-trade HOD measurement ─────────────────────────────────────────────────

results = []

for t in closed_longs:
    sym = t["symbol"]
    ep  = float(t["entry_price"])
    xp  = float(t.get("exit_price") or ep)
    pnl = float(t.get("pnl") or 0)

    try:
        entry_dt = datetime.fromisoformat(t["entry_time"])
    except Exception:
        continue

    date_str = entry_dt.strftime("%Y-%m-%d")
    entry_hour = entry_dt.hour

    # entry_dt in UTC-aware form for comparison with Alpaca index
    if entry_dt.tzinfo is None:
        # trades.json stores local market time — treat as US Eastern → UTC offset ~4-5h
        # We'll match bars within ±2 bar windows instead of exact tz conversion
        entry_dt_utc = entry_dt.replace(tzinfo=UTC) + timedelta(hours=4)
    else:
        entry_dt_utc = entry_dt.astimezone(UTC)

    df = fetch_intraday_bars(sym, date_str)
    if df is None:
        continue

    # Bars from open up to (and including) the entry bar
    bars_before_entry = df[df.index <= entry_dt_utc]
    if bars_before_entry.empty:
        # If timezone offset is off, widen the window by 1h
        bars_before_entry = df[df.index <= entry_dt_utc + timedelta(hours=1)]

    if bars_before_entry.empty:
        continue

    day_high_at_entry = float(bars_before_entry["high"].max())
    day_open          = float(df.iloc[0]["open"]) if not df.empty else None

    # How far below HOD was the entry (negative = entered BELOW HOD, 0 = at HOD)
    pct_from_hod = (ep - day_high_at_entry) / day_high_at_entry * 100  # ≤ 0

    # Pct move from open to entry (how extended was the stock when we entered)
    pct_from_open = ((ep - day_open) / day_open * 100) if day_open else None

    results.append({
        "symbol":          sym,
        "date":            date_str,
        "entry_hour":      entry_hour,
        "entry_price":     ep,
        "day_high_at_entry": day_high_at_entry,
        "day_open":        day_open,
        "pct_from_hod":    pct_from_hod,
        "pct_from_open":   pct_from_open,
        "exit_price":      xp,
        "pnl":             pnl,
        "win":             pnl > 0,
        "hold_minutes":    t.get("hold_minutes"),
        "exit_reason":     t.get("exit_reason", ""),
    })

print(f"\nTrades with HOD data: {len(results)} / {len(closed_longs)}")

if not results:
    print("No results — check Alpaca connectivity.")
    sys.exit(1)

df_r = pd.DataFrame(results)

# ── HOD proximity buckets ─────────────────────────────────────────────────────
# pct_from_hod is always ≤ 0
# 0.0  to -1.0%  → "at HOD" (within 1%)
# -1.0 to -3.0%  → "near HOD"
# -3.0 to -7.0%  → "below HOD"
# < -7.0%         → "well below HOD"

def hod_bucket(pct):
    if pct >= -1.0:
        return "1_AT_HOD (0–1% below)"
    elif pct >= -3.0:
        return "2_NEAR_HOD (1–3% below)"
    elif pct >= -7.0:
        return "3_BELOW_HOD (3–7% below)"
    else:
        return "4_WELL_BELOW (>7% below)"

df_r["hod_bucket"] = df_r["pct_from_hod"].apply(hod_bucket)

# ── Print results ─────────────────────────────────────────────────────────────

print("\n" + "="*70)
print("HOD PROXIMITY ANALYSIS — ALL CLOSED LONG EQUITY TRADES")
print("="*70)

bucket_stats = df_r.groupby("hod_bucket").agg(
    trades=("pnl", "count"),
    wins=("win", "sum"),
    avg_pnl=("pnl", "mean"),
    total_pnl=("pnl", "sum"),
    avg_pct_from_hod=("pct_from_hod", "mean"),
).reset_index()
bucket_stats["win_rate"] = bucket_stats["wins"] / bucket_stats["trades"] * 100

print(bucket_stats.to_string(index=False))

print("\n" + "="*70)
print("HOD PROXIMITY  ×  ENTRY HOUR")
print("="*70)

pivot = df_r.pivot_table(
    index="entry_hour",
    columns="hod_bucket",
    values="win",
    aggfunc=["count", "mean"],
    fill_value=0,
)
print(pivot.to_string())

print("\n" + "="*70)
print("WORST 'BUS ALREADY LEFT' TRADES  (entered within 1% of HOD, lost)")
print("="*70)

at_hod_losers = df_r[
    (df_r["pct_from_hod"] >= -1.0) & (df_r["pnl"] < 0)
].sort_values("pnl")

cols = ["symbol", "date", "entry_hour", "entry_price", "day_high_at_entry",
        "pct_from_hod", "pct_from_open", "exit_price", "pnl", "exit_reason"]
print(at_hod_losers[cols].to_string(index=False))

print("\n" + "="*70)
print("KEY METRICS SUMMARY")
print("="*70)

at_hod      = df_r[df_r["hod_bucket"] == "1_AT_HOD (0–1% below)"]
below_hod   = df_r[df_r["hod_bucket"] != "1_AT_HOD (0–1% below)"]

print(f"Entries within 1% of HOD:  {len(at_hod):3d}  |  "
      f"Win rate: {at_hod['win'].mean()*100:.1f}%  |  "
      f"Avg P&L: ${at_hod['pnl'].mean():+.0f}")
print(f"Entries >1% below HOD:     {len(below_hod):3d}  |  "
      f"Win rate: {below_hod['win'].mean()*100:.1f}%  |  "
      f"Avg P&L: ${below_hod['pnl'].mean():+.0f}")

print(f"\nMedian pct_from_hod at entry (all trades): {df_r['pct_from_hod'].median():.2f}%")
print(f"Median pct_from_hod — winners:             {df_r[df_r['win']]['pct_from_hod'].median():.2f}%")
print(f"Median pct_from_hod — losers:              {df_r[~df_r['win']]['pct_from_hod'].median():.2f}%")

print(f"\nMedian pct_from_open at entry (all):  {df_r['pct_from_open'].median():.2f}%")
print(f"Median pct_from_open — winners:       {df_r[df_r['win']]['pct_from_open'].median():.2f}%")
print(f"Median pct_from_open — losers:        {df_r[~df_r['win']]['pct_from_open'].median():.2f}%")

# ── Save CSV for further analysis ─────────────────────────────────────────────

out_path = os.path.join(ROOT, "data", "entry_timing_analysis.csv")
df_r.to_csv(out_path, index=False)
print(f"\nFull results saved to: {out_path}")
