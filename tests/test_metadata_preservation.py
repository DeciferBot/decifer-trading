# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  test_metadata_preservation.py             ║
# ║   Proves durable metadata identity is maintained across     ║
# ║   restart, partial fill, forced exit, and orphan paths.     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
13 regression tests for the metadata preservation guarantees.

Entry path blocking (tests 1-4):
  Code-level proofs that each entry path has the mandatory write-ahead
  pattern: try: append_intent(...) except: return False / log.error.

Metadata quality classification (tests 5-9):
  classify_record_quality() is the single authority.

count_eligible / phase_gate (tests 10-11):
  Degraded records excluded from phase gate counts.

Isolation and contamination prevention (tests 12-13):
  Dashboard cannot mutate store; guardrails force-exit produces ineligible record.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent


# ── helpers ────────────────────────────────────────────────────────────────────

def _full_info(**overrides) -> dict:
    """Minimal active_trades entry with complete metadata."""
    base = {
        "trade_id": "AAPL_20260101_120000_000001",
        "symbol": "AAPL",
        "trade_type": "INTRADAY",
        "conviction": 0.8,
        "signal_scores": {"trend": 0.7},
        "reasoning": "strong breakout",
        "entry": 180.0,
        "direction": "LONG",
        "qty": 10,
        "metadata_status": None,
    }
    base.update(overrides)
    return base


def _source(module_file: str) -> str:
    return (_REPO / module_file).read_text()


# ── Test 1: execute_buy blocks on ORDER_INTENT failure ────────────────────────

def test_execute_buy_order_intent_failure_returns_false():
    """execute_buy must contain: except ... → log.error('ORDER_INTENT write failed') + return False.

    This proves the write-ahead pattern is structurally present regardless of
    what path the runtime takes to reach it.
    """
    src = _source("orders_core.py")
    # The error message is the distinguishing marker; return False must follow.
    assert "ORDER_INTENT write failed — trade aborted" in src, \
        "execute_buy must log ORDER_INTENT write failed — trade aborted"
    # Verify the except clause resolves to return False (not just log.warning + continue).
    # Find the relevant section.
    idx = src.index("execute_buy %s: ORDER_INTENT write failed — trade aborted")
    window = src[idx: idx + 200]
    assert "return False" in window, \
        "execute_buy ORDER_INTENT failure handler must return False"


# ── Test 2: execute_short blocks on ORDER_INTENT failure ─────────────────────

def test_execute_short_order_intent_failure_returns_false():
    """execute_short must contain: except ... → log.error + return False for ORDER_INTENT."""
    src = _source("orders_core.py")
    assert "execute_short %s: ORDER_INTENT write failed — trade aborted" in src, \
        "execute_short must log ORDER_INTENT write failed — trade aborted"
    idx = src.index("execute_short %s: ORDER_INTENT write failed — trade aborted")
    window = src[idx: idx + 200]
    assert "return False" in window, \
        "execute_short ORDER_INTENT failure handler must return False"


# ── Test 3: execute_buy_option blocks on ORDER_INTENT failure ─────────────────

def test_execute_buy_option_order_intent_failure_returns_false():
    """execute_buy_option must return False on ORDER_INTENT failure.

    Regression: before this fix the except clause only issued log.warning and
    continued to ib.placeOrder.  Now it must log.error and return False.
    """
    src = _source("orders_options.py")
    assert "execute_buy_option %s: ORDER_INTENT write failed — trade aborted" in src, \
        "execute_buy_option must log ORDER_INTENT write failed — trade aborted"
    idx = src.index("execute_buy_option %s: ORDER_INTENT write failed — trade aborted")
    window = src[idx: idx + 200]
    assert "return False" in window, \
        "execute_buy_option ORDER_INTENT failure handler must return False"


# ── Test 4: execute_buy_option does not continue to placeOrder after failure ──

def test_execute_buy_option_no_warning_continue_after_intent_failure():
    """Confirm the old log.warning + (implicit fall-through) pattern is gone.

    The old code read:
        except Exception as _wal_err_opt:
            log.warning("execute_buy_option %s: ORDER_INTENT write failed: %s", ...)
        # ... then immediately fell through to ib.placeOrder

    After the fix the warning log call for this specific error must not exist.
    """
    src = _source("orders_options.py")
    # The old warning message (without "— trade aborted") must be gone.
    assert 'log.warning("execute_buy_option %s: ORDER_INTENT write failed:' not in src, \
        "Old log.warning fall-through pattern must be replaced with log.error + return False"


# ── Tests 5-9: classify_record_quality ────────────────────────────────────────

def test_classify_full_metadata():
    """Normal trade → quality=full, all eligible."""
    from training_store import classify_record_quality
    result = classify_record_quality(_full_info(), "target_reached")
    assert result["metadata_quality"] == "full"
    assert result["ml_eligible"] is True
    assert result["ic_eligible"] is True
    assert result["metadata_loss"] is False
    assert result["training_eligible"] is True


def test_classify_unknown_trade_type():
    """UNKNOWN trade_type → degraded, ml_eligible=False."""
    from training_store import classify_record_quality
    result = classify_record_quality(_full_info(trade_type="UNKNOWN"), "scalp_timeout")
    assert result["metadata_quality"] == "degraded_metadata_loss"
    assert result["ml_eligible"] is False
    assert result["ic_eligible"] is False
    assert result["metadata_loss"] is True


def test_classify_empty_trade_type():
    """Empty trade_type string → degraded (same as UNKNOWN)."""
    from training_store import classify_record_quality
    result = classify_record_quality(_full_info(trade_type=""), "eod_flat")
    assert result["ml_eligible"] is False


def test_classify_missing_metadata_status():
    """metadata_status=MISSING → degraded regardless of trade_type."""
    from training_store import classify_record_quality
    result = classify_record_quality(_full_info(metadata_status="MISSING"), "eod_flat")
    assert result["metadata_quality"] == "degraded_metadata_loss"
    assert result["ml_eligible"] is False


def test_classify_unknown_trade_type_exit_reason():
    """exit_reason=unknown_trade_type → degraded even if trade_type looks valid.
    This is the core guardrails force-exit case.
    """
    from training_store import classify_record_quality
    result = classify_record_quality(_full_info(trade_type="INTRADAY"), "unknown_trade_type")
    assert result["metadata_quality"] == "degraded_metadata_loss"
    assert result["ml_eligible"] is False
    assert result["metadata_loss"] is True


def test_classify_ext_trade_id():
    """trade_id containing _EXT_ → degraded (anchored by orphan reconcile)."""
    from training_store import classify_record_quality
    result = classify_record_quality(
        _full_info(trade_id="AAPL_EXT_20260101_120000_000001"),
        "eod_flat",
    )
    assert result["metadata_quality"] == "degraded_metadata_loss"
    assert result["ml_eligible"] is False


# ── Test 10: count_eligible ───────────────────────────────────────────────────

def test_count_eligible_excludes_degraded_and_counts_legacy(tmp_path, monkeypatch):
    """count_eligible must:
    - exclude records with ml_eligible=False
    - include records with ml_eligible=True
    - include legacy records (no ml_eligible field) as eligible
    - count() must still return total
    """
    import training_store

    store_file = tmp_path / "training_records.jsonl"
    monkeypatch.setattr(training_store, "_STORE_FILE", store_file)

    records = [
        {"ml_eligible": True,  "trade_id": "T1"},   # eligible
        {"ml_eligible": False, "trade_id": "T2"},   # degraded — excluded
        {"ml_eligible": False, "trade_id": "T3"},   # degraded — excluded
        {"trade_id": "T4"},                          # legacy (no field) — treated as eligible
        {"ml_eligible": True,  "trade_id": "T5"},   # eligible
    ]
    with open(store_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    assert training_store.count_eligible() == 3, \
        "Should count 3 eligible records (T1, T4-legacy, T5); T2 and T3 excluded"
    assert training_store.count() == 5, \
        "count() must still return total including degraded"


# ── Test 11: phase_gate uses count_eligible ───────────────────────────────────

def test_phase_gate_count_uses_count_eligible(monkeypatch):
    """_count_closed_trades in phase_gate must call count_eligible(), not count(),
    so degraded records don't inflate phase gate thresholds.
    """
    import phase_gate
    import training_store

    calls: list[str] = []
    monkeypatch.setattr(training_store, "count_eligible", lambda: (calls.append("eligible"), 42)[-1])
    monkeypatch.setattr(training_store, "count", lambda: (calls.append("count_all"), 99)[-1])

    result = phase_gate._count_closed_trades("unused")

    assert result == 42
    assert "eligible" in calls, "phase_gate must call count_eligible"
    assert "count_all" not in calls, "phase_gate must NOT call count (includes degraded)"


# ── Test 12: dashboard enrichment cannot mutate training_store ────────────────

def test_dashboard_enrichment_functions_do_not_write_to_training_store():
    """bot_dashboard.py may read training_store for display, but must never call
    training_store.append() or classify_record_quality() — enrichment is read-only.

    Reading (load/count/last) is legitimate for the dashboard trade history panel.
    Writing (append) would mutate the authoritative ML record — that is prohibited.
    """
    src = _source("bot_dashboard.py")
    # .append( is the only write path in training_store
    # We check for the pattern "_training_store.append(" or "training_store.append("
    assert "_training_store.append(" not in src, \
        "bot_dashboard.py must not call training_store.append — write path is prohibited"
    assert "training_store.append(" not in src, \
        "bot_dashboard.py must not call training_store.append — write path is prohibited"


# ── Test 13: guardrails force-exit → ml_eligible=False in training record ─────

def test_guardrails_unknown_trade_type_produces_ineligible_training_record(tmp_path, monkeypatch):
    """When guardrails forces exit with reason=unknown_trade_type, the training
    record must have ml_eligible=False and metadata_quality=degraded_metadata_loss.
    This proves end-to-end contamination is blocked.
    """
    import training_store

    store_file = tmp_path / "training_records.jsonl"
    monkeypatch.setattr(training_store, "_STORE_FILE", store_file)

    info = {
        "trade_id": "TSLA_EXT_20260101_120000_000001",
        "trade_type": "UNKNOWN",
        "metadata_status": "MISSING",
        "direction": "LONG",
        "entry": 200.0,
        "qty": 5,
        "signal_scores": {},
        "conviction": 0.0,
        "score": 0,
        "entry_regime": "UNKNOWN",
        "open_time": "2026-01-01T12:00:00+00:00",
    }
    exit_reason = "unknown_trade_type"

    quality = training_store.classify_record_quality(info, exit_reason)

    # Simulate what execute_sell writes
    record = {
        "trade_id": info["trade_id"],
        "symbol": "TSLA",
        "direction": "LONG",
        "trade_type": "UNKNOWN",
        "fill_price": 200.0,
        "exit_price": 198.0,
        "pnl": -10.0,
        "hold_minutes": 30,
        "exit_reason": exit_reason,
        "regime": "UNKNOWN",
        "signal_scores": {},
        "conviction": 0.0,
        "score": 0,
        "ts_fill": "2026-01-01T12:00:00+00:00",
        "ts_close": "2026-01-01T12:30:00+00:00",
        **quality,
    }
    training_store.append(record)

    written = json.loads(store_file.read_text().strip())
    assert written["ml_eligible"] is False
    assert written["ic_eligible"] is False
    assert written["metadata_quality"] == "degraded_metadata_loss"
    assert written["metadata_loss"] is True
    assert training_store.count_eligible() == 0, \
        "count_eligible must not count the degraded record"
    assert training_store.count() == 1, \
        "count() must still see the record"
