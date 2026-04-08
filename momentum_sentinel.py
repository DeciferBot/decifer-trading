#!/usr/bin/env python3
"""
momentum_sentinel.py — Real-time SPY momentum interrupt for Decifer.

Single responsibility: monitor SPY 1-minute bars from BAR_CACHE (live Alpaca
WebSocket stream) and fire an immediate scan bypass when SPY moves fast.

Why this exists
---------------
The main trading loop fires scans on a schedule (every 3–5 minutes). A strong
intraday rally or sell-off can be fully underway before the next scheduled scan
fires. This sentinel detects the move within 10–30 seconds of the threshold
being crossed and sets `bot_state._momentum_scan_requested` — an event flag the
main loop checks on every 1-second iteration. The scan then runs immediately on
the main thread, bypassing the scheduler entirely.

This follows the same background-thread + event-flag pattern as the News and
Catalyst sentinels.

Detection logic
---------------
Two independent signals — either fires the interrupt:
  1. Fast burst:  |SPY last 3 bars| > momentum_sentinel_fast_pct  (default 0.3%)
                  ~3 minutes of 1m bars. Catches acceleration spikes.
  2. Sustained:   |SPY last 10 bars| > momentum_sentinel_slow_pct (default 0.6%)
                  ~10 minutes of 1m bars. Catches relief rallies and sell-offs
                  that build over several candles.

A cooldown (default 15 min) prevents retriggering on the same move.
Direction is logged and passed to the scan so the regime router can confirm.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from config import CONFIG

log = logging.getLogger("decifer.momentum_sentinel")


class MomentumSentinel:
    """
    Background thread that monitors SPY 1-minute bars and fires an immediate
    scan when a fast directional move is detected.
    """

    def __init__(self) -> None:
        self._running    = False
        self._thread: threading.Thread | None = None
        self._last_fire  = None   # datetime of last trigger (for cooldown)
        self.stats: dict = {
            "status":         "stopped",
            "last_trigger":   None,
            "trigger_count":  0,
            "last_direction": None,
            "last_magnitude": None,
            "last_type":      None,
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._run,
            daemon=True,
            name="momentum-sentinel",
        )
        self._thread.start()
        self.stats["status"] = "running"
        log.info("MomentumSentinel: started")

    def stop(self) -> None:
        self._running        = False
        self.stats["status"] = "stopped"
        log.info("MomentumSentinel: stopped")

    # ── Background loop ────────────────────────────────────────────────────────

    def _run(self) -> None:
        poll_s      = CONFIG.get("momentum_sentinel_poll_s",     10)
        fast_pct    = CONFIG.get("momentum_sentinel_fast_pct",    0.3)
        slow_pct    = CONFIG.get("momentum_sentinel_slow_pct",    0.6)
        cooldown_m  = CONFIG.get("momentum_sentinel_cooldown_m",  15)

        while self._running:
            try:
                self._check(fast_pct, slow_pct, cooldown_m)
            except Exception as exc:
                log.debug("MomentumSentinel._check error: %s", exc)
            time.sleep(poll_s)

    def _check(self, fast_pct: float, slow_pct: float, cooldown_m: float) -> None:
        # ── Cooldown guard ─────────────────────────────────────────────────────
        if self._last_fire is not None:
            elapsed_m = (datetime.now() - self._last_fire).total_seconds() / 60
            if elapsed_m < cooldown_m:
                return

        # ── Read live SPY 1m bars from BAR_CACHE ──────────────────────────────
        # SPY is a STREAM_ANCHOR — always subscribed, so BAR_CACHE has its bars.
        from alpaca_stream import BAR_CACHE
        df = BAR_CACHE._data.get("SPY")   # raw 1m bars (thread-safe read under lock)
        if df is None or len(df) < 3:
            return

        closes = df["Close"].dropna()
        if len(closes) < 3:
            return

        spy_now = float(closes.iloc[-1])

        # Signal 1: fast burst — last 3 x 1m bars (~3 min)
        n_fast = min(3, len(closes))
        spy_3m_ago = float(closes.iloc[-n_fast])
        fast_move  = (spy_now - spy_3m_ago) / spy_3m_ago * 100

        # Signal 2: sustained — last 10 x 1m bars (~10 min)
        n_slow = min(10, len(closes))
        spy_10m_ago = float(closes.iloc[-n_slow])
        slow_move   = (spy_now - spy_10m_ago) / spy_10m_ago * 100

        # ── Evaluate thresholds ────────────────────────────────────────────────
        trigger_type = None
        magnitude    = 0.0

        if abs(fast_move) >= fast_pct:
            trigger_type = "fast_burst"
            magnitude    = fast_move
        elif abs(slow_move) >= slow_pct:
            trigger_type = "sustained"
            magnitude    = slow_move

        if trigger_type is None:
            return

        direction = "UP" if magnitude > 0 else "DOWN"
        self._fire(direction, magnitude, trigger_type)

    def _fire(self, direction: str, magnitude: float, trigger_type: str) -> None:
        import bot_state
        from bot_state import clog

        self._last_fire = datetime.now()
        self.stats["trigger_count"]  += 1
        self.stats["last_trigger"]    = self._last_fire.strftime("%H:%M:%S")
        self.stats["last_direction"]  = direction
        self.stats["last_magnitude"]  = round(magnitude, 3)
        self.stats["last_type"]       = trigger_type

        arrow = "▲" if direction == "UP" else "▼"
        clog(
            "SIGNAL",
            f"⚡ MOMENTUM SENTINEL [{trigger_type}]: SPY {arrow} {magnitude:+.2f}% "
            f"→ immediate scan requested",
        )

        # Signal the main loop to run a scan immediately.
        # The main loop checks this every 1 second and calls scheduled_scan().
        bot_state._momentum_scan_requested.set()


# ── Factory ────────────────────────────────────────────────────────────────────

def start_momentum_sentinel() -> MomentumSentinel:
    sentinel = MomentumSentinel()
    sentinel.start()
    return sentinel
