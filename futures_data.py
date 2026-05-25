"""
futures_data.py — ES=F / NQ=F advisory sensor.

Isolated yfinance usage for S&P 500 and Nasdaq futures only.
These symbols are not available via Alpaca (no futures support) and NQ=F
is blocked on FMP Premium. yfinance is the only viable free source.

Approved yfinance exception: see tests/test_no_yfinance_runtime.py
_YFINANCE_APPROVED list.

Returns are advisory evidence only — they do not affect the core 11-sensor
fail-closed count in live_driver_resolver.py.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

_ES = "ES=F"
_NQ = "NQ=F"


def _5d_return(close: pd.Series) -> float | None:
    """Return (close[-1] / close[-5 or earliest]) - 1, or None if < 2 bars."""
    s = close.dropna()
    if len(s) < 2:
        return None
    anchor = s.iloc[-6] if len(s) >= 6 else s.iloc[0]
    return float(s.iloc[-1] / anchor - 1)


def fetch_futures_returns() -> tuple[float | None, float | None]:
    """
    Fetch ES=F and NQ=F 5-day returns via yfinance.

    Returns (es_ret, nq_ret). Either or both may be None on failure.
    Never raises — fails closed so callers can always unpack safely.
    """
    try:
        import yfinance as yf
        df = yf.download(
            [_ES, _NQ],
            period="10d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) < 2:
            return None, None

        es_ret: float | None = None
        nq_ret: float | None = None

        try:
            es_ret = _5d_return(df[("Close", _ES)])
        except Exception as exc:
            log.debug("futures_data: ES=F return calc failed: %s", exc)

        try:
            nq_ret = _5d_return(df[("Close", _NQ)])
        except Exception as exc:
            log.debug("futures_data: NQ=F return calc failed: %s", exc)

        return es_ret, nq_ret

    except Exception as exc:
        log.debug("futures_data: fetch failed: %s", exc)
        return None, None
