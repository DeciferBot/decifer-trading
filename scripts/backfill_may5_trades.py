#!/usr/bin/env python3
"""
Backfill May 5 2026 missing closed-trade records.

Root cause: execute_sell() took the deferred path (status=EXITING), set close_order_id,
saved positions.json, then returned. On the next bot restart active_trades was reloaded
without those exited positions. When the IBKR fill callback fired, _close_position_record()
found active_trades.get(sym, {}) empty → silent early return. The ✅ callback clog was a
false positive (it fires unconditionally after the return).

Source of truth for exit_price and pnl: IBKR orderStatus callback logs.
Source of truth for fill_price: ORDER_FILLED events in trade_events.jsonl.
External positions (NBIS, AMAT-2, SNDK): IBKR reconciler EXTERNAL POSITION log entries.
"""

import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_LOG = os.path.join(REPO, "data", "trade_events.jsonl")
TRAINING_LOG = os.path.join(REPO, "data", "training_records.jsonl")

UTC = timezone.utc

def _dt(s):
    return datetime.fromisoformat(s)

def _hold(ts_fill, ts_close):
    return int((_dt(ts_close) - _dt(ts_fill)).total_seconds() / 60)

# ── Canonical backfill data ────────────────────────────────────────────────────
# All timestamps UTC. fill_price from ORDER_FILLED; exit/pnl from IBKR callback.
# regime=TRENDING_UP: confirmed from log (short_against_bull_regime, trending_up refs).
BACKFILL = [
    {
        "trade_id": "UBER_20260430_225921_540133",
        "symbol": "UBER",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 74.83,
        "intended_price": 74.7914,
        "exit_price": 74.03,
        "pnl": -351.66,
        "exit_reason": "stop_loss_hit",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T23:14:00+00:00",
        "ts_close": "2026-05-04T21:13:51+00:00",
    },
    {
        "trade_id": "V_20260430_134624_731011",
        "symbol": "V",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 331.371,
        "intended_price": 331.66,
        "exit_price": 327.25,
        "pnl": -32.48,
        "exit_reason": "regime_flip_loser",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T17:27:00+00:00",
        "ts_close": "2026-05-05T08:42:29+00:00",
    },
    {
        "trade_id": "AMAT_20260430_161923_354061",
        "symbol": "AMAT",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 390.2667,
        "intended_price": 390.62,
        "exit_price": 398.05,
        "pnl": 1159.71,
        "exit_reason": "swing_timeout_weak_return",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T16:24:00+00:00",
        "ts_close": "2026-05-05T10:12:46+00:00",
    },
    {
        "trade_id": "COST_20260430_134604_090757",
        "symbol": "COST",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 1005.978,
        "intended_price": 1007.0,
        "exit_price": 1007.0,
        "pnl": 59.32,
        "exit_reason": "swing_timeout_regime_flip",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T17:27:00+00:00",
        "ts_close": "2026-05-05T10:49:27+00:00",
    },
    {
        "trade_id": "XLK_20260430_125955_526901",
        "symbol": "XLK",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 160.238,
        "intended_price": 160.25,
        "exit_price": 163.52,
        "pnl": 502.33,
        "exit_reason": "scalp_timeout",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T17:27:00+00:00",
        "ts_close": "2026-05-05T12:21:20+00:00",
    },
    {
        # External position added at 19:02 +04 May 4 = 15:02 UTC May 4
        "trade_id": "NBIS_EXT_20260504_150241",
        "symbol": "NBIS",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 171.125,
        "intended_price": 171.125,
        "exit_price": 171.1917,
        "pnl": 17.63,
        "exit_reason": "swing_timeout_band_low",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-05-04T15:02:41+00:00",
        "ts_close": "2026-05-05T13:32:17+00:00",
    },
    {
        "trade_id": "WMT_20260430_133522_221692",
        "symbol": "WMT",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 129.835,
        "intended_price": 128.0,
        "exit_price": 130.51,
        "pnl": 233.55,
        "exit_reason": "swing_timeout_weak_gain",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T17:27:00+00:00",
        "ts_close": "2026-05-05T13:32:33+00:00",
    },
    {
        "trade_id": "NVDA_20260430_135423_397265",
        "symbol": "NVDA",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 204.295,
        "intended_price": 207.89,
        "exit_price": 199.68,
        "pnl": 308.28,
        "exit_reason": "swing_timeout_below_threshold",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T13:57:00+00:00",
        "ts_close": "2026-05-05T13:34:17+00:00",
    },
    {
        "trade_id": "MU_20260430_131852_701780",
        "symbol": "MU",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 529.209,
        "intended_price": 527.795,
        "exit_price": 648.42,
        "pnl": 6439.56,
        "exit_reason": "tp_hit",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T17:27:00+00:00",
        "ts_close": "2026-05-05T14:31:20+00:00",
    },
    {
        "trade_id": "OXY_20260430_211120_133181",
        "symbol": "OXY",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 60.555,
        "intended_price": 60.5,
        "exit_price": 59.3882,
        "pnl": 41.34,
        "exit_reason": "scalp_timeout",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T21:09:00+00:00",
        "ts_close": "2026-05-05T15:13:04+00:00",
    },
    {
        "trade_id": "CAT_20260501_134600_003466",
        "symbol": "CAT",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 898.9138,
        "intended_price": 903.2018,
        "exit_price": 903.85,
        "pnl": -112.80,
        "exit_reason": "stale_intraday_force_exit",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-05-01T13:49:00+00:00",
        "ts_close": "2026-05-05T15:56:33+00:00",
    },
    {
        # External position added at 19:56 +04 May 5 = 15:56 UTC. qty=144 @ 402.94.
        # pnl check: (412.63 - 402.94) * 144 = 9.69 * 144 = 1395.36 ≈ 1395.16 ✓
        "trade_id": "AMAT_EXT_20260505_155626",
        "symbol": "AMAT",
        "direction": "LONG",
        "trade_type": "SCALP",
        "instrument": "equity_long",
        "fill_price": 402.9414,
        "intended_price": 402.9414,
        "exit_price": 412.63,
        "pnl": 1395.16,
        "exit_reason": "swing_timeout",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-05-05T15:56:26+00:00",
        "ts_close": "2026-05-05T16:02:20+00:00",
    },
    {
        "trade_id": "USO_20260430_205400_814677",
        "symbol": "USO",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 147.665,
        "intended_price": 147.895,
        "exit_price": 143.27,
        "pnl": -1467.65,
        "exit_reason": "regime_flip",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T21:09:00+00:00",
        "ts_close": "2026-05-05T16:03:01+00:00",
    },
    {
        # External position added at 19:56 +04 May 5 = 15:56 UTC. qty=45 @ 1272.02.
        # pnl check: (1376.00 - 1272.02) * 45 = 103.98 * 45 = 4679.1 ≈ 4679.00 ✓
        "trade_id": "SNDK_EXT_20260505_155629",
        "symbol": "SNDK",
        "direction": "LONG",
        "trade_type": "SCALP",
        "instrument": "equity_long",
        "fill_price": 1272.0222,
        "intended_price": 1272.0222,
        "exit_price": 1376.0,
        "pnl": 4679.00,
        "exit_reason": "tp_exceeded",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-05-05T15:56:29+00:00",
        "ts_close": "2026-05-05T16:43:56+00:00",
    },
    {
        # Third QQQ close (16:57 UTC) not recorded. The other two (17:08, 17:19) are present.
        # fill_price estimated from QQQ_20260430_135447 fill (659.66); position loaded on restart.
        "trade_id": "QQQ_BACKFILL_20260505_165757",
        "symbol": "QQQ",
        "direction": "LONG",
        "trade_type": "SWING",
        "instrument": "equity_long",
        "fill_price": 659.6614,
        "intended_price": 659.6614,
        "exit_price": 681.36,
        "pnl": 624.02,
        "exit_reason": "regime_flip_tech_weakness",
        "regime": "TRENDING_UP",
        "ts_fill": "2026-04-30T13:57:00+00:00",
        "ts_close": "2026-05-05T16:57:57+00:00",
    },
]


def _load_closed_tids(path):
    """Return set of trade_ids that already have a POSITION_CLOSED in path."""
    closed = set()
    if not os.path.exists(path):
        return closed
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("event") == "POSITION_CLOSED":
                tid = r.get("trade_id")
                if tid:
                    closed.add(tid)
    return closed


def _load_training_tids(path):
    """Return set of trade_ids already in training_records."""
    tids = set()
    if not os.path.exists(path):
        return tids
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = r.get("trade_id")
            if tid:
                tids.add(tid)
    return tids


def main():
    now_ts = datetime.now(UTC).isoformat()

    closed_tids = _load_closed_tids(EVENTS_LOG)
    training_tids = _load_training_tids(TRAINING_LOG)

    events_written = 0
    training_written = 0
    skipped = 0

    with open(EVENTS_LOG, "a", encoding="utf-8") as ef, \
         open(TRAINING_LOG, "a", encoding="utf-8") as tf:

        for t in BACKFILL:
            tid = t["trade_id"]
            hold = _hold(t["ts_fill"], t["ts_close"])

            # ── trade_events.jsonl ────────────────────────────────────────────
            if tid in closed_tids:
                print(f"  SKIP events  {tid[:40]} (already closed)")
                skipped += 1
            else:
                event = {
                    "ts": t["ts_close"],
                    "event": "POSITION_CLOSED",
                    "trade_id": tid,
                    "symbol": t["symbol"],
                    "exit_price": t["exit_price"],
                    "pnl": t["pnl"],
                    "exit_reason": t["exit_reason"],
                    "hold_minutes": hold,
                    "_backfill": True,
                    "_backfill_ts": now_ts,
                }
                ef.write(json.dumps(event) + "\n")
                events_written += 1
                print(f"  WROTE event  {tid[:40]}  {t['symbol']} exit={t['exit_price']} pnl={t['pnl']}")

            # ── training_records.jsonl ────────────────────────────────────────
            if tid in training_tids:
                print(f"  SKIP training {tid[:40]} (already in training_records)")
                skipped += 1
            else:
                pnl_pct = round(
                    (t["exit_price"] - t["fill_price"]) / t["fill_price"] * 100, 4
                ) if t["fill_price"] else 0.0
                record = {
                    "trade_id": tid,
                    "symbol": t["symbol"],
                    "direction": t["direction"],
                    "trade_type": t["trade_type"],
                    "instrument": t["instrument"],
                    "fill_price": t["fill_price"],
                    "intended_price": t["intended_price"],
                    "exit_price": t["exit_price"],
                    "pnl": t["pnl"],
                    "hold_minutes": hold,
                    "exit_reason": t["exit_reason"],
                    "regime": t["regime"],
                    "signal_scores": {},
                    "conviction": 0.0,
                    "score": 0.0,
                    "ts_fill": t["ts_fill"],
                    "ts_close": t["ts_close"],
                    "setup_type": None,
                    "pattern_id": None,
                    "atr": None,
                    "score_breakdown": {},
                    "ic_weights_at_entry": None,
                    "pnl_pct": pnl_pct,
                    "ts_written": now_ts,
                    "_backfill": True,
                }
                tf.write(json.dumps(record) + "\n")
                training_written += 1
                print(f"  WROTE train  {tid[:40]}  {t['symbol']} pnl={t['pnl']}")

    print(f"\nDone. Events written: {events_written}, training written: {training_written}, skipped: {skipped}")
    total_closed = len(_load_closed_tids(EVENTS_LOG))
    total_training = len(_load_training_tids(TRAINING_LOG))
    print(f"trade_events total POSITION_CLOSED: {total_closed}")
    print(f"training_records total: {total_training}")


if __name__ == "__main__":
    main()
