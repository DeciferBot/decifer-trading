# Feature: Short-Candidate Scanner Pipeline

**Status:** Research Complete — Ready to Build
**Priority:** CRITICAL — Agents never see short candidates
**Estimated Build Time:** 2-3 days
**Files to Modify:** `scanner.py`, `bot.py`
**Dependencies:** None (can build independently of all other roadmap items)

---

## Problem

The TradingView Screener queries in `scanner.py` naturally surface stocks moving up — momentum gainers, volume spikes, breakout candidates. By the time the 6 agents see the filtered candidates, they're already biased toward longs. The agents are rubber-stamping a bullish pipeline. Even a perfectly direction-agnostic signal engine can't recommend shorts if it never sees breakdown candidates.

## Proposed Solution

Add a parallel short-candidate scan pipeline that runs alongside the existing long-candidate scans.

### Short Scan Criteria (all available in free data stack)

1. **Breakdown scan:** Price below 20-EMA AND 50-EMA, EMA(9) < EMA(21) < EMA(50) (bear alignment), ADX > 20 (trend is real, not noise)
2. **Volume distribution scan:** Price down on 2x+ average volume (institutional selling)
3. **Failed breakout scan:** Price crossed above Donchian 20-day high within last 5 days BUT has fallen back below — bull trap
4. **Bearish divergence scan:** Price making higher highs but OBV/MFI making lower highs — distribution
5. **High IV rank scan:** IV Rank > 70 with bearish price action — options market pricing in downside

### Implementation

1. Add `get_short_candidates()` to `scanner.py` — runs 3-4 TradingView Screener queries for bearish setups
2. Merge short candidates into the same universe as long candidates in `bot.py:run_scan()`
3. Tag each candidate with `scan_source: "long_scan" | "short_scan"` for attribution tracking
4. Feed both pools into `signals.py` — the direction-agnostic engine (see `01-direction-agnostic-signals.md`) will score them equally

### TradingView Screener Queries (Feasibility Confirmed)

TradingView Screener supports these filters for free:
- `close < EMA20 AND close < EMA50` — price below moving averages
- `relative_volume > 2.0 AND change < -1` — heavy volume selling
- `RSI < 35` — oversold / bearish momentum
- `change_from_open < -2` — intraday breakdown

## Risks

- **Inverse ETF overlap:** System already uses SPXS/SQQQ/UVXY for short exposure. Short candidates could conflict with inverse ETF positions. Need position-level check: if already short SPY via SPXS, don't also short SPY components.
- **Borrow availability:** IBKR paper allows short selling but live requires share borrowing. Can't get borrow data from free sources. Mitigation: on paper, assume all shorts are borrowable. On live, add `check_shortable_shares` via IBKR API before execution (IBKR MCP tool already available).
- **Short squeeze risk:** Heavily shorted stocks can squeeze violently. Add short interest check if data becomes available, or stick to liquid large-caps for shorts.

## Validation

- After 1 week of running both scan pipelines: measure ratio of LONG vs SHORT candidates surfaced. In a flat market, should be roughly balanced. In a trending market, should skew toward the trend direction naturally.
- Track win rate of trades originated from short scan vs long scan separately.

## References

- Current scanner: `scanner.py:get_candidates()`
- TradingView Screener API: already integrated via `tvDatafeed` or `tradingview_screener`
- IBKR short selling: `mcp__ibkr__check_shortable_shares` tool available
