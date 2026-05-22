# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  expression_router.py                       ║
# ║   Single responsibility: decide COMMON vs OPTION vs NO_TRADE ║
# ║   for options scanner signals.                               ║
# ║                                                              ║
# ║   Exposed API:                                               ║
# ║     route_expression(signal, flow_data, regime,              ║
# ║                      portfolio_state) -> ExpressionRoute     ║
# ║                                                              ║
# ║   Common stock is the default. Options require:              ║
# ║     1. Directional signal (CALL_BUYER or PUT_BUYER)          ║
# ║     2. Confirmed unusual flow from an approved provider      ║
# ║     3. Option score exceeds common by OPTION_SCORE_ADVANTAGE ║
# ║                                                              ║
# ║   No trading logic. No execution. Routing only.              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("decifer.expression_router")

# ── Constants ─────────────────────────────────────────────────────────
OPTION_SCORE_ADVANTAGE = 10   # option must beat common score by this margin to route OPTION
_MIN_COMMON_SCORE = 12        # minimum options_score to reach COMMON route


# ── Data contract ─────────────────────────────────────────────────────


@dataclass
class ExpressionRoute:
    """Routing decision for a single options scanner signal."""
    route: str                 # "COMMON" | "OPTION" | "NO_TRADE"
    reason: str                # human-readable reason for audit log
    common_expression_score: int
    option_expression_score: int
    option_gates_pass: bool
    common_gates_pass: bool
    skip_reason: str | None    # one of the standard skip reason keys if applicable


# ── Public API ────────────────────────────────────────────────────────


def route_expression(
    signal: dict,
    flow_data,                 # OptionsFlowData | None
    regime: dict | None,
    portfolio_state: dict | None,
) -> ExpressionRoute:
    """
    Decide how to express a trade given an options scanner signal.

    Args:
        signal:          options scanner signal dict (keys: symbol, signal, options_score,
                         unusual_calls, unusual_puts, provider_status, flow_definition, ...)
        flow_data:       OptionsFlowData | None from options_provider
        regime:          current market regime dict | None
        portfolio_state: portfolio state dict | None (reserved for future capacity checks)

    Returns:
        ExpressionRoute with route in {"COMMON", "OPTION", "NO_TRADE"}
    """
    if not signal:
        return ExpressionRoute(
            route="NO_TRADE",
            reason="empty signal dict",
            common_expression_score=0,
            option_expression_score=0,
            option_gates_pass=False,
            common_gates_pass=False,
            skip_reason="below_option_expression_score",
        )

    options_score: int = signal.get("options_score", 0)
    sig_type: str = signal.get("signal", "")
    unusual_calls: bool = bool(signal.get("unusual_calls", False))
    unusual_puts: bool = bool(signal.get("unusual_puts", False))
    provider_status: str = signal.get("provider_status") or "NULL"

    # ── Score computation ─────────────────────────────────────────────
    # Common score = scanner score (conviction proxy for common stock expression)
    common_score = options_score

    # Option score = common score + bonuses/penalties
    option_score = common_score
    bonus_reasons: list[str] = []

    if unusual_calls or unusual_puts:
        option_score += 15
        bonus_reasons.append("+15 confirmed unusual flow")

    flow_definition = ""
    if flow_data is not None:
        flow_definition = getattr(flow_data, "flow_definition", "")
        if flow_definition == "OI_RATIO":
            option_score += 5
            bonus_reasons.append("+5 OI_RATIO (highest confidence)")

    if provider_status in (None, "NULL", "NOT_USABLE_FOR_OPTIONS"):
        option_score -= 10
        bonus_reasons.append("-10 null provider")

    # ── Gate evaluation ───────────────────────────────────────────────
    # Option gates: ALL must pass for options expression to be considered
    option_gates_pass = (
        sig_type in {"CALL_BUYER", "PUT_BUYER"}
        and (unusual_calls or unusual_puts)
        and provider_status not in (None, "NULL", "NOT_USABLE_FOR_OPTIONS")
        and flow_data is not None
        and getattr(flow_data, "flow_metrics_available", False)
    )

    # Common gates: stock expression minimum threshold
    common_gates_pass = options_score >= _MIN_COMMON_SCORE and bool(signal)

    # ── Routing decision ──────────────────────────────────────────────
    if options_score < _MIN_COMMON_SCORE:
        return ExpressionRoute(
            route="NO_TRADE",
            reason=f"options_score={options_score} below minimum {_MIN_COMMON_SCORE}",
            common_expression_score=common_score,
            option_expression_score=option_score,
            option_gates_pass=option_gates_pass,
            common_gates_pass=common_gates_pass,
            skip_reason="below_option_expression_score",
        )

    if option_gates_pass and option_score >= common_score + OPTION_SCORE_ADVANTAGE:
        bonus_str = ", ".join(bonus_reasons) if bonus_reasons else "no bonus"
        return ExpressionRoute(
            route="OPTION",
            reason=(
                f"option_score={option_score} > common_score={common_score} + "
                f"advantage={OPTION_SCORE_ADVANTAGE} | {bonus_str} | "
                f"provider_status={provider_status}"
            ),
            common_expression_score=common_score,
            option_expression_score=option_score,
            option_gates_pass=option_gates_pass,
            common_gates_pass=common_gates_pass,
            skip_reason=None,
        )

    if common_gates_pass:
        if not option_gates_pass:
            gap_reasons = []
            if sig_type not in {"CALL_BUYER", "PUT_BUYER"}:
                gap_reasons.append(f"signal={sig_type} not directional")
            if not (unusual_calls or unusual_puts):
                gap_reasons.append("no confirmed unusual flow")
            if provider_status in (None, "NULL", "NOT_USABLE_FOR_OPTIONS"):
                gap_reasons.append(f"provider_status={provider_status}")
            if flow_data is None or not getattr(flow_data, "flow_metrics_available", False):
                gap_reasons.append("flow metrics unavailable")
            reason = (
                f"common expression preferred — options gates not met: "
                f"{'; '.join(gap_reasons) if gap_reasons else 'insufficient flow'} | "
                f"option_score={option_score} common_score={common_score}"
            )
        else:
            # Gates pass but advantage not met
            reason = (
                f"common expression preferred — option advantage insufficient: "
                f"option_score={option_score} vs common_score={common_score} "
                f"(need +{OPTION_SCORE_ADVANTAGE})"
            )

        return ExpressionRoute(
            route="COMMON",
            reason=reason,
            common_expression_score=common_score,
            option_expression_score=option_score,
            option_gates_pass=option_gates_pass,
            common_gates_pass=common_gates_pass,
            skip_reason="common_better_expression",
        )

    # Fallback — should not reach here given the score check above, but safety net
    return ExpressionRoute(
        route="NO_TRADE",
        reason=f"no viable expression — options_score={options_score}",
        common_expression_score=common_score,
        option_expression_score=option_score,
        option_gates_pass=option_gates_pass,
        common_gates_pass=common_gates_pass,
        skip_reason="below_option_expression_score",
    )
