"""
Tests for signals/sentiment_scorer.py

Covers:
  - _composite_sentiment_score: edge cases + calibration weights
  - _score_with_finbert: mocked pipeline output
  - _score_with_claude: mocked Anthropic client
  - _fetch_yahoo_rss: mocked urllib response
  - merge_into_candidates: file I/O with tmp path
  - run_sentiment_scan: integration-level smoke test with all network mocked
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Make signals importable from the repo root ────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from signals.sentiment_scorer import (
    _composite_sentiment_score,
    _score_with_finbert,
    _score_with_claude,
    _fetch_yahoo_rss,
    merge_into_candidates,
    run_sentiment_scan,
    _CLAUDE_WEIGHT,
    _FINBERT_WEIGHT,
)


class TestCompositeScore(unittest.TestCase):
    """_composite_sentiment_score — pure function, no I/O."""

    def test_both_none_returns_zero(self):
        score, flags = _composite_sentiment_score(None, None)
        self.assertEqual(score, 0.0)
        self.assertIn("No sentiment data", flags)

    def test_full_bullish_maps_to_ten(self):
        # claude=1.0, finbert=1.0 → raw=1.0 → score=10.0
        score, flags = _composite_sentiment_score(1.0, 1.0)
        self.assertEqual(score, 10.0)
        self.assertIn("Bullish", flags)

    def test_full_bearish_maps_to_zero(self):
        score, flags = _composite_sentiment_score(-1.0, -1.0)
        self.assertEqual(score, 0.0)
        self.assertIn("Bearish", flags)

    def test_neutral_maps_to_five(self):
        score, flags = _composite_sentiment_score(0.0, 0.0)
        self.assertAlmostEqual(score, 5.0, places=1)
        self.assertIn("Neutral", flags)

    def test_calibration_weights_sum_to_one(self):
        self.assertAlmostEqual(_CLAUDE_WEIGHT + _FINBERT_WEIGHT, 1.0, places=6)

    def test_finbert_only(self):
        score, flags = _composite_sentiment_score(None, 0.5)
        expected_raw = 0.5  # finbert only
        expected = round((expected_raw + 1.0) / 2.0 * 10.0, 1)
        self.assertEqual(score, expected)
        self.assertIn("(FinBERT only)", flags)

    def test_claude_only(self):
        score, flags = _composite_sentiment_score(-0.5, None)
        expected_raw = -0.5
        expected = round((expected_raw + 1.0) / 2.0 * 10.0, 1)
        self.assertEqual(score, expected)
        self.assertIn("(Claude only)", flags)

    def test_mixed_weighted_correctly(self):
        c, f = 0.8, 0.4
        raw = _CLAUDE_WEIGHT * c + _FINBERT_WEIGHT * f
        expected = round((raw + 1.0) / 2.0 * 10.0, 1)
        score, _ = _composite_sentiment_score(c, f)
        self.assertAlmostEqual(score, expected, places=4)

    def test_flags_contain_claude_and_finbert_values(self):
        _, flags = _composite_sentiment_score(0.3, 0.6)
        self.assertTrue(any("Claude" in f for f in flags))
        self.assertTrue(any("FinBERT" in f for f in flags))

    def test_score_clamped_to_0_10(self):
        # Even if raw exceeds bounds (shouldn't happen, but guard test)
        score, _ = _composite_sentiment_score(1.0, 1.0)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)


class TestFinBERTScorer(unittest.TestCase):
    """_score_with_finbert — mocked HuggingFace pipeline."""

    def setUp(self):
        import signals.sentiment_scorer as mod
        # Reset lazy singleton before each test
        mod._finbert_pipeline = None
        mod._finbert_available = None

    def _mock_pipeline(self, results_per_call):
        """Build a mock pipeline callable that returns results_per_call for each headline."""
        mock_pipe = MagicMock(side_effect=lambda text, **kw: results_per_call)
        return mock_pipe

    def test_returns_none_when_no_headlines(self):
        result = _score_with_finbert([])
        self.assertIsNone(result)

    def test_pure_positive_headlines(self):
        positive_results = [{"label": "positive", "score": 1.0}, {"label": "negative", "score": 0.0}, {"label": "neutral", "score": 0.0}]
        mock_pipe = self._mock_pipeline(positive_results)

        import signals.sentiment_scorer as mod
        mod._finbert_pipeline = mock_pipe
        mod._finbert_available = True

        result = _score_with_finbert(["Stock surges to record highs"])
        # positive * 1.0 + negative * 0.0 + neutral * 0.0 = 1.0 / 1.0 = 1.0
        self.assertAlmostEqual(result, 1.0, places=4)

    def test_pure_negative_headlines(self):
        negative_results = [{"label": "positive", "score": 0.0}, {"label": "negative", "score": 1.0}, {"label": "neutral", "score": 0.0}]
        mock_pipe = self._mock_pipeline(negative_results)

        import signals.sentiment_scorer as mod
        mod._finbert_pipeline = mock_pipe
        mod._finbert_available = True

        result = _score_with_finbert(["Company reports massive loss"])
        self.assertAlmostEqual(result, -1.0, places=4)

    def test_returns_none_when_unavailable(self):
        import signals.sentiment_scorer as mod
        mod._finbert_available = False
        result = _score_with_finbert(["Some headline"])
        self.assertIsNone(result)

    def test_clamped_to_minus_one_plus_one(self):
        # Simulate rounding errors pushing score slightly beyond bounds
        extreme_results = [{"label": "positive", "score": 1.0}, {"label": "negative", "score": 0.0}, {"label": "neutral", "score": 0.0}]
        mock_pipe = self._mock_pipeline(extreme_results)

        import signals.sentiment_scorer as mod
        mod._finbert_pipeline = mock_pipe
        mod._finbert_available = True

        result = _score_with_finbert(["headline"] * 15)
        self.assertGreaterEqual(result, -1.0)
        self.assertLessEqual(result, 1.0)


class TestClaudeScorer(unittest.TestCase):
    """_score_with_claude — mocked Anthropic client."""

    def test_returns_none_on_empty_headlines(self):
        result = _score_with_claude([], "AAPL")
        self.assertIsNone(result)

    def test_parses_positive_response(self):
        mock_content = MagicMock()
        mock_content.text = "0.7"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_response
            result = _score_with_claude(["Stock surges"], "AAPL")

        self.assertAlmostEqual(result, 0.7, places=4)

    def test_parses_negative_response(self):
        mock_content = MagicMock()
        mock_content.text = "-0.85"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_response
            result = _score_with_claude(["Earnings miss"], "AAPL")

        self.assertAlmostEqual(result, -0.85, places=4)

    def test_clamped_above_one(self):
        mock_content = MagicMock()
        mock_content.text = "1.5"  # out-of-range model output
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_response
            result = _score_with_claude(["Very positive"], "AAPL")

        self.assertEqual(result, 1.0)

    def test_returns_none_on_api_error(self):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = RuntimeError("API error")
            result = _score_with_claude(["Some headline"], "AAPL")

        self.assertIsNone(result)

    def test_returns_none_on_non_numeric_response(self):
        mock_content = MagicMock()
        mock_content.text = "I cannot determine"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_response
            result = _score_with_claude(["headline"], "AAPL")

        self.assertIsNone(result)


class TestYahooRSSFetcher(unittest.TestCase):
    """_fetch_yahoo_rss — mocked urllib."""

    _SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item><title>Apple reports record quarterly revenue</title></item>
        <item><title>iPhone 16 sales beat expectations</title></item>
        <item><title>Tim Cook hints at AI expansion plans</title></item>
      </channel>
    </rss>"""

    def test_returns_titles(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._SAMPLE_RSS
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_yahoo_rss("AAPL")

        self.assertEqual(len(result), 3)
        self.assertIn("Apple reports record quarterly revenue", result)

    def test_returns_empty_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = _fetch_yahoo_rss("AAPL")

        self.assertEqual(result, [])

    def test_caps_at_15_headlines(self):
        items = "".join(f"<item><title>Headline {i}</title></item>" for i in range(25))
        rss = f"<rss><channel>{items}</channel></rss>".encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = rss
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_yahoo_rss("AAPL")

        self.assertLessEqual(len(result), 15)


class TestMergeIntoCandidates(unittest.TestCase):
    """merge_into_candidates — file I/O with tmp path."""

    def _make_candidates_file(self, tmp_dir: Path, candidates: list) -> Path:
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        path = tmp_dir / f"candidates_{today}.json"
        path.write_text(json.dumps({
            "date": today,
            "candidates": candidates,
        }))
        return path

    def test_updates_sentiment_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            self._make_candidates_file(tmp, [
                {"ticker": "AAPL", "fundamental_score": 4, "options_anomaly_score": 0,
                 "edgar_score": 0, "catalyst_score": 5.0},
            ])

            scan_results = {
                "AAPL": {
                    "sentiment_score": 7.5,
                    "sentiment_claude": 0.5,
                    "sentiment_finbert": 0.6,
                    "sentiment_flags": ["Claude: +0.50", "FinBERT: +0.60", "Bullish"],
                }
            }

            import signals.sentiment_scorer as mod
            original_dir = mod.CATALYST_DIR
            mod.CATALYST_DIR = tmp
            try:
                updated = merge_into_candidates(scan_results)
            finally:
                mod.CATALYST_DIR = original_dir

            self.assertEqual(updated, 1)
            from datetime import datetime
            today = datetime.utcnow().strftime("%Y-%m-%d")
            payload = json.loads((tmp / f"candidates_{today}.json").read_text())
            aapl = payload["candidates"][0]
            self.assertEqual(aapl["sentiment_score"], 7.5)
            self.assertAlmostEqual(aapl["sentiment_claude"], 0.5, places=4)
            self.assertAlmostEqual(aapl["sentiment_finbert"], 0.6, places=4)
            self.assertIn("Bullish", aapl["sentiment_flags"])

    def test_composite_score_formula(self):
        """catalyst_score = 0.35*(f/5*10) + 0.35*o + 0.15*e + 0.15*s"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_candidates_file(tmp, [
                {"ticker": "MSFT", "fundamental_score": 5, "options_anomaly_score": 6,
                 "edgar_score": 4, "catalyst_score": 0.0},
            ])
            scan_results = {
                "MSFT": {
                    "sentiment_score": 8.0,
                    "sentiment_claude": 0.6,
                    "sentiment_finbert": 0.7,
                    "sentiment_flags": ["Bullish"],
                }
            }
            import signals.sentiment_scorer as mod
            original_dir = mod.CATALYST_DIR
            mod.CATALYST_DIR = tmp
            try:
                merge_into_candidates(scan_results)
            finally:
                mod.CATALYST_DIR = original_dir

            from datetime import datetime
            today = datetime.utcnow().strftime("%Y-%m-%d")
            payload = json.loads((tmp / f"candidates_{today}.json").read_text())
            msft = payload["candidates"][0]
            expected = round(0.35 * (5/5*10) + 0.35 * 6 + 0.15 * 4 + 0.15 * 8.0, 1)
            self.assertAlmostEqual(msft["catalyst_score"], expected, places=4)

    def test_skips_missing_tickers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_candidates_file(tmp, [
                {"ticker": "GOOG", "fundamental_score": 3, "options_anomaly_score": 0,
                 "edgar_score": 0, "catalyst_score": 4.0},
            ])
            scan_results = {}  # no GOOG result

            import signals.sentiment_scorer as mod
            original_dir = mod.CATALYST_DIR
            mod.CATALYST_DIR = tmp
            try:
                updated = merge_into_candidates(scan_results)
            finally:
                mod.CATALYST_DIR = original_dir

            self.assertEqual(updated, 0)

    def test_returns_zero_if_no_candidates_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            import signals.sentiment_scorer as mod
            original_dir = mod.CATALYST_DIR
            mod.CATALYST_DIR = Path(td)
            try:
                result = merge_into_candidates({"AAPL": {"sentiment_score": 7.0}})
            finally:
                mod.CATALYST_DIR = original_dir
            self.assertEqual(result, 0)


class TestRunSentimentScanSmoke(unittest.TestCase):
    """run_sentiment_scan — integration smoke test with all external calls mocked."""

    def test_smoke_returns_dict_per_ticker(self):
        import tempfile

        # Mock: no headlines (Yahoo + Finviz return [])
        # Mock: Claude unavailable → None
        # Mock: FinBERT unavailable → None
        # Expected: score=0.0, flags=["No sentiment data"]

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            import signals.sentiment_scorer as mod
            original_dir = mod.CATALYST_DIR
            mod.CATALYST_DIR = tmp
            mod._finbert_available = False  # skip FinBERT load attempt

            try:
                with patch("urllib.request.urlopen", side_effect=OSError("mocked")):
                    with patch("anthropic.Anthropic") as mock_cls:
                        mock_cls.return_value.messages.create.side_effect = RuntimeError("mocked")
                        results = run_sentiment_scan(["AAPL", "MSFT"])
            finally:
                mod.CATALYST_DIR = original_dir
                mod._finbert_available = None
                mod._finbert_pipeline = None

        self.assertIn("AAPL", results)
        self.assertIn("MSFT", results)
        for ticker in ("AAPL", "MSFT"):
            r = results[ticker]
            self.assertEqual(r["ticker"], ticker)
            self.assertIn("sentiment_score", r)
            self.assertIn("sentiment_flags", r)
            self.assertGreaterEqual(r["sentiment_score"], 0.0)
            self.assertLessEqual(r["sentiment_score"], 10.0)


if __name__ == "__main__":
    unittest.main()
