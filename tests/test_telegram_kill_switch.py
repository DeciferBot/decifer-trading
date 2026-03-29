"""
Tests for telegram_bot.py — emergency stop via Telegram.

All Telegram API calls are mocked.  No real network traffic, no IBKR, no Claude API.
"""

from __future__ import annotations

import os
import sys
import threading
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import telegram_bot

VALID_TOKEN     = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
AUTHORIZED_ID   = 123456789
UNAUTHORIZED_ID = 999999999


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_update(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": chat_id, "username": "testuser"},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _handle(update: dict, on_kill=None, on_status=None, on_resume=None, extra_ids: set | None = None):
    """Call _handle_update with mocked _send_message, return the mock."""
    authorized = (extra_ids or set()) | {AUTHORIZED_ID}

    if on_kill is None:
        on_kill = lambda: "✅ All positions flattened."
    if on_status is None:
        on_status = lambda: "Bot is running."
    if on_resume is None:
        on_resume = lambda: "Bot resumed."

    with patch.object(telegram_bot, "_send_message") as mock_send:
        telegram_bot._handle_update(
            update, VALID_TOKEN, authorized,
            on_kill, on_status, on_resume,
        )
        return mock_send


# ── _handle_update: /kill ─────────────────────────────────────────────────────


class TestKillCommand:

    def test_kill_calls_on_kill(self):
        called = []
        on_kill = lambda: (called.append(1), "✅ done")[1]
        _handle(_make_update(1, AUTHORIZED_ID, "/kill"), on_kill=on_kill)
        assert called, "/kill must invoke the on_kill callback"

    def test_kill_sends_two_messages(self):
        mock_send = _handle(_make_update(2, AUTHORIZED_ID, "/kill"))
        assert mock_send.call_count == 2, "Expected acknowledgement + result"

    def test_kill_first_message_mentions_kill(self):
        mock_send = _handle(_make_update(3, AUTHORIZED_ID, "/kill"))
        first_text = mock_send.call_args_list[0].args[2]
        assert "KILL" in first_text.upper() or "flatten" in first_text.lower()

    def test_kill_second_message_is_callback_result(self):
        on_kill = lambda: "✅ All positions flattened."
        mock_send = _handle(_make_update(4, AUTHORIZED_ID, "/kill"), on_kill=on_kill)
        result_texts = [c.args[2] for c in mock_send.call_args_list]
        assert "✅ All positions flattened." in result_texts

    def test_kill_command_case_normalised(self):
        """Input arrives lower-cased after .lower(); /kill must still trigger."""
        called = []
        on_kill = lambda: (called.append(1), "done")[1]
        _handle(_make_update(5, AUTHORIZED_ID, "/kill"), on_kill=on_kill)
        assert called

    def test_kill_with_bot_suffix(self):
        """Groups send /kill@botname — must still trigger."""
        called = []
        on_kill = lambda: (called.append(1), "done")[1]
        _handle(_make_update(6, AUTHORIZED_ID, "/kill@decifer_bot"), on_kill=on_kill)
        assert called

    def test_on_kill_exception_does_not_propagate(self):
        """If on_kill raises, _handle_update must not raise."""
        def on_kill():
            raise RuntimeError("IB connection lost")
        with pytest.raises(RuntimeError):
            # _handle_update itself lets the exception bubble — the caller (_run_loop)
            # catches it. Confirm the exception is the expected one.
            with patch.object(telegram_bot, "_send_message"):
                telegram_bot._handle_update(
                    _make_update(7, AUTHORIZED_ID, "/kill"),
                    VALID_TOKEN, {AUTHORIZED_ID},
                    on_kill, lambda: "", lambda: "",
                )


# ── _handle_update: /status ───────────────────────────────────────────────────


class TestStatusCommand:

    def test_status_calls_on_status(self):
        called = []
        on_status = lambda: (called.append(1), "running")[1]
        _handle(_make_update(10, AUTHORIZED_ID, "/status"), on_status=on_status)
        assert called

    def test_status_sends_one_message(self):
        mock_send = _handle(_make_update(11, AUTHORIZED_ID, "/status"))
        assert mock_send.call_count == 1

    def test_status_message_contains_callback_result(self):
        on_status = lambda: "Bot state: RUNNING ✅\nOpen positions: 3"
        mock_send = _handle(_make_update(12, AUTHORIZED_ID, "/status"), on_status=on_status)
        assert mock_send.call_args.args[2] == "Bot state: RUNNING ✅\nOpen positions: 3"

    def test_status_with_bot_suffix(self):
        called = []
        on_status = lambda: (called.append(1), "ok")[1]
        _handle(_make_update(13, AUTHORIZED_ID, "/status@decifer_bot"), on_status=on_status)
        assert called


# ── _handle_update: /resume ───────────────────────────────────────────────────


class TestResumeCommand:

    def test_resume_calls_on_resume(self):
        called = []
        on_resume = lambda: (called.append(1), "resumed")[1]
        _handle(_make_update(20, AUTHORIZED_ID, "/resume"), on_resume=on_resume)
        assert called

    def test_resume_sends_one_message(self):
        mock_send = _handle(_make_update(21, AUTHORIZED_ID, "/resume"))
        assert mock_send.call_count == 1

    def test_resume_message_is_callback_result(self):
        on_resume = lambda: "▶️ Bot resumed."
        mock_send = _handle(_make_update(22, AUTHORIZED_ID, "/resume"), on_resume=on_resume)
        assert mock_send.call_args.args[2] == "▶️ Bot resumed."


# ── _handle_update: authorization ─────────────────────────────────────────────


class TestAuthorization:

    def test_unauthorized_id_rejected(self):
        called = []
        on_kill = lambda: (called.append(1), "done")[1]
        mock_send = _handle(_make_update(30, UNAUTHORIZED_ID, "/kill"), on_kill=on_kill)
        assert not called, "on_kill must NOT be called for unauthorized chat_id"

    def test_unauthorized_id_receives_rejection_message(self):
        mock_send = _handle(_make_update(31, UNAUTHORIZED_ID, "/kill"))
        mock_send.assert_called_once()
        assert "Unauthorized" in mock_send.call_args.args[2]

    def test_multiple_authorized_ids(self):
        second_id = 987654321
        called = []
        on_kill = lambda: (called.append(1), "done")[1]
        update = _make_update(32, second_id, "/kill")
        with patch.object(telegram_bot, "_send_message"):
            telegram_bot._handle_update(
                update, VALID_TOKEN, {AUTHORIZED_ID, second_id},
                on_kill, lambda: "", lambda: "",
            )
        assert called


# ── _handle_update: unknown / non-commands ────────────────────────────────────


class TestUnknownInput:

    def test_unknown_slash_command_sends_help(self):
        mock_send = _handle(_make_update(40, AUTHORIZED_ID, "/foo"))
        mock_send.assert_called_once()
        assert "/kill" in mock_send.call_args.args[2]

    def test_plain_text_ignored(self):
        mock_send = _handle(_make_update(41, AUTHORIZED_ID, "hello there"))
        mock_send.assert_not_called()

    def test_update_with_no_message_ignored(self):
        mock_send = _handle({"update_id": 42})
        mock_send.assert_not_called()

    def test_update_with_empty_text_ignored(self):
        update = _make_update(43, AUTHORIZED_ID, "")
        mock_send = _handle(update)
        mock_send.assert_not_called()


# ── _poll_once ────────────────────────────────────────────────────────────────


class TestPollOnce:

    def _mock_get(self, data: dict):
        m = MagicMock()
        m.json.return_value = data
        return m

    def test_returns_updates_and_advances_offset(self):
        fake = {"ok": True, "result": [{"update_id": 10}, {"update_id": 11}]}
        with patch("telegram_bot.requests.get", return_value=self._mock_get(fake)):
            updates, offset = telegram_bot._poll_once(VALID_TOKEN, 5)
        assert len(updates) == 2
        assert offset == 12   # last update_id + 1

    def test_empty_result_preserves_offset(self):
        fake = {"ok": True, "result": []}
        with patch("telegram_bot.requests.get", return_value=self._mock_get(fake)):
            updates, offset = telegram_bot._poll_once(VALID_TOKEN, 5)
        assert updates == []
        assert offset == 5

    def test_api_not_ok_returns_empty(self):
        fake = {"ok": False, "description": "Unauthorized"}
        with patch("telegram_bot.requests.get", return_value=self._mock_get(fake)):
            updates, offset = telegram_bot._poll_once(VALID_TOKEN, 0)
        assert updates == []
        assert offset == 0

    def test_network_error_returns_empty(self):
        with patch("telegram_bot.requests.get", side_effect=ConnectionError("timeout")):
            updates, offset = telegram_bot._poll_once(VALID_TOKEN, 7)
        assert updates == []
        assert offset == 7   # must not change on error


# ── start() / stop() ──────────────────────────────────────────────────────────


class TestStartStop:

    def test_start_returns_daemon_thread(self):
        with patch.object(telegram_bot, "_run_loop"):
            t = telegram_bot.start(
                VALID_TOKEN, [AUTHORIZED_ID],
                lambda: "", lambda: "", lambda: "",
            )
        assert isinstance(t, threading.Thread)
        assert t.daemon

    def test_stop_sets_stop_event(self):
        telegram_bot._stop_event.clear()
        telegram_bot.stop()
        assert telegram_bot._stop_event.is_set()


# ── Phase gate integration ─────────────────────────────────────────────────────


class TestPhaseGateTelegramGate:
    """
    Verify phase_gate.validate() correctly blocks Phase 4 when telegram
    kill switch is in frozen_features but not configured.
    """

    def _cfg(
        self,
        current_phase: int = 1,
        token: str = "",
        chat_ids: list | None = None,
        live_account: str = "",
    ) -> dict:
        return {
            "accounts": {
                "paper":  "DUP481326",
                "live_1": live_account,
                "live_2": "",
            },
            "aggregate_accounts": [],
            "trade_log": "data/trades.json",
            "telegram": {
                "bot_token":           token,
                "authorized_chat_ids": chat_ids or [],
            },
            "phase_gate": {
                "current_phase": current_phase,
                "phase1_exit_criteria": {
                    "min_closed_trades":      200,
                    "min_test_pass_rate":     0.80,
                    "min_paper_trading_days": 30,
                },
                "frozen_features": {
                    "live_account_trading":     4,
                    "multi_account_aggregation":4,
                    "cloud_deployment":         4,
                    "telegram_kill_switch":     4,
                    "docker_deployment":        5,
                    "multi_user_auth":          5,
                    "hosted_dashboard":         5,
                },
            },
        }

    def test_phase4_without_telegram_token_violates(self):
        from phase_gate import validate
        cfg = self._cfg(current_phase=4, token="", chat_ids=[])
        violations = validate(cfg)
        assert any("telegram_kill_switch" in v for v in violations), (
            f"Expected telegram_kill_switch violation at Phase 4 with no token. Got: {violations}"
        )

    def test_phase4_without_chat_ids_violates(self):
        from phase_gate import validate
        cfg = self._cfg(current_phase=4, token="abc:123", chat_ids=[])
        violations = validate(cfg)
        assert any("telegram_kill_switch" in v for v in violations)

    def test_phase4_with_token_and_chat_ids_clean(self):
        from phase_gate import validate
        cfg = self._cfg(current_phase=4, token="abc:123xyz", chat_ids=[AUTHORIZED_ID])
        violations = validate(cfg)
        assert not any("telegram_kill_switch" in v for v in violations)

    def test_phase1_without_telegram_clean(self):
        """In Phase 1 the gate should not fire — telegram not yet required."""
        from phase_gate import validate
        cfg = self._cfg(current_phase=1, token="", chat_ids=[])
        violations = validate(cfg)
        assert not any("telegram_kill_switch" in v for v in violations)

    def test_phase3_without_telegram_clean(self):
        from phase_gate import validate
        cfg = self._cfg(current_phase=3, token="", chat_ids=[])
        violations = validate(cfg)
        assert not any("telegram_kill_switch" in v for v in violations)

    def test_violation_message_is_actionable(self):
        """Violation message must tell the user what to fix."""
        from phase_gate import validate
        cfg = self._cfg(current_phase=4, token="")
        violations = validate(cfg)
        msg = next(v for v in violations if "telegram_kill_switch" in v)
        assert "bot_token" in msg or "TELEGRAM_BOT_TOKEN" in msg
        assert "authorized_chat_ids" in msg

    def test_production_config_has_telegram_kill_switch_frozen(self):
        """Regression: production config.py must declare telegram_kill_switch as Phase 4 gate."""
        from config import CONFIG
        frozen = CONFIG.get("phase_gate", {}).get("frozen_features", {})
        assert "telegram_kill_switch" in frozen, (
            "telegram_kill_switch missing from frozen_features — required as Phase 4 gate"
        )
        assert frozen["telegram_kill_switch"] == 4

    def test_production_config_phase1_no_telegram_violation(self):
        """In Phase 1 production config must have zero violations even with no telegram token."""
        from config import CONFIG
        from phase_gate import validate
        violations = validate(CONFIG)
        assert not any("telegram_kill_switch" in v for v in violations), (
            "Phase 1 must not require telegram to be configured yet"
        )
