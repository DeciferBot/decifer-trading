"""
Tests for earnings_transcript_engine.py

Covers:
- _current_quarter() returns correct year/quarter
- _extract_intelligence() handles malformed LLM output gracefully
- process_symbol() suppresses low-confidence events
- process_recent_earnings() caps at _MAX_TRANSCRIPTS_PER_RUN
- Emitted event has required fields
- No imports from execution / broker / orders layers
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import types
import unittest
from datetime import date
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_engine():
    """Import module with fmp_client and anthropic stubbed out."""
    # Stub anthropic
    if "anthropic" not in sys.modules:
        stub = types.ModuleType("anthropic")
        stub.Anthropic = MagicMock
        sys.modules["anthropic"] = stub

    # Stub fmp_client at the import level used inside the engine
    if "fmp_client" not in sys.modules:
        stub = types.ModuleType("fmp_client")
        stub._get = MagicMock(return_value=None)
        stub.get_earnings_calendar = MagicMock(return_value=[])
        sys.modules["fmp_client"] = stub

    # Stub macro_event_layer constants used in _emit_event
    if "macro_event_layer" not in sys.modules:
        stub = types.ModuleType("macro_event_layer")
        stub._STORE_PATH = "/dev/null"
        import threading
        stub._LOCK = threading.Lock()
        sys.modules["macro_event_layer"] = stub

    import importlib
    if "earnings_transcript_engine" in sys.modules:
        del sys.modules["earnings_transcript_engine"]

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import earnings_transcript_engine as ete
    return ete


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestCurrentQuarter(unittest.TestCase):
    def test_q1(self):
        ete = _load_engine()
        y, q = ete._current_quarter(date(2026, 2, 15))
        self.assertEqual(q, 1)
        self.assertEqual(y, 2026)

    def test_q2(self):
        ete = _load_engine()
        _, q = ete._current_quarter(date(2026, 5, 31))
        self.assertEqual(q, 2)

    def test_q3(self):
        ete = _load_engine()
        _, q = ete._current_quarter(date(2026, 8, 1))
        self.assertEqual(q, 3)

    def test_q4(self):
        ete = _load_engine()
        _, q = ete._current_quarter(date(2026, 11, 30))
        self.assertEqual(q, 4)


class TestExtractIntelligence(unittest.TestCase):
    def _ete(self):
        return _load_engine()

    def test_valid_extraction(self):
        ete = self._ete()
        good_response = json.dumps({
            "guidance_direction": "raised",
            "tone": "confident",
            "key_topics": ["AI capex", "margins"],
            "forward_outlook": "Management raised full-year guidance citing strong data-center demand.",
            "diverges_from_headline": False,
            "confidence": 0.85,
        })
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=good_response)]
        )
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = ete._extract_intelligence("NVDA", "Q1 2026", "Prepared remarks...")
        self.assertIsNotNone(result)
        self.assertEqual(result["guidance_direction"], "raised")
        self.assertEqual(result["tone"], "confident")

    def test_invalid_guidance_direction_returns_none(self):
        ete = self._ete()
        bad_response = json.dumps({
            "guidance_direction": "invented_value",
            "tone": "neutral",
            "key_topics": [],
            "forward_outlook": "Something.",
            "diverges_from_headline": False,
            "confidence": 0.8,
        })
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=bad_response)]
        )
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = ete._extract_intelligence("AAPL", "Q2 2026", "text")
        self.assertIsNone(result)

    def test_invalid_tone_returns_none(self):
        ete = self._ete()
        bad_response = json.dumps({
            "guidance_direction": "maintained",
            "tone": "bullish",  # not in allowed set
            "key_topics": [],
            "forward_outlook": "Something.",
            "diverges_from_headline": False,
            "confidence": 0.8,
        })
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=bad_response)]
        )
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = ete._extract_intelligence("MSFT", "Q2 2026", "text")
        self.assertIsNone(result)

    def test_json_parse_error_returns_none(self):
        ete = self._ete()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="not valid json at all")]
        )
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = ete._extract_intelligence("X", "Q1 2026", "text")
        self.assertIsNone(result)

    def test_llm_exception_returns_none(self):
        ete = self._ete()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("network error")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = ete._extract_intelligence("X", "Q1 2026", "text")
        self.assertIsNone(result)

    def test_strips_markdown_fences(self):
        ete = self._ete()
        fenced = "```json\n" + json.dumps({
            "guidance_direction": "lowered",
            "tone": "cautious",
            "key_topics": ["margins"],
            "forward_outlook": "Margin pressure.",
            "diverges_from_headline": False,
            "confidence": 0.75,
        }) + "\n```"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=fenced)]
        )
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = ete._extract_intelligence("META", "Q2 2026", "text")
        self.assertIsNotNone(result)
        self.assertEqual(result["guidance_direction"], "lowered")


class TestProcessSymbol(unittest.TestCase):
    def _ete(self):
        return _load_engine()

    def _mock_intelligence(self, guidance="raised", tone="confident", confidence=0.9, diverges=False):
        return {
            "guidance_direction": guidance,
            "tone": tone,
            "key_topics": ["AI capex", "data center"],
            "forward_outlook": "Management raised full-year guidance.",
            "diverges_from_headline": diverges,
            "confidence": confidence,
        }

    def test_no_transcript_returns_none(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value=None):
            result = ete.process_symbol("NVDA", 2026, 1)
        self.assertIsNone(result)

    def test_low_confidence_suppressed(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value="transcript text"):
            with patch.object(ete, "_extract_intelligence",
                              return_value=self._mock_intelligence(confidence=0.30)):
                with patch.object(ete, "_emit_event") as mock_emit:
                    result = ete.process_symbol("NVDA", 2026, 1)
        self.assertIsNone(result)
        mock_emit.assert_not_called()

    def test_good_extraction_emits_event(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value="transcript text"):
            with patch.object(ete, "_extract_intelligence",
                              return_value=self._mock_intelligence()):
                with patch.object(ete, "_emit_event") as mock_emit:
                    result = ete.process_symbol("NVDA", 2026, 1)
        self.assertIsNotNone(result)
        mock_emit.assert_called_once()

    def test_event_has_required_fields(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value="transcript text"):
            with patch.object(ete, "_extract_intelligence",
                              return_value=self._mock_intelligence()):
                with patch.object(ete, "_emit_event"):
                    result = ete.process_symbol("NVDA", 2026, 1)
        required = [
            "event_type", "symbol", "period", "headline",
            "guidance_direction", "tone", "key_topics",
            "forward_outlook", "diverges_from_headline", "confidence",
        ]
        for field in required:
            self.assertIn(field, result, f"missing field: {field}")

    def test_event_type_is_earnings_call_guidance(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value="transcript text"):
            with patch.object(ete, "_extract_intelligence",
                              return_value=self._mock_intelligence()):
                with patch.object(ete, "_emit_event"):
                    result = ete.process_symbol("AAPL", 2026, 2)
        self.assertEqual(result["event_type"], "earnings_call_guidance")

    def test_diverges_flag_in_headline(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value="transcript text"):
            with patch.object(ete, "_extract_intelligence",
                              return_value=self._mock_intelligence(diverges=True)):
                with patch.object(ete, "_emit_event"):
                    result = ete.process_symbol("TSLA", 2026, 1)
        self.assertIn("diverges", result["headline"])

    def test_extraction_failure_returns_none(self):
        ete = self._ete()
        with patch.object(ete, "_fetch_transcript", return_value="some text"):
            with patch.object(ete, "_extract_intelligence", return_value=None):
                result = ete.process_symbol("X", 2026, 1)
        self.assertIsNone(result)


class TestProcessRecentEarnings(unittest.TestCase):
    def _ete(self):
        return _load_engine()

    def test_empty_universe_returns_empty(self):
        ete = self._ete()
        result = ete.process_recent_earnings([])
        self.assertEqual(result, [])

    def test_no_calendar_hits_returns_empty(self):
        ete = self._ete()
        with patch("fmp_client._get", return_value=[]):
            result = ete.process_recent_earnings(["NVDA", "AAPL"])
        self.assertEqual(result, [])

    def test_symbol_not_in_universe_skipped(self):
        ete = self._ete()
        calendar = [{"symbol": "GOOGL", "date": str(date.today())}]
        with patch("fmp_client._get", return_value=calendar):
            with patch.object(ete, "process_symbol") as mock_ps:
                result = ete.process_recent_earnings(["NVDA", "AAPL"])  # GOOGL not in universe
        mock_ps.assert_not_called()
        self.assertEqual(result, [])

    def test_caps_at_max_transcripts(self):
        ete = self._ete()
        many_symbols = [f"SYM{i}" for i in range(20)]
        today_str = str(date.today())
        calendar = [{"symbol": s, "date": today_str} for s in many_symbols]
        with patch("fmp_client._get", return_value=calendar):
            with patch.object(ete, "process_symbol", return_value={"event_type": "earnings_call_guidance"}):
                result = ete.process_recent_earnings(many_symbols)
        self.assertLessEqual(len(result), ete._MAX_TRANSCRIPTS_PER_RUN)


class TestLayerBoundary(unittest.TestCase):
    """Earnings transcript engine must not import from execution/broker layers."""

    def test_no_execution_imports(self):
        import ast
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "earnings_transcript_engine.py",
        )
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        forbidden = {
            "bot_ibkr", "orders_core", "orders_state", "execute_buy",
            "execute_sell", "bot_trading", "pm_engine", "pm_rails",
        }
        found = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name.split(".")[0]
                        if name in forbidden:
                            found.append(name)
                else:
                    name = (node.module or "").split(".")[0]
                    if name in forbidden:
                        found.append(name)
        self.assertEqual(found, [], f"Forbidden imports found: {found}")


if __name__ == "__main__":
    unittest.main()
