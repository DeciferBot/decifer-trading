"""
tests/test_apex_sentinel_ni.py

Regression tests for Apex Sentinel NEWS_INTERRUPT path.

Post-Decifer-3.0: the legacy run_sentinel_pipeline() is deleted.
bot_sentinel.handle_news_trigger() always invokes Apex via
apex_orchestrator._run_apex_pipeline with execute=True.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SENT = (_REPO / "bot_sentinel.py").read_text()


def test_sentinel_always_uses_apex():
    assert "apex_orchestrator as _aorch_s" in _SENT


def test_sentinel_apex_dispatch_execute_true():
    # _apex_dispatch must be called with execute=True in the NI path
    assert "_apex_dispatch(" in _SENT
    assert "execute=True" in _SENT


def test_sentinel_no_legacy_pipeline_check():
    assert "run_sentinel_pipeline" not in _SENT
    assert "sentinel_legacy_pipeline_enabled" not in _SENT
    assert "should_use_legacy_pipeline" not in _SENT


def test_build_news_trigger_payload_preserved():
    """The Apex payload builder must remain in sentinel_agents."""
    from sentinel_agents import build_news_trigger_payload
    result = build_news_trigger_payload(
        trigger={"symbol": "AAPL", "headlines": ["test"], "urgency": "HIGH"},
    )
    assert result["trigger_type"] == "NEWS_INTERRUPT"
    assert result["trigger_context"]["symbol"] == "AAPL"
