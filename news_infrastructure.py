"""
news_infrastructure.py — Shared infrastructure for news sentinels.

Provides:
  headline_hash(text)     — normalise and hash a headline for dedup
  HeadlineDeduplicator    — tracks seen headlines, prevents re-triggering
  SymbolCooldown          — prevents firing on the same symbol twice in N minutes
  shared_dedup            — module-level singleton shared by all sentinel feeds
  shared_cooldown         — module-level singleton shared by all sentinel feeds

AlpacaNewsStream and NewsSentinel share the same dedup+cooldown instances so a
headline arriving on one feed cannot re-fire via the other.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime


def headline_hash(text: str) -> str:
    """Normalise and hash a headline for dedup. First 120 chars after stripping punctuation."""
    clean = re.sub(r"[^a-z0-9 ]", "", text.lower().strip())
    return clean[:120]


class HeadlineDeduplicator:
    """
    Tracks seen headline hashes. Prevents the same headline from triggering twice.
    Caps memory at max_size by evicting the oldest half when full.
    """

    def __init__(self, max_size: int = 5000):
        self._seen: set[str] = set()
        self._max_size = max_size

    def is_seen(self, text: str) -> bool:
        return headline_hash(text) in self._seen

    def add(self, text: str) -> None:
        h = headline_hash(text)
        if not h:
            return
        self._seen.add(h)
        if len(self._seen) > self._max_size:
            to_remove = list(self._seen)[: self._max_size // 2]
            for k in to_remove:
                self._seen.discard(k)

    def add_if_new(self, text: str) -> bool:
        """Add headline and return True if it was new, False if already seen."""
        if self.is_seen(text):
            return False
        self.add(text)
        return True

    def __len__(self) -> int:
        return len(self._seen)


class SymbolCooldown:
    """
    Prevents firing on the same symbol twice within cooldown_minutes.
    """

    def __init__(self, cooldown_minutes: int = 10):
        self._cooldowns: dict[str, datetime] = {}
        self.cooldown_minutes = cooldown_minutes

    def is_on_cooldown(self, symbol: str) -> bool:
        last = self._cooldowns.get(symbol)
        if not last:
            return False
        elapsed = (datetime.now(UTC) - last).total_seconds()
        return elapsed < self.cooldown_minutes * 60

    def set_cooldown(self, symbol: str) -> None:
        self._cooldowns[symbol] = datetime.now(UTC)


# ── Shared singletons ─────────────────────────────────────────────────────────
# All sentinel feeds (AlpacaNewsStream, NewsSentinel) share these instances so a
# headline seen on one feed cannot re-fire via the other.
# Cooldown duration is read lazily from config to avoid import cycles.
def _default_cooldown_minutes() -> int:
    try:
        from config import CONFIG
        return int(CONFIG.get("sentinel_cooldown_minutes", 10))
    except Exception:
        return 10


shared_dedup: HeadlineDeduplicator = HeadlineDeduplicator(max_size=10000)
shared_cooldown: SymbolCooldown = SymbolCooldown(cooldown_minutes=_default_cooldown_minutes())
