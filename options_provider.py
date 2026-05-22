# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_provider.py                        ║
# ║   Single responsibility: fetch real options flow metrics      ║
# ║   for unusual-volume detection.                              ║
# ║                                                              ║
# ║   Exposed API:                                               ║
# ║     get_options_flow_data(symbol, min_dte, max_dte)          ║
# ║       → OptionsFlowData | None                              ║
# ║                                                              ║
# ║   Provider audit results:                                    ║
# ║     FMP: NOT_USABLE_FOR_OPTIONS (all endpoints 404/403)      ║
# ║     Alpaca: PARTIAL_FLOW (real volume, no OI)                ║
# ║                                                              ║
# ║   No trading logic. No signals. Data only.                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from config import CONFIG

log = logging.getLogger("decifer.options_provider")

# ── Alpaca request class (bound at import time so test stubs can't shadow it) ─
try:
    from alpaca.data.requests import OptionChainRequest as _OptionChainRequest
except ImportError:
    _OptionChainRequest = None  # alpaca-py not installed

# ── Provider audit result ─────────────────────────────────────────────
# Audit 2026-05-22: all FMP stable options endpoints return 404.
# Legacy v4 endpoints return 403. No FMP code in production options runtime.
FMP_PROVIDER_STATUS = "NOT_USABLE_FOR_OPTIONS"

# ── Detection thresholds ──────────────────────────────────────────────
UNUSUAL_VOL_OI_RATIO = 0.25       # volume / OI threshold (OI path — OI not currently available)
MIN_SIDE_VOLUME = 250             # minimum call or put contracts traded today
MIN_CONTRACT_VOLUME = 25          # minimum for a selected contract
MIN_SIDE_TRADE_COUNT = 20         # minimum trade executions (n field from dailyBar)
MIN_OPEN_INTEREST = 100           # OI floor (OI path — not currently available from Alpaca)
MIN_DAY_OVER_DAY_RATIO = 1.75     # today_vol / prev_vol threshold for volume expansion signal
PREV_VOLUME_FLOOR = 50            # denominator floor for day-over-day ratio (avoids division by tiny prev)

# ── OCC symbol parsing ────────────────────────────────────────────────
_OCC_RE = re.compile(r"^([A-Z ]{1,6})(\d{6})([CP])(\d{8})$")


def _parse_opt_type(sym: str) -> str | None:
    """Parse OCC option symbol and return 'C', 'P', or None."""
    m = _OCC_RE.match(sym.strip())
    if not m:
        return None
    return m.group(3)  # 'C' or 'P'


def _parse_exp_date(sym: str) -> date | None:
    """Parse OCC option symbol and return expiry date or None."""
    m = _OCC_RE.match(sym.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(2), "%y%m%d").date()
    except Exception:
        return None


# ── Raw Alpaca client singleton ───────────────────────────────────────
_raw_client_lock = threading.Lock()
_raw_options_client = None


def _get_raw_client():
    """Return a lazily-created raw OptionHistoricalDataClient (raw_data=True), or None."""
    global _raw_options_client
    if _raw_options_client is not None:
        return _raw_options_client
    with _raw_client_lock:
        if _raw_options_client is not None:
            return _raw_options_client
        api_key = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")
        if not api_key or not secret_key:
            log.debug("options_provider: ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
            return None
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
        except ImportError as exc:
            log.error(f"options_provider: alpaca-py import failed ({exc})")
            return None
        try:
            _raw_options_client = OptionHistoricalDataClient(api_key, secret_key, raw_data=True)
            log.info("options_provider: raw OptionHistoricalDataClient initialised")
            return _raw_options_client
        except Exception as exc:
            log.error(f"options_provider: raw client init failed — {type(exc).__name__}: {exc}")
            return None


# ── Data contract ─────────────────────────────────────────────────────


@dataclass
class OptionsFlowData:
    """
    Canonical options flow data container.

    All fields are explicitly populated — no optional fields silently omitted.
    source labels track exactly where each number came from.
    """
    symbol: str
    expiry: str        # nearest expiry in window "YYYY-MM-DD"
    dte: int           # days to expiry

    # Call side
    call_volume: float
    call_volume_source: str           # e.g. "alpaca_rest_dailyBar" or "unavailable"
    call_trade_count: float
    call_trade_count_source: str
    call_prev_volume: float
    call_prev_volume_source: str      # "alpaca_rest_prevDailyBar" or "unavailable"
    call_open_interest: float | None  # None = not available from this provider
    call_open_interest_source: str    # "unavailable" when None

    # Put side
    put_volume: float
    put_volume_source: str
    put_trade_count: float
    put_trade_count_source: str
    put_prev_volume: float
    put_prev_volume_source: str
    put_open_interest: float | None
    put_open_interest_source: str

    # Provider metadata
    provider: str                     # e.g. "alpaca_rest_dailyBar"
    provider_status: str              # "FULL_FLOW" | "PARTIAL_FLOW" | "NULL"
    flow_definition: str              # "OI_RATIO" | "VOLUME_EXPANSION" | "NONE"
    provider_timestamp: str           # ISO timestamp of fetch
    data_quality: str                 # "REAL" | "MISSING"
    flow_metrics_available: bool      # True when sufficient data to evaluate unusual flow


# ── Public API ────────────────────────────────────────────────────────


def get_options_flow_data(symbol: str, min_dte: int, max_dte: int) -> OptionsFlowData | None:
    """
    Fetch real options flow metrics for unusual-volume detection.

    Provider chain:
      1. FMP — skipped (NOT_USABLE_FOR_OPTIONS; all endpoints return 404/403)
      2. Alpaca raw chain (raw_data=True) — PARTIAL_FLOW (real volume, no OI)
      3. None — null provider if Alpaca unavailable

    Returns OptionsFlowData or None if no approved provider is available.
    """
    # Step 1: FMP — skip (audit result: NOT_USABLE_FOR_OPTIONS)
    log.debug(f"options_provider: FMP={FMP_PROVIDER_STATUS} for {symbol} — skipping")

    # Step 2: Alpaca raw chain
    return _fetch_alpaca_flow(symbol, min_dte, max_dte)


def _fetch_alpaca_flow(symbol: str, min_dte: int, max_dte: int) -> OptionsFlowData | None:
    """
    Fetch options flow from Alpaca raw chain (raw_data=True).

    Uses dailyBar.v for real traded volume, dailyBar.n for trade count,
    and prevDailyBar.v for prior day volume (absent on ~20% of contracts — uses 0).
    Open interest is NOT available from Alpaca — always None.
    """
    client = _get_raw_client()
    if client is None:
        log.debug(f"options_provider: Alpaca raw client unavailable for {symbol}")
        return None

    if _OptionChainRequest is None:
        log.warning(f"options_provider: alpaca-py not installed for {symbol}")
        return None

    today = date.today()
    date_min = today + timedelta(days=min_dte)
    date_max = today + timedelta(days=max_dte)

    try:
        req = _OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=date_min,
            expiration_date_lte=date_max,
        )
        raw = client.get_option_chain(req)  # returns dict of {occ_symbol: raw_dict}
    except Exception as exc:
        log.warning(f"options_provider: Alpaca chain fetch failed for {symbol} — {exc}")
        return None

    if not raw:
        log.debug(f"options_provider: Alpaca returned empty chain for {symbol}")
        return None

    # Aggregate call and put volumes across all contracts in the DTE window
    call_volume = 0.0
    call_trade_count = 0.0
    call_prev_volume = 0.0
    put_volume = 0.0
    put_trade_count = 0.0
    put_prev_volume = 0.0

    # Track nearest expiry for metadata
    nearest_exp: date | None = None

    for occ_sym, raw_dict in raw.items():
        opt_type = _parse_opt_type(occ_sym)
        if opt_type is None:
            continue

        exp_date = _parse_exp_date(occ_sym)
        if exp_date is not None:
            if nearest_exp is None or exp_date < nearest_exp:
                nearest_exp = exp_date

        daily_bar = raw_dict.get("dailyBar") or {}
        prev_bar = raw_dict.get("prevDailyBar") or {}  # absent on ~20% of contracts

        v = float(daily_bar.get("v", 0) or 0)   # real traded contracts today
        n = float(daily_bar.get("n", 0) or 0)   # real trade count today
        pv = float(prev_bar.get("v", 0) or 0)   # previous day volume (0 if absent)

        if opt_type == "C":
            call_volume += v
            call_trade_count += n
            call_prev_volume += pv
        else:  # 'P'
            put_volume += v
            put_trade_count += n
            put_prev_volume += pv

    # Compute expiry metadata
    if nearest_exp is not None:
        expiry_str = nearest_exp.strftime("%Y-%m-%d")
        dte = (nearest_exp - today).days
    else:
        expiry_str = ""
        dte = 0

    provider_ts = datetime.now(tz=timezone.utc).isoformat()

    # Flow metrics are available when we have real volume data from Alpaca
    # (even if OI is unavailable — VOLUME_EXPANSION path uses dailyBar.v)
    flow_metrics_available = (call_volume > 0 or put_volume > 0)

    return OptionsFlowData(
        symbol=symbol,
        expiry=expiry_str,
        dte=dte,
        call_volume=call_volume,
        call_volume_source="alpaca_rest_dailyBar",
        call_trade_count=call_trade_count,
        call_trade_count_source="alpaca_rest_dailyBar",
        call_prev_volume=call_prev_volume,
        call_prev_volume_source="alpaca_rest_prevDailyBar",
        call_open_interest=None,          # not available from Alpaca
        call_open_interest_source="unavailable",
        put_volume=put_volume,
        put_volume_source="alpaca_rest_dailyBar",
        put_trade_count=put_trade_count,
        put_trade_count_source="alpaca_rest_dailyBar",
        put_prev_volume=put_prev_volume,
        put_prev_volume_source="alpaca_rest_prevDailyBar",
        put_open_interest=None,           # not available from Alpaca
        put_open_interest_source="unavailable",
        provider="alpaca_rest_dailyBar",
        provider_status="PARTIAL_FLOW",   # real volume, no OI
        flow_definition="VOLUME_EXPANSION",
        provider_timestamp=provider_ts,
        data_quality="REAL",
        flow_metrics_available=flow_metrics_available,
    )
