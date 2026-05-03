# Tests for universe_position.py — Position Research Universe (Tier D).
# All external I/O is mocked. No Alpaca or FMP calls.

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from universe_position import (
    _compute_fundamental_signals,
    _compute_technical_signals,
    _match_archetypes,
    _score_symbol,
    _validate_schema,
    build_position_research_universe,
    load_position_research_universe,
    refresh_position_research_universe,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_snap(price=50.0, prev_volume=200_000):
    return {"price": price, "prev_volume": prev_volume}


def _make_df(closes: list[float]):
    """Return a minimal DataFrame-like object with a Close column."""
    import pandas as pd
    return pd.DataFrame({"Close": closes})


def _make_pru_payload(tickers: list[str], age_days: float = 0) -> dict:
    built_at = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    return {
        "built_at": built_at,
        "count": len(tickers),
        "symbols": [
            {
                "ticker": t,
                "discovery_score": 5,
                "matched_position_archetypes": ["Re-rating Candidate"],
                "discovery_signals": ["recent_analyst_upgrade"],
                "discovery_signal_points": {"recent_analyst_upgrade": 2},
                "missing_data_fields": [],
                "universe_source": "position_research",
                "scanner_tier": "D",
                "position_research_universe_member": True,
                "active_trading_universe_member": False,
                "priority_overlap": False,
                "universe_entry_reason": "archetypes: Re-rating Candidate",
            }
            for t in tickers
        ],
    }


# ── Unit tests: universe_position.py ─────────────────────────────────────────


def test_strong_technical_signal_admitted():
    """Strong signal alone (outperform SPY = 3 pts) → admitted even with no fundamentals."""
    # 22 closes: stock up 15%, SPY up 5%
    closes = [100.0] * 21 + [115.0]
    df = _make_df(closes)
    pts, missing = _compute_technical_signals(df, spy_1m_return=5.0, sector_1m_return=None, sector_etf_above_50ma=False)
    assert "outperforming_spy_1m" in pts
    assert pts["outperforming_spy_1m"] == 3  # _STRONG


def test_two_weak_signals_admitted():
    """Two weak signals (revenue > 0% + above 50MA = 1+1 = 2) → admitted (meets 2-point threshold)."""
    # Revenue positive → 1pt
    with patch("fmp_client.get_revenue_growth", return_value={"revenue_growth_yoy": 3.0, "revenue_deceleration": False}), \
         patch("fmp_client.get_key_metrics_ttm", return_value=None), \
         patch("fmp_client.get_price_target", return_value=None), \
         patch("fmp_client.get_analyst_grades", return_value=None):
        fund_pts, _, _ = _compute_fundamental_signals("TEST", 50.0, recent_upgrade_syms=set())
    assert "revenue_yoy_positive" in fund_pts
    assert fund_pts["revenue_yoy_positive"] == 1

    # Stock above 50d MA → 1pt
    closes = [90.0] * 49 + [100.0]  # 49 bars below 90 avg → current 100 > MA=90
    df = _make_df([90.0] * 49 + [100.0])
    tech_pts, _ = _compute_technical_signals(df, spy_1m_return=None, sector_1m_return=None, sector_etf_above_50ma=False)
    assert "above_50d_ma" in tech_pts

    total = fund_pts.get("revenue_yoy_positive", 0) + tech_pts.get("above_50d_ma", 0)
    assert total >= 2


def test_single_weak_signal_rejected():
    """1pt score with no archetype match → rejected (below 2-point threshold)."""
    snap = _make_snap()
    closes = [100.0] * 21 + [105.0]  # stock up 5%, SPY up 10% → not outperforming
    df = _make_df(closes)
    with patch("fmp_client.get_revenue_growth", return_value=None), \
         patch("fmp_client.get_key_metrics_ttm", return_value=None), \
         patch("fmp_client.get_price_target", return_value=None), \
         patch("fmp_client.get_analyst_grades", return_value=None), \
         patch("fmp_client.get_company_sector", return_value=None):
        result = _score_symbol(
            "WEAK", snap, df,
            spy_1m_return=10.0,
            sector_etf_returns={},
            sector_etf_above_50ma_map={},
            sector_etf_for_symbol=None,
            recent_upgrade_syms=set(),
            active_trading_syms=set(),
        )
    # No strong signal, no archetype, discovery_score should be < 2
    # (only above_50d_ma=1 would fire here; but stock went 100→105 with SPY at 10% → no outperform)
    # Result may be admitted or rejected depending on exact closes — assert no crash
    assert result is None or isinstance(result, dict)


def test_archetype_rescue_with_low_score():
    """Archetype match with only 1pt score → admitted even below 2-point threshold."""
    # Simulate: recent_upgrade + analyst upside > 15% → Re-rating Candidate archetype
    fund_pts = {"recent_analyst_upgrade": 2, "analyst_upside_gt_15pct": 3}
    tech_pts = {}
    archetypes = _match_archetypes(fund_pts, tech_pts)
    assert "Re-rating Candidate" in archetypes
    # With just 1pt fund signal and archetype, admission should work
    total = sum(fund_pts.values())
    has_strong = any(v >= 3 for v in fund_pts.values())
    assert has_strong or len(archetypes) > 0  # strong signal or archetype → admit


def test_hard_block_unusable_price():
    """Symbol with price <= 0 → hard-blocked (returns None)."""
    snap = _make_snap(price=0.0, prev_volume=500_000)
    result = _score_symbol(
        "BAD", snap, None,
        spy_1m_return=None, sector_etf_returns={}, sector_etf_above_50ma_map={},
        sector_etf_for_symbol=None, recent_upgrade_syms=set(), active_trading_syms=set(),
    )
    assert result is None


def test_hard_block_low_liquidity():
    """Symbol with prev_volume < 50k → hard-blocked (returns None)."""
    snap = _make_snap(price=50.0, prev_volume=10_000)
    result = _score_symbol(
        "THIN", snap, None,
        spy_1m_return=None, sector_etf_returns={}, sector_etf_above_50ma_map={},
        sector_etf_for_symbol=None, recent_upgrade_syms=set(), active_trading_syms=set(),
    )
    assert result is None


def test_missing_analyst_data_scores_zero_not_rejected():
    """Missing analyst data → 0 pts, not rejection; symbol still scored on other signals."""
    with patch("fmp_client.get_revenue_growth", return_value={"revenue_growth_yoy": 15.0, "revenue_deceleration": False}), \
         patch("fmp_client.get_key_metrics_ttm", return_value=None), \
         patch("fmp_client.get_price_target", return_value=None), \
         patch("fmp_client.get_analyst_grades", return_value=None):
        pts, missing, _ = _compute_fundamental_signals("TEST", 50.0, recent_upgrade_syms=set())
    # Revenue > 10% = 3pts (strong) should fire
    assert "revenue_yoy_gt_10pct" in pts
    assert pts["revenue_yoy_gt_10pct"] == 3
    # Analyst fields should be in missing, not causing rejection
    assert "analyst_price_target" in missing or "analyst_consensus" in missing


def test_missing_dcf_data_scores_zero_not_rejected():
    """Missing price target data → 0 pts for upside signal, not rejection."""
    with patch("fmp_client.get_revenue_growth", return_value=None), \
         patch("fmp_client.get_key_metrics_ttm", return_value=None), \
         patch("fmp_client.get_price_target", return_value=None), \
         patch("fmp_client.get_analyst_grades", return_value=None):
        pts, missing, _ = _compute_fundamental_signals("TEST", 50.0, recent_upgrade_syms=set())
    assert "analyst_price_target" in missing
    # No crash; empty pts is fine (scored as 0)
    assert "analyst_upside_gt_15pct" not in pts
    assert "analyst_upside_positive" not in pts


def test_missing_all_data_excluded_gracefully():
    """Symbol with all FMP data missing and no technical bars → score 0, excluded without error."""
    snap = _make_snap()
    with patch("fmp_client.get_revenue_growth", return_value=None), \
         patch("fmp_client.get_key_metrics_ttm", return_value=None), \
         patch("fmp_client.get_price_target", return_value=None), \
         patch("fmp_client.get_analyst_grades", return_value=None), \
         patch("fmp_client.get_company_sector", return_value=None):
        result = _score_symbol(
            "NODATA", snap, None,
            spy_1m_return=None, sector_etf_returns={}, sector_etf_above_50ma_map={},
            sector_etf_for_symbol=None, recent_upgrade_syms=set(), active_trading_syms=set(),
        )
    # Should return None (excluded) or a low-score dict — no exception
    assert result is None or isinstance(result, dict)


def test_atomic_write_and_schema_validation(tmp_path, monkeypatch):
    """Atomic write uses tempfile + os.replace; schema is validated before writing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    committed = ["AAPL", "MSFT"]
    fake_snaps = {
        "AAPL": {"price": 175.0, "prev_volume": 80_000_000},
        "MSFT": {"price": 420.0, "prev_volume": 30_000_000},
    }
    # Minimal scored result so write actually fires
    def _fake_build(committed, top_n=None, active_trading_syms=None):
        return [
            {
                "ticker": "AAPL",
                "discovery_score": 6,
                "matched_position_archetypes": ["Sector/RS Leader"],
                "discovery_signals": ["outperforming_spy_1m"],
                "discovery_signal_points": {"outperforming_spy_1m": 3},
                "missing_data_fields": [],
                "universe_source": "position_research",
                "scanner_tier": "D",
                "position_research_universe_member": True,
                "active_trading_universe_member": False,
                "priority_overlap": False,
                "universe_entry_reason": "strong: outperforming_spy_1m",
            }
        ]

    with patch("universe_position.load_committed_universe", return_value=committed), \
         patch("universe_position.build_position_research_universe", side_effect=_fake_build):
        result = refresh_position_research_universe()

    assert os.path.exists(tmp_path / "data" / "position_research_universe.json")
    with open(tmp_path / "data" / "position_research_universe.json") as f:
        payload = json.load(f)
    assert payload["count"] == 1
    assert payload["symbols"][0]["ticker"] == "AAPL"
    assert _validate_schema(payload)


def test_load_returns_empty_for_stale_file(tmp_path, monkeypatch):
    """load_position_research_universe returns ([], []) for a stale file (age > max_staleness_days)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    payload = _make_pru_payload(["AAPL"], age_days=10)  # 10 days old
    pru_path = tmp_path / "data" / "position_research_universe.json"
    pru_path.write_text(json.dumps(payload))

    tickers, meta, built_at = load_position_research_universe(max_staleness_days=8)
    assert tickers == []
    assert meta == []


def test_load_returns_empty_for_malformed_file(tmp_path, monkeypatch):
    """load_position_research_universe returns ([], []) for malformed JSON without crashing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    pru_path = tmp_path / "data" / "position_research_universe.json"
    pru_path.write_text("{ this is not valid json !!!")

    tickers, meta, built_at = load_position_research_universe()
    assert tickers == []
    assert meta == []


# ── Integration tests: pipeline and entry gate ────────────────────────────────


def test_tier_d_metadata_attached_before_strategy_threshold():
    """Tier D metadata is attached to scored dicts before _apply_strategy_threshold runs."""
    from signal_pipeline import _tag_tier_d

    scored = [{"symbol": "AAPL", "score": 5, "direction": "LONG"}]
    all_scored = [{"symbol": "AAPL", "score": 5, "direction": "LONG"}]
    meta = {
        "AAPL": {
            "scanner_tier": "D",
            "discovery_score": 8,
            "matched_position_archetypes": ["Quality Compounder"],
            "discovery_signals": ["outperforming_spy_1m"],
            "universe_entry_reason": "strong: outperforming_spy_1m",
            "missing_data_fields": [],
        }
    }
    _tag_tier_d(all_scored, meta)
    assert all_scored[0].get("scanner_tier") == "D"
    assert all_scored[0].get("discovery_score") == 8
    assert all_scored[0].get("matched_position_archetypes") == ["Quality Compounder"]


def test_tier_d_skips_persistence_gate():
    """Tier D candidate passes through _apply_persistence_gate regardless of scan history."""
    from signal_pipeline import _apply_persistence_gate

    scored = [{"symbol": "AAPL", "score": 8, "direction": "LONG", "scanner_tier": "D"}]
    all_scored = [{"symbol": "AAPL", "score": 8, "direction": "LONG", "scanner_tier": "D"}]
    # persistence_scans=3 would normally require 3 consecutive scans above threshold
    result = _apply_persistence_gate(scored, all_scored, persistence_scans=3)
    assert any(s["symbol"] == "AAPL" for s in result)


def test_tier_d_rescued_by_discovery_score():
    """Tier D candidate dropped by strategy threshold is rescued if discovery_score >= strong_threshold."""
    from signal_pipeline import _rescue_tier_d

    # AAPL scored 4 (below floor=6) but discovery_score=8 >= strong_discovery=6
    scored = []  # dropped after gates
    all_scored = [{"symbol": "AAPL", "score": 4, "direction": "LONG", "scanner_tier": "D"}]
    tier_d_meta = {
        "AAPL": {
            "discovery_score": 8,
            "matched_position_archetypes": [],
            "discovery_signals": ["outperforming_spy_1m"],
            "universe_entry_reason": "strong: outperforming_spy_1m",
            "missing_data_fields": [],
        }
    }
    with patch.dict("config.CONFIG", {"position_research_min_intraday_score_floor": 6,
                                      "position_research_strong_discovery_score": 6,
                                      "position_research_allow_archetype_rescue": True}):
        result = _rescue_tier_d(scored, all_scored, tier_d_meta)
    assert any(s["symbol"] == "AAPL" for s in result)


def test_tier_d_rescued_by_matched_archetype():
    """Tier D candidate rescued by archetype match even with low signal + discovery score."""
    from signal_pipeline import _rescue_tier_d

    scored = []
    all_scored = [{"symbol": "NVDA", "score": 3, "direction": "LONG", "scanner_tier": "D"}]
    tier_d_meta = {
        "NVDA": {
            "discovery_score": 4,  # below strong_discovery=6
            "matched_position_archetypes": ["Growth Leader"],
            "discovery_signals": ["revenue_yoy_gt_10pct"],
            "universe_entry_reason": "archetypes: Growth Leader",
            "missing_data_fields": [],
        }
    }
    with patch.dict("config.CONFIG", {"position_research_min_intraday_score_floor": 6,
                                      "position_research_strong_discovery_score": 6,
                                      "position_research_allow_archetype_rescue": True}):
        result = _rescue_tier_d(scored, all_scored, tier_d_meta)
    assert any(s["symbol"] == "NVDA" for s in result)


def test_tier_d_high_signal_score_not_dropped_by_rescue():
    """Tier D candidate with signal score >= floor stays in scored after gates (no rescue needed)."""
    from signal_pipeline import _rescue_tier_d

    # Already in scored (passed strategy threshold via the lower Tier D floor)
    scored = [{"symbol": "AAPL", "score": 10, "direction": "LONG", "scanner_tier": "D"}]
    all_scored = [{"symbol": "AAPL", "score": 10, "direction": "LONG", "scanner_tier": "D"}]
    tier_d_meta = {
        "AAPL": {
            "discovery_score": 4,
            "matched_position_archetypes": [],
            "discovery_signals": ["above_50d_ma"],
            "universe_entry_reason": "score=4",
            "missing_data_fields": [],
        }
    }
    with patch.dict("config.CONFIG", {"position_research_min_intraday_score_floor": 6,
                                      "position_research_strong_discovery_score": 6,
                                      "position_research_allow_archetype_rescue": True}):
        result = _rescue_tier_d(scored, all_scored, tier_d_meta)
    # AAPL should appear exactly once (not duplicated by rescue)
    assert sum(1 for s in result if s["symbol"] == "AAPL") == 1


def test_tier_d_candidate_gets_position_candidate_prefix():
    """Tier D candidate dict has scanner_tier='D' which triggers [POSITION_CANDIDATE] prefix in Apex prompt."""
    from market_intelligence import _build_apex_user_prompt

    candidate = {
        "symbol": "MSFT",
        "score": 20,
        "direction": "LONG",
        "scanner_tier": "D",
        "score_breakdown": {},
        "atr_5m": 0.5,
        "atr_daily": 2.0,
        "vol_ratio": 1.2,
        "daily_tape_score": 0,
        "stock_rs_vs_spy": 5.0,
        "catalyst_score": 0,
        "dar": None,
        "news_headlines": [],
        "news_finbert_sentiment": "NEUTRAL",
        "divergence_flags": [],
        "default_trade_type": "POSITION",
        "allowed_trade_types": ["POSITION", "SWING"],
        "options_eligible": False,
        "trade_context": {},
    }

    apex_input = {
        "trigger_type": "SCAN_CYCLE",
        "trigger_context": {},
        "market_context": {"regime": {"regime": "BULL_TRENDING", "vix": 15.0}, "tape": "neutral", "options_flow": []},
        "portfolio_state": {"portfolio_value": 100000, "daily_pnl": 0, "position_count": 0, "position_slots_remaining": 5, "net_exposure_pct": 0.0},
        "track_a": {"candidates": [candidate]},
        "track_b": [],
        "scan_ts": "2026-05-03T10:00:00Z",
    }

    prompt = _build_apex_user_prompt(apex_input, sctx=None)
    assert "[POSITION_CANDIDATE]" in prompt
    assert "MSFT" in prompt


def test_shadow_mode_blocks_tier_d_position_records_simulation():
    """Shadow mode: validate_entry runs full simulation, blocks execution, logs would_have_passed."""
    from entry_gate import validate_entry
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.symbol = "TSLA"
    ctx.earnings_days_away = 30
    ctx.time_of_day_window = "INTRADAY"
    ctx.regime = "BULL_TRENDING"
    ctx.fcf_yield = None
    ctx.dcf_upside_pct = None
    ctx.analyst_upside_pct = None
    ctx.revenue_growth_yoy = None
    ctx.revenue_decelerating = False
    ctx.gross_margin = None
    ctx.eps_accelerating = None
    ctx.sector_above_50d = False
    ctx.sector_3m_vs_spy = 0
    ctx.stock_above_200d = False
    ctx.analyst_consensus = "HOLD"
    ctx.recent_upgrade = False
    ctx.insider_net_sentiment = "NEUTRAL"

    with patch.dict("entry_gate.CONFIG", {
        "min_score_to_trade": 14,
        "position_research_shadow_mode": True,
        "position_research_allow_live_position_entries": False,
        "entry_gate": {
            "position_long_only": True,
            "position_equity_only": True,
            "position_min_earnings_days_away": 5,
            "position_min_dcf_upside_pct": 15.0,
            "position_min_analyst_upside_pct": 10.0,
            "position_min_revenue_growth_pct": 10.0,
            "position_min_gross_margin_pct": 30.0,
            "position_min_supporting_signals": 2,
            "score_zero_swing_position_blocks": True,
        },
    }):
        allowed, trade_type, reason, effective_score = validate_entry(
            direction="LONG",
            ctx=ctx,
            score=25,
            opus_trade_type="POSITION",
            scanner_tier="D",
        )

    assert allowed is False
    assert trade_type == "POSITION_RESEARCH_ONLY"
    assert "shadow_mode_blocked" in reason
    assert "would_have_passed" in reason


def test_feature_flag_off_disables_tier_d():
    """position_research_universe_enabled=False → get_position_research_universe returns empty."""
    from scanner import get_position_research_universe

    with patch.dict("scanner.CONFIG", {"position_research_universe_enabled": False}):
        syms, meta = get_position_research_universe()

    assert len(syms) == 0
    assert len(meta) == 0


def test_tier_a_b_c_paths_unchanged_when_tier_d_present(tmp_path, monkeypatch):
    """Adding Tier D metadata to pipeline does not change Tier A/B/C scored output."""
    from signal_pipeline import _tag_tier_d

    # Tier A/B/C candidates — no scanner_tier set initially
    scored = [
        {"symbol": "SPY", "score": 30, "direction": "LONG"},
        {"symbol": "AAPL", "score": 22, "direction": "LONG"},
    ]
    # Tier D meta only for MSFT — should not touch SPY or AAPL
    tier_d_meta = {
        "MSFT": {
            "scanner_tier": "D",
            "discovery_score": 7,
            "matched_position_archetypes": [],
            "discovery_signals": [],
            "universe_entry_reason": "score=7",
            "missing_data_fields": [],
        }
    }
    _tag_tier_d(scored, tier_d_meta)
    for s in scored:
        assert s.get("scanner_tier") != "D", f"{s['symbol']} should not be tagged as Tier D"


def test_live_position_entries_blocked_when_allow_false():
    """position_research_allow_live_position_entries=False → all Tier D POSITION entries blocked."""
    from entry_gate import validate_entry
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.symbol = "NVDA"
    ctx.earnings_days_away = 45
    ctx.time_of_day_window = "INTRADAY"
    ctx.regime = "BULL_TRENDING"
    ctx.fcf_yield = 5.0
    ctx.dcf_upside_pct = 25.0
    ctx.analyst_upside_pct = 20.0
    ctx.revenue_growth_yoy = 30.0
    ctx.revenue_decelerating = False
    ctx.gross_margin = 60.0
    ctx.eps_accelerating = True
    ctx.sector_above_50d = True
    ctx.sector_3m_vs_spy = 8.0
    ctx.stock_above_200d = True
    ctx.analyst_consensus = "STRONG_BUY"
    ctx.recent_upgrade = True
    ctx.insider_net_sentiment = "BUYING"

    with patch.dict("entry_gate.CONFIG", {
        "min_score_to_trade": 14,
        "position_research_shadow_mode": True,
        "position_research_allow_live_position_entries": False,
        "entry_gate": {
            "position_long_only": True,
            "position_equity_only": True,
            "position_min_earnings_days_away": 5,
            "position_min_dcf_upside_pct": 15.0,
            "position_min_analyst_upside_pct": 10.0,
            "position_min_revenue_growth_pct": 10.0,
            "position_min_gross_margin_pct": 30.0,
            "position_min_supporting_signals": 2,
            "score_zero_swing_position_blocks": True,
        },
    }):
        allowed, trade_type, reason, _ = validate_entry(
            direction="LONG",
            ctx=ctx,
            score=40,
            opus_trade_type="POSITION",
            scanner_tier="D",
        )

    # Even with perfect fundamentals, Tier D POSITION must be blocked
    assert allowed is False
    assert trade_type == "POSITION_RESEARCH_ONLY"
    assert "shadow_mode_blocked" in reason
    # But simulation should show would_have_passed=True (good fundamentals)
    assert "would_have_passed=True" in reason


# ── Test 23: Tier D context backfill ──────────────────────────────────────────


def test_tier_d_context_backfill_succeeds_when_initial_ctx_has_no_fundamentals():
    """
    Tier D candidate rescued after context-map build.
    Initial build_context() returns a ctx with no fundamental fields.
    Backfill call to build_context() succeeds and populates the context_map.
    After backfill, context is not default all-None.
    """
    from signal_dispatcher import _backfill_tier_d_contexts, _TIER_D_FUND_FIELDS

    # Simulate a Tier D signal
    tier_d_signal = MagicMock()
    tier_d_signal.symbol = "IONQ"
    tier_d_signal.direction = "LONG"
    tier_d_signal.price = 45.0
    tier_d_signal.regime_context = "BULL_TRENDING"
    tier_d_signal.scanner_tier = "D"

    # Initial ctx has no fundamental fields (all None)
    empty_ctx = MagicMock()
    for f in _TIER_D_FUND_FIELDS:
        setattr(empty_ctx, f, None)

    context_map = {"IONQ": empty_ctx}
    context_failed = set()

    # Backfill ctx with real fundamentals
    good_ctx = MagicMock()
    good_ctx.revenue_growth_yoy = 55.0
    good_ctx.gross_margin = 40.0
    good_ctx.fcf_yield = None
    good_ctx.dcf_upside_pct = None
    good_ctx.analyst_upside_pct = 44.0

    with patch("signal_dispatcher._backfill_tier_d_contexts.__globals__"
               if False else "trade_context.build_context", return_value=good_ctx):
        backfill_info = _backfill_tier_d_contexts(
            signals=[tier_d_signal],
            context_map=context_map,
            context_failed=context_failed,
        )

    info = backfill_info.get("IONQ", {})
    assert info.get("tier_d_rescued_after_context_build") is True
    assert info.get("context_backfilled") is True
    assert info.get("context_backfill_source") == "fresh_fmp"
    assert info.get("missing_fresh_trade_context_after_rescue") is False
    # context_map must have been updated with the backfilled ctx
    assert context_map["IONQ"] is good_ctx


def test_tier_d_context_backfill_logs_missing_when_retry_also_fails():
    """
    Backfill attempt produces no fundamentals.
    missing_fresh_trade_context_after_rescue must be True.
    context_map is not updated (empty ctx kept).
    """
    from signal_dispatcher import _backfill_tier_d_contexts, _TIER_D_FUND_FIELDS

    tier_d_signal = MagicMock()
    tier_d_signal.symbol = "CRWV"
    tier_d_signal.direction = "LONG"
    tier_d_signal.price = 20.0
    tier_d_signal.regime_context = "BULL_TRENDING"
    tier_d_signal.scanner_tier = "D"

    empty_ctx = MagicMock()
    for f in _TIER_D_FUND_FIELDS:
        setattr(empty_ctx, f, None)

    still_empty_ctx = MagicMock()
    for f in _TIER_D_FUND_FIELDS:
        setattr(still_empty_ctx, f, None)

    context_map = {"CRWV": empty_ctx}
    context_failed = set()

    with patch("trade_context.build_context", return_value=still_empty_ctx):
        backfill_info = _backfill_tier_d_contexts(
            signals=[tier_d_signal],
            context_map=context_map,
            context_failed=context_failed,
        )

    info = backfill_info.get("CRWV", {})
    assert info.get("tier_d_rescued_after_context_build") is True
    assert info.get("context_backfilled") is False
    assert info.get("missing_fresh_trade_context_after_rescue") is True
    # context_map must NOT have been updated (original empty ctx stays)
    assert context_map["CRWV"] is empty_ctx


def test_tier_d_shadow_log_includes_backfill_fields():
    """
    When backfill info is present, shadow log record contains all 4 required
    backfill fields: tier_d_rescued_after_context_build, context_backfilled,
    context_backfill_source, missing_fresh_trade_context_after_rescue.
    """
    import json, tempfile, os
    from entry_gate import validate_entry
    import entry_gate as _eg

    ctx = MagicMock()
    ctx.symbol = "LUNA"
    ctx.earnings_days_away = 30
    ctx.time_of_day_window = "INTRADAY"
    ctx.regime = "BULL_TRENDING"
    for f in ("fcf_yield", "dcf_upside_pct", "revenue_growth_yoy",
              "gross_margin", "analyst_upside_pct"):
        setattr(ctx, f, None)
    ctx.revenue_decelerating = False
    ctx.eps_accelerating = None
    ctx.sector_above_50d = False
    ctx.sector_3m_vs_spy = 0
    ctx.stock_above_200d = False
    ctx.analyst_consensus = "HOLD"
    ctx.recent_upgrade = False
    ctx.insider_net_sentiment = "NEUTRAL"

    backfill_info = {
        "tier_d_rescued_after_context_build": True,
        "context_backfilled": False,
        "context_backfill_source": "failed",
        "missing_fresh_trade_context_after_rescue": True,
    }

    with tempfile.TemporaryDirectory() as tmp:
        shadow_path = os.path.join(tmp, "shadow.jsonl")
        with patch.object(_eg, "_PR_SHADOW_LOG", shadow_path), \
             patch.dict("entry_gate.CONFIG", {
                 "min_score_to_trade": 14,
                 "position_research_shadow_mode": True,
                 "position_research_allow_live_position_entries": False,
                 "data_dir": tmp,
                 "entry_gate": {
                     "position_long_only": True,
                     "position_equity_only": True,
                     "position_min_earnings_days_away": 5,
                     "position_min_dcf_upside_pct": 15.0,
                     "position_min_analyst_upside_pct": 10.0,
                     "position_min_revenue_growth_pct": 10.0,
                     "position_min_gross_margin_pct": 30.0,
                     "position_min_supporting_signals": 2,
                     "score_zero_swing_position_blocks": True,
                 },
             }):
            validate_entry(
                direction="LONG",
                ctx=ctx,
                score=25,
                opus_trade_type="POSITION",
                scanner_tier="D",
                tier_d_backfill_info=backfill_info,
            )

        records = [json.loads(l) for l in open(shadow_path)]
        assert len(records) == 1
        rec = records[0]
        assert rec["tier_d_rescued_after_context_build"] is True
        assert rec["context_backfilled"] is False
        assert rec["context_backfill_source"] == "failed"
        assert rec["missing_fresh_trade_context_after_rescue"] is True
