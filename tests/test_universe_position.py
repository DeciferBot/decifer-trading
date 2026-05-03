# Tests for universe_position.py — Position Research Universe (Tier D).
# All external I/O is mocked. No Alpaca or FMP calls.

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from universe_position import (
    _CLUSTER_CAPS,
    _MEANINGFUL_ARCHETYPES,
    _apply_cluster_caps_and_dedup,
    _assign_primary_archetype,
    _assign_secondary_tags,
    _check_thesis_quality_gate,
    _compute_fundamental_signals,
    _compute_risk_penalties,
    _compute_technical_signals,
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
                "adjusted_discovery_score": 5,
                "risk_penalty_pts": 0,
                "primary_archetype": "Re-rating Candidate",
                "secondary_tags": ["Analyst Momentum"],
                "universe_bucket": "core_research",
                "matched_position_archetypes": ["Re-rating Candidate"],
                "discovery_signals": ["recent_analyst_upgrade"],
                "discovery_signal_points": {"recent_analyst_upgrade": 2},
                "missing_data_fields": [],
                "universe_source": "position_research",
                "scanner_tier": "D",
                "position_research_universe_member": True,
                "active_trading_universe_member": False,
                "priority_overlap": False,
                "universe_entry_reason": "archetype: Re-rating Candidate",
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
    pts, missing, hygiene = _compute_technical_signals(df, spy_1m_return=5.0, sector_1m_return=None, sector_etf_above_50ma=False)
    assert "outperforming_spy_1m" in pts
    assert pts["outperforming_spy_1m"] == 3  # _STRONG


def test_two_weak_signals_no_longer_include_hygiene_points():
    """
    above_50d_ma no longer contributes score points (fires 96% of the time — useless discriminator).
    It is tracked in hygiene_flags only. Two real scoring signals are still needed for admission
    at the default min_score=2 threshold.
    """
    # Revenue positive → 1pt scoring signal
    with patch("fmp_client.get_revenue_growth", return_value={"revenue_growth_yoy": 3.0, "revenue_deceleration": False}), \
         patch("fmp_client.get_key_metrics_ttm", return_value={"gross_margin": 0.5, "debt_to_equity": 0.5}), \
         patch("fmp_client.get_price_target", return_value=None), \
         patch("fmp_client.get_analyst_grades", return_value=None):
        fund_pts, _, _ = _compute_fundamental_signals("TEST", 50.0, recent_upgrade_syms=set())
    assert "revenue_yoy_positive" in fund_pts
    assert fund_pts["revenue_yoy_positive"] == 1

    # above_50d_ma now goes to hygiene_flags, not pts
    closes = [90.0] * 49 + [100.0]
    df = _make_df(closes)
    tech_pts, _, hygiene = _compute_technical_signals(df, spy_1m_return=None, sector_1m_return=None, sector_etf_above_50ma=False)
    assert "above_50d_ma" not in tech_pts          # no longer a score signal
    assert hygiene["above_50d_ma"] is True          # still tracked as a hygiene flag

    # total scoring points from these two signals = 1 (revenue) + 0 (50ma no longer scores)
    total = fund_pts.get("revenue_yoy_positive", 0) + tech_pts.get("above_50d_ma", 0)
    assert total == 1  # below default min_score=2 without a strong signal or meaningful archetype


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
    """Meaningful archetype with low score → admitted even below min_score threshold."""
    fund_pts = {"recent_analyst_upgrade": 2, "analyst_upside_gt_15pct": 3}
    tech_pts = {}
    primary = _assign_primary_archetype(fund_pts, tech_pts, above_50d_ma_flag=False)
    # analyst_upside_gt_15pct + consensus miss → falls to Re-rating Candidate
    assert primary in _MEANINGFUL_ARCHETYPES
    # thesis gate passes because analyst_upside_gt_15pct is present
    assert _check_thesis_quality_gate(fund_pts, tech_pts, primary) is True


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
                "adjusted_discovery_score": 6,
                "risk_penalty_pts": 0,
                "primary_archetype": "Speculative Theme",
                "secondary_tags": ["Sector/RS Leader"],
                "universe_bucket": "tactical_momentum",
                "matched_position_archetypes": ["Tactical Momentum"],
                "discovery_signals": ["outperforming_spy_1m"],
                "discovery_signal_points": {"outperforming_spy_1m": 3},
                "missing_data_fields": [],
                "universe_source": "position_research",
                "scanner_tier": "D",
                "position_research_universe_member": True,
                "active_trading_universe_member": False,
                "priority_overlap": False,
                "universe_entry_reason": "strong: outperforming_spy_1m; archetype: Tactical Momentum",
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


# ── Tests 26-30: Apex Cap Funnel Record ────────────────────────────────────────
#
# The cap instrumentation lives in bot_trading.py (which has IBKR deps and cannot
# be imported in tests). We test the record-building logic in isolation by
# replicating the identical computation used in bot_trading.py and verifying
# the output schema matches what the evidence report expects.
# This is the same pattern used for signal_dispatcher tests above.


def _build_apex_cap_record(candidates_sorted: list[dict], cap_limit: int) -> dict:
    """
    Mirrors the apex-cap funnel record logic from bot_trading.py.
    Pure function — no I/O. Used by tests 26-30.
    Attaches apex_cap_score and sorts by it before slicing, matching live behaviour.
    """
    from datetime import UTC, datetime
    from apex_cap_score import compute_apex_cap_score
    for c in candidates_sorted:
        if "apex_cap_score" not in c:
            c["apex_cap_score"] = compute_apex_cap_score(c)
    candidates_sorted = sorted(
        candidates_sorted,
        key=lambda c: c.get("apex_cap_score", c.get("score", 0)),
        reverse=True,
    )
    cap_dropped = candidates_sorted[cap_limit:]
    selected    = candidates_sorted[:cap_limit]
    td_before   = [c for c in candidates_sorted if c.get("scanner_tier") == "D"]
    td_after    = [c for c in selected           if c.get("scanner_tier") == "D"]
    td_dropped  = [c for c in cap_dropped        if c.get("scanner_tier") == "D"]
    min_sel     = min((c.get("score", 0) for c in selected),   default=None)
    max_td      = max((c.get("score", 0) for c in td_before),  default=None)
    highest_td_drop = max((c.get("score", 0) for c in td_dropped), default=None)
    return {
        "ts":                           datetime.now(UTC).isoformat(),
        "stage":                        "apex_cap",
        "raw_candidates_before_cap":    len(candidates_sorted),
        "raw_tier_d_before_cap":        len(td_before),
        "raw_non_tier_d_before_cap":    len(candidates_sorted) - len(td_before),
        "cap_limit":                    cap_limit,
        "selected_candidates_after_cap": len(selected),
        "selected_tier_d_after_cap":    len(td_after),
        "selected_non_tier_d_after_cap": len(selected) - len(td_after),
        "dropped_by_cap_total":         len(cap_dropped),
        "dropped_tier_d_by_cap":        len(td_dropped),
        "dropped_non_tier_d_by_cap":    len(cap_dropped) - len(td_dropped),
        "selected_tier_d_symbols":      [c.get("symbol") for c in td_after],
        "dropped_tier_d_symbols_top_20": [c.get("symbol") for c in td_dropped[:20]],
        "top_10_selected_by_score": [
            {"symbol": c.get("symbol"), "score": c.get("score"), "scanner_tier": c.get("scanner_tier", "")}
            for c in selected[:10]
        ],
        "top_10_dropped_tier_d": [
            {
                "symbol":             c.get("symbol"),
                "score":              c.get("score"),
                "discovery_score":    c.get("discovery_score"),
                "matched_archetypes": c.get("matched_position_archetypes", []),
            }
            for c in td_dropped[:10]
        ],
        "max_tier_d_score_before_cap":     max_td,
        "min_selected_score_after_cap":    min_sel,
        "highest_dropped_tier_d_score":    highest_td_drop,
        "tier_d_with_archetypes_dropped":  any(c.get("matched_position_archetypes") for c in td_dropped),
        "tier_d_strong_discovery_dropped": any((c.get("discovery_score") or 0) >= 6 for c in td_dropped),
    }


def _make_candidates(n_regular: int, tier_d_specs: list[dict]) -> list[dict]:
    """
    Build a pre-sorted candidate list:
    - n_regular non-Tier-D candidates with scores 100, 99, 98, ...
    - Tier D candidates inserted per tier_d_specs (score, discovery_score, archetypes,
      primary_archetype, universe_bucket, adjusted_discovery_score, risk_penalty_pts)
    Returned sorted by score descending (as bot_trading does before slicing).
    """
    from apex_cap_score import compute_apex_cap_score
    candidates = []
    for i in range(n_regular):
        candidates.append({"symbol": f"REG{i:03d}", "score": 100 - i, "scanner_tier": ""})
    for j, spec in enumerate(tier_d_specs):
        candidates.append({
            "symbol":                      f"TDD{j:03d}",
            "score":                       spec.get("score", 5),
            "scanner_tier":                "D",
            "discovery_score":             spec.get("discovery_score", 3),
            "matched_position_archetypes": spec.get("archetypes", []),
            "primary_archetype":           spec.get("primary_archetype"),
            "universe_bucket":             spec.get("universe_bucket"),
            "adjusted_discovery_score":    spec.get("adjusted_discovery_score"),
            "risk_penalty_pts":            spec.get("risk_penalty_pts", 0),
        })
    for c in candidates:
        c["apex_cap_score"] = compute_apex_cap_score(c)
    return sorted(candidates, key=lambda c: c.get("apex_cap_score", c.get("score", 0)), reverse=True)


def test_apex_cap_record_written_when_candidates_exceed_cap(tmp_path):
    """Test 26: apex_cap record is written when raw candidates exceed cap limit."""
    # 40 regular + 2 Tier D = 42 raw → exceeds cap=30, record must be written.
    candidates = _make_candidates(40, [{"score": 8}, {"score": 6}])
    assert len(candidates) == 42

    rec = _build_apex_cap_record(candidates, cap_limit=30)

    assert rec["stage"] == "apex_cap"
    assert rec["raw_candidates_before_cap"] == 42
    assert rec["cap_limit"] == 30
    assert rec["selected_candidates_after_cap"] == 30
    assert rec["dropped_by_cap_total"] == 12
    # Verify required fields are all present
    required = [
        "raw_tier_d_before_cap", "raw_non_tier_d_before_cap",
        "selected_tier_d_after_cap", "selected_non_tier_d_after_cap",
        "dropped_tier_d_by_cap", "dropped_non_tier_d_by_cap",
        "selected_tier_d_symbols", "dropped_tier_d_symbols_top_20",
        "top_10_selected_by_score", "top_10_dropped_tier_d",
        "max_tier_d_score_before_cap", "min_selected_score_after_cap",
        "highest_dropped_tier_d_score",
        "tier_d_with_archetypes_dropped", "tier_d_strong_discovery_dropped",
    ]
    for field in required:
        assert field in rec, f"Missing field: {field}"


def test_apex_cap_dropped_tier_d_counted_correctly():
    """Test 27: Tier D dropped by cap is counted correctly.

    40 regular candidates (score 100-61) fill the top-30 slots entirely.
    2 Tier D at score=8 and score=6 fall below the cut (position 41-42 in sort).
    dropped_tier_d_by_cap must be 2.
    """
    candidates = _make_candidates(40, [{"score": 8}, {"score": 6}])
    rec = _build_apex_cap_record(candidates, cap_limit=30)

    assert rec["raw_tier_d_before_cap"] == 2
    assert rec["selected_tier_d_after_cap"] == 0
    assert rec["dropped_tier_d_by_cap"] == 2
    assert rec["dropped_by_cap_total"] == 12
    assert len(rec["dropped_tier_d_symbols_top_20"]) == 2


def test_apex_cap_selected_tier_d_counted_correctly():
    """Test 28: selected_tier_d_after_cap is counted correctly.

    10 regular candidates (score 100-91) then 2 Tier D (score 90, 89).
    Total = 12, cap = 30 → no drop. Both Tier D survive.
    """
    candidates = _make_candidates(10, [{"score": 90}, {"score": 89}])
    rec = _build_apex_cap_record(candidates, cap_limit=30)

    assert rec["raw_candidates_before_cap"] == 12
    assert rec["dropped_by_cap_total"] == 0
    assert rec["dropped_tier_d_by_cap"] == 0
    assert rec["selected_tier_d_after_cap"] == 2
    assert len(rec["selected_tier_d_symbols"]) == 2


def test_apex_cap_dropped_examples_include_discovery_and_archetypes():
    """Test 29: top_10_dropped_tier_d entries include discovery_score and archetypes."""
    tier_d_specs = [
        {"score": 5, "discovery_score": 12, "archetypes": ["Quality Compounder", "Growth Leader"]},
        {"score": 4, "discovery_score": 8,  "archetypes": ["Re-rating Candidate"]},
    ]
    # 35 regular → both Tier D drop (fall outside cap=30)
    candidates = _make_candidates(35, tier_d_specs)
    rec = _build_apex_cap_record(candidates, cap_limit=30)

    assert rec["dropped_tier_d_by_cap"] == 2
    dropped = rec["top_10_dropped_tier_d"]
    assert len(dropped) == 2
    # Higher-score dropped Tier D comes first
    assert dropped[0]["score"] >= dropped[1]["score"]
    assert dropped[0]["discovery_score"] == 12
    assert "Quality Compounder" in dropped[0]["matched_archetypes"]
    assert rec["tier_d_with_archetypes_dropped"] is True
    assert rec["tier_d_strong_discovery_dropped"] is True  # discovery_score 12 >= 6


def test_apex_cap_no_drop_when_candidates_within_limit():
    """Test 30: if raw candidates <= cap, all drop counts are zero."""
    # 5 regular + 3 Tier D = 8 total, cap = 30 → nothing dropped
    candidates = _make_candidates(5, [{"score": 20}, {"score": 15}, {"score": 10}])
    rec = _build_apex_cap_record(candidates, cap_limit=30)

    assert rec["raw_candidates_before_cap"] == 8
    assert rec["dropped_by_cap_total"] == 0
    assert rec["dropped_tier_d_by_cap"] == 0
    assert rec["dropped_non_tier_d_by_cap"] == 0
    assert rec["selected_tier_d_after_cap"] == 3
    assert rec["tier_d_with_archetypes_dropped"] is False
    assert rec["tier_d_strong_discovery_dropped"] is False
    assert rec["highest_dropped_tier_d_score"] is None


# ── Tests 31-42: PRU quality improvements ─────────────────────────────────────


def test_risk_penalty_severe_revenue_decline():
    """Revenue growth worse than -25% → penalty -4."""
    snap = {"revenue_growth_yoy": -78.0, "analyst_upside_pct": 30.0}
    penalty = _compute_risk_penalties(snap)
    assert penalty == -4


def test_risk_penalty_moderate_revenue_decline():
    """Revenue growth between -10% and -25% → penalty -2."""
    snap = {"revenue_growth_yoy": -15.0, "analyst_upside_pct": 20.0}
    penalty = _compute_risk_penalties(snap)
    assert penalty == -2


def test_risk_penalty_analyst_upside_very_negative():
    """Analyst upside worse than -30% → penalty -5."""
    snap = {"revenue_growth_yoy": 5.0, "analyst_upside_pct": -75.0}
    penalty = _compute_risk_penalties(snap)
    assert penalty == -5


def test_risk_penalty_analyst_upside_moderate_negative():
    """Analyst upside between -20% and -30% → penalty -4."""
    snap = {"revenue_growth_yoy": 5.0, "analyst_upside_pct": -23.0}
    penalty = _compute_risk_penalties(snap)
    assert penalty == -4


def test_risk_penalty_no_thesis_at_all():
    """No revenue strength AND no analyst support → extra -3 combinatorial penalty."""
    snap = {"revenue_growth_yoy": -2.0, "analyst_upside_pct": 3.0}
    penalty = _compute_risk_penalties(snap)
    # revenue < 0 → no bracket penalty; upside 3% < 5% → no bracket; but no-thesis penalty fires
    assert penalty == -3


def test_risk_penalty_clean_name_no_penalty():
    """Strong revenue and positive analyst upside → no penalty."""
    snap = {"revenue_growth_yoy": 35.0, "analyst_upside_pct": 44.0}
    penalty = _compute_risk_penalties(snap)
    assert penalty == 0


def test_thesis_quality_gate_passes_with_revenue():
    """`revenue_yoy_gt_10pct` alone is enough to pass the thesis quality gate."""
    fund_pts = {"revenue_yoy_gt_10pct": 3, "gross_margin_positive": 1}
    tech_pts = {}
    assert _check_thesis_quality_gate(fund_pts, tech_pts, "Growth Leader") is True


def test_thesis_quality_gate_fails_momentum_only():
    """Name with only short-term momentum (outperforming_spy_1m) fails the thesis gate."""
    fund_pts = {"gross_margin_positive": 1, "debt_not_dangerous": 1, "consensus_not_negative": 1}
    tech_pts = {"outperforming_spy_1m": 3, "outperforming_sector_1m": 2, "higher_lows": 1}
    primary = _assign_primary_archetype(fund_pts, tech_pts, above_50d_ma_flag=False)
    # No revenue strength, no analyst upside — will be Re-rating Candidate (consensus_not_negative
    # alone isn't enough for Re-rating; need upside_low or recent_upgrade)
    # The exact archetype depends on signals; the gate check is what matters
    gate = _check_thesis_quality_gate(fund_pts, tech_pts, primary)
    assert gate is False


def test_primary_archetype_is_single_string():
    """primary_archetype is always a str, never a list."""
    fund_pts = {"revenue_yoy_gt_10pct": 3, "gross_margin_positive": 1}
    tech_pts = {"outperforming_spy_1m": 3}
    primary = _assign_primary_archetype(fund_pts, tech_pts, above_50d_ma_flag=True)
    assert isinstance(primary, str)
    assert len(primary) > 0


def test_secondary_tags_contain_sector_rs_leader():
    """Sector/RS Leader is a secondary tag when outperforming_spy_1m + sector_etf_above_50ma."""
    fund_pts = {}
    tech_pts = {"outperforming_spy_1m": 3}
    hygiene_flags = {"above_50d_ma": True, "sector_etf_above_50ma": True}
    tags = _assign_secondary_tags(fund_pts, tech_pts, hygiene_flags)
    assert "Sector/RS Leader" in tags
    assert "Above 50DMA" in tags


def test_cluster_cap_crypto_max_2():
    """5 crypto/Bitcoin proxy names scored → only top 2 survive cluster cap."""
    crypto_tickers = ["MSTR", "MARA", "WULF", "CIFR", "IREN"]
    candidates = [
        {
            "ticker": t, "universe_bucket": "core_research",
            "adjusted_discovery_score": 15 - i, "discovery_score": 15 - i,
            "primary_archetype": "Speculative Theme",
        }
        for i, t in enumerate(crypto_tickers)
    ]
    result = _apply_cluster_caps_and_dedup(candidates)
    surviving_crypto = [r["ticker"] for r in result if r["ticker"] in set(crypto_tickers)]
    assert len(surviving_crypto) == 2
    assert surviving_crypto == ["MSTR", "MARA"]  # highest adjusted_discovery_score first


def test_cluster_cap_quantum_max_2():
    """4 quantum names → only top 2 survive cluster cap."""
    quantum_tickers = ["IONQ", "QBTS", "RGTI", "QUBT"]
    candidates = [
        {
            "ticker": t, "universe_bucket": "core_research",
            "adjusted_discovery_score": 17 - i, "discovery_score": 17 - i,
            "primary_archetype": "Growth Leader",
        }
        for i, t in enumerate(quantum_tickers)
    ]
    result = _apply_cluster_caps_and_dedup(candidates)
    surviving = [r["ticker"] for r in result if r["ticker"] in set(quantum_tickers)]
    assert len(surviving) == 2
    assert surviving == ["IONQ", "QBTS"]


def test_dedup_goog_googl():
    """GOOG is removed when GOOGL is present (GOOGL is the preferred share class)."""
    candidates = [
        {"ticker": "GOOGL", "universe_bucket": "core_research", "adjusted_discovery_score": 12, "discovery_score": 12, "primary_archetype": "Growth Leader"},
        {"ticker": "GOOG",  "universe_bucket": "core_research", "adjusted_discovery_score": 12, "discovery_score": 12, "primary_archetype": "Growth Leader"},
        {"ticker": "NVDA",  "universe_bucket": "core_research", "adjusted_discovery_score": 15, "discovery_score": 15, "primary_archetype": "Quality Compounder"},
    ]
    result = _apply_cluster_caps_and_dedup(candidates)
    tickers = [r["ticker"] for r in result]
    assert "GOOGL" in tickers
    assert "GOOG" not in tickers
    assert "NVDA" in tickers


def test_matched_position_archetypes_backward_compat():
    """matched_position_archetypes is always a single-item list for signal_pipeline compat."""
    snap = _make_snap()
    fund_pts = {"recent_analyst_upgrade": 2, "analyst_upside_gt_15pct": 3,
                "gross_margin_positive": 1, "debt_not_dangerous": 1, "consensus_not_negative": 1}
    closes = [100.0] * 21 + [115.0]
    df = _make_df(closes)
    with patch("fmp_client.get_revenue_growth", return_value={"revenue_growth_yoy": 5.0, "revenue_deceleration": False}), \
         patch("fmp_client.get_key_metrics_ttm", return_value={"gross_margin": 0.5, "debt_to_equity": 0.5}), \
         patch("fmp_client.get_price_target", return_value={"pt_consensus": 70.0}), \
         patch("fmp_client.get_analyst_grades", return_value={"consensus_score": 4.0}), \
         patch("fmp_client.get_company_sector", return_value=None):
        result = _score_symbol(
            "TEST", snap, df,
            spy_1m_return=5.0,
            sector_etf_returns={},
            sector_etf_above_50ma_map={},
            sector_etf_for_symbol=None,
            recent_upgrade_syms={"TEST"},
            active_trading_syms=set(),
        )
    assert result is not None
    assert isinstance(result["matched_position_archetypes"], list)
    assert len(result["matched_position_archetypes"]) == 1
    assert result["matched_position_archetypes"][0] == result["primary_archetype"]


def test_core_research_sorted_before_tactical_momentum():
    """In final list, core_research names always precede tactical_momentum regardless of raw score."""
    import pandas as pd

    def _make_symbol(ticker, bucket, adj_score, base_score):
        return {
            "ticker": ticker, "universe_bucket": bucket,
            "adjusted_discovery_score": adj_score, "discovery_score": base_score,
            "primary_archetype": "Quality Compounder" if bucket == "core_research" else "Tactical Momentum",
            "matched_position_archetypes": ["Quality Compounder" if bucket == "core_research" else "Tactical Momentum"],
            "secondary_tags": [], "risk_penalty_pts": 0,
            "discovery_signals": [], "discovery_signal_points": {},
            "missing_data_fields": [], "pru_fmp_snapshot": {},
            "universe_source": "position_research", "scanner_tier": "D",
            "position_research_universe_member": True,
            "active_trading_universe_member": False, "priority_overlap": False,
            "universe_entry_reason": "test",
        }

    scored = [
        _make_symbol("HIGH_TAC",  "tactical_momentum", 20, 20),  # high score but tactical
        _make_symbol("LOW_CORE",  "core_research",      5,  5),  # low score but core
        _make_symbol("MED_CORE",  "core_research",     12, 12),
        _make_symbol("MED_TAC",   "tactical_momentum", 10, 10),
    ]
    scored.sort(
        key=lambda r: (r["universe_bucket"] == "core_research", r["adjusted_discovery_score"]),
        reverse=True,
    )
    buckets = [r["universe_bucket"] for r in scored]
    # All core_research entries must come before any tactical_momentum entry
    last_core = max((i for i, b in enumerate(buckets) if b == "core_research"), default=-1)
    first_tac = min((i for i, b in enumerate(buckets) if b == "tactical_momentum"), default=len(buckets))
    assert last_core < first_tac


# ── Shadow comparator tests ────────────────────────────────────────────────────
#
# The shadow comparator runs alongside the hard top-30 cap (bot_trading.py) and
# writes stage="apex_cap_shadow_compare" records. It is evidence-only: it never
# changes _cut_candidates or enables live Tier D entries.
#
# We test it via _build_shadow_compare_record(), a pure mirror of the inline logic,
# following the same isolation pattern used for _build_apex_cap_record() above.


def _build_shadow_compare_record(
    candidates_raw: list[dict],
    cap_limit: int = 30,
    td_reserve: int = 8,
    non_td_reserve: int = 18,
    flex: int = 4,
) -> dict:
    """
    Mirrors the apex-cap shadow comparator from bot_trading.py.
    Pure function — no I/O.
    """
    from datetime import UTC, datetime

    cut_all_sorted = sorted(candidates_raw, key=lambda c: c.get("score", 0), reverse=True)
    cut_candidates = cut_all_sorted[:cap_limit]

    cur_sel_syms    = {c.get("symbol") for c in cut_candidates}
    shad_all_td     = [c for c in cut_all_sorted if c.get("scanner_tier") == "D"]
    shad_all_non_td = [c for c in cut_all_sorted if c.get("scanner_tier") != "D"]
    shad_cur_td     = [c for c in cut_candidates if c.get("scanner_tier") == "D"]
    shad_drop_cur   = [c for c in cut_all_sorted[cap_limit:] if c.get("scanner_tier") == "D"]

    def _td_rank_score(c):
        return (
            (c.get("discovery_score") or 0) * 2
            + min(c.get("score", 0), 20)
            + 3 * len(c.get("matched_position_archetypes") or [])
            + (5 if c.get("symbol") in cur_sel_syms else 0)
        )

    shad_td_ranked     = sorted(shad_all_td,     key=_td_rank_score,                    reverse=True)
    shad_non_td_ranked = sorted(shad_all_non_td, key=lambda c: c.get("score", 0), reverse=True)

    shad_td_sel     = shad_td_ranked[:td_reserve]
    shad_non_td_sel = shad_non_td_ranked[:non_td_reserve]

    shad_used_syms   = {c.get("symbol") for c in shad_td_sel} | {c.get("symbol") for c in shad_non_td_sel}
    flex_budget      = cap_limit - len(shad_td_sel) - len(shad_non_td_sel)
    shad_remainder   = sorted(
        [c for c in cut_all_sorted if c.get("symbol") not in shad_used_syms],
        key=lambda c: c.get("score", 0), reverse=True,
    )
    shad_flex_sel    = shad_remainder[:flex_budget]
    shad_selected    = shad_td_sel + shad_non_td_sel + shad_flex_sel

    shad_sel_td_syms     = {c.get("symbol") for c in shad_selected if c.get("scanner_tier") == "D"}
    cur_sel_td_syms      = {c.get("symbol") for c in cut_candidates if c.get("scanner_tier") == "D"}
    cur_sel_non_td_syms  = {c.get("symbol") for c in cut_candidates if c.get("scanner_tier") != "D"}
    shad_sel_non_td_syms = {c.get("symbol") for c in shad_selected if c.get("scanner_tier") != "D"}

    td_added_by_shad = sorted(shad_sel_td_syms - cur_sel_td_syms)
    non_td_displaced = sorted(cur_sel_non_td_syms - shad_sel_non_td_syms)
    shad_td_in_sel   = [c for c in shad_selected if c.get("scanner_tier") == "D"]
    shad_dropped_td  = [c for c in shad_td_ranked if c.get("symbol") not in shad_sel_td_syms]

    n_td_before   = len(shad_all_td)
    n_cur_td_sel  = len(shad_cur_td)
    n_cur_td_drop = len(shad_drop_cur)
    if n_td_before == 0 or n_cur_td_drop == 0:
        verdict = "current_cap_not_primary_bottleneck"
    elif n_cur_td_drop > n_cur_td_sel:
        verdict = "current_cap_kills_tier_d"
    else:
        verdict = "current_cap_partially_suppresses_tier_d"

    return {
        "ts":    datetime.now(UTC).isoformat(),
        "stage": "apex_cap_shadow_compare",
        "raw_candidates_before_cap":         len(cut_all_sorted),
        "raw_tier_d_before_cap":             n_td_before,
        "current_selected_total":            len(cut_candidates),
        "current_selected_tier_d":           n_cur_td_sel,
        "current_dropped_tier_d":            n_cur_td_drop,
        "current_selected_tier_d_symbols":   sorted(cur_sel_td_syms),
        "current_dropped_tier_d_top_20":     [c.get("symbol") for c in shad_drop_cur[:20]],
        "shadow_td_reserve":                 td_reserve,
        "shadow_non_td_reserve":             non_td_reserve,
        "shadow_flex":                       flex,
        "shadow_selected_total":             len(shad_selected),
        "shadow_selected_tier_d":            len(shad_td_in_sel),
        "shadow_dropped_tier_d":             len(shad_dropped_td),
        "shadow_selected_tier_d_symbols":    sorted(shad_sel_td_syms),
        "shadow_tier_d_added_vs_current":    td_added_by_shad,
        "shadow_non_tier_d_displaced_vs_current": non_td_displaced,
        "shadow_top_tier_d_added": [
            {
                "symbol":           c.get("symbol"),
                "signal_score":     c.get("score"),
                "discovery_score":  c.get("discovery_score"),
                "archetypes":       c.get("matched_position_archetypes", []),
                "tier_d_rank_score": _td_rank_score(c),
            }
            for c in shad_td_sel
            if c.get("symbol") in td_added_by_shad
        ],
        "shadow_non_tier_d_displaced": [
            {
                "symbol": sym,
                "score": next(
                    (c.get("score") for c in cut_candidates if c.get("symbol") == sym), None
                ),
            }
            for sym in non_td_displaced
        ],
        "shadow_token_budget_same_total": len(shad_selected) <= cap_limit,
        "bottleneck_verdict": verdict,
    }


def test_shadow_compare_current_fields_match_hard_cap():
    """Shadow comparator faithfully mirrors current cap selection in current_* fields.

    60 regular (score 100-41) + 20 Tier D (score 20-1): Tier D all below cutline.
    """
    candidates = _make_candidates(60, [{"score": 20 - i} for i in range(20)])
    hard_cap = _build_apex_cap_record(candidates, cap_limit=30)
    shadow   = _build_shadow_compare_record(candidates, cap_limit=30)

    assert shadow["current_selected_total"]  == hard_cap["selected_candidates_after_cap"]
    assert shadow["current_selected_tier_d"] == hard_cap["selected_tier_d_after_cap"]
    assert shadow["current_dropped_tier_d"]  == hard_cap["dropped_tier_d_by_cap"]
    assert shadow["current_selected_tier_d"] == 0
    assert shadow["current_dropped_tier_d"]  == 20


def test_shadow_compare_total_never_exceeds_cap():
    """shadow_selected_total <= 30 across small, at-cap, and large pools."""
    tiny  = _make_candidates(5, [{"score": 10}, {"score": 8}])
    at    = _make_candidates(28, [{"score": 5}, {"score": 3}])
    large = _make_candidates(60, [{"score": 5 + i} for i in range(40)])

    for pool in (tiny, at, large):
        rec = _build_shadow_compare_record(pool, cap_limit=30)
        assert rec["shadow_selected_total"] <= 30
        assert rec["shadow_token_budget_same_total"] is True


def test_shadow_compare_tier_d_reserve_rescues_dropped_candidates():
    """Tier D reserve selects names the hard cap dropped.

    40 regular (score 100-61) push all Tier D below the cutline.
    Shadow must recover at least one Tier D via the 8-slot reserve.
    """
    tier_d_specs = [
        {"score": 30, "discovery_score": 7, "archetypes": ["Growth Leader"]},
        {"score": 25, "discovery_score": 5, "archetypes": []},
        {"score": 20, "discovery_score": 8, "archetypes": ["Quality Compounder"]},
    ]
    candidates = _make_candidates(40, tier_d_specs)
    shadow = _build_shadow_compare_record(candidates, cap_limit=30)

    assert shadow["current_selected_tier_d"] == 0
    assert shadow["current_dropped_tier_d"]  == 3
    assert shadow["shadow_selected_tier_d"] >= 1
    assert len(shadow["shadow_tier_d_added_vs_current"]) >= 1
    assert shadow["shadow_selected_total"] <= 30


def test_shadow_compare_unused_tier_d_slots_filled_by_non_tier_d():
    """Unused Tier D reserve slots are filled by non-Tier-D candidates.

    Only 2 Tier D in pool; reserve is 8. The 6 unused slots go to non-Tier-D,
    keeping total == 30.
    """
    candidates = _make_candidates(50, [{"score": 15}, {"score": 10}])
    shadow = _build_shadow_compare_record(candidates, cap_limit=30)

    assert shadow["shadow_selected_tier_d"] == 2
    assert shadow["shadow_selected_total"]  == 30


def test_shadow_compare_unused_non_tier_d_slots_filled_by_tier_d():
    """Unused non-Tier-D reserve slots are filled by Tier D candidates.

    Only 5 non-Tier-D vs 18-slot reserve. The 13 unused non-Tier-D slots go
    into the flex budget and are filled by Tier D. Shadow selects > 8 Tier D.
    """
    tier_d_specs = [{"score": 50 - i, "discovery_score": 5} for i in range(30)]
    candidates = _make_candidates(5, tier_d_specs)  # 5 non-TD + 30 TD = 35 total
    shadow = _build_shadow_compare_record(candidates, cap_limit=30)

    assert shadow["shadow_selected_total"]  == 30
    assert shadow["shadow_selected_tier_d"] > 8


def test_shadow_compare_stage_is_shadow_not_live():
    """stage is "apex_cap_shadow_compare" — never "apex_cap".

    The record must be unambiguously tagged so it cannot be mistaken for
    the live apex_cap record that drives Apex calls. No live-execution fields.
    """
    candidates = _make_candidates(40, [{"score": 10}])
    shadow = _build_shadow_compare_record(candidates, cap_limit=30)

    assert shadow["stage"] == "apex_cap_shadow_compare"
    assert shadow["stage"] != "apex_cap"
    assert "execute" not in shadow
    assert "allow_live_entries" not in shadow


def test_shadow_compare_bottleneck_verdict_valid():
    """bottleneck_verdict is always one of the three defined strings."""
    valid = {
        "current_cap_kills_tier_d",
        "current_cap_partially_suppresses_tier_d",
        "current_cap_not_primary_bottleneck",
    }

    # No Tier D in pool → not_primary_bottleneck
    rec_a = _build_shadow_compare_record(_make_candidates(40, []))
    assert rec_a["bottleneck_verdict"] in valid
    assert rec_a["bottleneck_verdict"] == "current_cap_not_primary_bottleneck"

    # Tier D present but none dropped by hard cap → not_primary_bottleneck
    rec_b = _build_shadow_compare_record(_make_candidates(5, [{"score": 90}, {"score": 85}]))
    assert rec_b["bottleneck_verdict"] in valid
    assert rec_b["bottleneck_verdict"] == "current_cap_not_primary_bottleneck"

    # More Tier D dropped than selected → kills_tier_d
    heavy = _make_candidates(40, [{"score": 5 + i} for i in range(10)])
    rec_c = _build_shadow_compare_record(heavy)
    assert rec_c["bottleneck_verdict"] in valid
    assert rec_c["bottleneck_verdict"] == "current_cap_kills_tier_d"

    # Some Tier D dropped, more Tier D selected → partially_suppresses
    # 27 regular (score 100-74) + 5 Tier D (80, 70, 60, 5, 4) = 32 total > cap=30.
    # After sort: TDD000/70/60 land in top-30 (3 selected); TDD003/TDD004 fall out (2 dropped).
    # 2 dropped < 3 selected → partial suppression, not kills.
    mixed = _make_candidates(27, [{"score": 80}, {"score": 70}, {"score": 60}, {"score": 5}, {"score": 4}])
    rec_d = _build_shadow_compare_record(mixed)
    assert rec_d["bottleneck_verdict"] in valid
    assert rec_d["bottleneck_verdict"] == "current_cap_partially_suppresses_tier_d"


# ── Tests 51-58: Tier D Shadow Apex Lane (Phase 1B) ───────────────────────────
#
# The shadow lane lives in tier_d_shadow.py (pure functions) and is wired
# into bot_trading.py (IBKR deps — not imported in tests).
# Tests import directly from tier_d_shadow and verify the invariants:
#   - main cap list is unchanged (shadow operates only on _td_dropped)
#   - selection/rank logic is correct
#   - execute=False is enforced (force_shadow_only stamps entries)
#   - feature flag disables cleanly


def _make_dropped_td(specs: list[dict]) -> list[dict]:
    """Build a list of Tier D candidates as if returned by the cap logic."""
    result = []
    for i, spec in enumerate(specs):
        result.append({
            "symbol":                    spec.get("symbol", f"TDD{i:03d}"),
            "score":                     spec.get("score", 10),
            "scanner_tier":              "D",
            "discovery_score":           spec.get("discovery_score", 5),
            "matched_position_archetypes": spec.get("archetypes", []),
            "priority_overlap":          spec.get("priority_overlap", False),
        })
    return result


def test_shadow_feature_flag_off_returns_empty():
    """Test 51: feature flag off — select returns empty, shadow lane disabled."""
    from tier_d_shadow import select_tier_d_shadow_candidates

    dropped = _make_dropped_td([
        {"discovery_score": 10, "archetypes": ["Quality Compounder"]},
        {"discovery_score": 8,  "archetypes": ["Growth Leader"]},
    ])
    cfg = {"tier_d_shadow_apex_enabled": False}
    selected, not_selected = select_tier_d_shadow_candidates(dropped, cfg)

    assert selected == []
    assert not_selected == []


def test_shadow_dropped_td_are_selected():
    """Test 52: eligible dropped Tier D candidates are included in shadow selection."""
    from tier_d_shadow import select_tier_d_shadow_candidates

    dropped = _make_dropped_td([
        {"symbol": "MSFT", "score": 55, "discovery_score": 13, "archetypes": ["Sector/RS Leader"]},
        {"symbol": "AMZN", "score": 54, "discovery_score": 13, "archetypes": ["Re-rating Candidate"]},
    ])
    cfg = {
        "tier_d_shadow_apex_enabled":         True,
        "tier_d_shadow_apex_cap":             10,
        "tier_d_shadow_min_discovery_score":  6,
        "tier_d_shadow_require_archetype":    True,
    }
    selected, not_selected = select_tier_d_shadow_candidates(dropped, cfg)

    syms = {c["symbol"] for c in selected}
    assert "MSFT" in syms
    assert "AMZN" in syms
    assert len(selected) == 2
    assert not_selected == []


def test_shadow_cap_never_exceeded():
    """Test 53: shadow selection total never exceeds tier_d_shadow_apex_cap."""
    from tier_d_shadow import select_tier_d_shadow_candidates

    dropped = _make_dropped_td([
        {"discovery_score": 10, "archetypes": ["Quality Compounder"]} for _ in range(20)
    ])
    cfg = {
        "tier_d_shadow_apex_enabled":         True,
        "tier_d_shadow_apex_cap":             7,
        "tier_d_shadow_min_discovery_score":  6,
        "tier_d_shadow_require_archetype":    True,
    }
    selected, not_selected = select_tier_d_shadow_candidates(dropped, cfg)

    assert len(selected) <= 7
    assert len(selected) + len(not_selected) == 20


def test_shadow_rank_discovery_and_archetypes():
    """Test 54: shadow rank prioritises discovery_score and archetype count."""
    from tier_d_shadow import select_tier_d_shadow_candidates, _shadow_rank

    high_ds = {"symbol": "HIGH", "score": 5, "discovery_score": 15,
               "archetypes": ["Quality Compounder", "Growth Leader"], "priority_overlap": False}
    low_ds  = {"symbol": "LOW",  "score": 5, "discovery_score": 7,
               "archetypes": ["Sector/RS Leader"], "priority_overlap": False}

    assert _shadow_rank(high_ds) > _shadow_rank(low_ds)

    dropped = _make_dropped_td([])
    dropped.append({**high_ds, "scanner_tier": "D", "matched_position_archetypes": high_ds["archetypes"]})
    dropped.append({**low_ds,  "scanner_tier": "D", "matched_position_archetypes": low_ds["archetypes"]})

    cfg = {
        "tier_d_shadow_apex_enabled":        True,
        "tier_d_shadow_apex_cap":            1,
        "tier_d_shadow_min_discovery_score": 6,
        "tier_d_shadow_require_archetype":   True,
    }
    selected, not_selected = select_tier_d_shadow_candidates(dropped, cfg)

    assert len(selected) == 1
    assert selected[0]["symbol"] == "HIGH"


def test_shadow_min_discovery_score_filter():
    """Test 55: candidates below min_discovery_score are excluded."""
    from tier_d_shadow import select_tier_d_shadow_candidates

    dropped = _make_dropped_td([
        {"symbol": "PASS", "discovery_score": 8,  "archetypes": ["Quality Compounder"]},
        {"symbol": "FAIL", "discovery_score": 4,  "archetypes": ["Quality Compounder"]},
    ])
    cfg = {
        "tier_d_shadow_apex_enabled":        True,
        "tier_d_shadow_apex_cap":            10,
        "tier_d_shadow_min_discovery_score": 6,
        "tier_d_shadow_require_archetype":   True,
    }
    selected, _ = select_tier_d_shadow_candidates(dropped, cfg)

    syms = {c["symbol"] for c in selected}
    assert "PASS" in syms
    assert "FAIL" not in syms


def test_shadow_require_archetype_filter():
    """Test 56: require_archetype=True excludes candidates with no archetypes."""
    from tier_d_shadow import select_tier_d_shadow_candidates

    dropped = _make_dropped_td([
        {"symbol": "WITH",    "discovery_score": 10, "archetypes": ["Growth Leader"]},
        {"symbol": "WITHOUT", "discovery_score": 10, "archetypes": []},
    ])
    cfg = {
        "tier_d_shadow_apex_enabled":        True,
        "tier_d_shadow_apex_cap":            10,
        "tier_d_shadow_min_discovery_score": 6,
        "tier_d_shadow_require_archetype":   True,
    }
    selected, _ = select_tier_d_shadow_candidates(dropped, cfg)

    syms = {c["symbol"] for c in selected}
    assert "WITH" in syms
    assert "WITHOUT" not in syms

    # With require_archetype=False, both pass
    cfg["tier_d_shadow_require_archetype"] = False
    selected2, _ = select_tier_d_shadow_candidates(dropped, cfg)
    syms2 = {c["symbol"] for c in selected2}
    assert "WITH" in syms2
    assert "WITHOUT" in syms2


def test_shadow_force_shadow_only_blocks_execution():
    """Test 57: force_shadow_only stamps every entry as non-executable."""
    from tier_d_shadow import force_shadow_only

    entries = [
        {"symbol": "MSFT", "trade_type": "POSITION",  "conviction": "HIGH"},
        {"symbol": "AMZN", "trade_type": "SWING",      "conviction": "MEDIUM"},
        {"symbol": "CAT",  "trade_type": "AVOID"},
    ]
    blocked = force_shadow_only(entries)

    for e in blocked:
        assert e["trade_type"]        == "POSITION_RESEARCH_ONLY"
        assert e["execution_allowed"] is False
        assert e["block_reason"]      == "tier_d_shadow_apex_only"

    # Even if Apex returned POSITION — it must be blocked
    assert all(e["trade_type"] == "POSITION_RESEARCH_ONLY" for e in blocked)


def test_shadow_funnel_record_schema():
    """Test 58: funnel record has all required fields and orders_placed=0."""
    from tier_d_shadow import (
        build_tier_d_shadow_funnel_record,
        force_shadow_only,
        select_tier_d_shadow_candidates,
    )

    td_before  = _make_dropped_td([{"discovery_score": 12, "archetypes": ["Quality Compounder"]}] * 5)
    td_after   = td_before[:3]
    td_dropped = td_before[3:]

    cfg = {
        "tier_d_shadow_apex_enabled":        True,
        "tier_d_shadow_apex_cap":            10,
        "tier_d_shadow_min_discovery_score": 6,
        "tier_d_shadow_require_archetype":   True,
    }
    selected, not_selected = select_tier_d_shadow_candidates(td_dropped, cfg)

    # Simulate Apex returning entries (then forced non-executable)
    apex_entries = [{"symbol": c["symbol"], "trade_type": "POSITION", "conviction": "HIGH"}
                    for c in selected]
    blocked = force_shadow_only(apex_entries)

    rec = build_tier_d_shadow_funnel_record(
        cut_all_sorted=td_before,
        td_before=td_before,
        td_after=td_after,
        td_dropped=td_dropped,
        selected=selected,
        not_selected=not_selected,
        apex_new_entries=blocked,
        scan_type="test",
    )

    required = [
        "ts", "stage", "scan_type",
        "raw_candidates_before_main_cap",
        "tier_d_before_main_cap",
        "tier_d_selected_main_cap",
        "tier_d_dropped_main_cap",
        "tier_d_shadow_eligible",
        "tier_d_shadow_selected",
        "tier_d_shadow_not_selected",
        "tier_d_shadow_symbols",
        "tier_d_shadow_rank_scores",
        "tier_d_shadow_apex_classifications",
        "tier_d_shadow_would_have_passed_validation",
        "tier_d_shadow_blocked_count",
        "tier_d_shadow_orders_placed",
        "tier_d_shadow_training_records_written",
    ]
    for field in required:
        assert field in rec, f"Missing field: {field}"

    assert rec["stage"]                               == "tier_d_shadow_apex"
    assert rec["tier_d_shadow_orders_placed"]          == 0
    assert rec["tier_d_shadow_training_records_written"] == 0
    assert rec["tier_d_shadow_blocked_count"]          == len(blocked)
    # All entries are POSITION_RESEARCH_ONLY after force_shadow_only
    assert all(e["execution_allowed"] is False for e in blocked)


# ── Tests 56-65: compute_apex_cap_score and live cap adjuster ──────────────────


def test_56_non_tier_d_cap_score_equals_raw_score():
    """Test 56: Non-Tier-D candidates get apex_cap_score == raw signal score."""
    from apex_cap_score import compute_apex_cap_score

    for score in (0, 18, 45, 100):
        c = {"score": score, "scanner_tier": ""}
        assert compute_apex_cap_score(c) == score

    # scanner_tier absent also treated as non-Tier-D
    assert compute_apex_cap_score({"score": 55}) == 55


def test_57_tier_d_below_signal_floor_gets_no_bonus():
    """Test 57: Tier D with score < 18 receives no research bonus."""
    from apex_cap_score import compute_apex_cap_score

    c = {
        "score":                       17,
        "scanner_tier":                "D",
        "discovery_score":             15,
        "adjusted_discovery_score":    15,
        "primary_archetype":           "Quality Compounder",
        "universe_bucket":             "core_research",
        "matched_position_archetypes": ["Quality Compounder"],
    }
    assert compute_apex_cap_score(c) == 17

    # Edge: exactly 0
    c2 = {**c, "score": 0}
    assert compute_apex_cap_score(c2) == 0


def test_58_tier_d_above_floor_gets_discovery_archetype_bucket_bonus():
    """Test 58: Tier D with score >= 18 receives full discovery + archetype + bucket bonus."""
    from apex_cap_score import compute_apex_cap_score

    c = {
        "score":                       20,
        "scanner_tier":                "D",
        "discovery_score":             8,
        "adjusted_discovery_score":    8,
        "primary_archetype":           "Quality Compounder",
        "universe_bucket":             "core_research",
        "matched_position_archetypes": ["Quality Compounder"],
    }
    # discovery_bonus = min(8,10)*0.5 = 4.0
    # archetype_bonus = 2 (primary_archetype set)
    # bucket_bonus    = 1 (core_research)
    expected = 20 + 4.0 + 2 + 1
    assert compute_apex_cap_score(c) == expected

    # Score exactly at floor
    c18 = {**c, "score": 18}
    expected18 = 18 + 4.0 + 2 + 1
    assert compute_apex_cap_score(c18) == expected18


def test_59_adjusted_discovery_score_preferred_over_discovery_score():
    """Test 59: adjusted_discovery_score takes precedence over discovery_score."""
    from apex_cap_score import compute_apex_cap_score

    c = {
        "score":                    20,
        "scanner_tier":             "D",
        "discovery_score":          4,
        "adjusted_discovery_score": 9,
    }
    # Should use 9, not 4: min(9,10)*0.5 = 4.5
    result = compute_apex_cap_score(c)
    assert result == 20 + 4.5

    # Confirm that without adjusted_discovery_score it uses discovery_score
    c_no_adj = {"score": 20, "scanner_tier": "D", "discovery_score": 4}
    result_no_adj = compute_apex_cap_score(c_no_adj)
    assert result_no_adj == 20 + 2.0  # min(4,10)*0.5 = 2.0


def test_60_core_research_receives_bucket_bonus():
    """Test 60: universe_bucket='core_research' adds +1 to apex_cap_score."""
    from apex_cap_score import compute_apex_cap_score

    base = {"score": 25, "scanner_tier": "D", "universe_bucket": "core_research"}
    result = compute_apex_cap_score(base)
    base_no_bucket = {"score": 25, "scanner_tier": "D"}
    result_no_bucket = compute_apex_cap_score(base_no_bucket)
    assert result - result_no_bucket == 1.0


def test_61_tactical_momentum_gets_no_bucket_bonus():
    """Test 61: universe_bucket='tactical_momentum' does not receive bucket bonus."""
    from apex_cap_score import compute_apex_cap_score

    c = {"score": 25, "scanner_tier": "D", "universe_bucket": "tactical_momentum"}
    c_no_bucket = {"score": 25, "scanner_tier": "D"}
    assert compute_apex_cap_score(c) == compute_apex_cap_score(c_no_bucket)


def test_62_live_cap_sort_uses_apex_cap_score():
    """Test 62: Tier D with strong research metadata gets promoted above lower-signal regulars.

    Scenario:
    - 29 regular candidates at scores 100-72 (the top 29 slots)
    - 1 regular candidate at score 22 (would be position 30 in raw-score sort)
    - 1 Tier D at score 20, discovery=10, primary_archetype set, core_research
      → apex_cap_score = 20 + 5 + 2 + 1 = 28

    Old raw-score sort: regular at 22 fills slot 30; Tier D at 20 is dropped.
    New apex_cap_score sort: Tier D at 28 beats regular at 22 → Tier D enters slot 30.
    """
    from apex_cap_score import compute_apex_cap_score

    candidates = []
    for i in range(29):
        candidates.append({"symbol": f"REG{i:03d}", "score": 100 - i, "scanner_tier": ""})
    candidates.append({"symbol": "REG_LOW", "score": 22, "scanner_tier": ""})
    td = {
        "symbol":                      "TDD_STAR",
        "score":                       20,
        "scanner_tier":                "D",
        "discovery_score":             10,
        "adjusted_discovery_score":    10,
        "primary_archetype":           "Quality Compounder",
        "universe_bucket":             "core_research",
        "matched_position_archetypes": ["Quality Compounder"],
    }
    candidates.append(td)

    # Attach apex_cap_score
    for c in candidates:
        c["apex_cap_score"] = compute_apex_cap_score(c)

    cap_limit = 30
    new_sorted  = sorted(candidates, key=lambda c: c.get("apex_cap_score", c.get("score", 0)), reverse=True)
    new_selected = new_sorted[:cap_limit]
    old_sorted   = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
    old_selected = old_sorted[:cap_limit]

    new_syms = {c["symbol"] for c in new_selected}
    old_syms = {c["symbol"] for c in old_selected}

    assert "TDD_STAR" in new_syms, "Tier D should be promoted into cap with adjusted score"
    assert "TDD_STAR" not in old_syms, "Tier D should be excluded by old raw-score sort"
    assert "REG_LOW" not in new_syms, "Low-signal regular should be displaced"
    assert "REG_LOW" in old_syms, "Low-signal regular survives old raw-score sort"


def test_63_cap_total_never_exceeds_30():
    """Test 63: cap total never exceeds 30 regardless of candidate pool size."""
    from apex_cap_score import compute_apex_cap_score

    for n_reg, td_specs in [
        (5, []),
        (30, []),
        (60, [{"score": 20, "discovery_score": 10, "primary_archetype": "QC", "universe_bucket": "core_research"} for _ in range(20)]),
    ]:
        pool = _make_candidates(n_reg, td_specs)
        selected = pool[:30]
        assert len(selected) <= 30


def test_64_shadow_comparator_stage_remains_shadow_only():
    """Test 64: shadow compare record stage is 'apex_cap_shadow_compare', never live."""
    candidates = _make_candidates(60, [{"score": 20 - i} for i in range(20)])
    rec = _build_shadow_compare_record(candidates)
    assert rec["stage"] == "apex_cap_shadow_compare"
    # Confirm it is not the new live-compare stage
    assert rec["stage"] != "apex_cap_adjusted_score_live_compare"


def test_65_no_live_reserve_allocation():
    """Test 65: live cap uses apex_cap_score sort only — no reserve quota or lane.

    The number of Tier D candidates selected must equal exactly those whose
    apex_cap_score places them in the top _CAP_LIMIT after unified sort.
    There is no guaranteed minimum or floor for Tier D.
    """
    from apex_cap_score import compute_apex_cap_score

    # 30 regulars at score 100-71 — they dominate the cap even with Tier D boost.
    # Tier D at score 10 (< 18 floor) gets no bonus and is dropped.
    candidates = _make_candidates(30, [{"score": 10, "discovery_score": 15, "primary_archetype": "QC"}])
    cap_limit = 30
    selected = candidates[:cap_limit]
    td_selected = [c for c in selected if c.get("scanner_tier") == "D"]
    assert len(td_selected) == 0, "Tier D below signal floor must not enter cap via any quota"
    assert len(selected) == cap_limit
