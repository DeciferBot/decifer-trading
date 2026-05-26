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
#
# Sprint M11A additions: key_events, what_changed, known_conflicts,
#   section_freshness, sectors, themes, radar, watch_next, market_mood,
#   source_notes.
# Approved by Amit for customer-only Market Map fields in Sprint M11A.
#
# All Sprint M11A fields are subject to the same nested-blocked-field
# validation as every other field — see _validate_no_nested_blocked().
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
    # ── Sprint M11A — customer Market Map sections (approved by Amit) ──
    "key_events",
    "what_changed",
    "known_conflicts",
    "section_freshness",
    "sectors",
    "themes",
    "radar",
    "watch_next",
    "market_mood",
    "source_notes",
    # ── Sprint M12A — Theme Transmission Graph (approved by Amit) ──
    "theme_graph_themes",
    "theme_graph_buckets",
    "theme_graph_symbol_card",
    "theme_graph_reason_path",
    "theme_graph_search_results",
    # ── Sprint M11C — Customer universe snapshot (approved by Amit) ──
    "universe_snapshot",
})

# Field-name substrings that are forbidden anywhere in the payload — even
# nested inside an approved field. Sprint M11A guardrail: an approved
# top-level field MUST NOT smuggle private trading data through nested keys.
_FORBIDDEN_NESTED_FIELD_SUBSTRINGS: tuple[str, ...] = (
    # Position / order / execution
    "position_size", "qty", "quantity", "shares",
    "entry_price", "exit_price", "stop_price", "limit_price",
    "take_profit", "stop_order", "market_order",
    "order_id", "client_order_id", "ibkr_order_id",
    # Account / broker
    "broker_account", "account_id", "ibkr_account",
    "buying_power", "account_value", "portfolio_value",
    # P&L
    "pnl", "unrealized_pnl", "realized_pnl", "cost_basis",
    "daily_pnl", "total_pnl",
    # Raw market data / scores
    "raw_score", "signal_score", "ic_weight", "raw_quote", "raw_bar",
    # PM / trade history internals
    "pm_action", "trade_id", "execution_signal",
    # Radar guardrail — keep radar strictly intelligence
    "buy_signal", "sell_signal", "trade_recommendation",
    "execution_readiness", "account_exposure",
)


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

    # ── Sprint M11A — Market Map sections (customer-only) ──
    #
    # All optional. When omitted from to_dict() output (they're empty by
    # default), the validator treats them as absent and applies its normal
    # allowlist rules. When populated they MUST contain only customer-safe
    # data — see _FORBIDDEN_NESTED_FIELD_SUBSTRINGS for the nested guard.

    # One-line plain-English market mood.
    # Example: "Risk-on — fresh de-escalation or risk-premium unwind"
    market_mood: str = ""

    # Short bullets describing what just changed in the last ~30 minutes.
    what_changed: list[str] = field(default_factory=list)

    # Customer-safe summaries of recent events from customer_event_tape.json.
    # Each entry must NOT contain prices, position sizes, or execution fields.
    key_events: list[dict[str, Any]] = field(default_factory=list)

    # Per-sector mood: {name, mood, reasons[], from_events[]}.
    sectors: list[dict[str, Any]] = field(default_factory=list)

    # Per-theme state: {theme, state, event_signal?, from_events?}.
    themes: list[dict[str, Any]] = field(default_factory=list)

    # Symbols on the radar — strictly customer intelligence.
    # Each entry: {symbol, reason_to_watch, theme_link, confirmation_signal,
    #               invalidation_signal}.
    # MUST NOT contain: buy/sell, entry/exit, stop, target, position size,
    #                    trade recommendation, execution readiness, account
    #                    exposure, or P&L. (Enforced by nested-field guard.)
    radar: list[dict[str, Any]] = field(default_factory=list)

    # What the intelligence layer is monitoring next.
    watch_next: list[str] = field(default_factory=list)

    # Plain-English description of conflicts between price drivers and event
    # evidence — e.g. "Defence stocks still reflect recent geopolitical risk,
    # but fresh de-escalation headlines suggest the premium may be fading."
    known_conflicts: list[str] = field(default_factory=list)

    # Per-section freshness: {events, macro_drivers, sectors, themes, radar,
    #                          ask_context} -> {status, age_hours, processed_at}.
    section_freshness: dict[str, Any] = field(default_factory=dict)

    # Plain-English provenance notes — never includes file paths or APIs.
    source_notes: list[str] = field(default_factory=list)

    # Sprint M11C — customer-safe universe snapshot.
    # Projected from active_opportunity_universe; execution fields stripped.
    # Each item: {symbol, company_name, theme_id, why_connected, transmission}.
    universe_snapshot: list[dict[str, Any]] = field(default_factory=list)

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


def _collect_nested_field_names(value: Any, *, path: str = "") -> list[tuple[str, str]]:
    """Recursively collect (full_path, key) for every dict key in `value`.

    Returns a list of (path, leaf_key) tuples so callers can report where
    a forbidden field was found.
    """
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            sub_path = f"{path}.{k}" if path else str(k)
            out.append((sub_path, str(k)))
            out.extend(_collect_nested_field_names(v, path=sub_path))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            sub_path = f"{path}[{i}]"
            out.extend(_collect_nested_field_names(item, path=sub_path))
    return out


def _validate_no_nested_blocked(payload: dict[str, Any]) -> None:
    """Sprint M11A guardrail — block private trading data inside nested fields.

    Top-level allowlist isn't enough: an approved field like ``radar`` could
    smuggle private state via nested keys (e.g. ``radar[0].position_size``).
    This walker rejects any blocked or broker-like key anywhere in the payload.
    """
    nested = _collect_nested_field_names(payload)
    for full_path, key in nested:
        kl = key.lower()
        # Explicit blocked set
        if key in _BLOCKED_FIELDS:
            raise SaaSPayloadValidationError(
                f"Payload contains nested blocked field at {full_path!r} "
                f"(key={key!r}). Blocked fields are forbidden anywhere, "
                "including inside approved containers like 'radar' or 'key_events'."
            )
        # Broker-like substrings
        for sub in _BROKER_FIELD_SUBSTRINGS:
            if sub in kl:
                raise SaaSPayloadValidationError(
                    f"Payload contains nested broker-like field at {full_path!r} "
                    f"(key={key!r}). Broker/account state must not appear in "
                    "customer output, even nested."
                )
        # Sprint M11A forbidden nested substrings
        for sub in _FORBIDDEN_NESTED_FIELD_SUBSTRINGS:
            if sub in kl:
                raise SaaSPayloadValidationError(
                    f"Payload contains forbidden nested field at {full_path!r} "
                    f"(key={key!r}; matched substring {sub!r}). Private trading "
                    "state must not appear in customer output."
                )


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

    # Rule 3b (Sprint M11A): nested-blocked-field guard.
    # Approved containers like 'radar', 'key_events', 'sectors' must not smuggle
    # private trading data through nested keys.
    _validate_no_nested_blocked(payload)

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
