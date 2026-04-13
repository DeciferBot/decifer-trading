"""tests/test_atr_sizer_integration.py

Integration tests for the ATR sizer end-to-end path and execution agent wiring.

Risk context: 6+ significant commits in 18 hours mean features may be shipped
but not correctly wired into the execution path.  These tests pin the three
most critical integration seams:

  1. ATR value from Signal reaches calculate_position_size(atr=...) inside execute_buy
  2. Execution agent plan (order_type) routes to the correct IBKR Order class
  3. Execution agent fill_watcher_params reach FillWatcher constructor
  4. ATR=0 aborts a non-tranche trade via zero-R:R check (defensive behaviour)
  5. ATR cap in calculate_position_size caps qty more conservatively than Kelly

Each test is narrow: only the seam under test uses real logic; everything
else (IBKR, yfinance, FillWatcher threads) is stubbed or patched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Project root on path (conftest.py already does this, kept for safety)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Real imports — conftest.py has already stubbed ib_async, yfinance, etc.
# ---------------------------------------------------------------------------
import orders
import risk
from config import CONFIG

# test_bot.py is collected AFTER this file (b > a alphabetically) but its
# module-level code mutates the real risk and orders modules that are already
# in sys.modules:
#
#   risk_stub  = sys.modules["risk"]            # the REAL risk module!
#   risk_stub.calculate_stops = lambda ...      # overwrites real function
#   orders_stub.execute_buy   = lambda ...      # same for orders
#
# Capture the real callables NOW (before test_bot.py's collection runs) so
# tests can call them directly, immune to later lambda replacement.
from orders_core import execute_buy as _real_execute_buy
from risk import calculate_position_size as _real_calculate_position_size
from risk import calculate_stops as _real_calculate_stops

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_REGIME_BULL: dict = {
    "regime": "TRENDING_UP",
    "position_size_multiplier": 1.0,
    "vix": 15.0,
    "spy_price": 500.0,
}

_PORTFOLIO = 100_000.0
_PRICE = 50.0
_SCORE = 30  # >= high_conviction_score → conviction_mult = 1.5

# Kelly path (rank=0, regime_mult=1): base_risk=1500, *1.5=2250, position_value=min(112500,10000)=10000
# kelly_qty = int(10000/50) = 200

# ATR cap fires only when it's MORE conservative than Kelly (< 200 shares).
# Tight ATR = 10.0 → cap = int(1000/10) = 100  (wins over Kelly's 200)
# Loose ATR = 0.01 → cap = 100000              (Kelly's 200 wins)
_ATR_TIGHT = 10.0  # ATR cap = 100  < Kelly = 200 → cap wins
_ATR_LOOSE = 0.01  # ATR cap ≫ Kelly = 200        → Kelly wins


# ---------------------------------------------------------------------------
# Helper: build a minimal IB mock that satisfies execute_buy's call surface
# ---------------------------------------------------------------------------


def _make_ib(price: float = _PRICE) -> MagicMock:
    trade = MagicMock()
    trade.orderStatus.status = "Submitted"
    trade.order.orderId = 42

    ticker = MagicMock()
    ticker.last = price
    ticker.bid = price - 0.1
    ticker.ask = price + 0.1

    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.qualifyContracts.return_value = None
    ib.reqMktData.return_value = ticker
    ib.cancelMktData.return_value = None
    ib.sleep.return_value = None
    ib.placeOrder.return_value = trade
    return ib


# ---------------------------------------------------------------------------
# Helper: build a default ExecutionPlan (LIMIT, no FillWatcher thread)
# ---------------------------------------------------------------------------


def _default_plan():
    from execution_agent import ExecutionPlan

    return ExecutionPlan(
        order_type="LIMIT",
        limit_price=0,
        aggression="normal",
        split_into_n_tranches=1,
        timeout_secs=90,
        fallback_strategy="cancel",
        fill_watcher_params={
            "initial_wait_secs": 30.0,
            "interval_secs": 20.0,
            "max_attempts": 3,
            "step_pct": 0.002,
            "max_chase_pct": 0.01,
        },
        reasoning="Default test plan.",
    )


# ---------------------------------------------------------------------------
# Helper: run execute_buy with all external deps stubbed; caller can
# override individual patches via extra_patches.
# ---------------------------------------------------------------------------


def _run_execute_buy(
    ib: MagicMock,
    symbol: str = "AAPL",
    price: float = _PRICE,
    atr: float = _ATR_TIGHT,
    score: int = _SCORE,
    portfolio_value: float = _PORTFOLIO,
    regime: dict | None = None,
    tranche_mode: bool = True,
    extra_patches: dict | None = None,
) -> bool:
    if regime is None:
        regime = _REGIME_BULL

    orders.active_trades.clear()

    base_patches = {
        "orders._get_ibkr_price": MagicMock(return_value=price),
        "orders._get_ibkr_bid_ask": MagicMock(return_value=(price - 0.1, price + 0.1)),
        "orders._get_alpaca_price": MagicMock(return_value=price),
        "orders.get_tv_signal_cache": MagicMock(return_value={}),
        "orders._is_duplicate_check_enabled": MagicMock(return_value=False),
        "orders.has_open_order_for": MagicMock(return_value=False),
        "orders._check_ibkr_open_order": MagicMock(return_value=False),
        "orders.check_correlation": MagicMock(return_value=(True, "ok")),
        "orders.check_combined_exposure": MagicMock(return_value=(True, "ok")),
        "orders.check_sector_concentration": MagicMock(return_value=(True, "ok")),
        "execution_agent.get_execution_plan": MagicMock(return_value=_default_plan()),
        # Stub file I/O so tests never write to disk regardless of what
        # collection-time mutations set learning.ORDER_LOG_FILE / TRADE_LOG_FILE to.
        # orders.log_order: rebound from orders module namespace at call time (line ~482)
        "orders.log_order": MagicMock(),
        # learning.log_trade: imported fresh inside execute_buy via `from learning import log_trade`
        "learning.log_trade": MagicMock(),
        # Disable fill watcher thread so no background threads leak
        "orders.CONFIG": {
            **CONFIG,
            "active_account": "DUP00000",
            "fill_watcher": {**CONFIG.get("fill_watcher", {}), "enabled": False},
        },
    }
    if extra_patches:
        base_patches.update(extra_patches)

    ctx_mgrs = [patch(k, v) for k, v in base_patches.items()]
    try:
        for cm in ctx_mgrs:
            cm.__enter__()
        # Call _real_execute_buy directly — test_bot.py replaces orders.execute_buy
        # with a lambda at collection time, so calling orders.execute_buy() would
        # silently no-op.  _real_execute_buy was bound before that replacement.
        return _real_execute_buy(
            ib=ib,
            symbol=symbol,
            price=price,
            atr=atr,
            score=score,
            portfolio_value=portfolio_value,
            regime=regime,
            tranche_mode=tranche_mode,
        )
    except Exception:
        return False
    finally:
        for cm in reversed(ctx_mgrs):
            cm.__exit__(None, None, None)


# ===========================================================================
# 1. ATR value is forwarded from call-site through execute_buy to
#    calculate_position_size.
# ===========================================================================


class TestAtrForwardedToSizer:
    """Verifies that execute_buy calls calculate_position_size with the exact
    atr value it received — the seam between signal dispatch and risk sizing."""

    def test_atr_kwarg_forwarded_to_calculate_position_size(self):
        """execute_buy must pass atr=<received value> to calculate_position_size."""
        ib = _make_ib()
        spy = MagicMock(return_value=10)

        _run_execute_buy(
            ib,
            atr=_ATR_TIGHT,
            extra_patches={"orders.calculate_position_size": spy},
        )

        spy.assert_called_once()
        kw = spy.call_args.kwargs
        assert "atr" in kw, "calculate_position_size was not called with the atr keyword — ATR cap is silently disabled"
        assert kw["atr"] == pytest.approx(_ATR_TIGHT), f"Expected atr={_ATR_TIGHT}, got atr={kw['atr']!r}"

    def test_zero_atr_forwarded_not_replaced_by_default(self):
        """atr=0.0 must be passed through so the sizer can apply its own guard."""
        ib = _make_ib()
        spy = MagicMock(return_value=10)

        _run_execute_buy(
            ib,
            atr=0.0,
            extra_patches={"orders.calculate_position_size": spy},
        )

        kw = spy.call_args.kwargs
        assert kw.get("atr", -1) == pytest.approx(0.0), "atr=0 must not be silently replaced before position sizing"

    def test_atr_also_forwarded_to_calculate_stops(self):
        """calculate_stops must receive the same atr so SL/TP distances match."""
        ib = _make_ib()
        size_spy = MagicMock(return_value=10)
        stop_spy = MagicMock(return_value=(_PRICE - 1.0, _PRICE + 2.0))

        _run_execute_buy(
            ib,
            atr=_ATR_TIGHT,
            extra_patches={
                "orders.calculate_position_size": size_spy,
                "orders.calculate_stops": stop_spy,
            },
        )

        stop_spy.assert_called_once()
        args, _ = stop_spy.call_args
        assert len(args) >= 2, "calculate_stops called with too few positional args"
        assert args[1] == pytest.approx(_ATR_TIGHT), f"calculate_stops received atr={args[1]!r}, expected {_ATR_TIGHT}"


# ===========================================================================
# 2. Execution agent plan: order_type routes to the correct IBKR Order class.
# ===========================================================================


class TestExecutionAgentOrderTypeRouting:
    """When get_execution_plan returns a specific order_type, execute_buy must
    construct the matching IBKR order object — not always a LimitOrder."""

    def _plan(self, order_type: str):
        from execution_agent import ExecutionPlan

        return ExecutionPlan(
            order_type=order_type,
            limit_price=0,
            aggression="normal",
            split_into_n_tranches=1,
            timeout_secs=90,
            fallback_strategy="cancel",
            fill_watcher_params={
                "initial_wait_secs": 30.0,
                "interval_secs": 20.0,
                "max_attempts": 3,
                "step_pct": 0.002,
                "max_chase_pct": 0.01,
            },
            reasoning="Test plan.",
        )

    def test_mkt_plan_uses_market_order(self):
        """order_type='MKT' must route to MarketOrder — not LimitOrder."""
        ib = _make_ib()
        mkt_mock = MagicMock(return_value=MagicMock())
        lmt_mock = MagicMock(return_value=MagicMock())

        _run_execute_buy(
            ib,
            extra_patches={
                "execution_agent.get_execution_plan": MagicMock(return_value=self._plan("MKT")),
                "orders.calculate_position_size": MagicMock(return_value=10),
                "orders.calculate_stops": MagicMock(return_value=(_PRICE - 1, _PRICE + 2)),
                "orders.MarketOrder": mkt_mock,
                "orders.LimitOrder": lmt_mock,
            },
        )

        assert mkt_mock.called, "execution_agent returned order_type='MKT' but MarketOrder was never constructed"

    def test_limit_plan_does_not_use_market_order(self):
        """order_type='LIMIT' must NOT construct a MarketOrder for the entry leg."""
        ib = _make_ib()
        mkt_mock = MagicMock(return_value=MagicMock())
        lmt_mock = MagicMock(return_value=MagicMock())

        _run_execute_buy(
            ib,
            extra_patches={
                "execution_agent.get_execution_plan": MagicMock(return_value=self._plan("LIMIT")),
                "orders.calculate_position_size": MagicMock(return_value=10),
                "orders.calculate_stops": MagicMock(return_value=(_PRICE - 1, _PRICE + 2)),
                "orders.MarketOrder": mkt_mock,
                "orders.LimitOrder": lmt_mock,
            },
        )

        assert not mkt_mock.called, "execution_agent returned order_type='LIMIT' but MarketOrder was constructed"
        assert lmt_mock.called, "execution_agent returned order_type='LIMIT' but LimitOrder was never constructed"

    def test_limit_plan_with_agent_price_uses_that_price(self):
        """When limit_price > 0, execute_buy must pass the agent's price to LimitOrder."""
        ib = _make_ib()
        _agent_price = 49.50

        from execution_agent import ExecutionPlan

        plan = ExecutionPlan(
            order_type="LIMIT",
            limit_price=_agent_price,
            aggression="normal",
            split_into_n_tranches=1,
            timeout_secs=90,
            fallback_strategy="cancel",
            fill_watcher_params={
                "initial_wait_secs": 30.0,
                "interval_secs": 20.0,
                "max_attempts": 3,
                "step_pct": 0.002,
                "max_chase_pct": 0.01,
            },
            reasoning="Patient limit at agent price.",
        )

        lmt_mock = MagicMock(return_value=MagicMock())

        _run_execute_buy(
            ib,
            extra_patches={
                "execution_agent.get_execution_plan": MagicMock(return_value=plan),
                "orders.calculate_position_size": MagicMock(return_value=10),
                "orders.calculate_stops": MagicMock(return_value=(_PRICE - 1, _PRICE + 2)),
                "orders.LimitOrder": lmt_mock,
            },
        )

        assert lmt_mock.called, "No LimitOrder was constructed"
        # Entry LimitOrder is the first call; price is the 3rd positional arg
        entry_call = lmt_mock.call_args_list[0]
        entry_price = entry_call[0][2] if len(entry_call[0]) >= 3 else None
        assert entry_price == pytest.approx(_agent_price), (
            f"Expected agent limit_price={_agent_price}, LimitOrder was called with {entry_price!r}"
        )


# ===========================================================================
# 3. Execution agent fill_watcher_params reach the FillWatcher constructor.
# ===========================================================================


def _build_fw_patches(plan, fw_mock) -> dict:
    """Return a flat dict of all patches needed for the FillWatcher wiring test."""
    import types as _types

    fw_module = _types.ModuleType("fill_watcher")
    fw_module.FillWatcher = fw_mock
    fw_module._active_watchers = {}
    fw_module._watchers_lock = MagicMock()

    return {
        "execution_agent.get_execution_plan": MagicMock(return_value=plan),
        "orders.calculate_position_size": MagicMock(return_value=10),
        "orders.calculate_stops": MagicMock(return_value=(_PRICE - 1, _PRICE + 2)),
        "orders._get_ibkr_price": MagicMock(return_value=_PRICE),
        "orders._get_ibkr_bid_ask": MagicMock(return_value=(_PRICE - 0.1, _PRICE + 0.1)),
        "orders._get_alpaca_price": MagicMock(return_value=_PRICE),
        "orders.get_tv_signal_cache": MagicMock(return_value={}),
        "orders._is_duplicate_check_enabled": MagicMock(return_value=False),
        "orders.has_open_order_for": MagicMock(return_value=False),
        "orders._check_ibkr_open_order": MagicMock(return_value=False),
        "orders.check_correlation": MagicMock(return_value=(True, "ok")),
        "orders.check_combined_exposure": MagicMock(return_value=(True, "ok")),
        "orders.check_sector_concentration": MagicMock(return_value=(True, "ok")),
        "orders.LimitOrder": MagicMock(return_value=MagicMock()),
        "orders.StopOrder": MagicMock(return_value=MagicMock()),
        "orders.threading": MagicMock(),
        "orders.log_order": MagicMock(),
        "learning.log_trade": MagicMock(),
        "orders.CONFIG": {
            **CONFIG,
            "active_account": "DUP00000",
            "fill_watcher": {**CONFIG.get("fill_watcher", {}), "enabled": True},
        },
        # Intercept the lazy fill_watcher import inside execute_buy
        "sys.modules": {**sys.modules, "fill_watcher": fw_module},
    }


class TestFillWatcherParamsWired:
    """FillWatcher must be initialised with exec_plan.fill_watcher_params,
    not the static CONFIG values, so per-trade aggression is respected."""

    def test_exec_plan_params_passed_to_fill_watcher(self):
        """When exec_plan has custom fill_watcher_params, FillWatcher gets them."""
        from execution_agent import ExecutionPlan

        custom_params = {
            "initial_wait_secs": 10.0,
            "interval_secs": 5.0,
            "max_attempts": 2,
            "step_pct": 0.003,
            "max_chase_pct": 0.015,
        }
        plan = ExecutionPlan(
            order_type="LIMIT",
            limit_price=0,
            aggression="aggressive",
            split_into_n_tranches=1,
            timeout_secs=20,
            fallback_strategy="cancel",
            fill_watcher_params=custom_params,
            reasoning="Aggressive plan for high conviction.",
        )

        fw_mock = MagicMock()
        fw_mock.return_value = MagicMock(run=MagicMock())

        ib = _make_ib()
        orders.active_trades.clear()

        patches = _build_fw_patches(plan, fw_mock)

        # Apply all patches except the sys.modules override (handled via patch.dict)
        fw_module = patches.pop("sys.modules")["fill_watcher"]
        ctx_mgrs = [patch(k, v) for k, v in patches.items()]
        for cm in ctx_mgrs:
            cm.__enter__()
        try:
            with patch.dict(sys.modules, {"fill_watcher": fw_module}):
                _real_execute_buy(
                    ib=ib,
                    symbol="AMD",
                    price=_PRICE,
                    atr=_ATR_TIGHT,
                    score=_SCORE,
                    portfolio_value=_PORTFOLIO,
                    regime=_REGIME_BULL,
                    tranche_mode=False,
                )
        finally:
            for cm in reversed(ctx_mgrs):
                cm.__exit__(None, None, None)

        assert fw_mock.called, "FillWatcher was never constructed — fill_watcher_params cannot be verified"
        fw_kwargs = fw_mock.call_args.kwargs
        assert "watcher_params" in fw_kwargs, "FillWatcher constructor was not passed watcher_params kwarg"
        assert fw_kwargs["watcher_params"] is custom_params, (
            "FillWatcher received different fill_watcher_params than the execution plan — "
            "exec_plan.fill_watcher_params not wired through"
        )


# ===========================================================================
# 4. ATR=0 uses the ATR floor and still executes the trade.
# ===========================================================================


class TestAtrZeroDefensiveBehaviour:
    """When atr=0, calculate_stops applies a 0.3%-of-price floor so the stop
    distance is non-zero.  The trade is allowed through with a valid stop rather
    than being rejected with a hair-trigger (sl == price) stop.

    This behaviour was introduced in commit 98f4d90 ("fix(agents): enforce
    scanner direction alignment + ATR stop floor")."""

    def test_atr_zero_uses_floor_and_proceeds(self):
        """execute_buy with atr=0 must proceed — the ATR floor produces a valid stop."""
        ib = _make_ib()
        ib.placeOrder.reset_mock()

        result = _run_execute_buy(
            ib,
            atr=0.0,
            tranche_mode=False,
            extra_patches={
                # Real calculate_stops so the ATR floor logic runs
                "orders.calculate_stops": _real_calculate_stops,
                "orders.calculate_position_size": MagicMock(return_value=10),
            },
        )

        assert result is True, (
            "execute_buy with atr=0 must succeed: the ATR floor (0.3%% of price) "
            "produces a viable stop distance, so there is no reason to abort"
        )
        assert ib.placeOrder.called, "ib.placeOrder must be called — the ATR floor gives a valid stop distance"

    def test_calculate_stops_with_zero_atr_uses_floor(self):
        """Direct unit: calculate_stops(price, 0, 'LONG') applies the 0.3%-of-price
        ATR floor so sl < price (non-zero risk distance)."""
        price = 100.0
        sl, _tp = _real_calculate_stops(price, 0.0, "LONG")
        risk_distance = price - sl
        expected_floor = price * 0.003 * CONFIG.get("atr_stop_multiplier", 1.5)
        assert risk_distance == pytest.approx(expected_floor), (
            f"Expected risk distance={expected_floor:.4f} (ATR floor), got sl={sl}"
        )
        assert sl < price, "Stop must be below entry for a LONG position"


# ===========================================================================
# 5. ATR cap in calculate_position_size yields a smaller qty than Kelly.
#    (Regression guard for the feat(risk) commit.)
# ===========================================================================


class TestAtrCapEndToEnd:
    """Verifies the ATR volatility cap is active and correctly calibrated.

    With VIX-adaptive Kelly (base_kelly=0.5, vix_rank=0.0, risk_pct=0.005):
      base_risk  = 100000 * 0.005 * 0.5 = 250
      risk_amount = 250 * 1.5 (conviction) = 375
      kelly_qty  = int(375 / (10 * 1.5)) = 25   ← below cap, cap cannot fire

    To exercise the cap we need kelly_qty > atr_cap (100).  We patch
    risk_pct_per_trade=0.05 so kelly_qty=250 > cap=100 → cap fires → qty=100.
    """

    def test_tight_atr_produces_fewer_shares_than_loose_atr(self):
        """Qty with tight ATR must be strictly less than qty with loose ATR."""
        with (
            patch.object(risk, "get_vix_rank", return_value=0.0),
            patch.object(risk, "get_session", return_value="MARKET"),
        ):
            qty_tight = _real_calculate_position_size(_PORTFOLIO, _PRICE, _SCORE, _REGIME_BULL, atr=_ATR_TIGHT)
            qty_loose = _real_calculate_position_size(_PORTFOLIO, _PRICE, _SCORE, _REGIME_BULL, atr=_ATR_LOOSE)

        assert qty_tight < qty_loose, (
            f"Tight ATR ({_ATR_TIGHT}) should produce fewer shares than loose ATR "
            f"({_ATR_LOOSE}): got tight={qty_tight}, loose={qty_loose}"
        )

    def test_atr_cap_formula_matches_expected_qty(self):
        """ATR-capped qty == int(portfolio * atr_vol_target_pct / atr) when cap wins.

        With default risk_pct=0.005 Kelly yields 25 shares (below the 100-share cap).
        We patch risk_pct_per_trade=0.05 so Kelly yields 250 shares, forcing the cap
        to fire and reduce qty to 100.
        """
        # atr=10 → cap = int(100000*0.01/10) = 100
        expected_cap = int((_PORTFOLIO * CONFIG["atr_vol_target_pct"]) / _ATR_TIGHT)

        with (
            patch.object(risk, "get_vix_rank", return_value=0.0),
            patch.object(risk, "get_session", return_value="MARKET"),
            patch.dict(risk.CONFIG, {"risk_pct_per_trade": 0.05}),
        ):
            qty = _real_calculate_position_size(_PORTFOLIO, _PRICE, _SCORE, _REGIME_BULL, atr=_ATR_TIGHT)

        assert qty == expected_cap, (
            f"ATR cap formula mismatch: expected {expected_cap}, got {qty}. "
            f"(atr={_ATR_TIGHT}, portfolio={_PORTFOLIO}, target_pct={CONFIG['atr_vol_target_pct']})"
        )

    def test_atr_cap_disabled_when_atr_is_zero(self):
        """When atr=0, ATR cap must not fire — Kelly determines qty."""
        with (
            patch.object(risk, "get_vix_rank", return_value=0.0),
            patch.object(risk, "get_session", return_value="MARKET"),
        ):
            qty_zero = _real_calculate_position_size(_PORTFOLIO, _PRICE, _SCORE, _REGIME_BULL, atr=0.0)
            qty_none = _real_calculate_position_size(_PORTFOLIO, _PRICE, _SCORE, _REGIME_BULL)

        assert qty_zero == qty_none, (
            f"atr=0 and atr omitted must produce identical sizing; got zero_atr={qty_zero}, no_atr={qty_none}"
        )
