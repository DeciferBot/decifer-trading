"""
macro_transmission_matrix.py — deterministic macro-to-theme transmission engine.

Single responsibility: load transmission_rules.json and fire matching rules
against a caller-supplied driver_state. No LLM involvement. No side effects
on the live bot. Writes no files.

Public surface:
    TransmissionResult       — structured output dataclass
    MacroTransmissionMatrix  — loads rules once, exposes fire()

    fire_transmission(driver_state, rules_path) -> TransmissionResult
        Convenience function for one-shot callers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FiredRule:
    rule_id: str
    driver: str
    driver_alias: str
    output_type: str
    affected_targets: list[str]
    direction: str
    strength: float
    confidence: float
    horizon: str
    reason: str


@dataclass
class BlockedRule:
    rule_id: str
    driver: str
    blocked_by: list[str]


@dataclass
class TransmissionResult:
    theme_tailwinds: list[str] = field(default_factory=list)
    theme_headwinds: list[str] = field(default_factory=list)
    sector_tailwinds: list[str] = field(default_factory=list)
    sector_headwinds: list[str] = field(default_factory=list)
    transmission_rules_fired: list[FiredRule] = field(default_factory=list)
    blocked_rules: list[BlockedRule] = field(default_factory=list)
    skipped_rules: list[str] = field(default_factory=list)
    active_drivers: list[str] = field(default_factory=list)
    live_output_changed: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme_tailwinds":           self.theme_tailwinds,
            "theme_headwinds":           self.theme_headwinds,
            "sector_tailwinds":          self.sector_tailwinds,
            "sector_headwinds":          self.sector_headwinds,
            "transmission_rules_fired":  [vars(r) for r in self.transmission_rules_fired],
            "blocked_rules":             [vars(b) for b in self.blocked_rules],
            "skipped_rules":             self.skipped_rules,
            "active_drivers":            self.active_drivers,
            "live_output_changed":       self.live_output_changed,
            "errors":                    self.errors,
        }


class MacroTransmissionMatrix:
    """
    Loads transmission_rules.json once and fires matching rules deterministically.

    driver_state schema:
        {
          "active_drivers": ["ai_capex_growth", ...],      # driver or driver_alias values
          "blocked_conditions": ["capex_guidance_cut", ...]  # blocked_if values present
        }
    """

    def __init__(self, rules_path: str = "data/intelligence/transmission_rules.json") -> None:
        self._rules_path = rules_path
        self._rules: list[dict] = []
        self._load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._rules_path):
            self._load_error = f"transmission_rules.json not found: {self._rules_path}"
            return
        try:
            with open(self._rules_path, encoding="utf-8") as f:
                data = json.load(f)
            self._rules = data.get("rules", [])
        except (json.JSONDecodeError, OSError) as e:
            self._load_error = f"Failed to load {self._rules_path}: {e}"

    def fire(self, driver_state: dict[str, Any]) -> TransmissionResult:
        result = TransmissionResult()

        if self._load_error:
            result.errors.append(self._load_error)
            return result

        active_drivers: set[str] = set(driver_state.get("active_drivers") or [])
        blocked_conditions: set[str] = set(driver_state.get("blocked_conditions") or [])
        result.active_drivers = sorted(active_drivers)

        for rule in self._rules:
            rule_id = rule.get("rule_id", "")
            driver = rule.get("driver", "")
            driver_alias = rule.get("driver_alias", "")

            # Rule fires when driver or driver_alias appears in active_drivers
            driver_match = driver in active_drivers or driver_alias in active_drivers
            if not driver_match:
                result.skipped_rules.append(rule_id)
                continue

            # Check blocked_if conditions
            blocked_by = [c for c in (rule.get("blocked_if") or []) if c in blocked_conditions]
            if blocked_by:
                result.blocked_rules.append(BlockedRule(
                    rule_id=rule_id,
                    driver=driver,
                    blocked_by=blocked_by,
                ))
                continue

            # Rule fires — accumulate targets by output type
            output_type = rule.get("output_type", "")
            targets = rule.get("affected_targets") or []

            if output_type == "theme_tailwind":
                result.theme_tailwinds.extend(t for t in targets if t not in result.theme_tailwinds)
            elif output_type == "theme_headwind":
                result.theme_headwinds.extend(t for t in targets if t not in result.theme_headwinds)
            elif output_type == "sector_tailwind":
                result.sector_tailwinds.extend(t for t in targets if t not in result.sector_tailwinds)
            elif output_type == "sector_headwind":
                result.sector_headwinds.extend(t for t in targets if t not in result.sector_headwinds)

            result.transmission_rules_fired.append(FiredRule(
                rule_id=rule_id,
                driver=driver,
                driver_alias=driver_alias,
                output_type=output_type,
                affected_targets=targets,
                direction=rule.get("direction", ""),
                strength=float(rule.get("strength", 0.0)),
                confidence=float(rule.get("confidence", 0.0)),
                horizon=rule.get("horizon", ""),
                reason=rule.get("reason", ""),
            ))

        return result


def fire_transmission(
    driver_state: dict[str, Any],
    rules_path: str = "data/intelligence/transmission_rules.json",
) -> TransmissionResult:
    """Convenience one-shot function. Creates a matrix instance and fires rules."""
    matrix = MacroTransmissionMatrix(rules_path=rules_path)
    return matrix.fire(driver_state)
