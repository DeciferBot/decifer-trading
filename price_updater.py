# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  price_updater.py                          ║
# ║   Single responsibility: propagate live QUOTE_CACHE prices  ║
# ║   into active_trades and dash every 2 seconds.              ║
# ║                                                             ║
# ║   Reads:  QUOTE_CACHE, BAR_CACHE  (alpaca_stream singletons)║
# ║   Writes: active_trades["current"], dash["regime"]["spy_price"]║
# ║                                                             ║
# ║   No trading logic. No signals. No order submission.        ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import threading

log = logging.getLogger("decifer.price_updater")

_INTERVAL = 2  # seconds between price update passes


def get_live_prices() -> dict:
    """
    Return {symbol: {mid, bid, ask, spread_pct, source}} for all open
    positions plus SPY and QQQ anchors.

    source = "stream"  — live from QUOTE_CACHE (real-time bid/ask)
           = "bar"     — last 1-minute bar close from BAR_CACHE (~1-2 min lag)
           = "stale"   — neither cache has data; price unknown
    """
    from alpaca_stream import BAR_CACHE, QUOTE_CACHE
    from orders_state import _trades_lock, active_trades

    with _trades_lock:
        # symbol field may differ from key for options (e.g. KOD_C_35.0_2026-04-17)
        symbols = {v.get("symbol", k) for k, v in active_trades.items()}

    symbols.update({"SPY", "QQQ"})
    result: dict = {}

    for sym in symbols:
        quote = QUOTE_CACHE.get(sym)
        if quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0:
            mid = round((quote["bid"] + quote["ask"]) / 2, 4)
            result[sym] = {
                "mid": mid,
                "bid": round(quote["bid"], 4),
                "ask": round(quote["ask"], 4),
                "spread_pct": round(quote.get("spread_pct") or 0, 6),
                "source": "stream",
            }
        else:
            df = BAR_CACHE.get_5m(sym)
            if df is not None and not df.empty:
                close = float(df.iloc[-1]["Close"])
                result[sym] = {
                    "mid": round(close, 4),
                    "bid": 0.0,
                    "ask": 0.0,
                    "spread_pct": 0.0,
                    "source": "bar",
                }
            else:
                result[sym] = {
                    "mid": 0.0,
                    "bid": 0.0,
                    "ask": 0.0,
                    "spread_pct": 0.0,
                    "source": "stale",
                }

    return result


class PriceUpdater:
    """
    Background daemon thread: reads QUOTE_CACHE every _INTERVAL seconds and
    writes mid-prices into active_trades["current"] and dash["regime"]["spy_price"].

    Thread-safe: all active_trades mutations use _trades_lock (RLock).
    Falls back to BAR_CACHE last-close when QUOTE_CACHE has no data for a symbol.
    If neither cache has data, leaves the existing "current" value unchanged.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background price propagation thread (non-blocking)."""
        if self._thread and self._thread.is_alive():
            log.debug("PriceUpdater: already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="price-updater",
        )
        self._thread.start()
        log.info("PriceUpdater: started (2s interval, QUOTE_CACHE → active_trades)")

    def stop(self) -> None:
        """Signal the background thread to exit cleanly."""
        self._stop_event.set()
        log.info("PriceUpdater: stopped")

    def _run(self) -> None:
        import bot_state
        from alpaca_stream import BAR_CACHE, QUOTE_CACHE
        from orders_state import _trades_lock, active_trades

        while not self._stop_event.wait(_INTERVAL):
            try:
                self._update_once(
                    QUOTE_CACHE,
                    BAR_CACHE,
                    active_trades,
                    _trades_lock,
                    bot_state.dash,
                )
            except Exception as exc:
                log.debug(f"PriceUpdater: update skipped — {exc}")

    @staticmethod
    def _update_once(QUOTE_CACHE, BAR_CACHE, active_trades, _trades_lock, dash) -> None:
        """Single price propagation pass."""
        # Snapshot keys without holding the lock during slow cache reads
        with _trades_lock:
            items = [(k, v.get("symbol", k)) for k, v in active_trades.items()]

        for key, sym in items:
            quote = QUOTE_CACHE.get(sym)
            if quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0:
                mid = round((quote["bid"] + quote["ask"]) / 2, 4)
                with _trades_lock:
                    if key in active_trades:
                        active_trades[key]["current"] = mid
            else:
                df = BAR_CACHE.get_5m(sym)
                if df is not None and not df.empty:
                    close = float(df.iloc[-1]["Close"])
                    if close > 0:
                        with _trades_lock:
                            if key in active_trades:
                                active_trades[key]["current"] = round(close, 4)
                # If neither cache has data, leave "current" unchanged

        # Update SPY price in the regime display
        spy = QUOTE_CACHE.get("SPY")
        if spy and spy.get("bid", 0) > 0 and spy.get("ask", 0) > 0:
            spy_mid = round((spy["bid"] + spy["ask"]) / 2, 2)
            regime = dash.get("regime")
            if isinstance(regime, dict):
                regime["spy_price"] = spy_mid
