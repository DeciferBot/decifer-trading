# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  phase_gate.py                              ║
# ║   Enforces phase sequencing: Phase 4+ features (live         ║
# ║   accounts, cloud, Docker, multi-user) are frozen until      ║
# ║   Phase 1 paper-trading validation exit criteria are met.    ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Root cause this addresses
# ─────────────────────────
# The feature plan tracks 25 features across Phases 1–5. Modules
# from Phase 3 (ml_engine), Phase 4 (social_sentiment), and Phase 5
# (smart_execution, options_scanner) were built before Phase 1
# (paper trading validation) produced a single closed trade.
# config.py already contains live_1 / live_2 / aggregate_accounts —
# Phase 4 multi-account infrastructure embedded with no gate.
#
# This module provides the enforcement layer.  Call validate() at
# bot startup.  Call assert_feature_allowed() before activating
# any Phase 4+ feature. Call get_status() for dashboard display.

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Public exception ──────────────────────────────────────────────


class PhaseGateViolation(RuntimeError):
    """Raised when a frozen feature is activated in the wrong phase."""


# ── Phase definitions (informational) ────────────────────────────


PHASE_DESCRIPTIONS: dict[int, str] = {
    1: "Paper trading validation — single account, core pipeline stable",
    2: "Bias removal & regime adaptation — roadmap A/B/C/D features",
    3: "Signal validation & ML calibration — Alphalens, walk-forward",
    4: "Advanced data & execution — multi-account, live accounts, cloud",
    5: "Infrastructure — Docker, multi-user, hosted deployment",
}


# ── Internal helpers ──────────────────────────────────────────────


def _load_config() -> dict[str, Any]:
    from config import CONFIG  # local import avoids circular at module level
    return CONFIG


def _count_closed_trades(trades_path: str) -> int:
    """Return the number of closed (exited) trades from trades.json."""
    p = Path(trades_path)
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if isinstance(data, list):
        return sum(1 for t in data if t.get("status") in ("closed", "exited", "filled"))
    return 0


def _get_test_pass_rate() -> float | None:
    """
    Run pytest in collection-only mode to count tests, then parse the
    last pytest result from .pytest_cache if available.  Returns None
    if the cache cannot be read.
    """
    cache_dir = Path(".pytest_cache") / "v" / "cache"
    result_file = cache_dir / "lastfailed"
    # pytest stores last-failed; we infer pass rate from test counts elsewhere.
    # A lightweight proxy: read nodeids from stepwise or use the cache.
    # If the cache is absent we return None (criteria treated as unmet).
    nodeids_file = cache_dir / "nodeids"
    if not nodeids_file.exists():
        return None
    try:
        nodeids = json.loads(nodeids_file.read_text())
        if not nodeids:
            return None
        failed = json.loads(result_file.read_text()) if result_file.exists() else {}
        n_total = len(nodeids)
        n_failed = len(failed) if isinstance(failed, dict) else 0
        return (n_total - n_failed) / n_total
    except (json.JSONDecodeError, OSError):
        return None


# ── Public API ────────────────────────────────────────────────────


@dataclass
class PhaseStatus:
    current_phase: int
    phase_description: str
    closed_trades: int
    min_closed_trades: int
    test_pass_rate: float | None
    min_test_pass_rate: float
    min_paper_trading_days: int
    frozen_features: dict[str, int]
    criteria_met: dict[str, bool] = field(default_factory=dict)
    phase1_complete: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_phase": self.current_phase,
            "phase_description": self.phase_description,
            "closed_trades": self.closed_trades,
            "min_closed_trades": self.min_closed_trades,
            "test_pass_rate": self.test_pass_rate,
            "min_test_pass_rate": self.min_test_pass_rate,
            "min_paper_trading_days": self.min_paper_trading_days,
            "frozen_features": self.frozen_features,
            "criteria_met": self.criteria_met,
            "phase1_complete": self.phase1_complete,
        }


def get_status(config: dict[str, Any] | None = None) -> PhaseStatus:
    """
    Return a PhaseStatus describing the current gate state.
    Reads config.py and data/trades.json.  Safe to call at any time.
    """
    if config is None:
        config = _load_config()

    pg = config.get("phase_gate", {})
    current_phase: int = pg.get("current_phase", 1)
    criteria: dict[str, Any] = pg.get("phase1_exit_criteria", {})
    frozen: dict[str, int] = pg.get("frozen_features", {})

    min_trades: int = criteria.get("min_closed_trades", 200)
    min_pass: float = criteria.get("min_test_pass_rate", 0.80)
    min_days: int = criteria.get("min_paper_trading_days", 30)

    trades_path: str = config.get("trade_log", "data/trades.json")
    closed_trades = _count_closed_trades(trades_path)
    pass_rate = _get_test_pass_rate()

    criteria_met = {
        "min_closed_trades": closed_trades >= min_trades,
        "min_test_pass_rate": (pass_rate is not None and pass_rate >= min_pass),
        # min_paper_trading_days is validated by Amit manually — defaults False until
        # current_phase is manually advanced.
        "min_paper_trading_days": current_phase > 1,
    }
    phase1_complete = all(criteria_met.values())

    return PhaseStatus(
        current_phase=current_phase,
        phase_description=PHASE_DESCRIPTIONS.get(current_phase, "Unknown phase"),
        closed_trades=closed_trades,
        min_closed_trades=min_trades,
        test_pass_rate=pass_rate,
        min_test_pass_rate=min_pass,
        min_paper_trading_days=min_days,
        frozen_features=frozen,
        criteria_met=criteria_met,
        phase1_complete=phase1_complete,
    )


def assert_feature_allowed(feature_name: str, config: dict[str, Any] | None = None) -> None:
    """
    Raise PhaseGateViolation if *feature_name* is frozen at the current phase.

    Usage::

        from phase_gate import assert_feature_allowed
        assert_feature_allowed("live_account_trading")   # raises in Phase 1

    The check is intentionally strict: unknown features are allowed (opt-in
    freeze list, not allowlist) so the gate does not break existing code paths
    that have no phase restriction.
    """
    if config is None:
        config = _load_config()

    pg = config.get("phase_gate", {})
    current_phase: int = pg.get("current_phase", 1)
    frozen: dict[str, int] = pg.get("frozen_features", {})

    required_phase = frozen.get(feature_name)
    if required_phase is None:
        return  # Feature not in the frozen list — allowed

    if current_phase < required_phase:
        raise PhaseGateViolation(
            f"Feature '{feature_name}' is frozen until Phase {required_phase}. "
            f"Current phase is {current_phase} ({PHASE_DESCRIPTIONS.get(current_phase, '?')}). "
            f"Complete Phase 1 exit criteria before advancing: "
            f"200+ closed paper trades, 80%+ test pass rate, "
            f"30+ consecutive paper trading days."
        )


def validate(config: dict[str, Any] | None = None) -> list[str]:
    """
    Validate the current config against the phase gate.  Returns a list of
    violation messages (empty = all clear).  Does NOT raise — callers decide
    whether to abort or warn.

    Checks:
    - Live account IDs are present while in Phase 1 / Phase < 4
    - aggregate_accounts is non-empty while in Phase < 4
    - Any frozen feature flag is activated below its required phase
    """
    if config is None:
        config = _load_config()

    pg = config.get("phase_gate", {})
    current_phase: int = pg.get("current_phase", 1)
    frozen: dict[str, int] = pg.get("frozen_features", {})
    violations: list[str] = []

    # Check live account IDs
    accounts: dict[str, str] = config.get("accounts", {})
    live_accounts = {k: v for k, v in accounts.items() if k.startswith("live_") and v}
    if live_accounts:
        required = frozen.get("live_account_trading", 4)
        if current_phase < required:
            violations.append(
                f"FROZEN [live_account_trading, Phase {required}]: "
                f"Live account IDs are set ({list(live_accounts.keys())}) but current_phase={current_phase}. "
                f"Live accounts must not be used for order execution until Phase {required}."
            )

    # Check aggregate_accounts
    aggregate: list = config.get("aggregate_accounts", [])
    if aggregate:
        required = frozen.get("multi_account_aggregation", 4)
        if current_phase < required:
            violations.append(
                f"FROZEN [multi_account_aggregation, Phase {required}]: "
                f"aggregate_accounts={aggregate} is non-empty but current_phase={current_phase}. "
                f"Multi-account aggregation is a Phase {required} feature."
            )

    # Check telegram kill switch is configured before live trading (Phase 4+)
    # This is a safety gate: non-technical users must have an out-of-band emergency
    # stop that works even when the web dashboard is unreachable.
    if "telegram_kill_switch" in frozen:
        tg_required = frozen["telegram_kill_switch"]
        if current_phase >= tg_required:
            tg_cfg = config.get("telegram", {})
            tg_token = tg_cfg.get("bot_token", "")
            tg_ids = tg_cfg.get("authorized_chat_ids", [])
            if not tg_token or not tg_ids:
                violations.append(
                    f"FROZEN [telegram_kill_switch, Phase {tg_required}]: "
                    f"Live trading (Phase {current_phase}) requires a configured Telegram kill switch. "
                    f"Set config['telegram']['bot_token'] (or TELEGRAM_BOT_TOKEN env var) "
                    f"and config['telegram']['authorized_chat_ids'] before enabling live accounts."
                )

    # Check any other explicitly frozen features via feature flags in config
    # (future-proof: if a key matching a frozen feature name appears in config
    #  and is truthy, warn)
    _handled = {"live_account_trading", "multi_account_aggregation", "telegram_kill_switch"}
    for feature, required_phase in frozen.items():
        if feature in _handled:
            continue  # Already checked above with richer context
        flag_value = config.get(feature)
        if flag_value:
            if current_phase < required_phase:
                violations.append(
                    f"FROZEN [{feature}, Phase {required_phase}]: "
                    f"config['{feature}']={flag_value!r} is enabled but current_phase={current_phase}."
                )

    return violations


def validate_or_raise(config: dict[str, Any] | None = None) -> None:
    """Call validate() and raise PhaseGateViolation on the first violation found."""
    violations = validate(config)
    if violations:
        raise PhaseGateViolation("\n".join(violations))
