"""
intelligence_adapters.py — read-only adapters for existing bot sources.

Single responsibility: wrap each existing production source in a read-only
adapter that extracts structured output without modifying, calling live APIs,
spawning threads, or triggering market data fetches. Unsafe sources are
explicitly skipped with a documented reason.

Adapter safety contract (all adapters):
  - side_effects_triggered = False always
  - live_data_called = False always
  - No mutations to any source file or module
  - No new threads spawned
  - No network calls
  - Unsafe function calls are skipped and marked

Public surface:
    AdapterResult            — per-adapter result dataclass
    AdapterSnapshot          — full snapshot of all adapter results
    generate_adapter_snapshot(output_path) -> dict
    load_adapter_snapshot(path) -> dict | None
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_ADAPTER_SCHEMA_VERSION = "1.0"
_DEFAULT_SNAPSHOT_PATH = "data/intelligence/source_adapter_snapshot.json"

# Source file paths (read-only)
_OVERNIGHT_NOTES_PATH = "data/overnight_notes.json"
_TIER_D_PATH = "data/position_research_universe.json"
_TIER_B_PATH = "data/daily_promoted.json"
_FAVOURITES_PATH = "data/favourites.json"
_POSITIONS_PATH = "data/positions.json"
_COMMITTED_PATH = "data/committed_universe.json"


def _read_json(path: str) -> tuple[Any, str | None]:
    if not os.path.exists(path):
        return None, f"Not found: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"Failed to read {path}: {e}"


@dataclass
class AdapterResult:
    adapter_name: str
    source_status: str          # "available" | "unavailable" | "skipped_due_side_effect_risk"
    source_path_or_module: str
    records_read: int
    symbols_read: list[str]
    fields_available: list[str]
    fields_missing: list[str]
    side_effects_triggered: bool = False
    live_data_called: bool = False
    output_summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name":           self.adapter_name,
            "source_status":          self.source_status,
            "source_path_or_module":  self.source_path_or_module,
            "records_read":           self.records_read,
            "symbols_read":           self.symbols_read,
            "symbols_count":          len(self.symbols_read),
            "fields_available":       self.fields_available,
            "fields_missing":         self.fields_missing,
            "side_effects_triggered": self.side_effects_triggered,
            "live_data_called":       self.live_data_called,
            "output_summary":         self.output_summary,
            "warnings":               self.warnings,
            "skipped_reason":         self.skipped_reason,
        }


# ---------------------------------------------------------------------------
# Adapter 1 — Scanner regime
# ---------------------------------------------------------------------------

def adapt_scanner_regime() -> AdapterResult:
    """
    SKIPPED: scanner.get_market_regime() requires live Alpaca/FMP/yfinance
    data fetches — unsafe for a read-only shadow adapter.

    Scanner constants (CORE_SYMBOLS, CORE_EQUITIES) are already consumed
    directly by universe_builder._load_tier_a() and are not duplicated here.
    """
    return AdapterResult(
        adapter_name="scanner_regime",
        source_status="skipped_due_side_effect_risk",
        source_path_or_module="scanner.get_market_regime()",
        records_read=0,
        symbols_read=[],
        fields_available=[],
        fields_missing=["regime", "vix_proxy", "spy_ema", "breadth",
                        "credit_spread", "tape_context"],
        side_effects_triggered=False,
        live_data_called=False,
        output_summary={},
        skipped_reason=(
            "get_market_regime() calls _regime_download() which fetches live "
            "data from Alpaca, FMP, and yfinance. This is a live network call "
            "that cannot safely run inside a read-only adapter. CORE_SYMBOLS "
            "and CORE_EQUITIES constants are read separately by universe_builder."
        ),
    )


# ---------------------------------------------------------------------------
# Adapter 2 — theme_tracker roster
# ---------------------------------------------------------------------------

def adapt_theme_tracker() -> AdapterResult:
    """
    Read theme_tracker.THEMES for legacy roster confirmation.
    Safe import: module-level load_custom_themes() reads a file only (no network).
    """
    try:
        from theme_tracker import THEMES  # type: ignore[import]
        symbols_by_theme: dict[str, list[str]] = {}
        all_symbols: list[str] = []
        themes_read: list[str] = []

        for theme_name, theme_data in THEMES.items():
            if not isinstance(theme_data, dict):
                continue
            syms = [
                s for s in (theme_data.get("symbols") or [])
                if isinstance(s, str) and s.strip()
            ]
            symbols_by_theme[theme_name] = syms
            all_symbols.extend(syms)
            themes_read.append(theme_name)

        # Dedup preserving order
        seen_order: dict[str, None] = {}
        for sym in all_symbols:
            seen_order[sym] = None
        all_symbols_dedup = list(seen_order)

        return AdapterResult(
            adapter_name="theme_tracker_roster",
            source_status="available",
            source_path_or_module="theme_tracker.THEMES",
            records_read=len(themes_read),
            symbols_read=all_symbols_dedup,
            fields_available=["symbols", "keywords", "active", "priority"],
            fields_missing=[],
            output_summary={
                "themes_read":         themes_read,
                "symbols_by_theme":    symbols_by_theme,
                "total_symbols":       len(all_symbols_dedup),
                "source_label":        "legacy_theme_tracker_read_only",
            },
        )
    except ImportError as exc:
        return AdapterResult(
            adapter_name="theme_tracker_roster",
            source_status="unavailable",
            source_path_or_module="theme_tracker.THEMES",
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols_by_theme"],
            warnings=[f"Import failed: {exc}"],
        )
    except Exception as exc:
        return AdapterResult(
            adapter_name="theme_tracker_roster",
            source_status="unavailable",
            source_path_or_module="theme_tracker.THEMES",
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols_by_theme"],
            warnings=[f"Unexpected error: {exc}"],
        )


# ---------------------------------------------------------------------------
# Adapter 3 — overnight research
# ---------------------------------------------------------------------------

def adapt_overnight_research(path: str = _OVERNIGHT_NOTES_PATH) -> AdapterResult:
    """
    Read data/overnight_notes.json — the pre-computed overnight research output.
    Does NOT call generate_overnight_notes() which makes live API calls.
    """
    data, err = _read_json(path)
    if err:
        return AdapterResult(
            adapter_name="overnight_research",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["market_tone", "sector_tone", "movers", "macro"],
            warnings=[err],
        )

    if not isinstance(data, dict):
        return AdapterResult(
            adapter_name="overnight_research",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["market_tone", "sector_tone", "movers", "macro"],
            warnings=["File is not a JSON object"],
        )

    # Extract symbols from movers and market_tone and sector_tone
    symbol_set: dict[str, None] = {}
    for entry in (data.get("movers") or []):
        sym = entry.get("sym", "")
        if isinstance(sym, str) and sym.strip():
            symbol_set[sym] = None
    for entry in (data.get("market_tone") or []):
        sym = entry.get("sym", "")
        if isinstance(sym, str) and sym.strip():
            symbol_set[sym] = None
    for entry in (data.get("sector_tone") or []):
        sym = entry.get("sym", "")
        if isinstance(sym, str) and sym.strip():
            symbol_set[sym] = None
    symbols = list(symbol_set)

    fields_available = [k for k in data.keys() if data.get(k) is not None]
    fields_missing_candidates = ["movers", "market_tone", "sector_tone", "macro", "macro_snapshot"]
    fields_missing = [f for f in fields_missing_candidates if f not in data or not data[f]]

    # Build macro snapshot
    macro_snapshot: dict[str, Any] = {}
    for item in (data.get("macro") or []):
        if isinstance(item, dict) and item.get("name"):
            macro_snapshot[item["name"]] = {
                "value": item.get("value"),
                "unit":  item.get("unit"),
                "date":  item.get("date"),
            }

    generated = data.get("generated", "unknown")
    available_flag = data.get("available", False)

    warnings: list[str] = []
    if not available_flag:
        warnings.append("overnight_notes.json available=False — research may be stale")

    return AdapterResult(
        adapter_name="overnight_research",
        source_status="available",
        source_path_or_module=path,
        records_read=len(data),
        symbols_read=symbols,
        fields_available=fields_available,
        fields_missing=fields_missing,
        output_summary={
            "generated":           generated,
            "available":           available_flag,
            "movers_count":        len(data.get("movers") or []),
            "market_tone_count":   len(data.get("market_tone") or []),
            "sector_tone_count":   len(data.get("sector_tone") or []),
            "macro_snapshot":      macro_snapshot,
            "news_summary":        data.get("news") or {},
            "performance_summary": data.get("performance") or {},
            "source_label":        "overnight_research_read_only",
        },
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Adapter 4 — catalyst engine
# ---------------------------------------------------------------------------

def adapt_catalyst_engine() -> AdapterResult:
    """
    Read today's catalyst watchlist from data/candidates_YYYY-MM-DD.json.
    Does NOT call CatalystEngine.start() or spawn any threads.
    If today's file is absent, marks adapter as unavailable.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    candidates_path = f"data/candidates_{today}.json"

    data, err = _read_json(candidates_path)
    if err:
        return AdapterResult(
            adapter_name="catalyst_engine",
            source_status="unavailable",
            source_path_or_module=candidates_path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["catalyst_candidates", "scores", "catalyst_reason"],
            warnings=[f"No catalyst candidates file for today ({today}): {err}"],
            skipped_reason="",
        )

    if not isinstance(data, (dict, list)):
        return AdapterResult(
            adapter_name="catalyst_engine",
            source_status="unavailable",
            source_path_or_module=candidates_path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["catalyst_candidates"],
            warnings=["candidates file is not a dict or list"],
        )

    # File can be a dict {symbol: {...}} or a list [{symbol: ..., ...}]
    if isinstance(data, dict):
        entries = list(data.values())
        symbols = list(data.keys())
    else:
        entries = data
        symbols = [e.get("symbol", e.get("ticker", "")) for e in entries if isinstance(e, dict)]
        symbols = [s for s in symbols if isinstance(s, str) and s.strip()]

    catalyst_candidates = []
    for sym, entry in zip(symbols, entries) if isinstance(data, dict) else [(e.get("symbol", e.get("ticker", "")), e) for e in entries]:
        if not isinstance(sym, str) or not sym.strip():
            continue
        catalyst_candidates.append({
            "symbol":        sym,
            "catalyst_score": entry.get("catalyst_score", entry.get("score", 0)) if isinstance(entry, dict) else 0,
            "reason":        entry.get("reason", entry.get("catalyst_type", "")) if isinstance(entry, dict) else "",
            "route_hint":    ["swing", "watchlist"],
            "source_label":  "catalyst_watchlist_read_only",
        })

    return AdapterResult(
        adapter_name="catalyst_engine",
        source_status="available",
        source_path_or_module=candidates_path,
        records_read=len(catalyst_candidates),
        symbols_read=[c["symbol"] for c in catalyst_candidates],
        fields_available=["symbol", "catalyst_score", "reason", "route_hint"],
        fields_missing=[],
        output_summary={
            "catalyst_candidates":   catalyst_candidates,
            "catalyst_count":        len(catalyst_candidates),
            "source_label":          "catalyst_watchlist_read_only",
            "route_hint":            ["swing", "watchlist"],
        },
    )


# ---------------------------------------------------------------------------
# Adapter 5 — Tier D position research
# ---------------------------------------------------------------------------

def adapt_tier_d(path: str = _TIER_D_PATH) -> AdapterResult:
    """
    Read data/position_research_universe.json.
    Does NOT call universe_position.rebuild_position_research_universe().
    """
    data, err = _read_json(path)
    if err:
        return AdapterResult(
            adapter_name="tier_d_position_research",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols", "discovery_score"],
            warnings=[err],
        )
    if not isinstance(data, dict):
        return AdapterResult(
            adapter_name="tier_d_position_research",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols"],
            warnings=["Not a JSON object"],
        )

    records = [s for s in (data.get("symbols") or []) if isinstance(s, dict) and s.get("ticker")]
    symbols = [r["ticker"] for r in records]
    has_score = any("discovery_score" in r for r in records[:3])

    return AdapterResult(
        adapter_name="tier_d_position_research",
        source_status="available",
        source_path_or_module=path,
        records_read=len(records),
        symbols_read=symbols,
        fields_available=["ticker", "discovery_score", "primary_archetype", "secondary_tags"] if has_score
                        else ["ticker"],
        fields_missing=[] if has_score else ["discovery_score"],
        output_summary={
            "count":          len(records),
            "built_at":       data.get("built_at", "unknown"),
            "reason_to_care": "structural_candidate_source",
            "route_hint":     ["position", "watchlist"],
            "source_label":   "tier_d_position_research_read_only",
        },
    )


# ---------------------------------------------------------------------------
# Adapter 6 — Tier B daily promoted
# ---------------------------------------------------------------------------

def adapt_tier_b(path: str = _TIER_B_PATH) -> AdapterResult:
    """
    Read data/daily_promoted.json.
    Does NOT trigger live promoter refresh or fetch 5-minute data.
    """
    data, err = _read_json(path)
    if err:
        return AdapterResult(
            adapter_name="tier_b_daily_promoted",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols", "score"],
            warnings=[err],
        )
    if not isinstance(data, dict):
        return AdapterResult(
            adapter_name="tier_b_daily_promoted",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols"],
            warnings=["Not a JSON object"],
        )

    records = [s for s in (data.get("symbols") or []) if isinstance(s, dict) and s.get("ticker")]
    symbols = [r["ticker"] for r in records]
    has_score = any("score" in r for r in records[:3])

    return AdapterResult(
        adapter_name="tier_b_daily_promoted",
        source_status="available",
        source_path_or_module=path,
        records_read=len(records),
        symbols_read=symbols,
        fields_available=["ticker", "score", "gap_pct", "catalyst_score"] if has_score else ["ticker"],
        fields_missing=[],
        output_summary={
            "count":          len(records),
            "promoted_at":    data.get("promoted_at", "unknown"),
            "reason_to_care": "attention_shadow_only",
            "route_hint":     ["intraday_swing", "watchlist"],
            "source_label":   "tier_b_daily_promoted_read_only",
        },
    )


# ---------------------------------------------------------------------------
# Adapter 7 — committed universe
# ---------------------------------------------------------------------------

def adapt_committed_universe(path: str = _COMMITTED_PATH) -> AdapterResult:
    """
    Read committed universe via data/committed_universe.json.
    Equivalent to universe_committed.load_committed_universe() which is a safe
    file read. Avoids import to keep adapter self-contained.
    """
    data, err = _read_json(path)
    if err:
        return AdapterResult(
            adapter_name="committed_universe",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols", "eligible_pool"],
            warnings=[err],
        )
    if not isinstance(data, dict):
        return AdapterResult(
            adapter_name="committed_universe",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols"],
            warnings=["Not a JSON object"],
        )

    raw_symbols = data.get("symbols") or []
    if raw_symbols and isinstance(raw_symbols[0], dict):
        symbols = [s["symbol"] for s in raw_symbols if isinstance(s, dict) and s.get("symbol")]
    else:
        symbols = [s for s in raw_symbols if isinstance(s, str) and s.strip()]

    return AdapterResult(
        adapter_name="committed_universe",
        source_status="available",
        source_path_or_module=path,
        records_read=len(symbols),
        symbols_read=symbols,
        fields_available=["symbol", "dollar_volume", "price", "exchange"],
        fields_missing=[],
        output_summary={
            "count":      len(symbols),
            "built_at":   data.get("built_at", "unknown"),
            "threshold":  data.get("threshold_dollar_volume"),
            "source_label": "committed_universe_read_only",
        },
    )


# ---------------------------------------------------------------------------
# Adapter 8 — favourites / manual conviction
# ---------------------------------------------------------------------------

def adapt_favourites(path: str = _FAVOURITES_PATH) -> AdapterResult:
    """Read data/favourites.json — manual conviction list."""
    data, err = _read_json(path)
    if err:
        return AdapterResult(
            adapter_name="favourites_manual_conviction",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols"],
            warnings=[err],
        )

    if isinstance(data, list):
        symbols = [s for s in data if isinstance(s, str) and s.strip()]
    elif isinstance(data, dict):
        symbols = [s for s in (data.get("symbols") or data.get("favourites") or [])
                   if isinstance(s, str) and s.strip()]
    else:
        return AdapterResult(
            adapter_name="favourites_manual_conviction",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols"],
            warnings=["Unexpected format"],
        )

    return AdapterResult(
        adapter_name="favourites_manual_conviction",
        source_status="available",
        source_path_or_module=path,
        records_read=len(symbols),
        symbols_read=symbols,
        fields_available=["symbol"],
        fields_missing=[],
        output_summary={
            "count":      len(symbols),
            "protected":  True,
            "route_hint": ["manual_conviction", "watchlist"],
            "source_label": "favourites_read_only",
        },
    )


# ---------------------------------------------------------------------------
# Adapter 9 — held positions
# ---------------------------------------------------------------------------

def adapt_held_positions(path: str = _POSITIONS_PATH) -> AdapterResult:
    """
    Read data/positions.json if available.
    Does NOT call live IBKR connection or query broker.
    If file is absent, marks adapter unavailable.
    """
    data, err = _read_json(path)
    if err:
        return AdapterResult(
            adapter_name="held_positions",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["held_symbols", "pnl", "qty"],
            warnings=[err],
            skipped_reason="",
        )

    if isinstance(data, dict):
        symbols = [k for k in data.keys() if isinstance(k, str) and k.strip()]
    elif isinstance(data, list):
        symbols = [p.get("symbol", p.get("ticker", "")) for p in data
                   if isinstance(p, dict)]
        symbols = [s for s in symbols if s.strip()]
    else:
        return AdapterResult(
            adapter_name="held_positions",
            source_status="unavailable",
            source_path_or_module=path,
            records_read=0,
            symbols_read=[],
            fields_available=[],
            fields_missing=["symbols"],
            warnings=["Unexpected format"],
        )

    return AdapterResult(
        adapter_name="held_positions",
        source_status="available",
        source_path_or_module=path,
        records_read=len(symbols),
        symbols_read=symbols,
        fields_available=["symbol", "qty", "pnl", "entry"],
        fields_missing=[],
        output_summary={
            "count":      len(symbols),
            "protected":  True,
            "route_hint": ["held"],
            "source_label": "held_positions_static_read_only",
            "note":       "Static snapshot — not live IBKR state",
        },
    )


# ---------------------------------------------------------------------------
# Snapshot assembler
# ---------------------------------------------------------------------------

_ADAPTER_FUNCTIONS = [
    ("scanner_regime",              adapt_scanner_regime),
    ("theme_tracker_roster",        adapt_theme_tracker),
    ("overnight_research",          adapt_overnight_research),
    ("catalyst_engine",             adapt_catalyst_engine),
    ("tier_d_position_research",    adapt_tier_d),
    ("tier_b_daily_promoted",       adapt_tier_b),
    ("committed_universe",          adapt_committed_universe),
    ("favourites_manual_conviction", adapt_favourites),
    ("held_positions",              adapt_held_positions),
]


def generate_adapter_snapshot(
    output_path: str = _DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """
    Run all read-only adapters and write source_adapter_snapshot.json.

    Returns the snapshot dict. Writes the file as a side-effect.
    The snapshot is purely informational — no production state is mutated.
    """
    now = datetime.now(timezone.utc)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    adapter_results: list[AdapterResult] = []
    all_warnings: list[str] = []
    source_files: list[str] = []

    for _name, fn in _ADAPTER_FUNCTIONS:
        try:
            result = fn()
        except Exception as exc:
            result = AdapterResult(
                adapter_name=_name,
                source_status="unavailable",
                source_path_or_module="",
                records_read=0,
                symbols_read=[],
                fields_available=[],
                fields_missing=[],
                warnings=[f"Adapter raised unexpected exception: {exc}"],
            )
        adapter_results.append(result)
        all_warnings.extend(result.warnings)
        if result.source_status == "available" and result.source_path_or_module:
            source_files.append(result.source_path_or_module)

    available_count = sum(1 for r in adapter_results if r.source_status == "available")
    unavailable_count = sum(1 for r in adapter_results if r.source_status == "unavailable")
    skipped_count = sum(1 for r in adapter_results if r.source_status == "skipped_due_side_effect_risk")
    total_symbols = sum(len(r.symbols_read) for r in adapter_results)

    unavailable_sources = [
        {
            "adapter_name": r.adapter_name,
            "reason":       r.warnings[0] if r.warnings else r.skipped_reason or "unknown",
        }
        for r in adapter_results
        if r.source_status in ("unavailable", "skipped_due_side_effect_risk")
    ]

    snapshot: dict[str, Any] = {
        "schema_version":    _ADAPTER_SCHEMA_VERSION,
        "generated_at":      generated_at,
        "mode":              "read_only_adapter_snapshot",
        "source_files":      list(dict.fromkeys(source_files)),
        "adapters": {r.adapter_name: r.to_dict() for r in adapter_results},
        "adapter_summary": {
            "adapters_total":                      len(adapter_results),
            "adapters_available":                  available_count,
            "adapters_unavailable":                unavailable_count,
            "adapters_skipped_due_side_effect_risk": skipped_count,
            "total_symbols_read":                  total_symbols,
        },
        "unavailable_sources": unavailable_sources,
        "warnings":          all_warnings,
        "live_output_changed": False,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    return snapshot


def load_adapter_snapshot(path: str = _DEFAULT_SNAPSHOT_PATH) -> dict[str, Any] | None:
    """Load an existing adapter snapshot. Returns None if not found or invalid."""
    data, err = _read_json(path)
    if err or not isinstance(data, dict):
        return None
    return data


if __name__ == "__main__":
    snapshot = generate_adapter_snapshot()
    summary = snapshot["adapter_summary"]
    print(f"Adapter snapshot → {_DEFAULT_SNAPSHOT_PATH}")
    print(f"  adapters_available:  {summary['adapters_available']}/{summary['adapters_total']}")
    print(f"  adapters_unavailable: {summary['adapters_unavailable']}")
    print(f"  adapters_skipped:    {summary['adapters_skipped_due_side_effect_risk']}")
    print(f"  total_symbols_read:  {summary['total_symbols_read']}")
    print(f"  live_output_changed: {snapshot['live_output_changed']}")
    for name, adapter in snapshot["adapters"].items():
        status = adapter["source_status"]
        count = adapter.get("symbols_count", 0)
        print(f"  [{status:30s}] {name} ({count} symbols)")
