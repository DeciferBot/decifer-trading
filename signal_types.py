# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  signal_types.py                           ║
# ║   Typed Signal dataclass — canonical signal representation   ║
# ║                                                              ║
# ║   Produced by: scan_and_score() in bot.py                   ║
# ║   Consumed by: signal_dispatcher.py, IC tracker (future)    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Canonical path for the typed signals audit log — stored in data/, not root
SIGNALS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "signals_typed.jsonl")


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
    regime_context  : market regime label at time of signal ("TRENDING_UP" etc.)
    rationale       : human-readable summary from the agent layer (optional)
    price           : last price at scoring time (needed by dispatcher for order sizing)
    atr             : Average True Range at scoring time (needed for stop calculation)
    """

    symbol: str
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    conviction_score: float  # 0–10
    dimension_scores: dict  # {"trend": 7, "momentum": 5, ...}
    timestamp: datetime
    regime_context: str
    rationale: str = ""
    # Routing metadata — populated from raw scored dict, needed by execute_buy
    price: float = 0.0
    atr: float = 0.0  # 5-minute ATR (bar noise — used for stop sizing)
    atr_daily: float = 0.0  # Daily ATR (session range — used by trade advisor for PT sizing)
    candle_gate: str = "UNKNOWN"
    instrument: str = "stock"  # "stock", "fx", "option" — routes get_contract()
    scanner_tier: str = ""  # "D" for Position Research Universe; "" for Tier A/B/C
    extension_at_entry: dict | None = None  # {atr_distance_50ema, pct_from_20d_low, pct_above_donch_high}
    # Handoff provenance — populated when the symbol originated from the active opportunity universe.
    # Preserved in signals_log.jsonl so downstream analysis can attribute signal quality to source.
    handoff_source_labels: list | None = None
    handoff_route: str | None = None
    handoff_reason_to_care: str | None = None
    handoff_freshness_status: str | None = None
    handoff_candidate_id: str | None = None
    # Scan provenance — populated by signal_pipeline._scored_to_signals() for training linkage.
    # observation_id joins this signal to its signals_log.jsonl record and any derived training record.
    scan_id: str = ""
    observation_id: str = ""   # deterministic: "{scan_id}_{symbol}"
    ranking_position: int = 0  # rank within all scored symbols (1 = highest score)
    ranking_total: int = 0     # total symbols scored this scan cycle

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (timestamp as ISO string)."""
        d = {
            "symbol": self.symbol,
            "direction": self.direction,
            "conviction_score": round(self.conviction_score, 3),
            "dimension_scores": self.dimension_scores,
            "timestamp": self.timestamp.isoformat(),
            "regime_context": self.regime_context,
            "rationale": self.rationale,
            "price": self.price,
            "atr": self.atr,
            "atr_daily": self.atr_daily,
        }
        if self.handoff_source_labels is not None:
            d["handoff_source_labels"] = self.handoff_source_labels
        if self.handoff_route is not None:
            d["handoff_route"] = self.handoff_route
        if self.handoff_reason_to_care is not None:
            d["handoff_reason_to_care"] = self.handoff_reason_to_care
        if self.handoff_freshness_status is not None:
            d["handoff_freshness_status"] = self.handoff_freshness_status
        if self.handoff_candidate_id is not None:
            d["handoff_candidate_id"] = self.handoff_candidate_id
        if self.scan_id:
            d["scan_id"] = self.scan_id
        if self.observation_id:
            d["observation_id"] = self.observation_id
        if self.ranking_position:
            d["ranking_position"] = self.ranking_position
        if self.ranking_total:
            d["ranking_total"] = self.ranking_total
        return d

    def to_json(self) -> str:
        """Single-line JSON string for appending to signals_log.jsonl."""
        return json.dumps(self.to_dict())
