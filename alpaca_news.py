# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpaca_news.py                            ║
# ║   Single responsibility: receive real-time news from Alpaca  ║
# ║   WebSocket, assess materiality, fire trigger callback.      ║
# ║                                                              ║
# ║   Replaces Yahoo RSS polling + Finviz scraping.              ║
# ║   Push-based Benzinga feed — no polling, no scraping.        ║
# ║   Nothing else lives here. No market data. No trading logic. ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import threading
import logging

from config import CONFIG
from news_infrastructure import HeadlineDeduplicator, SymbolCooldown

log = logging.getLogger("decifer.alpaca_news")

# Match materiality thresholds used by news_sentinel
_KEYWORD_THRESHOLD     = CONFIG.get("sentinel_keyword_threshold", 3)
_CLAUDE_CONF_THRESHOLD = CONFIG.get("sentinel_claude_confidence", 7)


class AlpacaNewsStream:
    """
    Subscribes to Alpaca news WebSocket (v1beta1/news), subscribes to *.
    On each article: filters to current universe symbols → keyword score →
    Claude deep-read → fires on_trigger_fn if material.

    Drop-in replacement for NewsSentinel's Yahoo RSS / Finviz polling.
    Uses the same on_trigger_fn callback and trigger dict schema so
    bot_sentinel.handle_news_trigger requires no changes.

    Usage:
        stream = AlpacaNewsStream(
            get_universe_fn=lambda: [...symbols...],
            on_trigger_fn=handle_news_trigger,
        )
        stream.start()
    """

    def __init__(self, get_universe_fn, on_trigger_fn) -> None:
        self.get_universe = get_universe_fn
        self.on_trigger   = on_trigger_fn
        self._stream      = None
        self._thread: threading.Thread | None = None
        self._running     = False
        self._dedup       = HeadlineDeduplicator(max_size=5000)
        self._cooldown    = SymbolCooldown(
            cooldown_minutes=CONFIG.get("sentinel_cooldown_minutes", 10)
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start streaming. Non-blocking — runs in a daemon thread."""
        if self._running:
            log.debug("AlpacaNewsStream: already running")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="alpaca-news-stream"
        )
        self._thread.start()
        log.info("📰 Alpaca news stream started (Benzinga real-time feed)")

    def stop(self) -> None:
        """Stop the stream gracefully."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
        log.info("📰 Alpaca news stream stopped")

    # ── Background thread ─────────────────────────────────────────────────────

    def _run(self) -> None:
        """Connect to Alpaca news WebSocket and block until stop() is called."""
        api_key    = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")

        if not api_key or not secret_key:
            log.warning(
                "AlpacaNewsStream: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — disabled"
            )
            self._running = False
            return

        try:
            from alpaca.data.live import NewsDataStream

            self._stream = NewsDataStream(api_key, secret_key)

            async def on_news(article) -> None:
                try:
                    self._process_article(article)
                except Exception as exc:
                    log.error(f"AlpacaNewsStream: processing error — {exc}")

            self._stream.subscribe_news(on_news, "*")
            self._stream.run()   # blocks until stop() is called

        except ImportError:
            log.error("AlpacaNewsStream: alpaca-py not installed — pip install alpaca-py")
            self._running = False
        except Exception as exc:
            log.error(f"AlpacaNewsStream: stream error — {exc}")
            self._running = False

    # ── Article pipeline ──────────────────────────────────────────────────────

    def _process_article(self, article) -> None:
        """
        For each incoming article:
          1. Filter — only symbols in current universe
          2. Dedup  — skip if headline already processed
          3. Score  — keyword materiality gate
          4. Claude — deep-read to confirm and extract catalyst
          5. Fire   — on_trigger_fn callback if confirmed material
        """
        headline = getattr(article, "headline", "") or ""
        symbols  = getattr(article, "symbols",  []) or []

        if not headline or not symbols:
            return

        # ── 1. Universe filter ────────────────────────────────
        universe = set(self.get_universe())
        relevant = [s for s in symbols if s in universe]
        if not relevant:
            return

        # ── 2. Dedup ──────────────────────────────────────────
        if not self._dedup.add_if_new(headline):
            return

        # ── 3. Keyword materiality score ──────────────────────
        try:
            from news import keyword_score, BULLISH_STRONG, BEARISH_STRONG
        except ImportError:
            log.debug("AlpacaNewsStream: news module unavailable — skipping article")
            return

        kw = keyword_score([headline])

        is_material = False
        urgency     = "MODERATE"

        if abs(kw["score"]) >= _KEYWORD_THRESHOLD:
            is_material = True
            urgency = "CRITICAL" if abs(kw["score"]) >= 6 else "HIGH"

        headline_lower = headline.lower()
        if any(k in headline_lower for k in BULLISH_STRONG) or \
           any(k in headline_lower for k in BEARISH_STRONG):
            is_material = True
            if urgency == "MODERATE":
                urgency = "HIGH"

        if not is_material:
            return

        direction = (
            "BULLISH" if kw["score"] > 0 else
            "BEARISH" if kw["score"] < 0 else
            "NEUTRAL"
        )

        created = getattr(article, "created_at", None)
        ts = created.isoformat() if hasattr(created, "isoformat") else str(created or "")

        # ── 4 + 5. Per-symbol: Claude gate then fire ──────────
        for sym in relevant:
            if self._cooldown.is_on_cooldown(sym):
                log.debug(f"AlpacaNewsStream: {sym} on cooldown — skipping")
                continue

            trigger = {
                "symbol":         sym,
                "headlines":      [headline],
                "headline_count": 1,
                "keyword_score":  kw["score"],
                "keyword_hits":   kw.get("keywords", [])[:8],
                "direction":      direction,
                "urgency":        urgency,
                "sources":        ["alpaca_benzinga"],
                "age_hours":      0.0,
                "triggered_at":   ts,
            }

            # Claude deep-read — confirms materiality, extracts catalyst
            try:
                from news_sentinel import deep_read_trigger
                trigger = deep_read_trigger(trigger)
            except Exception as exc:
                log.debug(f"AlpacaNewsStream: Claude call failed for {sym} — {exc}")

            if (trigger.get("claude_confidence", 0) < 4 and
                    trigger["urgency"] != "CRITICAL"):
                log.info(
                    f"📰 {sym}: confidence {trigger.get('claude_confidence', 0)}/10 "
                    f"below threshold — skipped"
                )
                continue

            self._cooldown.set_cooldown(sym)
            log.info(
                f"📰 ALPACA NEWS TRIGGER: {sym} | {direction} | "
                f"urgency={urgency} | {headline[:80]}"
            )

            try:
                self.on_trigger(trigger)
            except Exception as exc:
                log.error(f"AlpacaNewsStream: on_trigger error for {sym} — {exc}")
