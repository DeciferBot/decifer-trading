# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_types.py                           ║
# ║   Typed Signal dataclass — canonical signal representation   ║
# ║                                                              ║
# ║   Produced by: scan_and_score() in bot.py                   ║
# ║   Consumed by: signal_dispatcher.py, IC tracker (future)    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# Canonical path for the typed signals audit log — single source of truth
SIGNALS_LOG = "signals_log.jsonl"


@dataclass
class Signal:
    """
    Canonical representation of a scored trading opportunity.

    Produced by the scan loop after multi-timeframe scoring.
    Consumed by dispatch_signals() for order routing and by the IC
    tracker for validation.

    Fields
    ------
    symbol          : ticker
    direction       : LONG | SHORT | NEUTRAL (weighted majority vote across dimensions)
    conviction_score: 0–10 float (raw engine score 0–50 divided by 5)
    dimension_scores: per-dimension breakdown {"trend": x, "momentum": y, ...} each 0–10
    timestamp       : UTC datetime when the signal was generated
    regime_context  : market regime label at time of signal ("BULL_TRENDING" etc.)
    source_agents   : list of agent IDs that agreed on this signal (populated post-agents)
    rationale       : human-readable summary from the agent layer (optional)
    price           : last price at scoring time (needed by dispatcher for order sizing)
    atr             : Average True Range at scoring time (needed for stop calculation)
    """

    symbol: str
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    conviction_score: float          # 0–10
    dimension_scores: dict           # {"trend": 7, "momentum": 5, ...}
    timestamp: datetime
    regime_context: str
    source_agents: list = field(default_factory=list)
    rationale: str = ""
    # Routing metadata — populated from raw scored dict, needed by execute_buy
    price: float = 0.0
    atr: float = 0.0         # 5-minute ATR (bar noise — used for stop sizing)
    atr_daily: float = 0.0   # Daily ATR (session range — used by trade advisor for PT sizing)
    candle_gate: str = "UNKNOWN"

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (timestamp as ISO string)."""
        return {
            "symbol":           self.symbol,
            "direction":        self.direction,
            "conviction_score": round(self.conviction_score, 3),
            "dimension_scores": self.dimension_scores,
            "timestamp":        self.timestamp.isoformat(),
            "regime_context":   self.regime_context,
            "source_agents":    self.source_agents,
            "rationale":        self.rationale,
            "price":            self.price,
            "atr":              self.atr,
            "atr_daily":        self.atr_daily,
        }

    def to_json(self) -> str:
        """Single-line JSON string for appending to signals_log.jsonl."""
        return json.dumps(self.to_dict())
