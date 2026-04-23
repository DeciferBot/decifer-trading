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


def call_apex_with_meta(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> tuple[str, dict]:
    """
    Opus call for the Apex Synthesizer. Returns (text, meta).

    meta is a dict containing:
        latency_ms        — wall-clock time spent in the API call (last attempt
                            only if retries occurred; attempts counted separately)
        attempts          — how many attempts were made (1 on success, up to 3)
        input_tokens      — from resp.usage if available, else None
        output_tokens     — from resp.usage if available, else None
        cache_read_tokens — prompt-cache hit tokens if available, else None
        cache_creation_tokens — prompt-cache miss tokens if available, else None
        model             — the model id actually called

    Phase 7C.3: added to support shadow-record latency fields without breaking
    existing call_apex callers (which still get the text-only return).
    """
    client = _get_client()
    model = CONFIG.get("claude_model_alpha", "claude-opus-4-6")
    last_err: Exception | None = None
    attempts = 0
    for attempt in range(3):
        attempts += 1
        t0 = time.perf_counter()
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
            latency_ms = int((time.perf_counter() - t0) * 1000)
            text = resp.content[0].text.strip()
            usage = getattr(resp, "usage", None)
            meta = {
                "latency_ms": latency_ms,
                "attempts": attempts,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
                "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", None),
                "model": model,
            }
            return text, meta
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


def call_apex(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> str:
    """Opus call for the Apex Synthesizer. System prompt is prompt-cached.

    Thin wrapper around call_apex_with_meta that discards the meta dict for
    backwards compatibility with callers that don't need latency/token data.
    """
    text, _meta = call_apex_with_meta(system_prompt, user_prompt, max_tokens)
    return text


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
