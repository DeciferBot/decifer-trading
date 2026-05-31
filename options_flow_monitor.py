# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_flow_monitor.py                   ║
# ║   Single responsibility: stream OPRA option trades,          ║
# ║   maintain rolling accumulators, detect unusual flow,         ║
# ║   and write results to data/options_flow/.                   ║
# ║                                                              ║
# ║   Runs as a standalone process (Docker: profile options).    ║
# ║   No execution. No broker. No IBKR. Discovery only.          ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone

import dotenv
from zoneinfo import ZoneInfo

from options_flow_engine import (
    FlowEvent,
    FlowPrint,
    UnderlyingWindow,
    detect_events,
    parse_occ,
)

dotenv.load_dotenv()
log = logging.getLogger("decifer.options_flow_monitor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ── Output paths ──────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_OUT_DIR = os.path.join(_BASE_DIR, "data", "options_flow")
_EVENTS_PATH = os.path.join(_OUT_DIR, "live_events.json")
_LEADERBOARD_PATH = os.path.join(_OUT_DIR, "leaderboard.json")

# ── Config ────────────────────────────────────────────────────────────────────
MAX_EVENTS = 500              # rolling cap on live_events.json
WRITE_INTERVAL_SECONDS = 60   # how often to evaluate windows and write files
SWEEP_SIZE_THRESHOLD = 50     # contracts — must match options_flow_engine.MIN_SWEEP_SIZE


# ── Universe loader ───────────────────────────────────────────────────────────

def _load_universe() -> frozenset[str]:
    path = os.path.join(_BASE_DIR, "data", "committed_universe.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        symbols = frozenset(e["symbol"] for e in data.get("symbols", []) if "symbol" in e)
        log.info("options_flow_monitor: loaded %d committed symbols", len(symbols))
        return symbols
    except Exception as exc:
        log.error("options_flow_monitor: failed to load committed universe — %s", exc)
        return frozenset()


# ── Shared state (protected by _lock) ─────────────────────────────────────────
_lock = threading.RLock()
_windows: dict[str, UnderlyingWindow] = {}   # underlying → window
_quote_cache: dict[str, float] = {}          # occ_symbol → best ask
_all_events: list[FlowEvent] = []            # rolling event log
_universe: frozenset[str] = frozenset()
_friday_snapshot_taken: bool = False   # reset each monitor process start


def _get_or_create_window(underlying: str) -> UnderlyingWindow:
    if underlying not in _windows:
        _windows[underlying] = UnderlyingWindow(underlying)
    return _windows[underlying]


# ── Stream callbacks ──────────────────────────────────────────────────────────

async def _on_trade(trade) -> None:
    sym = getattr(trade, "symbol", None) or (trade.get("S") if isinstance(trade, dict) else None)
    if not sym:
        return

    parsed = parse_occ(sym)
    if not parsed:
        return
    underlying, expiry, side, strike = parsed

    if underlying not in _universe:
        return

    price = float(getattr(trade, "price", 0) or (trade.get("p", 0) if isinstance(trade, dict) else 0))
    size = float(getattr(trade, "size", 0) or (trade.get("s", 0) if isinstance(trade, dict) else 0))
    if size <= 0:
        return

    # Sweep = large single print (ask unavailable from OPRA wildcard stream)
    ask = None
    is_sweep = size >= SWEEP_SIZE_THRESHOLD

    fp = FlowPrint(
        ts=datetime.now(tz=timezone.utc),
        occ_symbol=sym,
        underlying=underlying,
        side=side,
        expiry=expiry,
        strike=strike,
        price=price,
        size=size,
        ask=ask,
        is_sweep=is_sweep,
    )

    with _lock:
        _get_or_create_window(underlying).add(fp)


async def _on_quote(quote) -> None:
    sym = getattr(quote, "symbol", None) or (quote.get("S") if isinstance(quote, dict) else None)
    if not sym:
        return
    ask = getattr(quote, "ask_price", None) or (quote.get("ap") if isinstance(quote, dict) else None)
    if ask is None:
        return
    with _lock:
        _quote_cache[sym] = float(ask)


# ── Periodic writer ───────────────────────────────────────────────────────────

def _evaluate_and_write() -> None:
    with _lock:
        windows_snapshot = list(_windows.values())

    new_events: list[FlowEvent] = []
    for window in windows_snapshot:
        window._evict()
        new_events.extend(detect_events(window))

    if not new_events:
        return

    with _lock:
        _all_events.extend(new_events)
        if len(_all_events) > MAX_EVENTS:
            del _all_events[: len(_all_events) - MAX_EVENTS]
        events_snapshot = list(_all_events)

    _write_events(events_snapshot)
    _write_leaderboard(events_snapshot)
    log.info("options_flow_monitor: +%d events (total %d)", len(new_events), len(events_snapshot))


def _write_events(events: list[FlowEvent]) -> None:
    os.makedirs(_OUT_DIR, exist_ok=True)
    payload = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "count": len(events),
        "events": [asdict(e) for e in reversed(events)],  # newest first
    }
    _atomic_write(_EVENTS_PATH, payload)


def _write_leaderboard(events: list[FlowEvent]) -> None:
    scores: dict[str, dict] = {}
    for e in events:
        u = e.underlying
        if u not in scores:
            scores[u] = {
                "underlying": u,
                "call_sweep_count": 0,
                "put_sweep_count": 0,
                "cluster_count": 0,
                "cross_expiry_count": 0,
                "top_score": 0,
                "dominant_side": None,
                "driver_tags": e.driver_tags,
                "last_event_ts": e.ts,
                "total_contracts": 0,
            }
        row = scores[u]
        row["total_contracts"] += e.contracts
        row["top_score"] = max(row["top_score"], e.score)
        row["last_event_ts"] = e.ts
        if e.signal_type == "SWEEP":
            if e.side == "CALL":
                row["call_sweep_count"] += 1
            else:
                row["put_sweep_count"] += 1
        elif e.signal_type == "CLUSTER":
            row["cluster_count"] += 1
        elif e.signal_type == "CROSS_EXPIRY":
            row["cross_expiry_count"] += 1

    for row in scores.values():
        call_total = row["call_sweep_count"]
        put_total = row["put_sweep_count"]
        row["dominant_side"] = "CALL" if call_total > put_total else "PUT" if put_total > call_total else "MIXED"

    ranked = sorted(scores.values(), key=lambda r: r["top_score"], reverse=True)
    payload = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "count": len(ranked),
        "leaderboard": ranked,
    }
    _atomic_write(_LEADERBOARD_PATH, payload)


def _atomic_write(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str)
        os.replace(tmp, path)
    except Exception as exc:
        log.error("options_flow_monitor: write failed %s — %s", path, exc)


_ET = ZoneInfo("America/New_York")
_FRIDAY_SNAPSHOT_HOUR = 15
_FRIDAY_SNAPSHOT_MINUTE_START = 55   # take snapshot in 15:55–16:04 ET window
_FRIDAY_SNAPSHOT_MINUTE_END = 64     # (16:00–16:04 are minute 60–64 conceptually, handled via hour check)


def _maybe_take_friday_snapshot() -> None:
    """Write Friday close snapshot once per monitor session at ~15:55 ET."""
    global _friday_snapshot_taken
    if _friday_snapshot_taken:
        return
    now_et = datetime.now(tz=_ET)
    if now_et.weekday() != 4:   # 4 = Friday
        return
    # Window: 15:55–16:04 ET (minute 55–64; handle hour rollover)
    total_minutes = now_et.hour * 60 + now_et.minute
    if not (15 * 60 + 55 <= total_minutes <= 16 * 60 + 4):
        return
    _friday_snapshot_taken = True
    try:
        from options_flow_scanner import save_friday_close_snapshot
        save_friday_close_snapshot()
    except Exception as exc:
        log.error("options_flow_monitor: Friday snapshot failed — %s", exc)


def _writer_thread() -> None:
    while True:
        time.sleep(WRITE_INTERVAL_SECONDS)
        try:
            _evaluate_and_write()
            _maybe_take_friday_snapshot()
        except Exception as exc:
            log.error("options_flow_monitor: evaluate/write error — %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global _universe
    _universe = _load_universe()
    if not _universe:
        log.error("options_flow_monitor: empty universe — aborting")
        return

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        log.error("options_flow_monitor: ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        return

    os.makedirs(_OUT_DIR, exist_ok=True)

    threading.Thread(target=_writer_thread, daemon=True, name="flow-writer").start()
    log.info("options_flow_monitor: writer thread started (interval=%ds)", WRITE_INTERVAL_SECONDS)

    try:
        from alpaca.data.live.option import OptionDataStream
        from alpaca.data.enums import OptionsFeed
    except ImportError as exc:
        log.error("options_flow_monitor: alpaca-py not installed — %s", exc)
        return

    while True:
        try:
            stream = OptionDataStream(api_key, secret_key, feed=OptionsFeed.OPRA)
            stream.subscribe_trades(_on_trade, "*")
            # Note: wildcard quote subscription is not permitted on OPRA feed (413).
            # Sweep detection uses size >= MIN_SWEEP_SIZE only (no ask comparison).
            log.info("options_flow_monitor: starting OPRA stream (universe=%d symbols)", len(_universe))
            stream.run()
        except Exception as exc:
            log.warning("options_flow_monitor: stream disconnected — %s — reconnecting in 30s", exc)
            time.sleep(30)


if __name__ == "__main__":
    main()
