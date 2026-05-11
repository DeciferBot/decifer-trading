"""
tests/test_av_multi_ticker_guard.py

Tests for AV multi-ticker guard and Error Message handling.
All tests mock requests.get — no real AV calls are made.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import alpha_vantage_client as av


# ── Helpers ────────────────────────────────────────────────────────────────────


def _ok_response(data: object) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = data
    m.close.return_value = None
    return m


def _minimal_feed(ticker: str = "AAPL") -> dict:
    return {
        "feed": [
            {
                "title": "Test headline",
                "time_published": "20260407T130000",
                "topics": [{"topic": "Earnings"}],
                "ticker_sentiment": [
                    {
                        "ticker": ticker,
                        "relevance_score": "0.85",
                        "ticker_sentiment_score": "0.42",
                        "ticker_sentiment_label": "Somewhat-Bullish",
                    }
                ],
            }
        ]
    }


# ── Multi-ticker guard ─────────────────────────────────────────────────────────


class TestAvMultiTickerGuard:
    def setup_method(self):
        av._news_cache.clear()

    def test_multi_ticker_does_not_call_provider(self):
        with (
            patch.object(av, "_api_key", return_value="FAKE"),
            patch("alpha_vantage_client.requests.get") as mock_get,
        ):
            av.get_news_sentiment(["AAPL", "MSFT", "TSLA"])

        mock_get.assert_not_called()

    def test_multi_ticker_returns_empty_dict(self):
        with patch.object(av, "_api_key", return_value="FAKE"):
            result = av.get_news_sentiment(["AAPL", "MSFT"])
        assert result == {}

    def test_multi_ticker_emits_skip_log(self, caplog):
        with (
            patch.object(av, "_api_key", return_value="FAKE"),
            caplog.at_level(logging.INFO, logger="decifer.alphavantage"),
        ):
            av.get_news_sentiment(["AAPL", "MSFT"])

        assert any("AV news sentiment skipped" in r.message for r in caplog.records)

    def test_single_ticker_calls_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
        av._news_cache.clear()

        with (
            patch.object(av, "_api_key", return_value="FAKE"),
            patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
            patch("alpha_vantage_client.requests.get", return_value=_ok_response(_minimal_feed("AAPL"))) as mock_get,
        ):
            result = av.get_news_sentiment(["AAPL"])

        mock_get.assert_called_once()
        assert "AAPL" in result

    def test_single_ticker_error_message_logged_and_cached(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
        av._news_cache.clear()

        with (
            patch.object(av, "_api_key", return_value="FAKE"),
            patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
            patch("alpha_vantage_client.requests.get", return_value=_ok_response({"Error Message": "Invalid inputs"})),
            caplog.at_level(logging.WARNING, logger="decifer.alphavantage"),
        ):
            result = av.get_news_sentiment(["AAPL"])

        assert result == {}
        assert any("AV API message:" in r.message for r in caplog.records)
        # Should be cached so second call doesn't hit network
        cache_key = "AAPL"
        assert cache_key in av._news_cache

    def test_articles_error_message_logged(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
        import alpha_vantage_client
        alpha_vantage_client._articles_cache = (None, 0.0)

        with (
            patch.object(av, "_api_key", return_value="FAKE"),
            patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
            patch("alpha_vantage_client.requests.get", return_value=_ok_response({"Error Message": "API limit"})),
            caplog.at_level(logging.WARNING, logger="decifer.alphavantage"),
        ):
            result = av.get_news_articles(["AAPL"])

        assert result == []
        assert any("AV API message (articles):" in r.message for r in caplog.records)
