"""
tests/test_alpha_vantage_client.py

Tests for alpha_vantage_client.py.

All tests run without a real API key — every test patches out HTTP calls
or exercises the no-key / rate-limit paths.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import alpha_vantage_client as av

# ── Test key constants ─────────────────────────────────────────────────────────
# _consume_call uses key[-8:] as the per-key state ID in the rate limit file.
_TEST_KEY = "FAKE_TEST_KEY_12345678"
_TEST_KID = _TEST_KEY[-8:]  # "12345678"

# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_news_response(ticker: str = "AAPL") -> dict:
    """Minimal AV NEWS_SENTIMENT response for one ticker."""
    return {
        "feed": [
            {
                "title": "Apple beats earnings",
                "time_published": "20260407T130000",
                "topics": [{"topic": "Earnings", "relevance_score": "0.9"}],
                "ticker_sentiment": [
                    {
                        "ticker": ticker,
                        "relevance_score": "0.85",
                        "ticker_sentiment_score": "0.42",
                        "ticker_sentiment_label": "Somewhat-Bullish",
                    }
                ],
            },
            {
                "title": "Tech sector outlook positive",
                "time_published": "20260407T120000",
                "topics": [{"topic": "Technology", "relevance_score": "0.6"}],
                "ticker_sentiment": [
                    {
                        "ticker": ticker,
                        "relevance_score": "0.60",
                        "ticker_sentiment_score": "0.30",
                        "ticker_sentiment_label": "Somewhat-Bullish",
                    }
                ],
            },
        ]
    }


def _mock_earnings_csv() -> str:
    return (
        "symbol,name,reportDate,fiscalDateEnding,estimate,currency\r\n"
        "AAPL,Apple Inc,2026-05-01,2026-03-31,1.50,USD\r\n"
        "MSFT,Microsoft Corp,2026-04-25,2026-03-31,2.80,USD\r\n"
    )


# ── No-key guard ───────────────────────────────────────────────────────────────


def test_get_news_sentiment_no_key_returns_empty():
    with patch.object(av, "_api_key", return_value=""):
        result = av.get_news_sentiment(["AAPL", "MSFT"])
    assert result == {}


def test_get_earnings_calendar_no_key_returns_empty():
    with patch.object(av, "_api_key", return_value=""):
        result = av.get_earnings_calendar()
    assert result == {}


def test_get_news_sentiment_empty_list_returns_empty():
    result = av.get_news_sentiment([])
    assert result == {}


# ── Rate limiter ───────────────────────────────────────────────────────────────


def test_consume_call_respects_daily_limit(tmp_path, monkeypatch):
    limit_file = tmp_path / "av_rate_limit.json"
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(limit_file))

    today = date.today().isoformat()
    # New format: per-key state using last 8 chars of key as ID
    limit_file.write_text(json.dumps({"date": today, _TEST_KID: 25}))

    with (
        patch.object(av, "_api_keys", return_value=[_TEST_KEY]),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
    ):
        result = av._consume_call()

    assert result == ""


def test_consume_call_resets_on_new_day(tmp_path, monkeypatch):
    limit_file = tmp_path / "av_rate_limit.json"
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(limit_file))

    # Stale entry from yesterday — new per-key format
    limit_file.write_text(json.dumps({"date": "2000-01-01", _TEST_KID: 25}))

    with (
        patch.object(av, "_api_keys", return_value=[_TEST_KEY]),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
    ):
        result = av._consume_call()

    assert result == _TEST_KEY
    state = json.loads(limit_file.read_text())
    assert state["date"] == date.today().isoformat()
    assert state[_TEST_KID] == 1


def test_consume_call_increments_counter(tmp_path, monkeypatch):
    limit_file = tmp_path / "av_rate_limit.json"
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(limit_file))

    today = date.today().isoformat()
    # New per-key format: use kid as the counter key
    limit_file.write_text(json.dumps({"date": today, _TEST_KID: 3}))

    with (
        patch.object(av, "_api_keys", return_value=[_TEST_KEY]),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
    ):
        av._consume_call()

    state = json.loads(limit_file.read_text())
    assert state[_TEST_KID] == 4


# ── News sentiment ─────────────────────────────────────────────────────────────


def test_get_news_sentiment_parses_response(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    # Clear in-memory cache
    av._news_cache.clear()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _mock_news_response("AAPL")

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp),
    ):
        result = av.get_news_sentiment(["AAPL"])

    assert "AAPL" in result
    r = result["AAPL"]
    assert r["article_count"] == 2
    assert r["sentiment_score"] > 0  # bullish
    assert "Earnings" in r["topics"]
    assert r["relevance"] > 0


def test_get_news_sentiment_caches_result(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    av._news_cache.clear()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _mock_news_response("MSFT")

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp) as mock_get,
    ):
        av.get_news_sentiment(["MSFT"])
        av.get_news_sentiment(["MSFT"])  # second call — should use cache

    # requests.get should only have been called once (second call is cached)
    assert mock_get.call_count == 1


def test_get_news_sentiment_rate_limit_message_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    av._news_cache.clear()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 25..."}

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp),
    ):
        result = av.get_news_sentiment(["AAPL"])

    assert result == {}


def test_get_news_sentiment_http_error_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    av._news_cache.clear()

    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp),
    ):
        result = av.get_news_sentiment(["AAPL"])

    assert result == {}


# ── Earnings calendar ──────────────────────────────────────────────────────────


def test_get_earnings_calendar_parses_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    av._earnings_cache = (None, 0.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = _mock_earnings_csv()

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp),
    ):
        result = av.get_earnings_calendar()

    assert result.get("AAPL") == "2026-05-01"
    assert result.get("MSFT") == "2026-04-25"


def test_get_earnings_calendar_caches_result(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    av._earnings_cache = (None, 0.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = _mock_earnings_csv()

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp) as mock_get,
    ):
        av.get_earnings_calendar()
        av.get_earnings_calendar()  # second call — should use cache

    assert mock_get.call_count == 1


def test_get_earnings_calendar_deduplicates_symbol(tmp_path, monkeypatch):
    """When a symbol appears twice, keep the earlier reportDate."""
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "rl.json"))
    av._earnings_cache = (None, 0.0)

    csv_with_dup = (
        "symbol,name,reportDate,fiscalDateEnding,estimate,currency\r\n"
        "AAPL,Apple Inc,2026-06-01,2026-06-30,1.60,USD\r\n"
        "AAPL,Apple Inc,2026-04-30,2026-03-31,1.50,USD\r\n"
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = csv_with_dup

    with (
        patch.object(av, "_api_key", return_value="FAKE"),
        patch.dict(av.CONFIG, {"alpha_vantage_daily_limit": 25}),
        patch("alpha_vantage_client.requests.get", return_value=mock_resp),
    ):
        result = av.get_earnings_calendar()

    assert result["AAPL"] == "2026-04-30"


# ── get_calls_today ────────────────────────────────────────────────────────────


def test_get_calls_today_returns_zero_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(tmp_path / "nonexistent.json"))
    assert av.get_calls_today() == 0


def test_get_calls_today_returns_count(tmp_path, monkeypatch):
    limit_file = tmp_path / "av_rate_limit.json"
    monkeypatch.setattr(av, "_RATE_LIMIT_PATH", str(limit_file))
    # New per-key format: get_calls_today() sums per-key counts
    limit_file.write_text(json.dumps({"date": date.today().isoformat(), _TEST_KID: 7}))
    with patch.object(av, "_api_keys", return_value=[_TEST_KEY]):
        assert av.get_calls_today() == 7
