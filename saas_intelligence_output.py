# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  saas_intelligence_output.py               ║
# ║   SaaS-safe customer intelligence output boundary           ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
saas_intelligence_output.py — Customer-safe intelligence output boundary.

Defines the exact fields that may appear in customer-facing Decifer payloads.
No raw provider data, no broker state, no execution fields, no internal scores.

This module must NOT import from any execution module.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from typing import Any


# ---------------------------------------------------------------------------
# Blocked field registry
# ---------------------------------------------------------------------------

# Fields that must NEVER appear in a SaaS payload.
# The validator rejects any dict containing these keys.
_BLOCKED_FIELDS: frozenset[str] = frozenset({
    # Raw market data
    "bid", "ask", "last_price", "last", "close", "open", "high", "low",
    "volume", "vwap", "ohlcv", "candles", "bars", "quotes", "trades",
    "raw_price", "raw_quote", "raw_bar",
    # Options raw data
    "option_chain", "strike", "expiry", "delta", "gamma", "theta",
    "vega", "implied_volatility", "iv", "open_interest", "option_bid",
    "option_ask", "option_last",
    # Broker / account state
    "broker_account_id", "account_id", "broker_token", "api_key",
    "ibkr_account", "paper_account", "live_account",
    "order_id", "client_order_id", "ibkr_order_id",
    "position_size", "qty", "quantity", "shares",
    "stop_price", "stop_order", "take_profit", "take_profit_order",
    "limit_price", "market_order",
    # Internal scores and provider payloads
    "raw_score", "signal_score", "ic_weight", "ic_weights",
    "provider_payload", "raw_news_payload", "raw_feed",
    "execution_signal", "buy_signal", "sell_signal",
    # PnL / trade internals
    "entry_price", "exit_price", "pnl", "pnl_pct", "unrealized_pnl",
    "realized_pnl", "cost_basis",
})

# Allowed top-level field names for SaaSIntelligencePayload.
# Anything outside this set is rejected by validate_customer_payload().
_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "market_regime_label",
    "plain_english_summary",
    "key_drivers",
    "active_themes",
    "opportunity_explanations",
    "risk_notes",
    "what_to_watch",
    "freshness_timestamp",
    "confidence_label",
    "source_category_labels",
    "data_entitlement_note",
})


# ---------------------------------------------------------------------------
# SaaS payload dataclass
# ---------------------------------------------------------------------------

@dataclass
class SaaSIntelligencePayload:
    """
    The approved customer-facing intelligence payload for Decifer Trading v1.

    All fields are plain English or curated label strings.
    No raw prices, no broker state, no execution signals.
    """

    # A short human-readable label for the current market regime.
    # Examples: "Trending up", "Choppy", "Risk-off", "Market panic"
    market_regime_label: str = ""

    # 2–3 sentence plain English market summary suitable for non-technical users.
    plain_english_summary: str = ""

    # List of active macro/thematic drivers, in plain English.
    # Example: ["AI capex cycle expanding", "Bond yields rising"]
    key_drivers: list[str] = field(default_factory=list)

    # List of active investment themes by name.
    # Example: ["ai_compute_infrastructure", "gold_safe_haven"]
    active_themes: list[str] = field(default_factory=list)

    # Per-theme or per-sector plain English opportunity explanations.
    # Each entry: {"theme": "...", "explanation": "..."}
    opportunity_explanations: list[dict[str, str]] = field(default_factory=list)

    # Risk factors in plain English, e.g. ["Geopolitical tension elevated"]
    risk_notes: list[str] = field(default_factory=list)

    # What the intelligence layer is monitoring next.
    # Example: ["Fed rate decision Thursday", "NVDA earnings Wednesday"]
    what_to_watch: list[str] = field(default_factory=list)

    # ISO-8601 UTC timestamp of when this payload was generated.
    freshness_timestamp: str = ""

    # Human-readable confidence label.
    # One of: "High", "Moderate", "Low", "Insufficient data"
    confidence_label: str = "Moderate"

    # Which categories of intelligence sources contributed.
    # Example: ["market_data", "macro_drivers", "thematic_intelligence"]
    source_category_labels: list[str] = field(default_factory=list)

    # Disclaimer text for the customer UI.
    # Example: "Intelligence powered by Decifer. Not financial advice."
    data_entitlement_note: str = (
        "Market intelligence powered by Decifer. "
        "This is not financial advice. For informational purposes only."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Production hardening constants (Sprint M6)
# ---------------------------------------------------------------------------

# Maximum age of freshness_timestamp before the payload is rejected.
_FRESHNESS_WINDOW_HOURS: int = 6

# Terms that indicate execution or broker internals leaked into a value.
# These must never appear in customer-facing string content.
_EXECUTION_WORDING_PATTERNS: tuple[str, ...] = (
    "execute_buy", "execute_short", "execute_sell",
    "flatten_all", "ORDER_INTENT", "ORDER_FILLED",
    "ExecutionBlockedError", "DECIFER_RUNTIME_MODE",
    "DUP481326",
)

# Internal artefact file names that must never appear in customer output.
_INTERNAL_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "live_driver_state", "theme_activation.json",
    "current_manifest.json", "economic_candidate_feed",
    "apex_conversation_log", "training_records.jsonl",
    "event_log.jsonl",
)

# Field name substrings that indicate broker/account state.
# These catch new broker-like fields not yet in _BLOCKED_FIELDS.
_BROKER_FIELD_SUBSTRINGS: tuple[str, ...] = (
    "ibkr", "broker_account", "buying_power", "account_value",
)


def _collect_string_values(value: Any) -> list[str]:
    """Recursively collect all string values from a nested dict/list structure."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for v in value.values():
            result.extend(_collect_string_values(v))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_collect_string_values(item))
        return result
    return []


class SaaSPayloadValidationError(ValueError):
    """Raised when a payload dict contains blocked or unexpected fields."""


def validate_customer_payload(payload: dict[str, Any]) -> None:
    """
    Validate that `payload` contains only approved customer-safe fields.

    Fails closed if any of the following are true:
      - any key is in _BLOCKED_FIELDS
      - any key is not in _ALLOWED_FIELDS
      - any field name contains a broker/account substring
      - any string value contains execution-like wording
      - any string value contains a raw internal artefact name
      - data_entitlement_note is absent or empty
      - freshness_timestamp is absent, unparseable, or older than _FRESHNESS_WINDOW_HOURS
    """
    # Rule 1: no explicitly blocked field names
    blocked = [k for k in payload if k in _BLOCKED_FIELDS]
    if blocked:
        raise SaaSPayloadValidationError(
            f"Payload contains blocked fields (raw provider or execution data): {blocked}. "
            "These fields must never appear in a customer-facing SaaS payload."
        )

    # Rule 2: only allowed field names
    unexpected = [k for k in payload if k not in _ALLOWED_FIELDS]
    if unexpected:
        raise SaaSPayloadValidationError(
            f"Payload contains fields not in the approved customer-safe allowlist: {unexpected}. "
            "Add to _ALLOWED_FIELDS only after explicit Amit approval."
        )

    # Rule 3: no broker-like field name substrings (catches new fields not yet in blocklist)
    broker_like = [
        k for k in payload
        if any(sub in k.lower() for sub in _BROKER_FIELD_SUBSTRINGS)
    ]
    if broker_like:
        raise SaaSPayloadValidationError(
            f"Payload contains broker-like field names: {broker_like}. "
            "These suggest account or execution state and must not appear in customer output."
        )

    # Rule 4: no execution-like wording in string values
    all_strings = _collect_string_values(payload)
    for pattern in _EXECUTION_WORDING_PATTERNS:
        hits = [s for s in all_strings if pattern in s]
        if hits:
            raise SaaSPayloadValidationError(
                f"Payload string values contain execution-like wording ({pattern!r}). "
                "Internal execution terminology must not appear in customer-facing output."
            )

    # Rule 5: no raw internal artefact names in string values
    for pattern in _INTERNAL_ARTIFACT_PATTERNS:
        hits = [s for s in all_strings if pattern in s]
        if hits:
            raise SaaSPayloadValidationError(
                f"Payload string values contain internal artefact name ({pattern!r}). "
                "Internal file names must not leak into customer-facing output."
            )

    # Rule 6: data_entitlement_note must be non-empty
    note = payload.get("data_entitlement_note", "")
    if not note or not str(note).strip():
        raise SaaSPayloadValidationError(
            "data_entitlement_note is absent or empty. "
            "Every customer payload must carry a disclaimer."
        )

    # Rule 7: freshness_timestamp must be present, parseable, and within window
    ts_val = payload.get("freshness_timestamp", "")
    if not ts_val or not str(ts_val).strip():
        raise SaaSPayloadValidationError(
            "freshness_timestamp is absent or empty — payload cannot be proven fresh."
        )
    try:
        parsed = datetime.fromisoformat(str(ts_val))
        # Ensure timezone-aware for comparison
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600
        if age_hours > _FRESHNESS_WINDOW_HOURS:
            raise SaaSPayloadValidationError(
                f"freshness_timestamp is {age_hours:.1f}h old — exceeds the "
                f"{_FRESHNESS_WINDOW_HOURS}h freshness window. "
                "Serve a degraded payload with a fresh timestamp when intelligence data is stale."
            )
    except SaaSPayloadValidationError:
        raise
    except Exception as exc:
        raise SaaSPayloadValidationError(
            f"freshness_timestamp {ts_val!r} is not a valid ISO timestamp: {exc}"
        ) from exc


def get_allowed_fields() -> frozenset[str]:
    """Return the set of approved customer-safe field names."""
    return _ALLOWED_FIELDS


def get_blocked_fields() -> frozenset[str]:
    """Return the set of fields that must never appear in a customer payload."""
    return _BLOCKED_FIELDS
