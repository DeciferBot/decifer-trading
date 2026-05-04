"""
Tests for tier_d_evidence_report.py crash fixes and origin audit logic.

Covers:
  - Report does not crash when funnel_shadow_compare / funnel_shadow_apex are absent
  - shadow / enriched / legacy default to empty lists safely
  - shadow_on / live_off default to Phase 1 safe values
  - Trade origin classification logic
  - execute_buy / execute_short accept **intent_extras
  - dispatch() passes origin extras to execute_buy
"""

import inspect
import io
import json
import os
import sys
import tempfile
import types
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

# ── Project root on path ──────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Minimal funnel record for a scan cycle ───────────────────────────────────
_PIPELINE_RECORD = {
    "stage": "pipeline",
    "ts": "2026-05-04T10:00:00+00:00",
    "pru_loaded": 150,
    "in_universe": 120,
    "scored_all": 100,
    "above_regime_threshold": 20,
    "passed_strategy_threshold": 10,
    "passed_persistence": 8,
    "rescue_pool": 5,
    "rescued": 3,
    "dropped_final": 2,
    "pipeline_output": 11,
    "drop_at_all_scored": 20,
    "drop_at_strategy_threshold": 10,
}
_APEX_CAP_RECORD = {
    "stage": "apex_cap",
    "ts": "2026-05-04T10:00:01+00:00",
    "cap_limit": 30,
    "raw_candidates_before_cap": 40,
    "selected_candidates_after_cap": 30,
    "dropped_by_cap_total": 10,
    "raw_tier_d_before_cap": 15,
    "selected_tier_d_after_cap": 10,
    "dropped_tier_d_by_cap": 5,
    "selected_tier_d_symbols": ["ALAB", "TSM"],
    "dropped_tier_d_symbols_top_20": ["IONQ"],
    "tier_d_with_archetypes_dropped": True,
    "tier_d_strong_discovery_dropped": True,
    "max_tier_d_score_before_cap": 80,
    "min_selected_score_after_cap": 50,
    "highest_dropped_tier_d_score": 65,
    "top_10_selected_by_score": [
        {"symbol": "ALAB", "score": 80, "scanner_tier": "D"},
        {"symbol": "TSM",  "score": 75, "scanner_tier": "D"},
    ],
    "top_10_dropped_tier_d": [
        {"symbol": "IONQ", "score": 65, "discovery_score": 12, "matched_archetypes": []},
    ],
}
_PRU_DATA = {
    "built_at": "2026-05-04T00:00:00+00:00",
    "count": 2,
    "symbols": [
        {
            "ticker": "ALAB",
            "discovery_score": 12,
            "universe_bucket": "core_research",
            "primary_archetype": "Quality Compounder",
            "adjusted_discovery_score": 10,
        },
        {
            "ticker": "LUNR",
            "discovery_score": 8,
            "universe_bucket": "tactical_momentum",
            "primary_archetype": "Speculative Theme",
            "adjusted_discovery_score": 6,
        },
    ],
}


def _write_jsonl(path: str, records: list[dict]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f)


def _run_main_with_data(
    funnel_records: list[dict],
    pru_data: dict | None = None,
    trade_events: list[dict] | None = None,
) -> tuple[str, int]:
    """Run tier_d_evidence_report.main() with temp files, return (stdout, exit_code)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        funnel_path = os.path.join(tmpdir, "tier_d_funnel.jsonl")
        pru_path    = os.path.join(tmpdir, "position_research_universe.json")
        te_path     = os.path.join(tmpdir, "trade_events.jsonl")
        tr_path     = os.path.join(tmpdir, "training_records.jsonl")

        _write_jsonl(funnel_path, funnel_records)
        _write_json(pru_path, pru_data or _PRU_DATA)
        _write_jsonl(te_path, trade_events or [])
        _write_jsonl(tr_path, [])

        # Patch the module-level path constants — import via file path (no __init__.py in scripts/)
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "tier_d_evidence_report",
            os.path.join(PROJECT_ROOT, "scripts", "tier_d_evidence_report.py"),
        )
        mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(mod)

        # Override path constants
        orig_funnel    = mod.FUNNEL_JSONL
        orig_pru       = mod.PRU_JSON
        orig_te        = mod.TRADE_EVENTS
        orig_training  = mod.TRAINING
        mod.FUNNEL_JSONL = funnel_path
        mod.PRU_JSON     = pru_path
        mod.TRADE_EVENTS = te_path
        mod.TRAINING     = tr_path

        buf = io.StringIO()
        exit_code = 0
        try:
            with redirect_stdout(buf):
                mod.main()
        except SystemExit as e:
            exit_code = e.code or 0
        except Exception as exc:
            exit_code = 1
            buf.write(f"\nUNEXPECTED ERROR: {exc}")
        finally:
            mod.FUNNEL_JSONL = orig_funnel
            mod.PRU_JSON     = orig_pru
            mod.TRADE_EVENTS = orig_te
            mod.TRAINING     = orig_training

        return buf.getvalue(), exit_code


# ─────────────────────────────────────────────────────────────────────────────
# Tests: report does not crash on missing Phase 2 data
# ─────────────────────────────────────────────────────────────────────────────

class TestReportNoCrash:
    def test_no_crash_missing_shadow_compare(self):
        """Funnel has no apex_cap_shadow_compare records — Section 0c must not NameError."""
        out, code = _run_main_with_data([_PIPELINE_RECORD, _APEX_CAP_RECORD])
        assert "NameError" not in out, f"NameError in output: {out[:500]}"
        assert code == 0, f"Exit code={code}. Output:\n{out[:500]}"
        assert "No apex_cap_shadow_compare records yet" in out

    def test_no_crash_missing_shadow_apex(self):
        """Funnel has no tier_d_shadow_apex records — Section 0d must not NameError."""
        out, code = _run_main_with_data([_PIPELINE_RECORD, _APEX_CAP_RECORD])
        assert "NameError" not in out
        assert code == 0
        assert "No tier_d_shadow_apex records yet" in out

    def test_empty_shadow_defaults(self):
        """shadow / enriched / legacy default to empty — Sections 1-6 must not NameError."""
        out, code = _run_main_with_data([_PIPELINE_RECORD, _APEX_CAP_RECORD])
        assert "NameError" not in out
        assert code == 0
        # Section 1 should show 0 for shadow records
        assert "Total shadow records (all time):     0" in out

    def test_config_defaults_phase1_safe(self):
        """shadow_on=True, live_off=True appear in Phase 2 gate output."""
        out, code = _run_main_with_data([_PIPELINE_RECORD, _APEX_CAP_RECORD])
        assert code == 0
        assert "shadow_mode=True confirmed" in out
        assert "allow_live_position_entries=False confirmed" in out


# ─────────────────────────────────────────────────────────────────────────────
# Tests: trade origin classification
# ─────────────────────────────────────────────────────────────────────────────

class TestOriginClassification:
    """Test _classify_origin logic by running the report and checking audit output."""

    def test_unknown_origin_no_scanner_tier(self):
        """ORDER_INTENT for a PRU symbol with no scanner_tier → unknown_origin_needs_investigation."""
        trade_events = [
            {
                "event": "ORDER_INTENT",
                "symbol": "ALAB",
                "trade_type": "SWING",
                "ts": "2026-05-04T10:00:00+00:00",
                # No scanner_tier field
            }
        ]
        out, code = _run_main_with_data(
            [_PIPELINE_RECORD, _APEX_CAP_RECORD], trade_events=trade_events
        )
        assert code == 0
        assert "unknown_origin_needs_investigation: 1" in out
        assert "UNRESOLVED" in out

    def test_tier_d_paper_entry_classification(self):
        """ORDER_INTENT with tier_d_paper_entry=True → tier_d_paper_entry classification."""
        trade_events = [
            {
                "event": "ORDER_INTENT",
                "symbol": "ALAB",
                "trade_type": "POSITION",
                "ts": "2026-05-04T10:00:00+00:00",
                "scanner_tier": "D",
                "tier_d_paper_entry": True,
            }
        ]
        out, code = _run_main_with_data(
            [_PIPELINE_RECORD, _APEX_CAP_RECORD], trade_events=trade_events
        )
        assert code == 0
        assert "tier_d_paper_entry: 1" in out
        assert "tier_d_unexpected_execution: 0" in out

    def test_unexpected_execution_detected(self):
        """ORDER_INTENT with scanner_tier=D but no tier_d_paper_entry → unexpected execution."""
        trade_events = [
            {
                "event": "ORDER_INTENT",
                "symbol": "ALAB",
                "trade_type": "SWING",
                "ts": "2026-05-04T10:00:00+00:00",
                "scanner_tier": "D",
                # No tier_d_paper_entry
            }
        ]
        out, code = _run_main_with_data(
            [_PIPELINE_RECORD, _APEX_CAP_RECORD], trade_events=trade_events
        )
        assert code == 0
        assert "tier_d_unexpected_execution: 1" in out
        assert "SAFETY VIOLATION" in out

    def test_normal_trade_pru_overlap(self):
        """ORDER_INTENT with scanner_tier present and != D → normal_trade_pru_overlap."""
        trade_events = [
            {
                "event": "ORDER_INTENT",
                "symbol": "ALAB",
                "trade_type": "SWING",
                "ts": "2026-05-04T10:00:00+00:00",
                "scanner_tier": "A",
                "origin_path": "normal",
            }
        ]
        out, code = _run_main_with_data(
            [_PIPELINE_RECORD, _APEX_CAP_RECORD], trade_events=trade_events
        )
        assert code == 0
        assert "normal_trade_pru_overlap: 1" in out
        assert "SAFETY VIOLATION" not in out
        assert "UNRESOLVED" not in out


# ─────────────────────────────────────────────────────────────────────────────
# Tests: execute_buy / execute_short accept **intent_extras
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentExtrasSignature:
    def test_execute_buy_accepts_intent_extras(self):
        """execute_buy must have **intent_extras in its signature."""
        import orders_core
        sig = inspect.signature(orders_core.execute_buy)
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        assert has_var_keyword, "execute_buy must accept **intent_extras"

    def test_execute_short_accepts_intent_extras(self):
        """execute_short must have **intent_extras in its signature."""
        import orders_core
        sig = inspect.signature(orders_core.execute_short)
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        assert has_var_keyword, "execute_short must accept **intent_extras"


# ─────────────────────────────────────────────────────────────────────────────
# Test: dispatch passes origin extras to execute_buy
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatchOriginTagging:
    def test_dispatch_passes_scanner_tier_to_execute_buy(self):
        """When payload has scanner_tier=D, execute_buy must receive scanner_tier=D and origin_path=tier_d_main."""
        captured_kwargs: dict = {}

        def _fake_execute_buy(**kwargs):
            captured_kwargs.update(kwargs)
            return True

        # Build a minimal ApexDecision with one Tier D entry
        decision = {
            "new_entries": [
                {
                    "symbol": "ALAB",
                    "direction": "LONG",
                    "trade_type": "SWING",
                    "conviction": "HIGH",
                    "instrument": "stock",
                    "rationale": "test",
                }
            ],
            "pm_actions": [],
        }
        candidates_by_symbol = {
            "ALAB": {
                "symbol": "ALAB",
                "price": 190.0,
                "score": 80,
                "atr_5m": 1.5,
                "scanner_tier": "D",
                "universe_bucket": "core_research",
                "primary_archetype": "Quality Compounder",
                "discovery_score": 12,
                "score_breakdown": {},
            }
        }

        with patch("signal_dispatcher.execute_buy", side_effect=_fake_execute_buy), \
             patch("signal_dispatcher.calculate_stops", return_value=(185.0, 200.0)), \
             patch("risk.calculate_position_size", return_value=50):
            from signal_dispatcher import dispatch
            dispatch(
                decision=decision,
                candidates_by_symbol=candidates_by_symbol,
                regime={"regime": "MOMENTUM_BULL"},
                portfolio_value=50_000,
                ib=MagicMock(),
                execute=True,
                active_trades={},
            )

        assert captured_kwargs.get("scanner_tier") == "D", \
            f"scanner_tier not passed to execute_buy. Got: {captured_kwargs}"
        assert captured_kwargs.get("origin_path") == "tier_d_main", \
            f"origin_path not passed correctly. Got: {captured_kwargs}"
        assert captured_kwargs.get("position_research_universe_member") is True
