"""Tests for portfolio.py — multi-account position aggregation.

All IBKR and external dependencies are stubbed via conftest.py.
Tests run fully offline and never touch a live account.
"""

import sys
import os
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Import the module under test (conftest stubs ib_async before this runs)
# ---------------------------------------------------------------------------
import portfolio as pf


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_contract(symbol="AAPL", sec_type="STK",
                   currency="USD", strike=None, right=None, expiry=None):
    c = MagicMock()
    c.symbol   = symbol
    c.secType  = sec_type
    c.currency = currency
    c.strike   = strike
    c.right    = right
    c.lastTradeDateOrContractMonth = expiry
    return c


def _make_portfolio_item(symbol="AAPL", sec_type="STK", position=100,
                         market_price=150.0, market_value=15_000.0,
                         avg_cost=140.0, unrealized_pnl=1_000.0,
                         realized_pnl=0.0, currency="USD",
                         strike=None, right=None, expiry=None):
    item              = MagicMock()
    item.contract     = _make_contract(symbol, sec_type, currency, strike, right, expiry)
    item.position     = position
    item.marketPrice  = market_price
    item.marketValue  = market_value
    item.averageCost  = avg_cost
    item.unrealizedPNL = unrealized_pnl
    item.realizedPNL   = realized_pnl
    return item


# ---------------------------------------------------------------------------
# _portfolio_item_to_dict
# ---------------------------------------------------------------------------

class TestPortfolioItemToDict:
    def test_stock_fields(self):
        item = _make_portfolio_item(symbol="TSLA", position=50,
                                    market_value=10_000.0, unrealized_pnl=500.0)
        d = pf._portfolio_item_to_dict(item)
        assert d["symbol"]         == "TSLA"
        assert d["sec_type"]       == "STK"
        assert d["position"]       == 50
        assert d["market_value"]   == 10_000.0
        assert d["unrealized_pnl"] == 500.0
        assert d["strike"]         is None
        assert d["right"]          is None

    def test_option_fields(self):
        item = _make_portfolio_item(
            symbol="AAPL", sec_type="OPT", position=2,
            strike=150.0, right="C", expiry="20250117"
        )
        d = pf._portfolio_item_to_dict(item)
        assert d["sec_type"] == "OPT"
        assert d["strike"]   == 150.0
        assert d["right"]    == "C"
        assert d["expiry"]   == "20250117"

    def test_negative_position(self):
        item = _make_portfolio_item(position=-100, market_value=-15_000.0,
                                    unrealized_pnl=-300.0)
        d = pf._portfolio_item_to_dict(item)
        assert d["position"]       == -100
        assert d["market_value"]   == -15_000.0
        assert d["unrealized_pnl"] == -300.0


# ---------------------------------------------------------------------------
# _position_key
# ---------------------------------------------------------------------------

class TestPositionKey:
    def test_stock_key_is_symbol(self):
        pos = {"symbol": "MSFT", "sec_type": "STK"}
        assert pf._position_key(pos) == "MSFT"

    def test_option_key_is_composite(self):
        pos = {"symbol": "AAPL", "sec_type": "OPT",
               "right": "C", "strike": 150.0, "expiry": "20250117"}
        key = pf._position_key(pos)
        assert "AAPL" in key
        assert "C"    in key
        assert "150"  in key
        assert "20250117" in key

    def test_two_options_same_symbol_different_strikes_have_different_keys(self):
        call_150 = {"symbol": "AAPL", "sec_type": "OPT", "right": "C", "strike": 150.0, "expiry": "20250117"}
        call_155 = {"symbol": "AAPL", "sec_type": "OPT", "right": "C", "strike": 155.0, "expiry": "20250117"}
        assert pf._position_key(call_150) != pf._position_key(call_155)

    def test_call_and_put_same_strike_different_keys(self):
        call = {"symbol": "AAPL", "sec_type": "OPT", "right": "C", "strike": 150.0, "expiry": "20250117"}
        put  = {"symbol": "AAPL", "sec_type": "OPT", "right": "P", "strike": 150.0, "expiry": "20250117"}
        assert pf._position_key(call) != pf._position_key(put)


# ---------------------------------------------------------------------------
# get_accounts_to_aggregate
# ---------------------------------------------------------------------------

class TestGetAccountsToAggregate:
    def test_explicit_list_takes_priority(self):
        with patch.dict(pf.CONFIG, {"aggregate_accounts": ["ACC1", "ACC2"],
                                    "accounts": {"paper": "PAPER123"}}):
            result = pf.get_accounts_to_aggregate()
        assert result == ["ACC1", "ACC2"]

    def test_falls_back_to_accounts_registry(self):
        with patch.dict(pf.CONFIG, {
            "aggregate_accounts": [],
            "accounts": {"paper": "DUP481326", "live_1": "", "live_2": "U9999999"},
        }):
            result = pf.get_accounts_to_aggregate()
        # Empty strings excluded, order matches dict insertion
        assert "DUP481326" in result
        assert "U9999999"  in result
        assert ""          not in result

    def test_empty_config_returns_empty_list(self):
        with patch.dict(pf.CONFIG, {"aggregate_accounts": [], "accounts": {}}):
            result = pf.get_accounts_to_aggregate()
        assert result == []

    def test_explicit_list_with_empty_strings_filtered(self):
        with patch.dict(pf.CONFIG, {"aggregate_accounts": ["ACC1", "", "ACC2"]}):
            result = pf.get_accounts_to_aggregate()
        assert result == ["ACC1", "ACC2"]


# ---------------------------------------------------------------------------
# fetch_account_positions
# ---------------------------------------------------------------------------

class TestFetchAccountPositions:
    def test_returns_non_zero_positions(self):
        item_open   = _make_portfolio_item(symbol="AAPL", position=100)
        item_closed = _make_portfolio_item(symbol="MSFT", position=0)   # settled, skip

        fake_ib = MagicMock()
        fake_ib.portfolio.return_value = [item_open, item_closed]

        result = pf.fetch_account_positions(fake_ib, "DUP481326")
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_returns_empty_on_exception(self):
        fake_ib = MagicMock()
        fake_ib.portfolio.side_effect = RuntimeError("Connection lost")

        result = pf.fetch_account_positions(fake_ib, "DUP481326")
        assert result == []

    def test_calls_portfolio_with_account_id(self):
        fake_ib = MagicMock()
        fake_ib.portfolio.return_value = []

        pf.fetch_account_positions(fake_ib, "U3059777")
        fake_ib.portfolio.assert_called_once_with("U3059777")


# ---------------------------------------------------------------------------
# merge_positions
# ---------------------------------------------------------------------------

class TestMergePositions:
    def _pos(self, symbol, position, mv, upnl, rpnl=0.0, sec_type="STK"):
        return {
            "symbol": symbol, "sec_type": sec_type,
            "position": position, "market_value": mv,
            "avg_cost": mv / max(abs(position), 1),
            "unrealized_pnl": upnl, "realized_pnl": rpnl,
            "currency": "USD", "strike": None, "right": None, "expiry": None,
        }

    def test_single_account_single_position(self):
        account_data = {"ACC1": [self._pos("AAPL", 100, 15_000.0, 1_000.0)]}
        merged = pf.merge_positions(account_data)

        assert "AAPL" in merged
        assert merged["AAPL"]["net_position"]   == 100
        assert merged["AAPL"]["market_value"]   == 15_000.0
        assert merged["AAPL"]["unrealized_pnl"] == 1_000.0

    def test_same_symbol_in_two_accounts_sums_correctly(self):
        account_data = {
            "ACC1": [self._pos("AAPL", 100, 15_000.0, 1_000.0)],
            "ACC2": [self._pos("AAPL",  50,  7_500.0,   500.0)],
        }
        merged = pf.merge_positions(account_data)

        assert len(merged) == 1
        assert merged["AAPL"]["net_position"]   == 150
        assert merged["AAPL"]["market_value"]   == 22_500.0
        assert merged["AAPL"]["unrealized_pnl"] == 1_500.0

    def test_different_symbols_appear_separately(self):
        account_data = {
            "ACC1": [
                self._pos("AAPL", 100, 15_000.0, 1_000.0),
                self._pos("TSLA",  10,  2_000.0,   100.0),
            ]
        }
        merged = pf.merge_positions(account_data)
        assert len(merged) == 2
        assert "AAPL" in merged
        assert "TSLA" in merged

    def test_per_account_breakdown_stored(self):
        account_data = {
            "ACC1": [self._pos("AAPL", 100, 15_000.0, 1_000.0)],
            "ACC2": [self._pos("AAPL",  50,  7_500.0,   500.0)],
        }
        merged = pf.merge_positions(account_data)
        assert "ACC1" in merged["AAPL"]["accounts"]
        assert "ACC2" in merged["AAPL"]["accounts"]
        assert merged["AAPL"]["accounts"]["ACC1"]["position"] == 100

    def test_empty_account_data(self):
        merged = pf.merge_positions({})
        assert merged == {}

    def test_short_position_has_negative_net(self):
        account_data = {"ACC1": [self._pos("SPXS", -200, -5_000.0, -300.0)]}
        merged = pf.merge_positions(account_data)
        assert merged["SPXS"]["net_position"] == -200
        assert merged["SPXS"]["market_value"] == -5_000.0

    def test_long_and_short_of_same_symbol_nets_to_zero(self):
        """Two accounts holding opposite sides cancel out."""
        account_data = {
            "ACC1": [self._pos("AAPL",  100, 15_000.0, 0.0)],
            "ACC2": [self._pos("AAPL", -100, -15_000.0, 0.0)],
        }
        merged = pf.merge_positions(account_data)
        assert merged["AAPL"]["net_position"] == 0
        assert merged["AAPL"]["market_value"] == 0.0


# ---------------------------------------------------------------------------
# compute_net_exposure
# ---------------------------------------------------------------------------

class TestComputeNetExposure:
    def _merged_dict(self, positions_mv):
        """Build a minimal merged dict from {symbol: (net_qty, market_value)}."""
        return {
            sym: {
                "symbol": sym, "sec_type": "STK",
                "net_position": qty,
                "market_value": mv,
                "unrealized_pnl": 0.0, "realized_pnl": 0.0,
                "avg_cost": 0.0, "currency": "USD",
                "strike": None, "right": None, "expiry": None,
                "accounts": {},
            }
            for sym, (qty, mv) in positions_mv.items()
        }

    def test_single_position_has_100_pct_exposure(self):
        merged = self._merged_dict({"AAPL": (100, 15_000.0)})
        result = pf.compute_net_exposure(merged)
        assert result["AAPL"]["exposure_pct"] == pytest.approx(100.0)
        assert result["AAPL"]["direction"] == "LONG"

    def test_two_equal_positions_each_have_50_pct(self):
        merged = self._merged_dict({
            "AAPL": (100, 10_000.0),
            "TSLA": (100, 10_000.0),
        })
        result = pf.compute_net_exposure(merged)
        assert result["AAPL"]["exposure_pct"] == pytest.approx(50.0)
        assert result["TSLA"]["exposure_pct"] == pytest.approx(50.0)

    def test_negative_position_is_short(self):
        merged = self._merged_dict({"SQQQ": (-200, -5_000.0)})
        result = pf.compute_net_exposure(merged)
        assert result["SQQQ"]["direction"] == "SHORT"

    def test_zero_position_is_flat(self):
        merged = self._merged_dict({"AAPL": (0, 0.0)})
        result = pf.compute_net_exposure(merged)
        assert result["AAPL"]["direction"] == "FLAT"
        assert result["AAPL"]["exposure_pct"] == 0.0

    def test_exposure_uses_absolute_market_value(self):
        """Short position with negative MV should still count toward gross exposure."""
        merged = self._merged_dict({
            "AAPL": (100,  10_000.0),  # long
            "SPXS": (-100, -10_000.0), # short
        })
        result = pf.compute_net_exposure(merged)
        assert result["AAPL"]["exposure_pct"] == pytest.approx(50.0)
        assert result["SPXS"]["exposure_pct"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# get_aggregate_summary — end-to-end
# ---------------------------------------------------------------------------

class TestGetAggregateSummary:
    def _make_ib(self, positions_by_account):
        """Return a fake IB that returns preset positions per account."""
        def portfolio_side_effect(account_id):
            raw = positions_by_account.get(account_id, [])
            items = []
            for p in raw:
                item = _make_portfolio_item(**p)
                items.append(item)
            return items

        fake_ib = MagicMock()
        fake_ib.portfolio.side_effect = portfolio_side_effect
        return fake_ib

    def test_empty_accounts_returns_empty_summary(self):
        fake_ib = MagicMock()
        with patch.dict(pf.CONFIG, {"aggregate_accounts": [], "accounts": {}}):
            result = pf.get_aggregate_summary(fake_ib)
        assert result["accounts"] == []
        assert result["positions"] == {}
        assert result["totals"]["position_count"] == 0

    def test_single_account_single_position(self):
        ib = self._make_ib({
            "ACC1": [{"symbol": "AAPL", "position": 100,
                      "market_value": 15_000.0, "unrealized_pnl": 1_000.0}]
        })
        result = pf.get_aggregate_summary(ib, accounts=["ACC1"])

        assert "AAPL" in result["positions"]
        assert result["totals"]["position_count"] == 1
        assert result["totals"]["long_count"]     == 1
        assert result["totals"]["short_count"]    == 0
        assert result["totals"]["unrealized_pnl"] == pytest.approx(1_000.0)
        assert result["totals"]["gross_exposure"] == pytest.approx(15_000.0)

    def test_two_accounts_same_symbol_aggregated(self):
        ib = self._make_ib({
            "ACC1": [{"symbol": "AAPL", "position": 100, "market_value": 15_000.0, "unrealized_pnl": 1_000.0}],
            "ACC2": [{"symbol": "AAPL", "position":  50, "market_value":  7_500.0, "unrealized_pnl":   500.0}],
        })
        result = pf.get_aggregate_summary(ib, accounts=["ACC1", "ACC2"])

        assert result["totals"]["position_count"] == 1
        aapl = result["positions"]["AAPL"]
        assert aapl["net_position"]   == 150
        assert aapl["market_value"]   == pytest.approx(22_500.0)
        assert aapl["unrealized_pnl"] == pytest.approx(1_500.0)

    def test_accounts_list_in_result(self):
        ib = MagicMock()
        ib.portfolio.return_value = []
        result = pf.get_aggregate_summary(ib, accounts=["A1", "A2", "A3"])
        assert set(result["accounts"]) == {"A1", "A2", "A3"}

    def test_mixed_long_short_exposure_pcts_sum_to_100(self):
        ib = self._make_ib({
            "ACC1": [
                {"symbol": "AAPL", "position":  100, "market_value":  10_000.0, "unrealized_pnl": 0.0},
                {"symbol": "SPXS", "position": -200, "market_value": -10_000.0, "unrealized_pnl": 0.0},
            ]
        })
        result = pf.get_aggregate_summary(ib, accounts=["ACC1"])
        t = result["totals"]
        assert t["long_exposure_pct"] + t["short_exposure_pct"] == pytest.approx(100.0)

    def test_account_error_does_not_crash_summary(self):
        """One account failing should not prevent results from other accounts."""
        def portfolio_side_effect(account_id):
            if account_id == "BAD":
                raise RuntimeError("IBKR connection dropped")
            return [_make_portfolio_item(symbol="AAPL", position=100,
                                        market_value=15_000.0, unrealized_pnl=500.0)]

        fake_ib = MagicMock()
        fake_ib.portfolio.side_effect = portfolio_side_effect

        result = pf.get_aggregate_summary(fake_ib, accounts=["GOOD", "BAD"])
        assert "AAPL" in result["positions"]   # GOOD account results present
        assert result["totals"]["position_count"] == 1

    def test_uses_config_accounts_when_none_passed(self):
        ib = MagicMock()
        ib.portfolio.return_value = []

        with patch.dict(pf.CONFIG, {
            "aggregate_accounts": ["CFG_ACCT"],
            "accounts": {"paper": "PAPER123"},
        }):
            pf.get_aggregate_summary(ib)

        ib.portfolio.assert_called_once_with("CFG_ACCT")


# ---------------------------------------------------------------------------
# _compute_totals
# ---------------------------------------------------------------------------

class TestComputeTotals:
    def _pos_entry(self, net, mv, upnl, rpnl=0.0):
        return {
            "net_position": net, "market_value": mv,
            "unrealized_pnl": upnl, "realized_pnl": rpnl,
            "direction": "LONG" if net > 0 else "SHORT",
        }

    def test_single_long_position(self):
        merged = {"AAPL": self._pos_entry(100, 15_000.0, 1_000.0, 200.0)}
        t = pf._compute_totals(merged)
        assert t["market_value"]   == pytest.approx(15_000.0)
        assert t["unrealized_pnl"] == pytest.approx(1_000.0)
        assert t["realized_pnl"]   == pytest.approx(200.0)
        assert t["total_pnl"]      == pytest.approx(1_200.0)
        assert t["long_count"]     == 1
        assert t["short_count"]    == 0

    def test_gross_exposure_is_sum_of_abs_values(self):
        merged = {
            "AAPL": self._pos_entry(100,  10_000.0, 0.0),
            "SPXS": self._pos_entry(-50, -5_000.0,  0.0),
        }
        t = pf._compute_totals(merged)
        assert t["gross_exposure"] == pytest.approx(15_000.0)
        assert t["net_exposure"]   == pytest.approx(5_000.0)

    def test_long_exposure_pct(self):
        merged = {
            "AAPL": self._pos_entry(100, 10_000.0, 0.0),
            "SPXS": self._pos_entry(-50, -10_000.0, 0.0),
        }
        t = pf._compute_totals(merged)
        assert t["long_exposure_pct"]  == pytest.approx(50.0)
        assert t["short_exposure_pct"] == pytest.approx(50.0)

    def test_all_flat_returns_zero_exposure(self):
        merged = {}
        t = pf._compute_totals(merged)
        assert t["gross_exposure"]     == 0.0
        assert t["long_exposure_pct"]  == 0.0
        assert t["short_exposure_pct"] == 0.0
        assert t["position_count"]     == 0
