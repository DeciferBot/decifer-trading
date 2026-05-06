# MS $2M Position Incident — Definitive Root Cause Analysis

## Status
MS position was correctly closed on restart (POSITION_CLOSED via callback: exit=$193.63, pnl=-$1,821.52). This doc describes why it happened and what needs to be fixed to prevent recurrence.

---

## The Full Verified Timeline (reconstructed from audit_log + trade_events)

```
2026-05-05 17:03 UTC
  SELL STP LMT 321 @ $183.80 submitted → MS position existed from YESTERDAY
  Bot had an MS LONG position (321 shares) carried from a prior session, EXT orphan

2026-05-06 09:00 → 10:41 UTC  (5:00 AM → 6:41 AM ET, pre-market)
  6× SELL LMT 321 @ $187–$190.92 submitted, none fill
  Bot correctly trying to force-exit EXT LONG. Each scan cycle reprices upward.
  MS pre-market price is ABOVE the limit prices → orders queue as GTC but don't fill

10:41:23 UTC  ← TRIGGER POINT
  SELL LMT 321 @ $189.52 submitted (order #14349)
  2× POSITION_CLOSED written for same trade_id within 2 seconds:
    pnl=+481.11 (10:41:23) — (191.33 - 189.83) × 321 = +$481 ✓ LONG math correct
    pnl=−186.565 (10:41:25) — different entry/qty used (corrupted duplicate)
  ← RACE CONDITION: dispatch_forced_exit fired twice concurrently
  ← Two SELL 321 orders submitted, BOTH FILLED → IBKR: 321 → 0 → −321 (NET SHORT)

Next reconcile after 10:41 UTC
  IBKR shows item.position = −321
  EXT path (orders_portfolio.py:1014): direction = "SHORT" if item.position < 0
  New EXT created: direction=SHORT, qty=321, trade_type=UNKNOWN

10:59 → 13:16 UTC  (6:59 → 9:16 AM ET, pre-market / extended hours)
  9× BUY LMT 639 @ $193.10 → $192.38 submitted, none show FILLED
  execute_sell for SHORT position → close_action="BUY" (correct for covering short)
  These are GTC limit orders placed at/below market during extended hours
  Each dispatch_forced_exit cycle cancels the PREVIOUS close_order_id, but:
  ← NEW GTC BUY order submitted each cycle with a FRESH order_id
  ← Old GTC orders from prior cycles are NOT cancelled (different order_ids, not tracked)
  ← 9 GTC LMT BUY 639 orders accumulate as open orders in IBKR

13:30 UTC  (9:30 AM ET — REGULAR SESSION OPENS)
  MS price at open: ~$193. The 9 accumulated GTC LMT BUY orders at $192–$193
  fill at or near market open. 9 × 639 = 5,751 shares bought.
  IBKR: −321 (from double-sell) + 5751 = ~5,430 LONG (≈ $1.05M)
  ← No POSITION_CLOSED written because BUY fill callback has no SHORT-exit close path

13:31 → 15:14 UTC  (9:31 AM → 11:14 AM ET)
  Reconcile sees large LONG position in IBKR but preserves stored direction=SHORT
  (reconcile "known position" path reads stored direction, never cross-checks IBKR sign)
  Qty updated to match IBKR (1344 → 5376 → 10752 as position grew)
  3× MKT BUY 1344, 4× MKT BUY 5376, 10× MKT BUY 10752 — all CANCELLED by IBKR
  ← IBKR cancels MKT BUY orders that would push account beyond limits at that size

  Amit manually closes 50% → IBKR: 5,376 shares remain

15:25 UTC
  Reconcile (or new EXT after Amit's close): ORDER_INTENT direction=LONG, qty=5376
  ← Fresh state, IBKR position now cleanly visible

15:32 UTC
  SELL MKT 5376 submitted (order #15141) → CANCELLED
  ← Likely cancelled on bot shutdown/restart

~15:33 UTC  RESTART
  positions.json has no MS (or fresh LONG EXT from 15:25 with direction=LONG)
  Reconcile creates fresh EXT: direction=LONG (item.position=5376 > 0)
  dispatch_forced_exit → execute_sell → close_action=SELL → fills → POSITION_CLOSED
  "✅ POSITION_CLOSED via callback: MS exit=193.6279 pnl=-1821.52"
```

---

## 5 Verified Root Causes

### RC-1: Double-close race in `dispatch_forced_exit`
**File:** `signal_dispatcher.py:699-743`

`dispatch_forced_exit` has no per-symbol lock. Two threads called it for MS simultaneously. Both cancelled the existing close order. Both reset EXITING → ACTIVE. Both called execute_sell. Both submitted SELL 321 orders. Both filled. IBKR went from 321 LONG → 0 → **−321 NET SHORT**. This created the SHORT EXT that drove the entire cascade.

**Fix:** Add `_get_symbol_lock(symbol)` guard at the top of `dispatch_forced_exit` so only one thread can execute a forced exit for a given symbol at a time.

---

### RC-2: GTC exit orders accumulate across EXT recreations
**File:** `orders_core.py:2100-2138` (live GTC exit guard) and `signal_dispatcher.py:714-728` (cancel-and-resubmit)

When execute_sell submits a GTC LMT order during extended hours and the position is destroyed and recreated (new EXT trade_id), dispatch_forced_exit cancels only the `close_order_id` stored on the current position. Orders from PRIOR cycles (different order_ids, no longer tracked in `close_order_id`) remain open in IBKR. Over 9 scan cycles, 9 GTC BUY 639 orders accumulated. At market open they all filled, creating a 5,751-share LONG from a −321 SHORT.

The live GTC exit guard at line 2104-2115 only searches for orders matching `close_action` — it doesn't cancel ALL outstanding orders for the symbol before placing a new one.

**Fix:** In `execute_sell`, before submitting any new exit order, call `ib.openTrades()` and cancel ALL open orders for the symbol (excluding SL/TP brackets). Not just the one tracked in `close_order_id`. Add a helper: `_cancel_all_open_orders_for_symbol(ib, symbol, exclude_sl_tp=True)`.

---

### RC-3: Reconcile "known position" path never cross-checks direction against IBKR sign
**File:** `orders_portfolio.py:896-962`

```python
stored_direction = active_trades[key].get("direction", "LONG")
```

When MS had direction=SHORT in active_trades but IBKR showed a LARGE POSITIVE position (LONG), the reconcile blindly preserved "SHORT". No validation that `stored_direction` is consistent with IBKR's `item.position` sign. This caused ALL subsequent exit orders to be BUY (wrong) instead of SELL (correct).

**Fix:** After reading `stored_direction`, add:
```python
ibkr_direction = "SHORT" if item.position < 0 else "LONG"
if stored_direction != ibkr_direction:
    log.critical("Direction mismatch: stored=%s ibkr=%s for %s — correcting to IBKR",
                  stored_direction, ibkr_direction, key)
    stored_direction = ibkr_direction
    active_trades[key]["direction"] = ibkr_direction
```
IBKR is always the source of truth for position sign.

---

### RC-4: `execute_sell` uses local position `qty`, not IBKR actual qty
**File:** `orders_core.py:1956`
```python
sell_qty = qty_override if _is_partial else info["qty"]
```
`info["qty"]` is the locally tracked qty. When positions are reconstructed through EXT paths with stale or wrong qtys, the sell order closes the wrong amount. After the double-sell created a net short (-321), subsequent BUY exits were submitted at 639, 1344, 5376, 10752 — each from whatever qty reconcile had last written.

**Fix:** In `execute_sell`, after acquiring the position lock, query `ib.positions()` for the symbol's actual current qty. Use `max(info["qty"], ibkr_actual_qty)` to prevent partial closes. If they differ by more than 10%, log a CRITICAL warning.

---

### RC-5: No POSITION_CLOSED callback for SHORT exits (BUY fills)
**File:** `bot_ibkr.py:1445-1527`

The SELL fill handler (line 1512-1527) correctly writes POSITION_CLOSED when a LONG exit (SELL order) fills:
```python
if _t_pre.get("status") == "EXITING" and fill_price > 0:
    _close_position_record(...)  # ← correct
```

The BUY fill handler (line 1365-1443) handles LONG ENTRIES only. There is **no equivalent POSITION_CLOSED path** when a BUY fills for an EXITING SHORT position. When a SHORT exit (BUY to cover) fills, the position stays in `active_trades` as EXITING indefinitely. The deferred close handler also doesn't fire because IBKR still shows a position (the short was covered, position now 0 or positive — but not gone enough to trigger the "k not in price_map" condition).

**Fix:** In the BUY fill handler, add a SHORT exit path:
```python
# SHORT exit fill — if position is EXITING with direction=SHORT, write POSITION_CLOSED
if _t_pre.get("status") == "EXITING" and _t_pre.get("direction") == "SHORT" and fill_price > 0:
    _close_position_record(sym, exit_price=fill_price, exit_reason=..., pnl=...)
    clog("TRADE", f"✅ POSITION_CLOSED via callback (short cover): {sym} exit={fill_price:.4f}")
```

---

## Why Restart Fixed It (and Between-Scans Didn't)

**Between scans:** direction=SHORT was preserved by the reconcile "known position" path (RC-3). execute_sell submitted BUY (correct for SHORT, wrong for reality). BUY fills had no POSITION_CLOSED callback (RC-5). Deferred handler didn't fire (IBKR position grew, not shrank). Each scan cycle submitted a new GTC BUY that accumulated (RC-2). Loop continued.

**On restart:** The position in positions.json was either absent or the 15:25 EXT had direction=LONG. Reconcile created a fresh EXT with `direction = "LONG" if item.position > 0`. execute_sell submitted SELL (correct for LONG). SELL filled → `_on_order_status_event` callback (line 1516): `if _t_pre.get("status") == "EXITING" and fill_price > 0 → POSITION_CLOSED`. Done.

---

## Files to Modify

| File | Line(s) | Fix |
|------|---------|-----|
| `signal_dispatcher.py` | 699-743 | RC-1: per-symbol lock in `dispatch_forced_exit` |
| `orders_core.py` | ~2100 (before order placement) | RC-2: cancel ALL open orders for symbol before submitting exit |
| `orders_portfolio.py` | 901-902 | RC-3: cross-check `stored_direction` vs IBKR sign; correct if mismatch + log CRITICAL |
| `orders_core.py` | 1956 | RC-4: use `max(local_qty, ibkr_qty)` for sell_qty |
| `bot_ibkr.py` | ~1444 (end of BUY fill handler) | RC-5: add SHORT exit POSITION_CLOSED path for BUY fills |

---

## Implementation Order

1. **RC-3 first** — Correcting direction from IBKR sign is the single fix that would have prevented the whole cascade. Immediate safety. 3 lines.

2. **RC-1 second** — Prevent the double-close from ever creating a net short. Add symbol lock to dispatch_forced_exit. 5 lines.

3. **RC-2 third** — Cancel all open orders for symbol before submitting exit. This is the fix for the GTC accumulation that caused the $2M. Medium complexity.

4. **RC-5 fourth** — Add POSITION_CLOSED callback for BUY fills (SHORT exits). This closes the recovery gap that prevents between-scan self-healing.

5. **RC-4 last** — Use IBKR actual qty in execute_sell. Lower priority now that RC-3 prevents direction mislabeling.

---

## Verification

1. `python3 -m pytest tests/ -x -q` — must stay ≥2024 passing
2. Focus on: `tests/test_reconcile.py`, `tests/test_orders_core.py`, `tests/test_guardrails.py`
3. Simulate the double-close race: call `dispatch_forced_exit` twice concurrently for the same symbol. Verify only ONE SELL is submitted.
4. Simulate GTC accumulation: inject a "known SHORT position" with multiple stale open orders. Verify execute_sell cancels all of them before placing its own.
5. Simulate direction mismatch: create active_trades entry with direction=SHORT, but mock IBKR position as +500 (LONG). Verify reconcile corrects direction and logs CRITICAL.
