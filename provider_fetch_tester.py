"""
Sprint 7A.3 — Provider Fetch Tester

Runs safe, minimal, read-only fetch tests against each data provider to
validate actual connectivity and response quality.

Precise safety invariants:
  data_provider_api_called              = true   (Alpaca/FMP/AV/yfinance fetches attempted)
  trading_api_called                    = false  (no trade/order submission)
  broker_order_api_called               = false  (no order placement or cancellation)
  broker_account_api_called             = false  (no account info fetched)
  broker_position_api_called            = false  (no positions fetched)
  broker_execution_api_called           = false  (no execution reports fetched)
  ibkr_market_data_connection_attempted = true   (TCP connect to IB gateway attempted)
  ibkr_order_account_position_calls     = false  (no IBKR TWS API calls)
  env_presence_checked                  = true   (os.getenv() used to detect key presence)
  env_values_logged                     = false  (credential values never written to output)
  env_file_read                         = true   (load_dotenv() reads .env for key discovery)
  secrets_exposed                       = false  (no credential values in output)
  live_output_changed                   = false  (no positions, orders, or trades touched)

Output: data/reference/provider_fetch_test_results.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent
_REF_DIR = _HERE / "data" / "reference"

# ---------------------------------------------------------------------------
# Safety constants — never relaxed
# Precise flags distinguishing data-provider calls from trading/broker calls.
# ---------------------------------------------------------------------------
_SAFETY = {
    "data_provider_api_called": True,        # Alpaca/FMP/AV/yfinance fetches attempted
    "trading_api_called": False,             # no trade or order submission
    "broker_order_api_called": False,        # no order placement or cancellation
    "broker_account_api_called": False,      # no account info fetched
    "broker_position_api_called": False,     # no positions fetched
    "broker_execution_api_called": False,    # no execution reports fetched
    "ibkr_market_data_connection_attempted": True,   # TCP connect to IB gateway attempted
    "ibkr_order_account_position_calls": False,      # no IBKR TWS API calls beyond TCP probe
    "env_presence_checked": True,            # os.getenv() used to detect key presence
    "env_values_logged": False,              # credential values never written to output
    "env_file_read": True,                   # load_dotenv() reads .env for key discovery
    "secrets_exposed": False,               # no credential values in output
    "live_output_changed": False,            # no positions, orders, or trades touched
}

# Test ticker — liquid, non-controversial, stable symbol
_TEST_SYMBOL = "AAPL"
_TEST_DATE_FROM = "2025-01-01"
_TEST_DATE_TO = "2025-01-10"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """Load .env without exposing secrets."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _result(
    provider: str,
    endpoint: str,
    success: bool,
    latency_ms: float,
    credentials_present: bool,
    detail: str = "",
    data_sample: str = "",
    error: str = "",
) -> dict:
    return {
        "provider": provider,
        "endpoint": endpoint,
        "success": success,
        "latency_ms": round(latency_ms, 1),
        "credentials_present": credentials_present,
        "secrets_exposed": False,
        "live_output_changed": False,
        "detail": detail,
        "data_sample": data_sample,
        "error": error[:200] if error else "",
    }


def _timed_call(fn):
    """Return (result, elapsed_ms)."""
    t0 = time.monotonic()
    out = fn()
    return out, (time.monotonic() - t0) * 1000


# ---------------------------------------------------------------------------
# Alpaca tests
# ---------------------------------------------------------------------------

def _test_alpaca() -> list[dict]:
    results = []
    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    creds = bool(api_key and secret_key)

    # --- OHLCV bars ---
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(
            symbol_or_symbols=_TEST_SYMBOL,
            timeframe=TimeFrame.Day,
            start=datetime(2025, 1, 2, tzinfo=timezone.utc),
            end=datetime(2025, 1, 10, tzinfo=timezone.utc),
        )
        data, ms = _timed_call(lambda: client.get_stock_bars(req))
        # BarSet stores items in .data dict, not via direct __contains__
        bars = data.data.get(_TEST_SYMBOL, []) if hasattr(data, "data") else []
        sample = f"{len(bars)} bars" if bars else "0 bars"
        results.append(_result(
            "alpaca", "StockHistoricalDataClient.get_stock_bars",
            success=len(bars) > 0, latency_ms=ms,
            credentials_present=creds,
            detail="OHLCV daily bars — reference data layer",
            data_sample=sample,
        ))
    except Exception as exc:
        results.append(_result(
            "alpaca", "StockHistoricalDataClient.get_stock_bars",
            success=False, latency_ms=0.0,
            credentials_present=creds,
            error=str(exc),
        ))

    # --- Latest quote ---
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockLatestQuoteRequest(symbol_or_symbols=_TEST_SYMBOL)
        data, ms = _timed_call(lambda: client.get_stock_latest_quote(req))
        quote = data.get(_TEST_SYMBOL)
        sample = f"bid={getattr(quote, 'bid_price', 'n/a')} ask={getattr(quote, 'ask_price', 'n/a')}" if quote else "no quote"
        results.append(_result(
            "alpaca", "StockHistoricalDataClient.get_stock_latest_quote",
            success=quote is not None, latency_ms=ms,
            credentials_present=creds,
            detail="Real-time bid/ask — market sensor layer",
            data_sample=sample,
        ))
    except Exception as exc:
        results.append(_result(
            "alpaca", "StockHistoricalDataClient.get_stock_latest_quote",
            success=False, latency_ms=0.0,
            credentials_present=creds,
            error=str(exc),
        ))

    # --- Option chain (safe: market data only, no positions/orders) ---
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest

        client = OptionHistoricalDataClient(api_key, secret_key)
        req = OptionChainRequest(underlying_symbol=_TEST_SYMBOL)
        data, ms = _timed_call(lambda: client.get_option_chain(req))
        n = len(data) if data else 0
        results.append(_result(
            "alpaca", "OptionHistoricalDataClient.get_option_chain",
            success=n > 0, latency_ms=ms,
            credentials_present=creds,
            detail="Option chain — options data layer",
            data_sample=f"{n} contracts",
        ))
    except Exception as exc:
        results.append(_result(
            "alpaca", "OptionHistoricalDataClient.get_option_chain",
            success=False, latency_ms=0.0,
            credentials_present=creds,
            error=str(exc),
        ))

    return results


# ---------------------------------------------------------------------------
# FMP tests
# ---------------------------------------------------------------------------

def _test_fmp() -> list[dict]:
    import requests as rq

    results = []
    api_key = os.getenv("FMP_API_KEY", "")
    creds = bool(api_key)
    base = "https://financialmodelingprep.com/stable"

    endpoints = [
        ("profile", f"{base}/profile?symbol={_TEST_SYMBOL}&apikey={api_key}", "Company profile — fundamentals layer"),
        ("key_metrics_ttm", f"{base}/key-metrics-ttm?symbol={_TEST_SYMBOL}&apikey={api_key}", "Key metrics TTM — quality layer"),
        ("earnings", f"{base}/earnings?symbol={_TEST_SYMBOL}&limit=3&apikey={api_key}", "Earnings history — catalyst layer"),
        ("price_target_consensus", f"{base}/price-target-consensus?symbol={_TEST_SYMBOL}&apikey={api_key}", "Price target consensus — analyst layer"),
        ("news_stock", f"{base}/news/stock?tickers={_TEST_SYMBOL}&limit=3&apikey={api_key}", "Stock news — news layer"),
    ]

    for name, url, detail in endpoints:
        try:
            t0 = time.monotonic()
            resp = rq.get(url, timeout=10)
            ms = (time.monotonic() - t0) * 1000
            if resp.status_code == 200:
                body = resp.json()
                n = len(body) if isinstance(body, list) else (1 if isinstance(body, dict) and body else 0)
                results.append(_result(
                    "fmp", f"GET /stable/{name}",
                    success=n > 0, latency_ms=ms,
                    credentials_present=creds,
                    detail=detail,
                    data_sample=f"{n} record(s)",
                ))
            else:
                results.append(_result(
                    "fmp", f"GET /stable/{name}",
                    success=False, latency_ms=ms,
                    credentials_present=creds,
                    error=f"HTTP {resp.status_code}",
                ))
        except Exception as exc:
            results.append(_result(
                "fmp", f"GET /stable/{name}",
                success=False, latency_ms=0.0,
                credentials_present=creds,
                error=str(exc),
            ))

    return results


# ---------------------------------------------------------------------------
# Alpha Vantage tests
# ---------------------------------------------------------------------------

def _test_alpha_vantage() -> list[dict]:
    import requests as rq

    results = []
    api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    creds = bool(api_key)
    base = "https://www.alphavantage.co/query"

    tests = [
        (
            "TIME_SERIES_DAILY",
            {"function": "TIME_SERIES_DAILY", "symbol": _TEST_SYMBOL, "outputsize": "compact", "apikey": api_key},
            "Daily OHLCV — price data (free tier)",
            lambda j: "Time Series (Daily)" in j,
        ),
        (
            "OVERVIEW",
            {"function": "OVERVIEW", "symbol": _TEST_SYMBOL, "apikey": api_key},
            "Company overview — fundamentals (may rate-limit on free)",
            lambda j: "Symbol" in j,
        ),
        (
            "FEDERAL_FUNDS_RATE",
            {"function": "FEDERAL_FUNDS_RATE", "interval": "monthly", "apikey": api_key},
            "Fed funds rate — macro layer",
            lambda j: "data" in j,
        ),
        (
            "RSI",
            {"function": "RSI", "symbol": _TEST_SYMBOL, "interval": "daily",
             "time_period": 14, "series_type": "close", "apikey": api_key},
            "RSI indicator — technical layer (may rate-limit)",
            lambda j: "Technical Analysis: RSI" in j,
        ),
    ]

    for name, params, detail, check in tests:
        try:
            t0 = time.monotonic()
            resp = rq.get(base, params=params, timeout=15)
            ms = (time.monotonic() - t0) * 1000
            if resp.status_code == 200:
                body = resp.json()
                is_rate_limited = "Note" in body and "call frequency" in body.get("Note", "")
                is_premium = "Information" in body and "premium" in body.get("Information", "").lower()
                if is_rate_limited:
                    results.append(_result(
                        "alpha_vantage", f"GET ?function={name}",
                        success=False, latency_ms=ms,
                        credentials_present=creds,
                        detail=detail,
                        error="Rate limited (25 req/day on free tier)",
                    ))
                elif is_premium:
                    results.append(_result(
                        "alpha_vantage", f"GET ?function={name}",
                        success=False, latency_ms=ms,
                        credentials_present=creds,
                        detail=detail,
                        error="Premium endpoint — upgrade required",
                    ))
                else:
                    ok = check(body)
                    results.append(_result(
                        "alpha_vantage", f"GET ?function={name}",
                        success=ok, latency_ms=ms,
                        credentials_present=creds,
                        detail=detail,
                        data_sample="response ok" if ok else "unexpected shape",
                    ))
            else:
                results.append(_result(
                    "alpha_vantage", f"GET ?function={name}",
                    success=False, latency_ms=ms,
                    credentials_present=creds,
                    error=f"HTTP {resp.status_code}",
                ))
        except Exception as exc:
            results.append(_result(
                "alpha_vantage", f"GET ?function={name}",
                success=False, latency_ms=0.0,
                credentials_present=creds,
                error=str(exc),
            ))

    return results


# ---------------------------------------------------------------------------
# yfinance tests
# ---------------------------------------------------------------------------

def _test_yfinance() -> list[dict]:
    results = []

    # --- OHLCV ---
    try:
        import yfinance as yf
        t0 = time.monotonic()
        ticker = yf.Ticker(_TEST_SYMBOL)
        hist = ticker.history(start=_TEST_DATE_FROM, end=_TEST_DATE_TO)
        ms = (time.monotonic() - t0) * 1000
        n = len(hist)
        results.append(_result(
            "yfinance", "Ticker.history(daily)",
            success=n > 0, latency_ms=ms,
            credentials_present=True,  # no credentials required
            detail="Daily OHLCV — fallback price data",
            data_sample=f"{n} rows",
        ))
    except Exception as exc:
        results.append(_result(
            "yfinance", "Ticker.history(daily)",
            success=False, latency_ms=0.0,
            credentials_present=True,
            error=str(exc),
        ))

    # --- Info / fundamentals ---
    try:
        import yfinance as yf
        t0 = time.monotonic()
        ticker = yf.Ticker(_TEST_SYMBOL)
        info = ticker.info
        ms = (time.monotonic() - t0) * 1000
        has_data = bool(info and info.get("symbol"))
        results.append(_result(
            "yfinance", "Ticker.info",
            success=has_data, latency_ms=ms,
            credentials_present=True,
            detail="Company info — fallback fundamentals",
            data_sample=f"symbol={info.get('symbol', 'n/a')}" if has_data else "empty",
        ))
    except Exception as exc:
        results.append(_result(
            "yfinance", "Ticker.info",
            success=False, latency_ms=0.0,
            credentials_present=True,
            error=str(exc),
        ))

    return results


# ---------------------------------------------------------------------------
# IBKR tests — connection check only, NO account/position/order calls
# ---------------------------------------------------------------------------

def _test_ibkr() -> list[dict]:
    """
    IBKR: market-data gateway TCP probe only. No account, position, order, or
    trade endpoints are called. IBKR is the source of truth for portfolio state
    and must never be queried programmatically outside of bot_trading.py.

    This test probes whether the IB Gateway is reachable on the local network.
    A refused connection is not a trading failure — it means the gateway is not
    running (expected outside market hours or when TWS is closed).
    """
    results = []

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port_str = os.getenv("IBKR_PORT", "7497")
    account = os.getenv("IBKR_PAPER_ACCOUNT", "")
    creds = bool(account)

    import socket
    try:
        port = int(port_str)
        t0 = time.monotonic()
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
        ms = (time.monotonic() - t0) * 1000
        results.append(_result(
            "ibkr", f"market_data_gateway_tcp_probe {host}:{port}",
            success=True, latency_ms=ms,
            credentials_present=creds,
            detail=(
                "IB Gateway market-data port reachable — TCP probe only. "
                "No account/position/order/execution calls made."
            ),
            data_sample="TCP connect ok",
        ))
    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        results.append(_result(
            "ibkr", f"market_data_gateway_tcp_probe {host}:{port_str}",
            success=False, latency_ms=0.0,
            credentials_present=creds,
            detail=(
                "IB Gateway market-data port not reachable — gateway not running. "
                "Expected when TWS/IB Gateway is closed outside market hours. "
                "Not a trading failure — no order/account/position calls attempted."
            ),
            error=str(exc)[:100],
        ))

    return results


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary(all_results: list[dict]) -> dict:
    total = len(all_results)
    passed = sum(1 for r in all_results if r["success"])
    failed = total - passed

    by_provider: dict[str, dict] = {}
    for r in all_results:
        p = r["provider"]
        if p not in by_provider:
            by_provider[p] = {"total": 0, "passed": 0, "failed": 0}
        by_provider[p]["total"] += 1
        if r["success"]:
            by_provider[p]["passed"] += 1
        else:
            by_provider[p]["failed"] += 1

    latencies = [r["latency_ms"] for r in all_results if r["success"] and r["latency_ms"] > 0]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
    max_latency = round(max(latencies), 1) if latencies else 0.0

    return {
        "total_tests": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "avg_latency_ms": avg_latency,
        "max_latency_ms": max_latency,
        "by_provider": by_provider,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build() -> None:
    _load_env()
    _REF_DIR.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []

    print("  testing alpaca ...", flush=True)
    all_results.extend(_test_alpaca())

    print("  testing fmp ...", flush=True)
    all_results.extend(_test_fmp())

    print("  testing alpha_vantage ...", flush=True)
    all_results.extend(_test_alpha_vantage())

    print("  testing yfinance ...", flush=True)
    all_results.extend(_test_yfinance())

    print("  testing ibkr (connection check only) ...", flush=True)
    all_results.extend(_test_ibkr())

    summary = _build_summary(all_results)

    output = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "provider_fetch_tester",
        "test_symbol": _TEST_SYMBOL,
        "safety": _SAFETY,
        "summary": summary,
        "results": all_results,
    }

    out_path = _REF_DIR / "provider_fetch_test_results.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"  wrote {out_path}")

    # Print quick summary
    print(f"\nProvider fetch tests: {summary['passed']}/{summary['total_tests']} passed")
    for prov, stats in summary["by_provider"].items():
        print(f"  {prov}: {stats['passed']}/{stats['total']} passed")
    print(f"Safety: {', '.join(f'{k}={v}' for k, v in _SAFETY.items())}")


if __name__ == "__main__":
    build()
