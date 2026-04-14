# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  telegram_bot.py                            ║
# ║   Emergency kill switch accessible via Telegram.             ║
# ║   Polls the Telegram Bot API using long-polling (requests).  ║
# ║   No heavy async dependency — works alongside ib_insync.     ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Commands (authorized chat IDs only):
#   /kill   — flatten all positions, cancel all orders, halt bot
#   /status — report current bot state and open position count
#   /resume — clear the kill flag so the bot can trade again
#
# Wire-up (in bot.py main(), after IBKR connected):
#
#   import telegram_bot as _tg
#   _tg.start(token, chat_ids, on_kill, on_status, on_resume)

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import requests

log = logging.getLogger(__name__)

_LONG_POLL_TIMEOUT = 30  # seconds — Telegram server holds open until update arrives
_REQUEST_TIMEOUT = 35  # slightly longer than long-poll timeout
_POLL_INTERVAL = 0.5  # seconds between polls (back-to-back after updates arrive)
_MAX_BACKOFF = 60  # seconds — cap on retry delay after errors

_stop_event: threading.Event = threading.Event()
_bot_thread: threading.Thread | None = None


# ── Internal helpers ───────────────────────────────────────────


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _send_message(token: str, chat_id: int, text: str) -> None:
    """Send a text message to a chat. Silently swallows errors."""
    try:
        requests.post(
            _api_url(token, "sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram sendMessage failed (chat_id=%s): %s", chat_id, exc)


def _poll_once(token: str, offset: int) -> tuple[list[dict], int]:
    """
    Call getUpdates with long-polling.  Returns (updates, new_offset).
    Returns ([], offset) on any error so the loop can retry cleanly.
    """
    try:
        resp = requests.get(
            _api_url(token, "getUpdates"),
            params={"timeout": _LONG_POLL_TIMEOUT, "offset": offset},
            timeout=_REQUEST_TIMEOUT,
        )
        data = resp.json()
        if not data.get("ok"):
            log.warning("Telegram getUpdates not ok: %s", data.get("description"))
            return [], offset
        updates: list[dict] = data.get("result", [])
        if updates:
            offset = updates[-1]["update_id"] + 1
        return updates, offset
    except Exception as exc:
        log.warning("Telegram getUpdates error: %s", exc)
        return [], offset


def _handle_update(
    update: dict,
    token: str,
    authorized_ids: set[int],
    on_kill: Callable[[], str],
    on_status: Callable[[], str],
    on_resume: Callable[[], str],
) -> None:
    """Dispatch a single Telegram update to the appropriate handler."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id: int = message["chat"]["id"]
    text: str = (message.get("text") or "").strip().lower()
    sender: str = message.get("from", {}).get("username", str(chat_id))

    if chat_id not in authorized_ids:
        log.warning(
            "Telegram: unauthorized command from chat_id=%s (@%s): %r",
            chat_id,
            sender,
            text,
        )
        _send_message(token, chat_id, "⛔ Unauthorized. Your chat ID is not in the allowed list.")
        return

    if text in ("/kill", "/kill@decifer_bot"):
        log.warning("🚨 Telegram KILL command from @%s (chat_id=%s)", sender, chat_id)
        _send_message(token, chat_id, "🚨 <b>KILL SWITCH activated</b> — flattening all positions...")
        result = on_kill()
        _send_message(token, chat_id, result)

    elif text in ("/status", "/status@decifer_bot"):
        result = on_status()
        _send_message(token, chat_id, result)

    elif text in ("/resume", "/resume@decifer_bot"):
        result = on_resume()
        _send_message(token, chat_id, result)

    elif text.startswith("/"):
        _send_message(
            token,
            chat_id,
            "Commands:\n/kill — flatten all positions and halt\n/status — report bot state\n/resume — clear kill flag",
        )


# ── Polling loop ───────────────────────────────────────────────


def _run_loop(
    token: str,
    authorized_ids: set[int],
    on_kill: Callable[[], str],
    on_status: Callable[[], str],
    on_resume: Callable[[], str],
) -> None:
    offset = 0
    backoff = _POLL_INTERVAL
    log.info("Telegram kill-switch bot started (long-polling)")

    while not _stop_event.is_set():
        updates, offset = _poll_once(token, offset)

        if updates:
            backoff = _POLL_INTERVAL
            for update in updates:
                try:
                    _handle_update(
                        update,
                        token,
                        authorized_ids,
                        on_kill,
                        on_status,
                        on_resume,
                    )
                except Exception as exc:
                    log.error("Telegram update handler error: %s", exc)
        else:
            # No updates or error — apply backoff before retrying
            _stop_event.wait(timeout=backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue

        _stop_event.wait(timeout=_POLL_INTERVAL)

    log.info("Telegram kill-switch bot stopped")


# ── Public API ─────────────────────────────────────────────────


def start(
    token: str,
    authorized_chat_ids: list[int],
    on_kill: Callable[[], str],
    on_status: Callable[[], str],
    on_resume: Callable[[], str],
) -> threading.Thread:
    """
    Start the Telegram polling loop in a daemon thread.

    Args:
        token:               Telegram bot token from @BotFather.
        authorized_chat_ids: List of integer chat IDs that may issue commands.
        on_kill:             Called on /kill — must return a status string.
        on_status:           Called on /status — must return a status string.
        on_resume:           Called on /resume — must return a status string.

    Returns:
        The started daemon Thread (already running).
    """
    global _bot_thread
    _stop_event.clear()

    _bot_thread = threading.Thread(
        target=_run_loop,
        args=(token, set(authorized_chat_ids), on_kill, on_status, on_resume),
        daemon=True,
        name="telegram-kill-switch",
    )
    _bot_thread.start()
    return _bot_thread


def stop() -> None:
    """Signal the polling loop to stop gracefully."""
    _stop_event.set()
