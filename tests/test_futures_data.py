"""Tests for futures_data.py — ES=F / NQ=F advisory sensor."""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


# ── _5d_return ────────────────────────────────────────────────────────────────

def test_5d_return_normal():
    from futures_data import _5d_return
    s = pd.Series([100.0, 101.0, 102.0, 101.5, 103.0, 104.0])
    ret = _5d_return(s)
    assert ret == pytest.approx(104.0 / 100.0 - 1, rel=1e-5)


def test_5d_return_fewer_than_5_bars():
    from futures_data import _5d_return
    s = pd.Series([100.0, 102.0])
    ret = _5d_return(s)
    assert ret == pytest.approx(0.02, rel=1e-5)


def test_5d_return_single_bar_returns_none():
    from futures_data import _5d_return
    assert _5d_return(pd.Series([100.0])) is None


def test_5d_return_empty_returns_none():
    from futures_data import _5d_return
    assert _5d_return(pd.Series([], dtype=float)) is None


def test_5d_return_drops_nans():
    from futures_data import _5d_return
    s = pd.Series([float("nan"), 100.0, 101.0, 102.0])
    ret = _5d_return(s)
    assert ret is not None


# ── fetch_futures_returns ─────────────────────────────────────────────────────

def _make_mock_df(es_closes, nq_closes):
    """Build a MultiIndex DataFrame matching yfinance two-ticker output."""
    idx = pd.RangeIndex(len(es_closes))
    arrays = [
        ["Close"] * 2,
        ["ES=F", "NQ=F"],
    ]
    cols = pd.MultiIndex.from_arrays(arrays)
    return pd.DataFrame(
        list(zip(es_closes, nq_closes)),
        columns=cols,
        index=idx,
    )


def test_fetch_futures_returns_happy_path():
    from futures_data import fetch_futures_returns
    mock_df = _make_mock_df(
        [5800.0, 5820.0, 5840.0, 5860.0, 5880.0, 5900.0],
        [20000.0, 20100.0, 20200.0, 20300.0, 20400.0, 20500.0],
    )
    with patch("yfinance.download", return_value=mock_df):
        es_ret, nq_ret = fetch_futures_returns()
    assert es_ret == pytest.approx(5900.0 / 5800.0 - 1, rel=1e-4)
    assert nq_ret == pytest.approx(20500.0 / 20000.0 - 1, rel=1e-4)


def test_fetch_futures_returns_yfinance_exception_returns_none_none():
    from futures_data import fetch_futures_returns
    with patch("yfinance.download", side_effect=RuntimeError("network error")):
        es_ret, nq_ret = fetch_futures_returns()
    assert es_ret is None
    assert nq_ret is None


def test_fetch_futures_returns_empty_df_returns_none_none():
    from futures_data import fetch_futures_returns
    empty = pd.DataFrame()
    with patch("yfinance.download", return_value=empty):
        es_ret, nq_ret = fetch_futures_returns()
    assert es_ret is None
    assert nq_ret is None


def test_fetch_futures_returns_one_symbol_fails_gracefully():
    """NQ column missing — ES should still return a value."""
    from futures_data import fetch_futures_returns
    idx = pd.RangeIndex(6)
    cols = pd.MultiIndex.from_arrays([["Close"], ["ES=F"]])
    mock_df = pd.DataFrame(
        [[float(5800 + i * 20)] for i in range(6)],
        columns=cols,
        index=idx,
    )
    with patch("yfinance.download", return_value=mock_df):
        es_ret, nq_ret = fetch_futures_returns()
    assert es_ret is not None
    assert nq_ret is None


# ── guard: futures_data.py must import yfinance ───────────────────────────────

def test_futures_data_uses_yfinance():
    """Confirm futures_data.py actually imports yfinance (not silently broken)."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "futures_data.py").read_text()
    assert "import yfinance" in src
