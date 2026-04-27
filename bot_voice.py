"""
bot_voice.py — Voice alerts and query answering for Decifer.

TTS: macOS built-in `say` command (no dependencies, non-blocking).
STT: handled by the browser (Web Speech API); only text arrives here.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading

log = logging.getLogger("decifer.voice")

_VOICE_RATE = 180
_VOICE_NAME = "Daniel"

# Single-worker speech queue — serializes all TTS calls so alerts never overlap.
# Max 8 items: if the queue is full, new alerts are silently dropped to avoid
# a multi-minute backlog after a burst of simultaneous trade events.
_speech_queue: queue.Queue[str] = queue.Queue(maxsize=8)


def _speech_worker() -> None:
    while True:
        text = _speech_queue.get()
        try:
            subprocess.run(
                ["say", "-v", _VOICE_NAME, "-r", str(_VOICE_RATE), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.warning("Voice TTS error: %s", e)
        finally:
            _speech_queue.task_done()


threading.Thread(target=_speech_worker, daemon=True, name="voice-worker").start()

# Human-readable labels for internal codes
_REGIME_LABELS = {
    "BULL_TREND": "a bull trend",
    "BEAR_TREND": "a bear trend",
    "HIGH_VOL": "high volatility",
    "LOW_VOL": "low volatility",
    "RISK_OFF": "risk off",
    "RISK_ON": "risk on",
    "CHOPPY": "choppy conditions",
    "NEUTRAL": "neutral conditions",
    "UNKNOWN": "an unknown regime",
}

_EXIT_LABELS = {
    "stop_loss_hit": "stop loss hit",
    "take_profit_hit": "take profit hit",
    "externally_closed_by_user": "manually closed",
    "externally_closed": "externally closed",
    "trailing_stop_hit": "trailing stop hit",
}


def _clean(text: str) -> str:
    """Normalize internal codes and symbols so they sound natural when spoken."""
    import re

    # Replace underscores with spaces
    text = text.replace("_", " ")
    # Strip markdown-style formatting
    text = re.sub(r"\*+", "", text)
    # Turn $123.45 into "123 dollars"
    text = re.sub(r"\$([0-9,]+(?:\.[0-9]+)?)", lambda m: m.group(1).replace(",", "") + " dollars", text)
    # Remove leading + on numbers (reads as "plus")
    text = re.sub(r"(?<!\w)\+([0-9])", r"\1", text)
    # Remove % sign — just read the number
    text = text.replace("%", " percent")
    return text.strip()


def speak(msg: str) -> None:
    """Non-blocking TTS. Enqueues text for the single speech worker — never blocks the trading loop."""
    if not msg:
        return
    try:
        _speech_queue.put_nowait(_clean(msg))
    except queue.Full:
        log.debug("Voice queue full — dropping alert: %.60s…", msg)


def _generate_natural(event: str, fallback: str, **ctx) -> str:
    """
    Use Claude Haiku to turn a trade event into a friendly, first-person spoken alert.
    Falls back to `fallback` if Claude is unavailable.
    """
    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])

        _prompts = {
            "entry": (
                "The bot just entered a {direction} trade on {symbol}. "
                "Agent reasoning: {reason}. "
                "News context: {news}. "
                "Write a friendly first-person spoken alert explaining WHY we took this trade in plain English. "
                "Do NOT mention the score number. Focus on the actual reason — momentum, news, sector, setup, etc. "
                "Example: 'I just went long on Apple — tech is leading today and there's a clean breakout above resistance with strong volume behind it.'"
            ),
            "exit_agent": (
                "The bot is closing its position in {symbol}. "
                "Reason from agents: {reason}. "
                "News about this stock: {news}. "
                "Write a friendly first-person spoken alert explaining why, 1-2 sentences. "
                "Example: 'I'm closing out Nvidia — the thesis has changed and I'd rather lock in the gains.'"
            ),
            "exit_stop": (
                "The position in {symbol} was closed externally. Exit type: {exit_type}. P&L: {pnl} dollars. "
                "Full context: {reason}. "
                "Write a calm, friendly first-person spoken alert, 1-2 sentences. "
                "Example: 'Apple just hit its stop — we're down about 280 dollars on that one. Part of the process.'"
            ),
            "exit_pm": (
                "The portfolio manager decided to close {symbol}. Reason: {reason}. "
                "News: {news}. "
                "Write a friendly first-person spoken alert explaining why, 1-2 sentences."
            ),
            "regime": (
                "The market regime just shifted to: {regime}. VIX is at {vix}. "
                "Write a brief friendly spoken heads-up about what this means for trading, 1 sentence."
            ),
            "drawdown": (
                "The bot just hit its drawdown limit and is flattening all positions. "
                "Write a calm but serious spoken alert, 1 sentence."
            ),
            "options": (
                "The bot just bought a {option_type} option on {symbol} at the {strike} strike. Score: {score}. "
                "Write a friendly first-person spoken alert, 1 sentence."
            ),
            "trim": (
                "The bot just trimmed its {symbol} position — sold roughly {pct} percent of the position. "
                "Reason: {reason}. "
                "Write a friendly first-person spoken alert, 1 sentence. "
                "Example: 'I trimmed half my Apple position — locking in some gains while keeping exposure.'"
            ),
            "add": (
                "The bot just added {qty} shares to its existing {symbol} position. "
                "Reason: {reason}. "
                "Write a friendly first-person spoken alert, 1 sentence. "
                "Example: 'I added to my Nvidia position — conviction is building and the setup looks strong.'"
            ),
            "deferred_exit": (
                "An option position in {symbol} was queued for exit while the market was closed. "
                "The market just opened and the exit order has now been placed. Original reason: {reason}. "
                "Write a friendly first-person spoken alert, 1 sentence. "
                "Example: 'Closing the MRNA option now — I queued this exit last night when the market was closed.'"
            ),
        }

        template = _prompts.get(event, "Describe this trading event naturally in one sentence: {event}")
        prompt = template.format(event=event, **{k: (str(v) if v is not None else "unknown") for k, v in ctx.items()})

        from config import CONFIG as _CONFIG

        resp = client.messages.create(
            model=_CONFIG.get("claude_model_haiku", "claude-haiku-4-5-20251001"),
            max_tokens=120,
            system=(
                "You write very short, friendly, first-person spoken alerts for an autonomous trading bot called Decifer. "
                "Sound like a knowledgeable friend talking to the trader — warm, direct, and clear. "
                "1-2 sentences max. No markdown, no bullet points, no quotes around your output. "
                "Just write the words that will be spoken aloud."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.warning("Natural alert generation failed: %s", e)
        return fallback


def speak_natural(event: str, fallback: str = "", **ctx) -> None:
    """
    Generate a friendly spoken alert via Claude Haiku, then speak it.
    Fully non-blocking — runs in a background thread.
    """

    def _run():
        text = _generate_natural(event, fallback, **ctx) or fallback
        if text:
            speak(text)

    threading.Thread(target=_run, daemon=True).start()


def answer_voice_query(question: str, dash: dict) -> str:
    """
    Answer a voice question using live bot context.
    Handles: portfolio state, P&L, positions, regime, news, investment analysis.
    Deflects questions with no connection to trading or markets.
    Speaks the answer aloud and returns the text.
    """
    try:
        regime = dash.get("regime") or {}
        positions = dash.get("positions") or []
        pv = dash.get("portfolio_value") or 0
        daily_pnl = dash.get("daily_pnl") or 0
        scanning = dash.get("scanning", False)
        paused = dash.get("paused", False)
        killed = dash.get("killed", False)

        # Positions
        pos_lines = []
        for p in positions:
            sym = p.get("symbol", "")
            dirn = p.get("direction", "LONG")
            qty = p.get("qty", 0)
            entry = p.get("entry") or 0
            now = p.get("current") or 0
            pnl = p.get("pnl") or 0
            conv = p.get("conviction") or 0
            regime_ = p.get("entry_regime", "")
            pos_lines.append(
                f"{sym} {dirn} x{qty} entry ${entry:.2f} now ${now:.2f} P&L ${pnl:+.2f}"
                + (f" conviction {conv:.0%}" if conv else "")
                + (f" entered in {regime_}" if regime_ else "")
            )

        # Recent trades
        trades = dash.get("trades") or []
        trade_lines = []
        for t in trades[:5]:
            side = t.get("side", "")
            sym = t.get("symbol", "")
            price = t.get("price", "")
            pnl_t = t.get("pnl", "")
            pnl_s = f" P&L ${pnl_t:+.2f}" if isinstance(pnl_t, (int, float)) else ""
            trade_lines.append(f"{side} {sym} @ ${price}{pnl_s}")

        # News headlines
        news_data = dash.get("news_data") or {}
        news_lines = []
        for sym, nd in list(news_data.items())[:8]:
            for hl in (nd.get("headlines") or [])[:1]:
                if hl.strip():
                    sent = nd.get("claude_sentiment", "")
                    news_lines.append(f"{sym}: {hl.strip()}" + (f" [{sent}]" if sent else ""))

        # Sector bias
        sector_bias = dash.get("sector_bias") or {}
        sector_leaders = [e for e, _ in (sector_bias.get("ranked") or [])[:3]]
        sector_laggards = [e for e, _ in (sector_bias.get("ranked") or [])[-3:]]

        # Claude analysis from last scan
        claude_analysis = dash.get("claude_analysis", "")

        status = "KILLED" if killed else ("PAUSED" if paused else ("SCANNING" if scanning else "IDLE"))

        context = (
            f"Portfolio: ${pv:,.2f} | Day P&L: ${daily_pnl:+,.2f}\n"
            f"Regime: {regime.get('regime', 'UNKNOWN')} | VIX: {regime.get('vix', '?')} | "
            f"SPY: ${regime.get('spy_price', '?')} | Session: {regime.get('session', '?')}\n"
            f"Bot status: {status}\n"
            f"Open positions ({len(positions)}): {'; '.join(pos_lines) or 'None'}\n"
            f"Recent trades: {'; '.join(trade_lines) or 'None'}\n"
            + (f"Sector leaders: {', '.join(sector_leaders)}\n" if sector_leaders else "")
            + (f"Sector laggards: {', '.join(sector_laggards)}\n" if sector_laggards else "")
            + (f"Recent news: {' | '.join(news_lines)}\n" if news_lines else "")
            + (f"Last scan analysis: {claude_analysis}\n" if claude_analysis else "")
        )

        system = (
            "You are Decifer, an autonomous trading bot with live market data. "
            "You can answer questions about: portfolio state, positions, P&L, regime, recent trades, "
            "market news, sector rotation, and investment analysis based on the data you have. "
            "Give direct, actionable answers grounded in the context provided. "
            "If asked for investment analysis or opinions, give them — you have the data to do so. "
            "If the question has absolutely no connection to trading, markets, or finance, say: "
            "'That's outside what I can help with. Ask me about your portfolio or the market.' "
            "Keep answers to 2-3 sentences max. No markdown, no bullet points, no preamble."
        )

        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        from config import CONFIG as _CONFIG

        _resp = client.messages.create(
            model=_CONFIG.get("claude_model_haiku", "claude-haiku-4-5-20251001"),
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}],
        )
        answer = _resp.content[0].text.strip()
        answer = answer or "I couldn't retrieve that right now."
        speak(answer)
        return answer

    except Exception as e:
        log.error("Voice query error: %s", e)
        err = "Error processing your question."
        speak(err)
        return err


# ── Voice command infrastructure ─────────────────────────────────────────────

def _log_voice_audit(action: str, symbol: str | None, voice_text: str, result: str) -> None:
    """Append a voice command event to data/audit_log.jsonl."""
    import json
    import os
    from datetime import datetime, timezone

    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "audit_log.jsonl")
    try:
        with open(_path, "a") as f:
            f.write(json.dumps({
                "type": "voice_command",
                "action": action,
                "symbol": symbol,
                "voice_text": voice_text,
                "result": result,
                "ts": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
    except Exception:
        pass


def write_voice_memo(text: str) -> None:
    """Append a timestamped voice memo to data/voice_memos.md.

    The file is auto-created on first write. Opus reads it at the start of
    each scan cycle via agents.py (voice_block injection).
    """
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    line = f"[{stamp}] {text.strip()}\n"
    _vm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "voice_memos.md")
    with open(_vm_path, "a") as f:
        f.write(line)


def classify_voice_intent(text: str) -> dict:
    """Classify raw voice text into a structured intent via Claude Haiku.

    Returns: {"intent": str, "params": dict}

    Intents:
      QA            — questions about portfolio, P&L, regime, news (default)
      MEMO          — "remember that...", "note that...", "don't forget..."
      PAUSE         — pause scanning
      RESUME        — resume scanning
      WATCHLIST_ADD — add symbol to favourites
      TRADE_BUY     — buy a symbol (params: {symbol, qty_hint: null})
      TRADE_SELL    — close a position (params: {symbol, full_close: true})
      SKIP_SYMBOL   — skip a symbol this session (treated as MEMO)
      CONFIRM       — confirm a pending trade command
      CANCEL        — cancel a pending trade command

    On any failure, returns {"intent": "QA", "params": {}}.
    """
    try:
        import json as _json
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
        from config import CONFIG as _CONFIG

        system = (
            "You are an intent classifier for a trading bot voice interface.\n"
            "Classify the user's spoken text into exactly one intent and return JSON only.\n"
            "\n"
            "RULES:\n"
            "1. Default to 'QA' when ambiguous. NEVER assume a trade intent.\n"
            "2. TRADE_BUY requires an explicit buy/purchase/long verb AND a recognisable ticker symbol.\n"
            "3. TRADE_SELL requires an explicit sell/close/exit/short verb AND a recognisable ticker.\n"
            "4. CONFIRM: only when the text is essentially just 'confirm' or 'yes proceed'.\n"
            "5. CANCEL: only when the text is essentially just 'cancel' or 'abort' or 'never mind'.\n"
            "6. MEMO: phrases like 'remember that', 'note that', 'don't forget', 'write down'.\n"
            "7. WATCHLIST_ADD: 'add X to my watchlist' or 'track X' or 'watch X'.\n"
            "8. PAUSE / RESUME: explicit pause or resume scanning commands.\n"
            "9. SKIP_SYMBOL: 'skip X today' or 'ignore X' — treat like MEMO.\n"
            "\n"
            "Respond with JSON only, no prose:\n"
            '{"intent": "QA", "params": {}}\n'
            '{"intent": "MEMO", "params": {"text": "original text here"}}\n'
            '{"intent": "PAUSE", "params": {}}\n'
            '{"intent": "RESUME", "params": {}}\n'
            '{"intent": "WATCHLIST_ADD", "params": {"symbol": "AAPL"}}\n'
            '{"intent": "TRADE_BUY", "params": {"symbol": "AAPL", "qty_hint": null}}\n'
            '{"intent": "TRADE_SELL", "params": {"symbol": "AAPL", "full_close": true}}\n'
            '{"intent": "CONFIRM", "params": {}}\n'
            '{"intent": "CANCEL", "params": {}}\n'
        )

        resp = client.messages.create(
            model=_CONFIG.get("claude_model_haiku", "claude-haiku-4-5-20251001"),
            max_tokens=80,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        result = _json.loads(raw)
        if "intent" not in result:
            return {"intent": "QA", "params": {}}
        return result
    except Exception as e:
        log.warning("classify_voice_intent failed (%s) — defaulting to QA", e)
        return {"intent": "QA", "params": {}}


def handle_voice_command(intent: dict, dash: dict) -> str:
    """Route a classified non-QA intent to the appropriate action.

    Returns a spoken response string. Does NOT call speak() — the caller does.
    Trade commands (TRADE_BUY / TRADE_SELL) are queued for main-loop execution;
    they are NOT executed here (HTTP thread ≠ main thread).
    """
    import json as _json
    import os
    from datetime import datetime, timezone
    import bot_state

    kind = intent.get("intent", "QA")
    params = intent.get("params", {})

    # ── MEMO ─────────────────────────────────────────────────────────────────
    if kind == "MEMO":
        text = params.get("text", "")
        if text:
            write_voice_memo(text)
            _log_voice_audit("memo", None, text, "written")
            return "Got it. I'll remember that."
        return "I didn't catch what to remember."

    # ── PAUSE / RESUME ────────────────────────────────────────────────────────
    if kind == "PAUSE":
        dash["paused"] = True
        _log_voice_audit("pause", None, "", "paused")
        return "Scanning paused."

    if kind == "RESUME":
        dash["paused"] = False
        _log_voice_audit("resume", None, "", "resumed")
        return "Resuming scans."

    # ── WATCHLIST_ADD ─────────────────────────────────────────────────────────
    if kind == "WATCHLIST_ADD":
        symbol = params.get("symbol", "").upper().strip()
        if not symbol:
            return "I didn't catch the symbol to add."
        favs = dash.get("favourites", [])
        if symbol not in favs:
            favs.append(symbol)
            dash["favourites"] = favs
            try:
                _fav_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favourites.json")
                with open(_fav_path, "w") as f:
                    _json.dump(favs, f)
            except Exception as e:
                log.warning("Could not save favourites: %s", e)
        _log_voice_audit("watchlist_add", symbol, "", "added")
        return f"Added {symbol} to your watchlist."

    # ── TRADE_BUY / TRADE_SELL ────────────────────────────────────────────────
    if kind in ("TRADE_BUY", "TRADE_SELL"):
        symbol = params.get("symbol", "").upper().strip()
        if not symbol:
            return "I didn't catch the symbol. Please try again."

        # Block duplicate pending commands for the same symbol
        for existing in bot_state._pending_voice_commands:
            if existing.get("symbol") == symbol and not existing.get("confirmed"):
                return (
                    f"You already have a pending command for {symbol}. "
                    "Say cancel to clear it first."
                )

        cmd = {
            "type": kind,
            "symbol": symbol,
            "params": params,
            "confirmed": False,
            "created_at": datetime.now(timezone.utc),
            "voice_text": f"{kind} {symbol}",
        }
        bot_state._pending_voice_commands.append(cmd)
        _log_voice_audit(kind.lower(), symbol, cmd["voice_text"], "queued_unconfirmed")

        verb = "buy" if kind == "TRADE_BUY" else "close"
        return (
            f"You said {verb} {symbol}. "
            "Say confirm to proceed or cancel to abort."
        )

    # ── CONFIRM ───────────────────────────────────────────────────────────────
    if kind == "CONFIRM":
        for cmd in bot_state._pending_voice_commands:
            if not cmd.get("confirmed"):
                cmd["confirmed"] = True
                sym = cmd.get("symbol", "")
                _log_voice_audit("confirm", sym, "", "confirmed")
                return "Trade queued. Will execute between scans."
        return "Nothing to confirm."

    # ── CANCEL ────────────────────────────────────────────────────────────────
    if kind == "CANCEL":
        for cmd in list(bot_state._pending_voice_commands):
            if not cmd.get("confirmed"):
                try:
                    bot_state._pending_voice_commands.remove(cmd)
                except ValueError:
                    pass
                sym = cmd.get("symbol", "")
                _log_voice_audit("cancel", sym, "", "cancelled")
                return "Cancelled."
        return "Nothing to cancel."

    # Fallback — should not reach here
    return "I didn't understand that command."
