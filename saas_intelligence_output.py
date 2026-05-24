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

class SaaSPayloadValidationError(ValueError):
    """Raised when a payload dict contains blocked or unexpected fields."""


def validate_customer_payload(payload: dict[str, Any]) -> None:
    """
    Validate that `payload` contains only approved customer-safe fields.

    Raises SaaSPayloadValidationError if:
      - any key is in _BLOCKED_FIELDS, OR
      - any key is not in _ALLOWED_FIELDS
    """
    blocked = [k for k in payload if k in _BLOCKED_FIELDS]
    if blocked:
        raise SaaSPayloadValidationError(
            f"Payload contains blocked fields (raw provider or execution data): {blocked}. "
            "These fields must never appear in a customer-facing SaaS payload."
        )

    unexpected = [k for k in payload if k not in _ALLOWED_FIELDS]
    if unexpected:
        raise SaaSPayloadValidationError(
            f"Payload contains fields not in the approved customer-safe allowlist: {unexpected}. "
            "Add to _ALLOWED_FIELDS only after explicit Amit approval."
        )


def get_allowed_fields() -> frozenset[str]:
    """Return the set of approved customer-safe field names."""
    return _ALLOWED_FIELDS


def get_blocked_fields() -> frozenset[str]:
    """Return the set of fields that must never appear in a customer payload."""
    return _BLOCKED_FIELDS
