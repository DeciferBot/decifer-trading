# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  architecture/layer_boundary.py            ║
# ║   Layer definitions and boundary classification             ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
layer_boundary.py — Explicit layer definitions for Decifer Trading.

Decifer Trading v1 is an intelligence product.  The system is organised into
named layers with strict import boundaries:

  data_connector   — raw market data providers (Alpaca, FMP, AV, FRED, IBKR data)
  intelligence     — signal scoring, regime, macro, theme, candidate generation
  saas_output      — customer-facing payload builders and API adapters
  dashboard_admin  — internal operational dashboards (no customer access)
  execution        — broker integration, order placement, position mutation
  shared_library   — cross-layer utilities (config, schemas, logging, llm_client)
  test_only        — test-suite modules only; may cross any boundary in tests
  archive_or_deprecated — legacy code; not imported by runtime modules

Boundary rules (enforced by scripts/verify_intelligence_execution_separation.py)
────────────────────────────────────────────────────────────────────────────────
  intelligence   must NOT import execution
  saas_output    must NOT import execution
  data_connector must NOT import execution (data flows up, never down)
  execution      MAY import intelligence outputs and data_connector results
  shared_library may be imported by any layer
  test_only      may import any layer (tests only)
"""
from __future__ import annotations

import enum
from pathlib import Path


class Layer(str, enum.Enum):
    DATA_CONNECTOR       = "data_connector"
    INTELLIGENCE         = "intelligence"
    SAAS_OUTPUT          = "saas_output"
    DASHBOARD_ADMIN      = "dashboard_admin"
    EXECUTION            = "execution"
    SHARED_LIBRARY       = "shared_library"
    TEST_ONLY            = "test_only"
    ARCHIVE_OR_DEPRECATED = "archive_or_deprecated"


# ---------------------------------------------------------------------------
# Module-to-layer mapping
# Keyed by module stem (no .py, no path prefix).
# ---------------------------------------------------------------------------

_EXECUTION_MODULES: frozenset[str] = frozenset({
    "orders_core",
    "orders_options",
    "orders_portfolio",
    "orders_guards",
    "orders_state",
    "orders_contracts",
    "orders",              # legacy wrapper
    "bot_ibkr",
    "ibkr_reconciler",
    "ibkr_streaming",
    "smart_execution",
    "execution_agent",
    "signal_dispatcher",   # bridges intelligence→execution; treated as execution
    "bracket_health",
    "fill_watcher",
    "pdt_rule",
    "safety_overlay",
    "risk_gates",
    "guardrails",
    "bot_trading",         # orchestrates execution cycle
    "bot",                 # main entry point, starts execution
    # --- Execution-adjacent: these modules consume orders_state / orders_core ---
    # They sit at the intelligence→execution boundary but write to execution state.
    "apex_orchestrator",   # runs Track A/B with execute=True; writes via signal_dispatcher
    "options_entries",     # calls execute_buy_option directly
    "options",             # monitors live option positions; reads active_trades
    "options_scanner",     # scans for options opportunities; imports options (execution)
    "pm_engine",           # executes TRIM/FULL_EXIT/ROTATE via orders_core
    "pm_rails",            # guardrails wrapper around pm_engine (execution)
    "news_sentinel",       # triggers live execution via apex_call(execute=True)
    "alpaca_news",         # news stream that fires news_sentinel (execution trigger path)
    "price_updater",       # updates live position prices in orders_state
})

_INTELLIGENCE_MODULES: frozenset[str] = frozenset({
    "market_intelligence",
    "run_intelligence_pipeline",
    "live_driver_resolver",
    "candidate_resolver",
    "theme_activation_engine",
    "universe_builder",
    "universe_committed",
    "universe_promoter",
    "universe_position",
    "scanner",
    "market_observer",
    "signal_pipeline",
    "signal_types",
    "catalyst_engine",
    "macro_calendar",
    "macro_transmission_matrix",
    "earnings_calendar",
    "news",
    "news_infrastructure",
    "social_sentiment",
    "alpha_decay",
    "alpha_validation",
    "ic_calculator",
    "ic_validator",
    "ic_decision_writer",
    "pattern_library",
    "theme_tracker",
    "momentum_sentinel",
    "sympathy_scanner",
    "fx_signals",
    "ml_observation_writer",
    "training_store",
    "ic_weights",
    "phase_gate",
    "entry_gate",
    "handoff_reader",
    "expression_router",
    "overnight_research",
    "apex_cap_score",
    "pm_thesis",
    "pm_score_resolver",
    "pm_observability",
    "pm_outcome_tracker",
    "options_provider",
    "iv_skew",
    "portfolio_manager",
    "portfolio_optimizer",
    "position_sizing",
    "alpha_vantage_client",
    "fmp_client",
    "fred_client",
    "alpaca_data",
    "alpaca_options",
    "alpaca_stream",
    "worker_evidence",
    "hmm_regime",
    # ── Sprint M12A — Theme Transmission Graph ──
    "theme_graph",
    # ── Source-of-truth market data (movers/news/tape) for intelligence cloud ──
    "market_data_provider",
})

_SAAS_OUTPUT_MODULES: frozenset[str] = frozenset({
    "mobile_api",
    "saas_intelligence_output",
    "market_now_builder",
    "intelligence_api",   # Flask app serving the DigitalOcean intelligence cloud surface
    # ── Sprint M11A — Customer Event Tape (customer-only) ──
    "customer_event_classifier",  # pure deterministic event classifier
    "customer_event_tape",        # customer-only tape writer/reader
    "market_now_reconciler",      # helper for market_now_builder only
    # ── Sprint M12A — Theme Transmission Graph API ──
    "theme_graph_api",            # Flask blueprint for TTG customer routes
})

_DASHBOARD_ADMIN_MODULES: frozenset[str] = frozenset({
    "bot_dashboard",
    "dashboard",
    "bot_health",
    "bot_account",
    "bot_state",
    "bot_sentinel",
    "bot_hot_reload",
})

_SHARED_LIBRARY_MODULES: frozenset[str] = frozenset({
    "config",
    "runtime_config",
    "version",
    "schemas",
    "event_log",
    "llm_client",
    "trade_context",
    "trade_data_contract",
    "analytics",
    "voice_agent",
    "voice_context_builder",
    "voice_explainability_tools",
    "telegram_bot",
    # Cross-layer: used by both intelligence and execution
    "learning",   # trade analytics, performance logging; read by both layers
    "risk",       # risk classification; consulted by intelligence and execution
})


# ---------------------------------------------------------------------------
# Classification API
# ---------------------------------------------------------------------------

def classify_module_path(path: str | Path) -> Layer:
    """
    Return the Layer that the module at `path` belongs to.

    Uses the module stem (filename without .py) against the registered sets.
    Falls back to path-prefix heuristics for subdirectory modules.
    Returns Layer.SHARED_LIBRARY when no specific layer is matched.
    """
    p = Path(path)
    stem = p.stem

    # Test files — always test_only regardless of content
    parts = p.parts
    if "tests" in parts or stem.startswith("test_") or p.name == "conftest.py":
        return Layer.TEST_ONLY

    # Archive / deprecated
    if "archive" in parts or "deprecated" in parts or "chief-decifer" in parts:
        return Layer.ARCHIVE_OR_DEPRECATED

    # Signals subdirectory — intelligence
    if "signals" in parts:
        return Layer.INTELLIGENCE

    # Architecture subdirectory — shared library (meta, not runtime)
    if "architecture" in parts:
        return Layer.SHARED_LIBRARY

    # Scripts — test_only for purposes of boundary checking (run offline)
    if "scripts" in parts:
        return Layer.TEST_ONLY

    # Match by stem
    if stem in _EXECUTION_MODULES:
        return Layer.EXECUTION
    if stem in _INTELLIGENCE_MODULES:
        return Layer.INTELLIGENCE
    if stem in _SAAS_OUTPUT_MODULES:
        return Layer.SAAS_OUTPUT
    if stem in _DASHBOARD_ADMIN_MODULES:
        return Layer.DASHBOARD_ADMIN
    if stem in _SHARED_LIBRARY_MODULES:
        return Layer.SHARED_LIBRARY

    return Layer.SHARED_LIBRARY


def is_execution_path(path: str | Path) -> bool:
    """True if the module at `path` belongs to the execution layer."""
    return classify_module_path(path) == Layer.EXECUTION


def is_customer_safe_path(path: str | Path) -> bool:
    """
    True if the module is approved for customer-facing / SaaS output use.
    Only saas_output and shared_library modules are customer-safe.
    """
    layer = classify_module_path(path)
    return layer in (Layer.SAAS_OUTPUT, Layer.SHARED_LIBRARY)


def get_execution_module_names() -> frozenset[str]:
    """Return the frozenset of known execution module stems."""
    return _EXECUTION_MODULES


def get_intelligence_module_names() -> frozenset[str]:
    """Return the frozenset of known intelligence module stems."""
    return _INTELLIGENCE_MODULES


def get_saas_output_module_names() -> frozenset[str]:
    """Return the frozenset of known SaaS output module stems."""
    return _SAAS_OUTPUT_MODULES
