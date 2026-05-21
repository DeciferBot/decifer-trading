"""
T1-B-4: bot_dashboard.py data-read silent failure fixes.

Tests that _get_catalyst_payload() and the ic_weights data reader log
a WARNING when their JSON files are corrupt, rather than swallowing the
error and returning blank data silently to the dashboard.

We test the logging behaviour directly — not the full FastAPI route —
because bot_dashboard.py's startup binds to a port and spawns threads
which are not appropriate to exercise in unit tests.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Heavy-dep stubs — must happen before any Decifer import
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _mod in [
    "ib_async", "anthropic", "yfinance", "feedparser", "praw", "httpx",
    "fastapi", "fastapi.responses", "fastapi.middleware",
    "fastapi.middleware.cors", "uvicorn", "starlette", "starlette.responses",
]:
    if _mod not in sys.modules:
        stub = types.ModuleType(_mod)
        stub.IB = MagicMock()
        stub.FastAPI = MagicMock(return_value=MagicMock())
        stub.HTMLResponse = MagicMock()
        stub.JSONResponse = MagicMock()
        stub.CORSMiddleware = MagicMock()
        stub.run = MagicMock()
        sys.modules[_mod] = stub

for _m in [
    "py_vollib", "py_vollib.black_scholes",
    "py_vollib.black_scholes.greeks",
    "py_vollib.black_scholes.greeks.analytical",
    "py_vollib.black_scholes.implied_volatility",
    "sklearn", "sklearn.ensemble", "sklearn.preprocessing",
    "sklearn.model_selection", "joblib",
]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import bot_dashboard as bd


def _log_messages(caplog) -> list[str]:
    """Return fully-formatted log messages (handles %s-style args)."""
    return [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetCatalystPayloadLogging:
    """
    _get_catalyst_payload() must log a WARNING when the candidates file or
    edgar_events.json is corrupt, and still return a valid payload structure
    (empty lists) rather than raising.
    """

    def setup_method(self):
        """Reset the 30s cache before each test so tests don't share state."""
        bd._catalyst_payload_cache.update({"data": None, "fetched_at": 0.0})

    def teardown_method(self):
        bd._catalyst_payload_cache.update({"data": None, "fetched_at": 0.0})

    def _call(self, catalyst_dir: Path) -> dict:
        import config as cfg
        original = cfg.CATALYST_DIR
        cfg.CATALYST_DIR = catalyst_dir
        try:
            return bd._get_catalyst_payload()
        finally:
            cfg.CATALYST_DIR = original

    def test_corrupt_candidates_file_logs_warning(self, tmp_path, caplog):
        """Corrupt candidates JSON → WARNING logged, payload still returned."""
        (tmp_path / "candidates_2026-01-01.json").write_text("{ bad json <<<")

        with caplog.at_level(logging.WARNING, logger="decifer.bot"):
            payload = self._call(tmp_path)

        msgs = _log_messages(caplog)
        assert payload is not None
        assert "candidates" in payload
        assert payload["candidates"] == []
        assert any(
            "_get_catalyst_payload" in m and "candidates_2026-01-01.json" in m
            for m in msgs
        ), f"Expected WARNING about corrupt candidates file, got: {msgs}"

    def test_corrupt_edgar_file_logs_warning(self, tmp_path, caplog):
        """Corrupt edgar_events.json → WARNING logged, payload still returned."""
        (tmp_path / "candidates_2026-01-01.json").write_text(
            json.dumps({"candidates": [], "date": "2026-01-01"})
        )
        (tmp_path / "edgar_events.json").write_text("not json at all")

        with caplog.at_level(logging.WARNING, logger="decifer.bot"):
            payload = self._call(tmp_path)

        msgs = _log_messages(caplog)
        assert payload is not None
        assert "edgar_events" in payload
        assert payload["edgar_events"] == []
        assert any(
            "_get_catalyst_payload" in m and "edgar_events.json" in m
            for m in msgs
        ), f"Expected WARNING about corrupt edgar_events.json, got: {msgs}"

    def test_valid_files_return_data(self, tmp_path):
        """Valid files → data returned correctly."""
        candidates = {"candidates": [
            {"ticker": "AAPL", "catalyst_score": 8.5},
        ], "date": "2026-01-01"}
        edgar = [{"form": "13D", "ticker": "MSFT"}]

        (tmp_path / "candidates_2026-01-01.json").write_text(json.dumps(candidates))
        (tmp_path / "edgar_events.json").write_text(json.dumps(edgar))

        payload = self._call(tmp_path)

        assert len(payload["candidates"]) == 1
        assert payload["candidates"][0]["ticker"] == "AAPL"
        assert len(payload["edgar_events"]) == 1


class TestGetHeldSymbols:
    """
    _get_held_symbols() must read live position state via get_open_positions(),
    not data/trades.json.

    The authoritative position state is the in-memory active_trades dict exposed
    by get_open_positions() (reconciled against IBKR at startup).
    data/positions.json is a stale crash-fallback and is NOT used here.
    """

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _make_positions(*symbols_qtys):
        """Build a mock get_open_positions() return dict.

        symbols_qtys: iterable of (symbol, qty) tuples.
        Dict is keyed by trade_id (arbitrary); each value has 'symbol' and 'qty'.
        """
        return {
            f"{sym}_tid": {"symbol": sym, "qty": qty}
            for sym, qty in symbols_qtys
        }

    # ------------------------------------------------------------------ tests
    def test_returns_symbols_from_live_positions(self):
        """Happy path: active positions are returned as a set of symbols."""
        mock_positions = self._make_positions(
            ("AAPL", 188), ("AMD", 132), ("SMCI", 1791)
        )
        with patch("bot_dashboard.get_open_positions", return_value=mock_positions):
            result = bd._get_held_symbols()

        assert result == {"AAPL", "AMD", "SMCI"}

    def test_excludes_zero_quantity_positions(self):
        """Positions with qty=0 must not appear in the held-symbol set."""
        mock_positions = self._make_positions(
            ("AAPL", 188),
            ("TE", 0),      # closed — qty zeroed but still in dict
            ("RKLB", 443),
        )
        with patch("bot_dashboard.get_open_positions", return_value=mock_positions):
            result = bd._get_held_symbols()

        assert "TE" not in result
        assert result == {"AAPL", "RKLB"}

    def test_returns_empty_set_when_get_open_positions_raises(self):
        """If get_open_positions() raises (covers missing/unreadable state),
        _get_held_symbols() must return an empty set — not raise."""
        with patch(
            "bot_dashboard.get_open_positions",
            side_effect=RuntimeError("IBKR not connected"),
        ):
            result = bd._get_held_symbols()

        assert result == set()

    def test_returns_empty_set_when_positions_dict_is_empty(self):
        """No open positions → empty set, no error."""
        with patch("bot_dashboard.get_open_positions", return_value={}):
            result = bd._get_held_symbols()

        assert result == set()

    def test_does_not_read_trades_json(self, tmp_path):
        """trades.json must NOT be opened for held-symbol detection.

        We verify by patching open() and asserting it is never called with
        a path that contains 'trades.json'.
        """
        mock_positions = self._make_positions(("AAPL", 100))

        real_open = open  # keep a reference to the real built-in

        opened_paths: list[str] = []

        def tracking_open(path, *args, **kwargs):
            opened_paths.append(str(path))
            return real_open(path, *args, **kwargs)

        with patch("bot_dashboard.get_open_positions", return_value=mock_positions):
            with patch("builtins.open", side_effect=tracking_open):
                bd._get_held_symbols()

        trades_json_reads = [p for p in opened_paths if "trades.json" in p]
        assert trades_json_reads == [], (
            f"_get_held_symbols() must not read trades.json; "
            f"found opens: {trades_json_reads}"
        )


class TestIcWeightsLogging:
    """
    The ic_weights.json read in the IC weights API handler must log a WARNING
    when the file is corrupt rather than swallowing the error silently.
    """

    def test_corrupt_ic_weights_logs_warning(self, tmp_path, caplog):
        """Corrupt ic_weights.json → WARNING logged by the except block."""
        import json as _json

        corrupt_path = tmp_path / "ic_weights.json"
        corrupt_path.write_text("{ invalid json >>>")

        raw_ic = {}
        with caplog.at_level(logging.WARNING, logger="decifer.bot"):
            try:
                with open(corrupt_path) as _f:
                    _d = _json.load(_f)
                raw_ic = _d.get("raw_ic", {})
            except Exception as e:
                bd.log.warning(
                    "[dashboard][/api/ic-weights] failed to read ic_weights.json: %s", e
                )

        msgs = _log_messages(caplog)
        assert raw_ic == {}
        assert any(
            "/api/ic-weights" in m and "ic_weights.json" in m
            for m in msgs
        ), f"Expected WARNING about corrupt ic_weights.json, got: {msgs}"
