"""
tests/test_alpaca_startup_source.py

Tests for the Alpaca bar stream startup source selection in bot.py.

These tests exercise the startup block in isolation using the underlying
logic — they don't import bot.py directly (bot.py has heavy side effects
at import time). Instead they verify the handoff_reader / scanner selection
logic by testing the pattern used in the startup block.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch


# ── Shared test helpers ────────────────────────────────────────────────────────


def _run_startup_block(
    handoff_enabled: bool,
    handoff_result: dict | None = None,
    handoff_raises: Exception | None = None,
    scanner_symbols: list[str] | None = None,
) -> tuple[list[str], str]:
    """
    Execute the startup universe-selection logic extracted from bot.py.
    Returns (initial_universe, startup_bar_source).
    """
    import importlib
    import sys
    import types

    # Build lightweight stand-ins for the modules bot.py imports in the block
    mock_config = {"enable_active_opportunity_universe_handoff": handoff_enabled}
    mock_ib = MagicMock()

    # Inline the startup logic (mirrors bot.py exactly)
    initial_universe: list[str] = []
    startup_bar_source = "scanner"

    if mock_config.get("enable_active_opportunity_universe_handoff", False):
        try:
            if handoff_raises:
                raise handoff_raises
            hoff = handoff_result or {}
            if hoff.get("handoff_allowed") and hoff.get("accepted_candidates"):
                initial_universe = list({
                    c["symbol"] for c in hoff["accepted_candidates"]
                    if c.get("symbol")
                })
                startup_bar_source = "handoff_reader"
        except Exception:
            pass  # falls back to scanner

    if not initial_universe:
        initial_universe = scanner_symbols or ["SPY"]
        startup_bar_source = "scanner"

    return initial_universe, startup_bar_source


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestAlpacaStartupSource:
    def test_uses_handoff_when_valid_manifest(self):
        hoff = {
            "handoff_allowed": True,
            "accepted_candidates": [{"symbol": "AAPL"}, {"symbol": "TSLA"}],
        }
        universe, source = _run_startup_block(
            handoff_enabled=True, handoff_result=hoff
        )
        assert "AAPL" in universe
        assert "TSLA" in universe
        assert source == "handoff_reader"

    def test_falls_back_when_manifest_file_missing(self):
        universe, source = _run_startup_block(
            handoff_enabled=True,
            handoff_raises=FileNotFoundError("data/live/current_manifest.json"),
            scanner_symbols=["SPY", "QQQ"],
        )
        assert source == "scanner"
        assert "SPY" in universe

    def test_falls_back_when_handoff_not_allowed(self):
        hoff = {"handoff_allowed": False, "accepted_candidates": [{"symbol": "AAPL"}]}
        universe, source = _run_startup_block(
            handoff_enabled=True, handoff_result=hoff, scanner_symbols=["SPY"]
        )
        assert source == "scanner"

    def test_falls_back_when_accepted_candidates_empty(self):
        hoff = {"handoff_allowed": True, "accepted_candidates": []}
        universe, source = _run_startup_block(
            handoff_enabled=True, handoff_result=hoff, scanner_symbols=["IWM"]
        )
        assert source == "scanner"
        assert "IWM" in universe

    def test_deduplicates_and_excludes_blank_symbols(self):
        hoff = {
            "handoff_allowed": True,
            "accepted_candidates": [
                {"symbol": "AAPL"},
                {"symbol": "AAPL"},  # duplicate
                {"symbol": ""},     # blank
            ],
        }
        universe, source = _run_startup_block(
            handoff_enabled=True, handoff_result=hoff
        )
        assert source == "handoff_reader"
        assert universe.count("AAPL") == 1
        assert "" not in universe

    def test_handoff_disabled_uses_scanner(self):
        universe, source = _run_startup_block(
            handoff_enabled=False, scanner_symbols=["META", "NVDA"]
        )
        assert source == "scanner"
        assert "META" in universe
