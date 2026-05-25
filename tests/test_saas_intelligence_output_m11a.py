"""
test_saas_intelligence_output_m11a.py — Sprint M11A.

Covers Amit's required tests for the allowlist expansion:
  - A clean Market Map payload with the new fields passes validation
  - A nested blocked field inside any of the new fields fails validation
  - Top-level blocked / unexpected / broker-like / execution wording still
    rejected (regression on the existing rules)
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saas_intelligence_output import (
    SaaSIntelligencePayload,
    SaaSPayloadValidationError,
    _ALLOWED_FIELDS,
    _FORBIDDEN_NESTED_FIELD_SUBSTRINGS,
    validate_customer_payload,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _base_safe_payload() -> dict:
    """A payload that uses every approved allowlist field with safe data."""
    return SaaSIntelligencePayload(
        market_regime_label="Trending up",
        plain_english_summary="The market is currently trending up.",
        key_drivers=["AI capital spending cycle expanding"],
        active_themes=["ai_compute_infrastructure"],
        opportunity_explanations=[{"theme": "AI", "explanation": "Active demand."}],
        risk_notes=["Geopolitical tension elevated"],
        what_to_watch=["Fed rate decision Thursday"],
        freshness_timestamp=_now_iso(),
        confidence_label="High",
        source_category_labels=["market_data", "macro_drivers"],
        data_entitlement_note="Market intelligence powered by Decifer. Not financial advice.",
        market_mood="Risk-on — fresh de-escalation",
        what_changed=["[geopolitics] Iran deal reported"],
        key_events=[{
            "event_id": "abc-123",
            "event_family": "geopolitics",
            "event_type": "de_escalation",
            "status": "reported",
            "title": "Iran deal could happen today",
            "summary_plain_english": "Risk premium may fade.",
            "likely_positive_exposures": ["broad risk appetite"],
            "likely_negative_exposures": ["oil risk premium"],
            "affected_channels": ["geopolitical_risk"],
            "confirmation_signals": ["Oil falls"],
            "invalidation_signals": ["Deal collapses"],
            "freshness_status": "fresh",
            "processed_at": _now_iso(),
            "source_confidence": "medium",
            "materiality": "high",
        }],
        sectors=[{
            "name": "energy",
            "mood": "headwind",
            "reasons": ["risk premium unwinding"],
            "from_events": ["Iran deal could happen today"],
        }],
        themes=[{
            "theme": "energy",
            "state": "active",
            "event_signal": "weakening",
            "from_events": ["Iran deal"],
        }],
        radar=[{
            "symbol": "AAL",
            "reason_to_watch": "Airlines may benefit from oil unwind",
            "theme_link": "risk_on_rotation",
            "confirmation_signal": "Oil stays lower",
            "invalidation_signal": "Renewed strikes",
        }],
        watch_next=["VIX move", "Defence price action"],
        known_conflicts=["Geo-risk drivers active but events suggest unwind"],
        section_freshness={
            "events": {"status": "fresh", "age_hours": 0.1, "processed_at": _now_iso()},
            "macro_drivers": {"status": "fresh", "age_hours": 1.0, "processed_at": _now_iso()},
            "sectors": {"status": "fresh", "age_hours": 1.0, "processed_at": _now_iso()},
            "themes": {"status": "fresh", "age_hours": 1.0, "processed_at": _now_iso()},
            "radar": {"status": "fresh", "age_hours": 0.1, "processed_at": _now_iso()},
            "ask_context": {"status": "unknown", "age_hours": None, "processed_at": None},
        },
        source_notes=["Driver layer derived from market price evidence."],
    ).to_dict()


# ---------------------------------------------------------------------------

class TestAllowlistExpansion:

    def test_all_10_new_fields_present_in_allowlist(self):
        expected = {
            "key_events", "what_changed", "known_conflicts", "section_freshness",
            "sectors", "themes", "radar", "watch_next", "market_mood",
            "source_notes",
        }
        assert expected.issubset(_ALLOWED_FIELDS)

    def test_clean_payload_with_m11a_fields_passes(self):
        validate_customer_payload(_base_safe_payload())  # should not raise

    def test_empty_optional_m11a_fields_pass(self):
        """Optional fields with their default empty values must validate."""
        p = SaaSIntelligencePayload(
            freshness_timestamp=_now_iso(),
            confidence_label="High",
        ).to_dict()
        validate_customer_payload(p)


# ---------------------------------------------------------------------------

class TestNestedBlockedFieldGuard:

    def test_position_size_inside_radar_rejected(self):
        p = _base_safe_payload()
        p["radar"][0]["position_size"] = 100
        with pytest.raises(SaaSPayloadValidationError, match="position_size"):
            validate_customer_payload(p)

    def test_pnl_inside_key_events_rejected(self):
        p = _base_safe_payload()
        p["key_events"][0]["pnl"] = 250.50
        with pytest.raises(SaaSPayloadValidationError, match="pnl"):
            validate_customer_payload(p)

    def test_account_value_inside_sectors_rejected(self):
        p = _base_safe_payload()
        p["sectors"][0]["account_value"] = 100000
        with pytest.raises(SaaSPayloadValidationError, match="account_value"):
            validate_customer_payload(p)

    def test_order_id_inside_radar_rejected(self):
        p = _base_safe_payload()
        p["radar"][0]["order_id"] = "abc-123"
        with pytest.raises(SaaSPayloadValidationError, match="order_id"):
            validate_customer_payload(p)

    def test_trade_recommendation_inside_radar_rejected(self):
        p = _base_safe_payload()
        p["radar"][0]["trade_recommendation"] = "BUY"
        with pytest.raises(SaaSPayloadValidationError, match="trade_recommendation"):
            validate_customer_payload(p)

    def test_buy_signal_inside_radar_rejected(self):
        p = _base_safe_payload()
        p["radar"][0]["buy_signal"] = True
        with pytest.raises(SaaSPayloadValidationError, match="buy_signal"):
            validate_customer_payload(p)

    def test_broker_account_inside_section_freshness_rejected(self):
        p = _base_safe_payload()
        p["section_freshness"]["broker_account"] = "DUP-XYZ"
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)

    def test_unrealized_pnl_inside_themes_rejected(self):
        p = _base_safe_payload()
        p["themes"][0]["unrealized_pnl"] = 500.0
        with pytest.raises(SaaSPayloadValidationError, match="unrealized_pnl"):
            validate_customer_payload(p)

    def test_pm_action_inside_what_changed_rejected_via_dict(self):
        p = _base_safe_payload()
        # what_changed is a list of strings — convert to dict to test the guard
        p["sectors"].append({"name": "tech", "mood": "tailwind",
                             "pm_action": "TRIM"})
        with pytest.raises(SaaSPayloadValidationError, match="pm_action"):
            validate_customer_payload(p)


# ---------------------------------------------------------------------------

class TestTopLevelRegressions:

    def test_blocked_top_level_field_still_rejected(self):
        p = _base_safe_payload()
        p["pnl"] = 100
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)

    def test_unknown_top_level_field_still_rejected(self):
        p = _base_safe_payload()
        p["random_field"] = "should not be here"
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)

    def test_broker_like_top_level_still_rejected(self):
        # No way to add an ibkr-* field through dataclass; emulate manual dict
        p = _base_safe_payload()
        p["ibkr_state"] = {"foo": "bar"}
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)

    def test_execution_wording_in_summary_still_rejected(self):
        p = _base_safe_payload()
        p["plain_english_summary"] = "Will execute_buy soon"
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)

    def test_stale_freshness_timestamp_still_rejected(self):
        from datetime import timedelta
        p = _base_safe_payload()
        p["freshness_timestamp"] = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)

    def test_missing_disclaimer_still_rejected(self):
        p = _base_safe_payload()
        p["data_entitlement_note"] = ""
        with pytest.raises(SaaSPayloadValidationError):
            validate_customer_payload(p)


# ---------------------------------------------------------------------------

class TestForbiddenNestedRegistry:

    def test_radar_guardrail_substrings_present(self):
        # Sprint requirement: radar must never carry buy/sell/trade recommendation,
        # execution readiness, account exposure
        for sub in ("buy_signal", "sell_signal", "trade_recommendation",
                    "execution_readiness", "account_exposure"):
            assert sub in _FORBIDDEN_NESTED_FIELD_SUBSTRINGS

    def test_pnl_and_position_substrings_present(self):
        for sub in ("position_size", "entry_price", "exit_price",
                    "pnl", "unrealized_pnl", "realized_pnl", "cost_basis"):
            assert sub in _FORBIDDEN_NESTED_FIELD_SUBSTRINGS
