# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  learning.py                                ║
# ║   Trade logging, performance tracking, weekly review         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta

import anthropic

from config import CONFIG

log = logging.getLogger("decifer.learning")

TRADE_LOG_FILE = CONFIG.get("trade_log", "data/trades.json")
ORDER_LOG_FILE = CONFIG.get("order_log", "data/orders.json")
SIGNALS_LOG_FILE = CONFIG.get("signals_log", "data/signals_log.jsonl")
AUDIT_LOG_FILE = CONFIG.get("audit_log", "data/audit_log.jsonl")
CAPITAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "capital_base.json")
_capital_lock = threading.Lock()
_exec_ic_lock = threading.Lock()

# Rotate signals_log.jsonl once it exceeds this size to prevent the file growing forever.
# Archived files are named  data/signals_log_archive_YYYYMMDD_HHMMSS.jsonl  and kept
# alongside the live file so ic_calculator can still read them if needed.
_SIGNALS_LOG_ROTATE_BYTES = 50 * 1024 * 1024  # 50 MB


def _parse_ts(s: str) -> datetime:
    """Parse an ISO timestamp string and always return a UTC-aware datetime.

    Naive timestamps (no tzinfo) are assumed to be UTC, which is the
    convention used throughout Decifer. Comparing naive vs aware datetimes
    raises TypeError — this utility eliminates that class of bug.
    """
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ── Immutable audit log ────────────────────────────────────────────────


def _append_audit_event(event_type: str, **fields) -> None:
    """Append one audit event to DB (primary) and audit_log.jsonl (fallback).

    DB is the source of truth. File is kept for human readability and
    tooling that reads it directly (Chief Decifer, external scripts).
    """
    record = {"ts": datetime.utcnow().isoformat() + "Z", "event": event_type, **fields}
    os.makedirs(os.path.dirname(os.path.abspath(AUDIT_LOG_FILE)), exist_ok=True)
    try:
        with open(AUDIT_LOG_FILE, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.error(f"audit_log file write failed: {exc} — event was: {record}")


# ── Capital base tracking ──────────────────────────────────────────────


def load_capital_base() -> dict:
    """Load capital base with adjustments for deposits/withdrawals."""
    if os.path.exists(CAPITAL_FILE):
        try:
            with open(CAPITAL_FILE) as f:
                return json.load(f)
        except Exception as exc:
            log.error(f"load_capital_base: failed to parse {CAPITAL_FILE} — {exc}. Returning defaults.")
            _append_audit_event("CAPITAL_BASE_DEFAULTED", reason="parse_error", error=str(exc))
    else:
        log.error(f"load_capital_base: {CAPITAL_FILE} not found — using default starting capital. Position sizing may be wrong.")
        _append_audit_event("CAPITAL_BASE_DEFAULTED", reason="file_missing")
    # Default: starting capital from config, no adjustments
    return {"starting_capital": CONFIG.get("starting_capital", 1_000_000), "adjustments": []}


def get_effective_capital() -> float:
    """Return starting capital + all deposits - all withdrawals."""
    data = load_capital_base()
    base = data.get("starting_capital", CONFIG.get("starting_capital", 1_000_000))
    total_adj = sum(a.get("amount", 0) for a in data.get("adjustments", []))
    return base + total_adj


def record_capital_adjustment(amount: float, note: str = ""):
    """Record a deposit (+) or withdrawal (-) adjustment. Atomic write + locked."""
    with _capital_lock:
        data = load_capital_base()
        data["adjustments"].append({"amount": amount, "note": note, "timestamp": datetime.now().isoformat()})
        dir_path = os.path.dirname(CAPITAL_FILE) or "."
        os.makedirs(dir_path, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".tmp") as f:
                json.dump(data, f, indent=2)
                tmp_path = f.name
            os.replace(tmp_path, CAPITAL_FILE)
        except Exception as e:
            log.error(f"record_capital_adjustment: failed to write {CAPITAL_FILE}: {e}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return
    log.info(f"Capital adjustment: ${amount:+,.2f} ({note}). New base: ${get_effective_capital():,.2f}")


# ── Order logging (every order placed, regardless of fill status) ──────
def log_order(order_record: dict, trade_id: str | None = None):
    """
    Log an order to orders.json and append the matching event to decifer.db.

    trade_id: pass explicitly when in scope (execute_buy / execute_short / execute_sell).
    When omitted, the function tries order_record.get("trade_id") first, then falls back
    to a best-effort lookup from active_trades by symbol so fill_watcher and bot_ibkr
    callers get DB coverage without any call-site changes.

    order_record should contain:
      symbol, side (BUY/SELL), order_type (LMT/MKT/STP), qty, price,
      order_id, status (SUBMITTED/FILLED/CANCELLED/REJECTED),
      instrument (stock/option), timestamp, reasoning, etc.
    """
    # Sanitise price — reject float overflow / infinity / NaN
    import math

    price = order_record.get("price", 0)
    if isinstance(price, float) and (math.isnan(price) or math.isinf(price) or abs(price) > 1e10):
        log.warning(f"Corrupt price {price} for {order_record.get('symbol')} — setting to 0")
        order_record["price"] = 0

    orders = load_orders()

    # Dedup by order_id if present (and non-zero)
    oid = order_record.get("order_id")
    if oid:  # truthy order_id (non-zero, non-None)
        for existing in orders:
            if existing.get("order_id") == oid:
                # Update existing order (e.g. status change)
                existing.update({k: v for k, v in order_record.items() if v is not None})
                _save_orders(orders)
                log.info(f"Order updated: {order_record.get('symbol')} #{oid} → {order_record.get('status')}")
                return

    # For order_id=0 (bracket children, synced orders), dedup on
    # symbol + side + qty + price + instrument to prevent duplicates
    # from repeated sync cycles
    if oid == 0 or oid is None:
        sym = order_record.get("symbol")
        side = order_record.get("side")
        qty = order_record.get("qty")
        price = order_record.get("price")
        inst = order_record.get("instrument", "stock")
        for existing in orders:
            if (
                existing.get("order_id") in (0, None)
                and existing.get("symbol") == sym
                and existing.get("side") == side
                and existing.get("qty") == qty
                and existing.get("price") == price
                and existing.get("instrument", "stock") == inst
            ):
                # Already logged — just update status if changed
                if existing.get("status") != order_record.get("status"):
                    existing.update({k: v for k, v in order_record.items() if v is not None})
                    _save_orders(orders)
                    log.info(f"Order updated: {sym} (id=0) → {order_record.get('status')}")
                return

    orders.append(order_record)
    _save_orders(orders)
    log.info(
        f"Order logged: {order_record.get('side')} {order_record.get('symbol')} "
        f"qty={order_record.get('qty')} @ ${order_record.get('price', 0):.2f} "
        f"[{order_record.get('status', 'SUBMITTED')}]"
    )
    _append_audit_event(
        order_record.get("status", "SUBMITTED"),
        symbol=order_record.get("symbol"),
        order_id=order_record.get("order_id"),
        side=order_record.get("side"),
        qty=order_record.get("qty"),
        order_type=order_record.get("order_type"),
        limit_price=order_record.get("price"),
        fill_price=order_record.get("fill_price"),
    )

    # ── Mirror to decifer.db event log ───────────────────────────────────────
    # Resolve trade_id: explicit param → order_record field → active_trades lookup
    _db_trade_id = trade_id or order_record.get("trade_id")
    if not _db_trade_id:
        try:
            from orders_state import active_trades, _trades_lock
            _sym = order_record.get("symbol", "")
            with _trades_lock:
                _db_trade_id = active_trades.get(_sym, {}).get("trade_id")
        except Exception:
            pass
    if _db_trade_id:
        try:
            _status = order_record.get("status", "")
            _sym = order_record.get("symbol", "")
            _oid = order_record.get("order_id") or 0
            _qty = order_record.get("qty") or 0
            if _status == "FILLED":
                from event_log import append_fill as _el_fill
                _el_fill(_db_trade_id, _sym,
                         fill_price=order_record.get("fill_price") or order_record.get("price") or 0.0,
                         fill_qty=_qty, order_id=_oid)
        except Exception as _db_err:
            log.warning("log_order: DB write failed for %s: %s", order_record.get("symbol"), _db_err)


def update_order_status(order_id: int, status: str, fill_price: float | None = None, filled_qty: int | None = None):
    """Update an existing order's status (FILLED, CANCELLED, REJECTED, etc.)."""
    orders = load_orders()
    for o in orders:
        if o.get("order_id") == order_id:
            o["status"] = status
            o["updated"] = datetime.now(UTC).isoformat()
            if fill_price is not None:
                o["fill_price"] = fill_price
            if filled_qty is not None:
                o["filled_qty"] = filled_qty
            _save_orders(orders)
            log.info(f"Order #{order_id} status → {status}")
            return
    log.warning(f"Order #{order_id} not found for status update")


def load_orders() -> list:
    """Load all order records."""
    if not os.path.exists(ORDER_LOG_FILE):
        return []
    try:
        with open(ORDER_LOG_FILE) as f:
            return json.load(f)
    except Exception as exc:
        log.error(f"load_orders: failed to parse {ORDER_LOG_FILE} — {exc}. Returning empty list.")
        return []


def _save_orders(orders: list):
    """Write orders list to disk atomically, sanitising corrupt float values first.
    Falls back to a direct write if the directory does not support temp files (e.g. /dev/null in tests).
    """
    import math
    import tempfile

    # Sanitise any inf/nan prices that may have slipped through in existing records
    for o in orders:
        p = o.get("price")
        if isinstance(p, float) and (math.isnan(p) or math.isinf(p) or abs(p) > 1e10):
            o["price"] = 0
    target = ORDER_LOG_FILE
    dir_ = os.path.dirname(os.path.abspath(target)) or "."
    try:
        os.makedirs(dir_, exist_ok=True)
        # Atomic write: write to a temp file then rename so the file is never half-written
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(orders, f, indent=2)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        # Fallback: direct write (e.g. target is /dev/null in test environments)
        with open(target, "w") as f:
            json.dump(orders, f, indent=2)


def _rotate_signals_log() -> None:
    """
    Archive signals_log.jsonl when it exceeds _SIGNALS_LOG_ROTATE_BYTES.
    The live file is moved to data/signals_log_archive_YYYYMMDD_HHMMSS.jsonl so
    ic_calculator can still reference it, and a fresh file starts immediately.
    Errors are swallowed — log rotation must never block signal writing.
    """
    try:
        if not os.path.exists(SIGNALS_LOG_FILE):
            return
        size = os.path.getsize(SIGNALS_LOG_FILE)
        if size < _SIGNALS_LOG_ROTATE_BYTES:
            return
        import shutil

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        archive = SIGNALS_LOG_FILE.replace(".jsonl", f"_archive_{ts}.jsonl")
        shutil.move(SIGNALS_LOG_FILE, archive)
        log.info(f"signals_log rotated: {os.path.basename(archive)} ({size / 1_048_576:.1f} MB) — fresh log started")
    except Exception as exc:
        log.warning(f"signals_log rotation failed (non-fatal): {exc}")


def log_signal_scan(scored: list, regime: dict) -> None:
    """
    Append one line per scored symbol to signals_log.jsonl after each scan cycle.
    Each line records the full 9-dimension breakdown alongside regime context so
    forward returns can be correlated against individual dimension scores later.

    Expects the full universe from score_universe() (the all_scored return value),
    not the above-threshold subset — so the IC distribution is untruncated.
    """
    if not scored:
        return
    _rotate_signals_log()  # archive if file has grown past 50 MB
    scan_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    try:
        with open(SIGNALS_LOG_FILE, "a") as f:
            for sig in scored:
                _nd = sig.get("news") or {}
                record = {
                    "_schema_version": 1,
                    "ts": datetime.now(UTC).isoformat(),
                    "scan_id": scan_id,
                    "symbol": sig.get("symbol"),
                    "score": sig.get("score"),
                    "price": sig.get("price"),
                    "direction": sig.get("direction", "LONG"),
                    "regime": regime.get("session_character") or regime.get("regime"),
                    "vix": regime.get("vix"),
                    "score_breakdown": sig.get("score_breakdown", {}),
                    "disabled_dims": sig.get("disabled_dimensions", []),
                    "news_debug": {
                        "headline_count": _nd.get("headline_count", 0),
                        "recency_hours": _nd.get("recency_hours", 999),
                        "keyword_score": _nd.get("keyword_score", 0),
                        "keyword_hits": _nd.get("keyword_hits", []),
                        "claude_sentiment": _nd.get("claude_sentiment", ""),
                        "av_sentiment_label": _nd.get("av_sentiment_label", ""),
                    } if _nd else {},
                }
                f.write(json.dumps(record) + "\n")
    except Exception as e:
        log.warning(f"signals_log write failed: {e}")


def _compute_ic_weighted_score(signal_scores, ic_weights):
    """Weighted sum of signal_scores by ic_weights across all IC dimensions."""
    if not signal_scores or not ic_weights:
        return None
    try:
        from ic_calculator import DIMENSIONS, EQUAL_WEIGHTS

        return sum(float(signal_scores.get(d, 0.0)) * ic_weights.get(d, EQUAL_WEIGHTS[d]) for d in DIMENSIONS)
    except Exception:
        return None


def log_trade(trade: dict, agent_outputs: dict, regime: dict, action: str, outcome: dict | None = None):
    """
    Log every trade with full context for learning.
    action = "OPEN" or "CLOSE"
    """
    # Calculate hold time for CLOSE records
    hold_minutes = None
    if action == "CLOSE" and trade.get("open_time"):
        try:
            open_dt = _parse_ts(trade["open_time"])
            close_dt = datetime.now(UTC)
            hold_minutes = int((close_dt - open_dt).total_seconds() / 60)
        except Exception as _e:
            log.debug(
                f"hold_minutes unavailable for {trade.get('symbol')} (open_time={trade.get('open_time')!r}): {_e}"
            )

    _now_iso = datetime.now(UTC).isoformat()
    # entry_time = when position was opened (open_time on the active trade dict).
    # Falls back to current timestamp for OPEN records so the ML engine can parse it.
    _entry_time = trade.get("open_time") or trade.get("entry_time") or _now_iso
    # exit_time only meaningful on CLOSE records.
    _exit_time = _now_iso if action == "CLOSE" else None

    record = {
        "timestamp": _now_iso,
        "action": action,
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction", "LONG"),
        "qty": trade.get("qty"),
        "shares": trade.get("qty"),  # ML engine alias — TradeLabeler reads "shares"
        "entry_time": _entry_time,
        "exit_time": _exit_time,
        "entry_price": trade.get("entry"),
        "exit_price": outcome.get("exit_price") if outcome else None,
        "sl": trade.get("sl"),
        "tp": trade.get("tp"),
        "score": trade.get("score"),
        "entry_score": trade.get("entry_score") or trade.get("score"),
        "setup_type": trade.get("setup_type"),
        "reasoning": trade.get("reasoning"),
        "regime": regime.get("session_character") or regime.get("regime"),
        "vix": regime.get("vix"),
        "pnl": outcome.get("pnl") if outcome else None,
        "pnl_pct": outcome.get("pnl_pct") if outcome else None,
        "exit_reason": outcome.get("reason") if outcome else None,
        "hold_minutes": hold_minutes,
        "agents": {
            "technical": agent_outputs.get("technical", "")[:500],
            "macro": agent_outputs.get("macro", "")[:500],
            "opportunity": agent_outputs.get("opportunity", "")[:500],
            "devils": agent_outputs.get("devils", "")[:500],
            "risk": agent_outputs.get("risk", "")[:500],
        },
        "signal_scores": trade.get("signal_scores", {}),
        "score_breakdown": trade.get("signal_scores", {}),  # IC learning loop alias
        "ic_weights_at_entry": trade.get("ic_weights_at_entry"),
        "ic_weighted_score": _compute_ic_weighted_score(trade.get("signal_scores"), trade.get("ic_weights_at_entry")),
        "candle_gate": trade.get("candle_gate", "UNKNOWN"),
        # Sanitise to JSON-safe types — orderId can be a MagicMock in test environments
        "tranche_id": trade.get("tranche_id") if isinstance(trade.get("tranche_id"), (int, type(None))) else None,
        "parent_trade_id": trade.get("parent_trade_id")
        if isinstance(trade.get("parent_trade_id"), (int, str, type(None)))
        else None,
        "pattern_id": trade.get("pattern_id"),
        "advice_id": trade.get("advice_id", ""),
        "trade_type": trade.get("trade_type"),
        "conviction": trade.get("conviction"),
        "entry_thesis": trade.get("entry_thesis"),
        "entry_context": trade.get("entry_context"),  # TradeContext snapshot for post-trade IC analysis
    }

    # ── Options metadata — store if present so dashboard can display correctly ──
    if trade.get("instrument") == "option":
        record["instrument"] = "option"
        record["right"] = trade.get("right", "")
        record["strike"] = trade.get("strike", 0)
        record["expiry"] = trade.get("expiry_str") or trade.get("expiry", "")

    # Load existing log
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE) as f:
                trades = json.load(f)
        except Exception:
            trades = []

    # Deduplication — check if this exact trade already exists
    # For CLOSE records: any existing CLOSE for the same symbol on the same day is a dupe
    # (a position can only be closed once per trade)
    # For OPEN records: match on symbol + action within 5 minutes
    ts_new = _parse_ts(record["timestamp"])
    for existing in trades:
        if existing.get("symbol") != record.get("symbol"):
            continue
        if existing.get("action") != record.get("action"):
            continue
        if record.get("action") == "CLOSE":
            # Any prior CLOSE for this symbol within the same trading day is a dupe.
            # Partial fills of the same sell order can arrive in rapid succession —
            # always keep whichever record has the largest qty (most complete fill).
            try:
                ts_ex = _parse_ts(existing["timestamp"])
                if abs((ts_new - ts_ex).total_seconds()) < 86400:  # within 24 hours
                    # Pattern_id guard: different pattern_ids = different trade cycles, never dupes.
                    # A symbol can be traded, closed, and reopened within 24 hours — each has a
                    # distinct pattern_id. Only bypass this guard when one record lacks a pattern_id
                    # (older data that predates pattern tracking).
                    if (
                        record.get("pattern_id")
                        and existing.get("pattern_id")
                        and record["pattern_id"] != existing["pattern_id"]
                    ):
                        continue
                    # Tranche guard: T1 close and T2 close are distinct — never treat as dupes
                    if (
                        record.get("tranche_id") is not None
                        and existing.get("tranche_id") is not None
                        and record["tranche_id"] != existing["tranche_id"]
                    ):
                        continue
                    existing_qty = existing.get("qty") or existing.get("shares") or 0
                    new_qty = record.get("qty") or record.get("shares") or 0
                    # Prefer the record with better (non-zero) P&L or higher qty
                    should_update = (
                        record.get("pnl")
                        and record["pnl"] != 0.0
                        and (not existing.get("pnl") or existing.get("pnl") == 0.0)
                    ) or (new_qty > existing_qty and record.get("pnl") is not None)
                    if should_update:
                        existing.update({k: v for k, v in record.items() if v is not None})
                        _save_trades(trades)
                        log.info(
                            f"Updated existing CLOSE with better data: {record['symbol']} qty={new_qty} P&L=${record.get('pnl')}"
                        )
                    else:
                        log.info(f"Duplicate CLOSE skipped: {record['symbol']}")
                    return
            except Exception:
                pass
        else:
            # OPEN: match within 30 minutes — covers slow/partial fills of the same order
            try:
                ts_ex = _parse_ts(existing["timestamp"])
                if abs((ts_new - ts_ex).total_seconds()) < 1800:
                    # Tranche guard: T1 open and T2 open are distinct — never treat as dupes
                    if (
                        record.get("tranche_id") is not None
                        and existing.get("tranche_id") is not None
                        and record["tranche_id"] != existing["tranche_id"]
                    ):
                        continue
                    log.info(f"Duplicate OPEN skipped: {record['symbol']}")
                    return
            except Exception:
                pass

    trades.append(record)
    _save_trades(trades)
    log.info(f"Trade logged: {action} {trade.get('symbol')} | P&L: {outcome.get('pnl') if outcome else 'open'}")

    # ── BC-3: Execution IC stream ───────────────────────────────────────────────
    # Signal IC (log_signal_scan) measures distribution of ALL scored signals before
    # any filter. That tells us which signal dimensions predict price moves — but it
    # cannot answer whether the agents add or subtract alpha vs the raw signal.
    # Signals rejected by Risk Manager, direction-mismatched in Agent 4, or held by PM
    # leave no trace in the audit. This second stream records what actually executed.
    # The IC calculator can then compute: signal IC vs execution IC = agent alpha delta.
    if action == "OPEN":
        _exec_ic_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "execution_ic.jsonl")
        _exec_ic_record = {
            "timestamp": record["timestamp"],
            "symbol": record["symbol"],
            "direction": record.get("direction", "LONG"),
            "instrument": trade.get("instrument", "stock"),
            "score": record.get("score"),
            "score_breakdown": record.get("score_breakdown", {}),
            "regime": record.get("regime"),
            "entry_price": record.get("entry_price"),
            # agent_votes — present when agent_outputs contains structured votes
            "agent_technical": bool(agent_outputs.get("technical")),
            "agent_risk": bool(agent_outputs.get("risk")),
            "exit_reason": outcome.get("reason") if outcome else None,
        }
        try:
            with _exec_ic_lock:
                with open(_exec_ic_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(_exec_ic_record) + "\n")
                    _f.flush()
                    os.fsync(_f.fileno())
        except Exception as _e:
            log.debug(f"execution_ic.jsonl write failed (non-critical): {_e}")

    elif action == "CLOSE" and outcome:
        # Record the exit so the IC calculator can pair entry vs outcome.
        # Without this, every execution_ic entry has exit_reason=null and no pnl —
        # making it impossible to compute Information Coefficient.
        _exec_ic_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "execution_ic.jsonl")
        _exec_ic_close = {
            "action": "CLOSE",
            "timestamp": record["timestamp"],
            "symbol": record["symbol"],
            "direction": record.get("direction", "LONG"),
            "instrument": trade.get("instrument", "stock"),
            "score": record.get("score"),
            "exit_price": record.get("exit_price"),
            "exit_reason": record.get("exit_reason"),
            "pnl": record.get("pnl"),
            "pnl_pct": record.get("pnl_pct"),
            "hold_minutes": hold_minutes,
        }
        try:
            with _exec_ic_lock:
                with open(_exec_ic_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(_exec_ic_close) + "\n")
                    _f.flush()
                    os.fsync(_f.fileno())
        except Exception as _e:
            log.debug(f"execution_ic.jsonl close write failed (non-critical): {_e}")

    # (trade_advisor learning loop removed — deterministic sizing owns stops)

    # ── Close pattern library loop — record outcome against market observation ──
    if action == "CLOSE" and trade.get("pattern_id") and outcome:
        try:
            from pattern_library import record_outcome as _pl_record_outcome

            _pl_record_outcome(
                pattern_id=trade["pattern_id"],
                pnl=outcome.get("pnl", 0.0),
                pnl_pct=outcome.get("pnl_pct", 0.0),
                exit_reason=outcome.get("reason", ""),
            )
        except Exception as _e:
            log.debug(f"pattern_library record_outcome failed (non-critical): {_e}")


def _save_trades(trades: list) -> None:
    """Write trades list to disk atomically (tempfile + rename) so a crash never corrupts the file.
    Falls back to a direct write if the directory does not support temp files (e.g. /dev/null in tests).
    """
    import tempfile

    target = TRADE_LOG_FILE
    dir_ = os.path.dirname(os.path.abspath(target)) or "."
    try:
        os.makedirs(dir_, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(trades, f, indent=2)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        # Fallback: direct write (e.g. target is /dev/null in test environments)
        with open(target, "w") as f:
            json.dump(trades, f, indent=2)
    # Keep dash["all_trades"] in sync so the dashboard homepage reflects new trades
    # immediately — without this, only the PM-review close path updated memory.
    try:
        import bot_state as _bs
        _bs.dash["all_trades"] = trades
    except Exception:
        pass


def load_trades() -> list:
    """Load all trade records."""
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    try:
        with open(TRADE_LOG_FILE) as f:
            return json.load(f)
    except Exception as exc:
        log.error(f"load_trades: failed to parse {TRADE_LOG_FILE} — {exc}. Returning empty list.")
        return []


def get_performance_summary(trades: list | None = None) -> dict:
    """Calculate performance metrics from trade history."""
    if trades is None:
        trades = load_trades()

    closed = [
        t
        for t in trades
        if t.get("exit_price") is not None and t.get("pnl") is not None and t.get("exit_reason") != "manual"
    ]

    if not closed:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "total_pnl": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "profit_factor": 0,
            "expectancy": 0,
        }

    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]

    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    total = sum(t["pnl"] for t in closed)
    win_rate = len(wins) / len(closed) if closed else 0

    gross_profit = sum(t["pnl"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    return {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_pnl": round(total, 2),
        "best_trade": round(max((t["pnl"] for t in closed), default=0), 2),
        "worst_trade": round(min((t["pnl"] for t in closed), default=0), 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
    }


def get_directional_skew(window_hours: int = 48, regime: str | None = None) -> dict:
    """
    Calculate directional skew over a rolling window (roadmap #07).

    Returns:
        {
            "skew":        float,   # -1.0 (all short) to +1.0 (all long)
            "long_count":  int,
            "short_count": int,
            "total":       int,
            "regime_aligned": bool | None,  # GREEN/RED indicator
            "alert":       str | None,      # Warning message if misaligned
        }
    """
    trades = load_trades()
    cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()

    recent = [t for t in trades if t.get("timestamp", "") >= cutoff and t.get("action") == "OPEN"]

    long_count = sum(1 for t in recent if t.get("direction", "LONG") == "LONG")
    short_count = sum(1 for t in recent if t.get("direction") == "SHORT")
    total = long_count + short_count

    if total == 0:
        return {
            "skew": 0.0,
            "long_count": 0,
            "short_count": 0,
            "total": 0,
            "regime_aligned": None,
            "alert": None,
        }

    skew = (long_count - short_count) / total  # -1.0 to +1.0

    # Regime alignment check
    regime_aligned = None
    alert = None
    if regime:
        if regime in ("CHOPPY", "BEAR_TRENDING") and skew > 0.8:
            regime_aligned = False
            alert = (
                f"Directional skew {skew:+.2f} (heavy LONG) in {regime} regime. "
                f"Short scanner may not be surfacing enough candidates."
            )
        elif regime == "BULL_TRENDING" and skew < -0.8:
            regime_aligned = False
            alert = (
                f"Directional skew {skew:+.2f} (heavy SHORT) in {regime} regime. "
                f"Unusual bearish positioning in a bull market."
            )
        elif (regime == "BULL_TRENDING" and skew > 0.5) or (regime == "BEAR_TRENDING" and skew < -0.5):
            regime_aligned = True
        else:
            regime_aligned = True  # Neutral / no concern

    return {
        "skew": round(skew, 3),
        "long_count": long_count,
        "short_count": short_count,
        "total": total,
        "regime_aligned": regime_aligned,
        "alert": alert,
    }


def get_directional_skew_multi() -> dict:
    """
    Return skew across multiple time windows for dashboard display.
    """
    return {
        "48h": get_directional_skew(window_hours=48),
        "7d": get_directional_skew(window_hours=168),
    }


def run_weekly_review() -> str:
    """
    Run the weekly review agent — analyse all trades from last 7 days.
    Returns written review report.
    """
    trades = load_trades()
    if not trades:
        return "No trades to review yet."

    # Last 7 days
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    recent = [t for t in trades if t["timestamp"] >= cutoff]

    if not recent:
        return "No trades in the last 7 days."

    # RB-9: Filter to trades with a complete outcome before computing metrics.
    # Trades closed < 1 trading day ago may not have forward_return calculated yet
    # (the IC calculator runs on a delay). Including them in win-rate and avg-win/loss
    # analysis biases the weekly summary toward incomplete data.
    # Trades pending IC analysis are counted separately for transparency.
    closed = [t for t in recent if t.get("exit_price") is not None]
    complete = [t for t in closed if t.get("forward_return") is not None]
    pending_ic_count = len(closed) - len(complete)
    # Use complete trades for performance metrics; fall back to all closed if no
    # forward returns have been computed yet (early-stage system with few closed trades).
    _for_metrics = complete if complete else closed
    perf = get_performance_summary(_for_metrics)

    trade_details = "\n".join(
        [
            f"- {t['timestamp'][:10]} | {t['symbol']} {t.get('direction', '?')} | "
            f"P&L ${(t.get('pnl') or 0.0):+.2f} | Regime: {t.get('regime', 'N/A')} | "
            f"Reasoning: {t.get('reasoning', 'N/A')[:100]}"
            for t in closed[:30]
        ]
    )

    client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])

    _pending_note = (
        f"\nNote: {pending_ic_count} additional closed trade(s) are excluded — IC forward return not yet calculated.\n"
        if pending_ic_count > 0 else ""
    )

    prompt = f"""You are the Weekly Review Agent for Decifer, an autonomous trading system.
Analyse the past week's trading performance and provide actionable insights.

PERFORMANCE SUMMARY (based on {len(_for_metrics)} trades with complete IC data):
{_pending_note}Total trades: {perf["total_trades"]}
Win rate: {perf["win_rate"]}%
Average win: ${perf["avg_win"]}
Average loss: ${perf["avg_loss"]}
Total P&L: ${perf["total_pnl"]}
Profit factor: {perf["profit_factor"]}
Expectancy per trade: ${perf["expectancy"]}

INDIVIDUAL TRADES:
{trade_details}

Please provide:
1. OVERALL ASSESSMENT: Was this a good week? Why?
2. WHAT WORKED: Which setups, regimes, or asset classes performed best?
3. WHAT FAILED: Which setups, regimes, or asset classes underperformed?
4. AGENT QUALITY: Based on trade reasoning, which agent appears most/least accurate?
5. PATTERN RECOGNITION: Any recurring mistakes or missed opportunities?
6. PROMPT RECOMMENDATIONS: Specific suggestions to improve any agent's prompt next week
7. RISK ASSESSMENT: Was position sizing appropriate? Any near-misses on risk limits?
8. NEXT WEEK FOCUS: 2-3 specific things to watch for or improve

Be direct and specific. This report guides real improvements to the system."""

    try:
        resp = client.messages.create(
            model=CONFIG["claude_model"], max_tokens=1500, messages=[{"role": "user", "content": prompt}]
        )
        review = resp.content[0].text.strip()
        log.info("Weekly review completed")

        # Save review to file so agents can read it next scan
        review_file = "weekly_review.txt"
        header = f"=== WEEKLY REVIEW — {datetime.now().strftime('%Y-%m-%d')} ===\n\n"
        with open(review_file, "w") as f:
            f.write(header + review)
        log.info(f"Weekly review saved to {review_file}")

        return review
    except Exception as e:
        log.error(f"Weekly review error: {e}")
        return f"Weekly review failed: {e}"
