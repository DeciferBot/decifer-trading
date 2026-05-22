"""
tests/test_pm_outcome_tracker.py — Unit tests for PME outcome tracking.

All tests use synthetic decision records and injected price paths.
No Alpaca API calls, no filesystem side-effects beyond tmp_path.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys
import types

import pytest

UTC = datetime.timezone.utc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso(minutes_ago: float = 0.0) -> str:
    t = datetime.datetime.now(UTC) - datetime.timedelta(minutes=minutes_ago)
    return t.isoformat()


def _decision(
    symbol: str = "TSLA",
    action_type: str = "FULL_EXIT",
    final_status: str = "EXECUTED",
    ts: str | None = None,
    current_price: float = 200.0,
    position_pct_nlv: float = 0.055,
    score_delta: float = -25.0,
    thesis_status: str = "THESIS_DECAYING",
    score_source: str = "CYCLE_CANDIDATES",
    data_quality: str = "OK",
    safety_block_reason: str | None = None,
) -> dict:
    return {
        "ts":                  ts or _now_iso(120),  # 2 hours ago
        "symbol":              symbol,
        "action_type":         action_type,
        "final_status":        final_status,
        "thesis_status":       thesis_status,
        "score_delta":         score_delta,
        "score_source":        score_source,
        "data_quality":        data_quality,
        "current_price":       current_price,
        "entry_price":         current_price * 0.95,
        "position_pct_nlv":    position_pct_nlv,
        "unrealised_pnl_pct":  0.05,
        "proposed_notional":   50_000.0,
        "candidate_symbol":    None,
        "safety_block_reason": safety_block_reason,
        "market_regime":       "BULL_TRENDING",
    }


def _write_decisions(path: pathlib.Path, records: list[dict]) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_outcomes(path: pathlib.Path, records: list[dict]) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _patch_fetch(monkeypatch, return_value: float | None):
    """Inject a fixed price for every _fetch_price call."""
    import pm_outcome_tracker
    monkeypatch.setattr(pm_outcome_tracker, "_fetch_price",
                        lambda *a, **kw: return_value)


# ── decision_id ───────────────────────────────────────────────────────────────

def test_decision_id_is_deterministic():
    import pm_outcome_tracker
    d = _decision(symbol="AAPL", ts="2026-05-22T17:55:31.100+00:00")
    assert pm_outcome_tracker.decision_id(d) == "AAPL_20260522_175531"


def test_decision_id_no_colons_or_dots():
    import pm_outcome_tracker
    d = _decision(ts="2026-05-22T09:01:59.999+00:00")
    did = pm_outcome_tracker.decision_id(d)
    assert ":" not in did
    assert "." not in did


# ── _elapsed ──────────────────────────────────────────────────────────────────

def test_elapsed_past_returns_true():
    import pm_outcome_tracker
    ts = _now_iso(60)   # 60 min ago
    now = datetime.datetime.now(UTC)
    assert pm_outcome_tracker._elapsed(ts, 30, now) is True


def test_elapsed_future_returns_false():
    import pm_outcome_tracker
    ts = _now_iso(10)   # 10 min ago
    now = datetime.datetime.now(UTC)
    assert pm_outcome_tracker._elapsed(ts, 30, now) is False


def test_elapsed_bad_ts_returns_false():
    import pm_outcome_tracker
    now = datetime.datetime.now(UTC)
    assert pm_outcome_tracker._elapsed("not-a-timestamp", 1, now) is False


# ── _classify ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ret,expect_quality,expect_label", [
    (-0.03, "GOOD",    "caught_decline"),
    (+0.03, "BAD",     "cut_winner_early"),
    (+0.00, "NEUTRAL", "neutral_exit"),
    (-0.01, "NEUTRAL", "neutral_exit"),   # inside ±2% band
])
def test_classify_full_exit_executed(ret, expect_quality, expect_label):
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("FULL_EXIT", "EXECUTED", ret)
    assert q == expect_quality, f"Expected {expect_quality}, got {q}"
    assert lbl == expect_label


@pytest.mark.parametrize("ret,expect_quality,expect_label", [
    (-0.03, "BAD",     "rail_too_strict"),
    (+0.03, "GOOD",    "rail_correct"),
    (+0.00, "NEUTRAL", "rail_neutral"),
])
def test_classify_exit_safety_blocked(ret, expect_quality, expect_label):
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("FULL_EXIT", "SAFETY_BLOCKED", ret)
    assert q == expect_quality


@pytest.mark.parametrize("action", ["HOLD", "DO_NOTHING", "MONITORING"])
def test_classify_hold_types(action):
    import pm_outcome_tracker
    # Price rose: justified hold
    q, lbl = pm_outcome_tracker._classify(action, "HOLDING", +0.03)
    assert q == "GOOD" and lbl == "justified_hold"
    # Price fell: held too long
    q, lbl = pm_outcome_tracker._classify(action, "HOLDING", -0.03)
    assert q == "BAD"  and lbl == "held_too_long"
    # Flat: neutral
    q, lbl = pm_outcome_tracker._classify(action, "HOLDING", 0.0)
    assert q == "NEUTRAL"


def test_classify_dca_advisory():
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("DCA", "RECOMMENDATION", +0.04)
    assert q == "GOOD" and lbl == "dca_justified"
    q, lbl = pm_outcome_tracker._classify("DCA", "RECOMMENDATION", -0.04)
    assert q == "BAD"  and lbl == "dca_into_loss"


# ── _build_record ─────────────────────────────────────────────────────────────

def test_build_record_return_pct_computed():
    import pm_outcome_tracker
    d = _decision(current_price=200.0)
    rec = pm_outcome_tracker._build_record(d, "TEST_id", "30min", 200.0, 210.0)
    assert abs(rec["symbol_return_pct"] - 0.05) < 1e-6


def test_build_record_zero_return_when_no_decision_price():
    import pm_outcome_tracker
    d = _decision()
    rec = pm_outcome_tracker._build_record(d, "TEST_id", "30min", None, 210.0)
    assert rec["symbol_return_pct"] == 0.0


def test_build_record_counterfactual_scales_by_position_pct():
    import pm_outcome_tracker
    d = _decision(current_price=100.0, position_pct_nlv=0.05)
    rec = pm_outcome_tracker._build_record(d, "TEST_id", "1h", 100.0, 90.0)
    # return_pct = -0.10, position_pct_nlv = 0.05 → impact = -0.005
    assert abs(rec["counterfactual_nlv_impact"] - (-0.005)) < 1e-6


def test_build_record_required_fields_present():
    import pm_outcome_tracker
    d = _decision()
    rec = pm_outcome_tracker._build_record(d, "DID", "30min", 100.0, 105.0)
    for field in (
        "decision_id", "ts_decision", "ts_resolved", "symbol",
        "action_type", "final_status", "window",
        "price_at_decision", "price_at_outcome",
        "symbol_return_pct", "counterfactual_nlv_impact",
        "outcome_quality", "outcome_label",
    ):
        assert field in rec, f"Missing field: {field}"


# ── resolve_pending ───────────────────────────────────────────────────────────

def test_resolve_pending_writes_outcome_for_elapsed_window(tmp_path, monkeypatch):
    import pm_outcome_tracker

    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"

    _write_decisions(dec_path, [_decision(ts=_now_iso(120))])  # 2h ago — 30min elapsed
    _patch_fetch(monkeypatch, 210.0)

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path)
    assert written > 0
    assert out_path.exists()

    records = [json.loads(l) for l in out_path.read_text().splitlines()]
    assert all("outcome_quality" in r for r in records)
    assert all("window" in r for r in records)


def test_resolve_pending_skips_unelapsed_windows(tmp_path, monkeypatch):
    import pm_outcome_tracker

    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"

    # Decision 5 min ago — no window has elapsed
    _write_decisions(dec_path, [_decision(ts=_now_iso(5))])
    _patch_fetch(monkeypatch, 200.0)

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path)
    assert written == 0


def test_resolve_pending_skips_already_resolved(tmp_path, monkeypatch):
    import pm_outcome_tracker

    dec = _decision(symbol="NVDA", ts=_now_iso(120))
    did = pm_outcome_tracker.decision_id(dec)

    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"
    _write_decisions(dec_path, [dec])

    # Pre-populate outcomes with all 6 windows already resolved
    existing = [{"decision_id": did, "window": w} for w, *_ in pm_outcome_tracker._WINDOWS]
    _write_outcomes(out_path, existing)
    _patch_fetch(monkeypatch, 200.0)

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path)
    assert written == 0


def test_resolve_pending_respects_max_fetches(tmp_path, monkeypatch):
    import pm_outcome_tracker

    # 5 decisions, all 2h old → up to 30 windows eligible (6 each)
    decs = [_decision(symbol=f"S{i}", ts=_now_iso(120)) for i in range(5)]
    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"
    _write_decisions(dec_path, decs)
    _patch_fetch(monkeypatch, 200.0)

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path, max_fetches=3)
    assert written <= 3


def test_resolve_pending_skips_pm_skipped_records(tmp_path, monkeypatch):
    import pm_outcome_tracker

    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"
    # Only a PM_SKIPPED event — should produce zero outcomes
    _write_decisions(dec_path, [{"event": "PM_SKIPPED", "ts": _now_iso(120), "reason": "account_not_ready"}])
    _patch_fetch(monkeypatch, 200.0)

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path)
    assert written == 0


def test_resolve_pending_missing_price_yields_no_record(tmp_path, monkeypatch):
    import pm_outcome_tracker

    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"
    _write_decisions(dec_path, [_decision(ts=_now_iso(120))])
    _patch_fetch(monkeypatch, None)   # price unavailable

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path)
    assert written == 0


def test_resolve_pending_handles_empty_decisions_file(tmp_path, monkeypatch):
    import pm_outcome_tracker

    dec_path = tmp_path / "decisions.jsonl"
    out_path = tmp_path / "outcomes.jsonl"
    dec_path.write_text("")
    _patch_fetch(monkeypatch, 200.0)

    written = pm_outcome_tracker.resolve_pending(dec_path, out_path)
    assert written == 0


def test_resolve_pending_handles_missing_decisions_file(tmp_path, monkeypatch):
    import pm_outcome_tracker
    _patch_fetch(monkeypatch, 200.0)
    written = pm_outcome_tracker.resolve_pending(
        tmp_path / "missing.jsonl",
        tmp_path / "outcomes.jsonl",
    )
    assert written == 0


# ── get_summary ───────────────────────────────────────────────────────────────

def test_get_summary_counts_by_action_and_quality(tmp_path):
    import pm_outcome_tracker

    out_path = tmp_path / "outcomes.jsonl"
    records = [
        {"decision_id": "A_1", "window": "30min", "action_type": "FULL_EXIT", "outcome_quality": "GOOD"},
        {"decision_id": "A_2", "window": "30min", "action_type": "FULL_EXIT", "outcome_quality": "BAD"},
        {"decision_id": "A_3", "window": "30min", "action_type": "HOLD",      "outcome_quality": "GOOD"},
    ]
    _write_outcomes(out_path, records)

    summary = pm_outcome_tracker.get_summary(out_path)
    assert summary["total"] == 3
    assert summary["by_action"]["FULL_EXIT"]["GOOD"] == 1
    assert summary["by_action"]["FULL_EXIT"]["BAD"]  == 1
    assert summary["by_action"]["HOLD"]["GOOD"]      == 1
    assert summary["quality_counts"]["GOOD"] == 2
    assert summary["quality_counts"]["BAD"]  == 1


def test_get_summary_returns_empty_on_missing_file(tmp_path):
    import pm_outcome_tracker
    summary = pm_outcome_tracker.get_summary(tmp_path / "no_outcomes.jsonl")
    assert summary["total"] == 0
    assert summary["recent"] == []


def test_get_summary_recent_list_is_newest_first(tmp_path):
    import pm_outcome_tracker

    out_path = tmp_path / "outcomes.jsonl"
    records = [
        {"decision_id": f"D_{i}", "window": "30min",
         "action_type": "HOLD", "outcome_quality": "GOOD",
         "ts_decision": f"2026-05-22T0{i}:00:00+00:00"}
        for i in range(5)
    ]
    _write_outcomes(out_path, records)
    summary = pm_outcome_tracker.get_summary(out_path)
    # reversed() → last file record is first in "recent"
    assert summary["recent"][0]["ts_decision"] == records[-1]["ts_decision"]


# ── outcome quality for different final_status + action combos ─────────────────

def test_trim_executed_decline_is_good():
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("TRIM", "EXECUTED", -0.05)
    assert q == "GOOD" and lbl == "caught_decline"


def test_trim_executed_rise_is_bad():
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("TRIM", "EXECUTED", +0.05)
    assert q == "BAD" and lbl == "cut_winner_early"


def test_rotate_executed_decline_is_good():
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("ROTATE", "EXECUTED", -0.03)
    assert q == "GOOD"


def test_unknown_action_is_neutral():
    import pm_outcome_tracker
    q, lbl = pm_outcome_tracker._classify("UNKNOWN_ACTION", "EXECUTED", -0.10)
    assert q == "NEUTRAL" and lbl == "unknown"
