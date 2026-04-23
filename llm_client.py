# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  llm_client.py                              ║
# ║   Single Anthropic client singleton. All LLM calls route     ║
# ║   through here so there is exactly one place that            ║
# ║   instantiates anthropic.Anthropic().                        ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import time

import anthropic

from config import CONFIG

log = logging.getLogger("decifer.llm_client")

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    return _client


def call_apex(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> str:
    """Opus call for the Apex Synthesizer. System prompt is prompt-cached."""
    client = _get_client()
    model = CONFIG.get("claude_model_alpha", "claude-opus-4-6")
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )
            return resp.content[0].text.strip()
        except anthropic.APIStatusError as e:
            last_err = e
            if e.status_code in (529, 503, 502):
                wait = 2 ** attempt
                log.warning("Apex API transient error %s — retry in %ss", e.status_code, wait)
                time.sleep(wait)
            else:
                raise
        except anthropic.APIConnectionError as e:
            last_err = e
            wait = 2 ** attempt
            log.warning("Apex connection error — retry in %ss: %s", wait, e)
            time.sleep(wait)
    raise RuntimeError(f"Apex call failed after 3 attempts: {last_err}") from last_err


def call_sonnet(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 500,
) -> str:
    """Sonnet call for out-of-band uses only (learning.py weekly_review)."""
    client = _get_client()
    model = CONFIG.get("claude_model", "claude-sonnet-4-6")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error("Sonnet API error: %s", e)
        return ""
