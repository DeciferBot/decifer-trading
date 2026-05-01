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
import time

from config import CONFIG

log = logging.getLogger("decifer.price_updater")

_INTERVAL = 2  # seconds between price update passes

# Options price cache: occ_sym → {"mid": float, "bid": float, "ask": float, "ts": float}
# Avoids hammering the Alpaca REST API on every 2-second tick.
_OPTION_PRICE_CACHE: dict = {}
_OPTION_PRICE_TTL = 15  # seconds between Alpaca snapshot refreshes per contract


def _get_option_mid(trade: dict) -> dict | None:
    """
    Fetch current option mid price via Alpaca snapshot REST API.
    Uses a 15-second TTL cache to avoid API rate limits.

    Returns {mid, bid, ask, source} or None if unavailable.
    """
    symbol = trade.get("symbol", "")
    expiry_ibkr = trade.get("expiry_ibkr", "")
    right = trade.get("right", "")
    strike = trade.get("strike")

    if not (symbol and expiry_ibkr and right and strike):
        return None

    try:
        from alpaca_options import build_option_symbol, get_snapshot_greeks

        occ_sym = build_option_symbol(symbol, expiry_ibkr, right, float(strike))
    except Exception:
        return None

    cached = _OPTION_PRICE_CACHE.get(occ_sym)
    if cached and (time.monotonic() - cached["ts"]) < _OPTION_PRICE_TTL:
        return {
            "mid": cached["mid"],
            "bid": cached["bid"],
            "ask": cached["ask"],
            "source": "alpaca_snapshot",
        }

    snap = get_snapshot_greeks(occ_sym)
    if snap and snap.get("mid") is not None and snap["mid"] > 0:
        entry = {
            "mid": round(snap["mid"], 4),
            "bid": round(snap.get("bid", 0.0), 4),
            "ask": round(snap.get("ask", 0.0), 4),
            "ts": time.monotonic(),
        }
        _OPTION_PRICE_CACHE[occ_sym] = entry
        return {
            "mid": entry["mid"],
            "bid": entry["bid"],
            "ask": entry["ask"],
            "source": "alpaca_snapshot",
        }
    return None


def get_live_prices() -> dict:
    """
    Return {symbol: {mid, bid, ask, spread_pct, source}} for all open
    positions plus SPY and QQQ anchors.

    For stock/FX positions the key is the ticker symbol.
    For options the key is the underlying symbol (matches data-symbol on position cards).

    source = "stream"           — live from QUOTE_CACHE (real-time bid/ask)
           = "bar"              — last 1-minute bar close from BAR_CACHE (~1-2 min lag)
           = "alpaca_snapshot"  — Alpaca REST snapshot for option contracts
           = "stale"            — no data available
    """
    from alpaca_stream import BAR_CACHE, QUOTE_CACHE
    from orders_state import _trades_lock, active_trades

    with _trades_lock:
        snapshot = {k: dict(v) for k, v in active_trades.items()}

    # Stock/FX: look up QUOTE_CACHE by ticker symbol
    stock_symbols: set[str] = set()
    for v in snapshot.values():
        if v.get("instrument") != "option":
            stock_symbols.add(v.get("symbol", ""))

    stock_symbols.update({"SPY", "QQQ"})
    stock_symbols.discard("")

    result: dict = {}

    # Wide-spread quotes (pre-market stubs, illiquid prints) produce bad mid prices.
    # Gate: if spread > 5% of mid, treat the quote as unreliable and fall back to
    # the last bar close.  5% is generous for display purposes — the order-entry
    # gate (max_spread_pct = 0.3%) is far tighter and is applied separately.
    _MAX_DISPLAY_SPREAD = CONFIG.get("max_display_spread_pct", 0.05)

    for sym in stock_symbols:
        quote = QUOTE_CACHE.get(sym)
        _quote_usable = False
        if quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0:
            _bid, _ask = quote["bid"], quote["ask"]
            _mid = (_bid + _ask) / 2
            _spread_pct = (_ask - _bid) / _mid if _mid else 1.0
            _quote_usable = _spread_pct <= _MAX_DISPLAY_SPREAD

        if _quote_usable:
            mid = round(_mid, 4)
            result[sym] = {
                "mid": mid,
                "bid": round(_bid, 4),
                "ask": round(_ask, 4),
                "spread_pct": round(_spread_pct, 6),
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

    # Options: fetch premium via Alpaca snapshot, keyed by underlying symbol
    for v in snapshot.values():
        if v.get("instrument") != "option":
            continue
        underlying = v.get("symbol", "")
        if not underlying:
            continue
        price = _get_option_mid(v)
        if price:
            result[underlying] = {
                "mid": price["mid"],
                "bid": price["bid"],
                "ask": price["ask"],
                "spread_pct": 0.0,
                "source": price["source"],
            }

    return result


class PriceUpdater:
    """
    Background daemon thread: reads QUOTE_CACHE every _INTERVAL seconds and
    writes mid-prices into active_trades["current"] and dash["regime"]["spy_price"].

    Thread-safe: all active_trades mutations use _trades_lock (RLock).
    Falls back to BAR_CACHE last-close when QUOTE_CACHE has no data for a symbol.
    If neither cache has data, leaves the existing "current" value unchanged.
    Options positions are priced via Alpaca snapshot REST API (15s TTL cache).
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
        # Snapshot without holding lock during slow cache/API reads
        with _trades_lock:
            items = [(k, dict(v)) for k, v in active_trades.items()]

        for key, trade in items:
            if trade.get("instrument") == "option":
                # Options are not in StockDataStream — fetch premium via Alpaca REST snapshot
                price = _get_option_mid(trade)
                if price and price["mid"] > 0:
                    with _trades_lock:
                        if key in active_trades:
                            active_trades[key]["current"] = price["mid"]
                            active_trades[key]["current_premium"] = price["mid"]
                            active_trades[key]["current_ts"] = time.time()
                # If snapshot unavailable, leave "current" unchanged
            else:
                sym = trade.get("symbol", key)
                quote = QUOTE_CACHE.get(sym)
                if quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0:
                    mid = round((quote["bid"] + quote["ask"]) / 2, 4)
                    with _trades_lock:
                        if key in active_trades:
                            active_trades[key]["current"] = mid
                            active_trades[key]["current_ts"] = time.time()
                else:
                    df = BAR_CACHE.get_5m(sym)
                    if df is not None and not df.empty:
                        close = float(df.iloc[-1]["Close"])
                        if close > 0:
                            with _trades_lock:
                                if key in active_trades:
                                    active_trades[key]["current"] = round(close, 4)
                                    active_trades[key]["current_ts"] = time.time()
                    # If neither cache has data, leave "current" unchanged

        # Update SPY price in the regime display
        spy = QUOTE_CACHE.get("SPY")
        if spy and spy.get("bid", 0) > 0 and spy.get("ask", 0) > 0:
            spy_mid = round((spy["bid"] + spy["ask"]) / 2, 2)
            regime = dash.get("regime")
            if isinstance(regime, dict):
                regime["spy_price"] = spy_mid
