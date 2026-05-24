# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  runtime_config.py                         ║
# ║   Central runtime mode configuration                        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
runtime_config.py — Central runtime mode configuration for Decifer Trading.

Runtime modes
─────────────
  local_dev          — development on Mac; execution is NOT implied by default.
                       Developer must switch to paper_execution to run the bot.
  intelligence_cloud — DigitalOcean / SaaS deployment; execution unconditionally blocked.
  paper_execution    — Mac paper-trading node; execution requires DECIFER_EXECUTION_ENABLED=true.
  full_trading       — future live-trading node; execution requires DECIFER_EXECUTION_ENABLED=true.

Environment variables (read at import time; set in .env before importing)
─────────────────────────────────────────────────────────────────────────
  DECIFER_RUNTIME_MODE              default: local_dev
  DECIFER_EXECUTION_ENABLED         default: false
  DECIFER_CUSTOMER_OUTPUT_MODE      default: false
  DECIFER_MOBILE_READ_ONLY          default: true
  DECIFER_DASHBOARD_CONTROL_ENABLED default: false

Design invariant
────────────────
  intelligence_cloud ALWAYS blocks execution, regardless of any other env var.
  Execution is only possible when runtime_mode ∈ {paper_execution, full_trading}
  AND DECIFER_EXECUTION_ENABLED=true.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("decifer.runtime_config")

# ---------------------------------------------------------------------------
# Runtime mode constants
# ---------------------------------------------------------------------------

MODE_LOCAL_DEV: str = "local_dev"
MODE_INTELLIGENCE_CLOUD: str = "intelligence_cloud"
MODE_PAPER_EXECUTION: str = "paper_execution"
MODE_FULL_TRADING: str = "full_trading"

_VALID_MODES: frozenset[str] = frozenset(
    {MODE_LOCAL_DEV, MODE_INTELLIGENCE_CLOUD, MODE_PAPER_EXECUTION, MODE_FULL_TRADING}
)
_EXECUTION_MODES: frozenset[str] = frozenset({MODE_PAPER_EXECUTION, MODE_FULL_TRADING})


class ExecutionBlockedError(RuntimeError):
    """Raised when an execution action is attempted in a mode that forbids it."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def _read_runtime_mode() -> str:
    raw = os.environ.get("DECIFER_RUNTIME_MODE", MODE_LOCAL_DEV).strip().lower()
    if raw not in _VALID_MODES:
        raise ValueError(
            f"DECIFER_RUNTIME_MODE={raw!r} is not a valid runtime mode. "
            f"Valid modes: {sorted(_VALID_MODES)}"
        )
    return raw


# ---------------------------------------------------------------------------
# Module-level state — resolved once at import time
# ---------------------------------------------------------------------------

runtime_mode: str = _read_runtime_mode()
execution_enabled: bool = _bool_env("DECIFER_EXECUTION_ENABLED", False)
customer_output_mode: bool = _bool_env("DECIFER_CUSTOMER_OUTPUT_MODE", False)
mobile_read_only: bool = _bool_env("DECIFER_MOBILE_READ_ONLY", True)
dashboard_control_enabled: bool = _bool_env("DECIFER_DASHBOARD_CONTROL_ENABLED", False)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def is_intelligence_cloud_mode() -> bool:
    """True when running in cloud / SaaS deployment mode."""
    return runtime_mode == MODE_INTELLIGENCE_CLOUD


def is_execution_enabled() -> bool:
    """
    True only when ALL of the following hold:
      1. runtime_mode is paper_execution or full_trading
      2. DECIFER_EXECUTION_ENABLED=true

    intelligence_cloud → always False, regardless of other env vars.
    local_dev          → always False; developers must switch to paper_execution.
    """
    if runtime_mode == MODE_INTELLIGENCE_CLOUD:
        return False
    if runtime_mode not in _EXECUTION_MODES:
        return False
    return execution_enabled


# ---------------------------------------------------------------------------
# Guards — call at the top of every order-mutation function
# ---------------------------------------------------------------------------

def assert_execution_allowed(action_name: str) -> None:
    """
    Raise ExecutionBlockedError if execution is not permitted in the current runtime mode.

    Fails closed unconditionally in intelligence_cloud mode.
    Fails closed in all other modes unless is_execution_enabled() is True.

    Call this at the very top of execute_buy, execute_sell, execute_short,
    execute_buy_option, execute_sell_option, flatten_all, and any future
    order-mutation entry points.
    """
    if runtime_mode == MODE_INTELLIGENCE_CLOUD:
        raise ExecutionBlockedError(
            f"Execution action '{action_name}' is blocked: "
            "runtime_mode=intelligence_cloud. "
            "The intelligence cloud deployment never submits orders to any broker."
        )
    if not is_execution_enabled():
        raise ExecutionBlockedError(
            f"Execution action '{action_name}' is blocked: "
            f"runtime_mode={runtime_mode!r}, "
            f"DECIFER_EXECUTION_ENABLED={execution_enabled}. "
            "To enable: set DECIFER_RUNTIME_MODE=paper_execution "
            "and DECIFER_EXECUTION_ENABLED=true in .env."
        )


def assert_customer_output_allowed() -> None:
    """
    Raise RuntimeError if customer output is not enabled in the current mode.
    Customer output is always allowed in intelligence_cloud mode.
    In other modes it requires DECIFER_CUSTOMER_OUTPUT_MODE=true.
    """
    if not customer_output_mode and not is_intelligence_cloud_mode():
        raise RuntimeError(
            "Customer output is not enabled in the current runtime mode. "
            "Set DECIFER_CUSTOMER_OUTPUT_MODE=true or run in intelligence_cloud mode."
        )
