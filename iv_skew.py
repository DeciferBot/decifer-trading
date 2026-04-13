# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  iv_skew.py                                ║
# ║   Single responsibility: fetch Alpaca options chain for a   ║
# ║   symbol and compute OTM put / ATM call IV skew.            ║
# ║                                                             ║
# ║   Skew = IV(OTM put, delta ≈ -0.25) − IV(ATM call, δ≈0.50) ║
# ║   Positive skew = put fear premium = informed hedging.      ║
# ║   Wu & Tian (2024, Management Science): high put-call skew  ║
# ║   predicts negative next-period returns.                    ║
# ║                                                             ║
# ║   Used by: signals.fetch_multi_timeframe                    ║
# ║   Nothing else lives here. No trading logic.                ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import re
import threading
from datetime import date, timedelta

from config import CONFIG

log = logging.getLogger("decifer.iv_skew")

# ── Lazy client singleton ─────────────────────────────────────────────────────
# Thread-safe: double-checked lock guards initialisation.

_client = None
_client_lock = threading.Lock()


def _get_client():
    """Return a cached OptionHistoricalDataClient, or None if keys are not set."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        api_key = CONFIG.get("alpaca_api_key", "")
        secret_key = CONFIG.get("alpaca_secret_key", "")
        if not api_key or not secret_key:
            return None
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient

            _client = OptionHistoricalDataClient(api_key, secret_key)
            log.debug("iv_skew: OptionHistoricalDataClient initialised")
        except ImportError:
            log.debug("iv_skew: alpaca-py not installed — pip install alpaca-py")
        except Exception as exc:
            log.debug(f"iv_skew: client init failed — {exc}")
    return _client


# ── OCC option symbol parser ─────────────────────────────────────────────────
# Format: <underlying><YYMMDD><C|P><8-digit strike * 1000>
# e.g.   AAPL260417P00300000  → put, 2026-04-17, $300.00
#        AAPL260422C00247500  → call, 2026-04-22, $247.50
_OCC_RE = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")


def _parse_occ(symbol: str) -> tuple[str, str, float] | None:
    """
    Parse an OCC option symbol string.
    Returns (expiry_iso: str, ctype: 'call'|'put', strike: float) or None.
    """
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    _, date_str, cp, strike_str = m.groups()
    try:
        yy, mm, dd = int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6])
        expiry = date(2000 + yy, mm, dd).isoformat()
    except (ValueError, OverflowError):
        return None
    ctype = "call" if cp == "C" else "put"
    strike = int(strike_str) / 1000.0
    return expiry, ctype, strike


# ── Day-keyed result cache ────────────────────────────────────────────────────
# IV skew is a daily dimension — one fetch per (symbol, calendar date) is enough.
# Re-fetch automatically on a new trading day.

_cache: dict[tuple, dict] = {}


def get_iv_skew(symbol: str) -> dict | None:
    """
    Fetch Alpaca options chain for *symbol* and return the put-call IV skew.

    Selection rules
    ---------------
    - Expiry window  : [dte_min, dte_max] days out (default 7–60).
    - Target expiry  : closest to target_dte days (default 30) — avoids
                       gamma-dominated near-term contracts.
    - ATM call       : contract whose delta is closest to +atm_call_delta (0.50).
    - OTM put        : contract whose delta is closest to  otm_put_delta (-0.25).

    Skew formula
    ------------
    skew = otm_put_IV − atm_call_IV

    Positive skew means puts are priced higher than calls (fear / downside hedging).
    Wu & Tian (2024) show this predicts negative next-period equity returns.

    Return value
    ------------
    dict with keys:
        skew          (float)  raw OTM put IV − ATM call IV
        otm_put_iv    (float)  implied volatility of the selected OTM put
        atm_call_iv   (float)  implied volatility of the selected ATM call
        iv_skew_score (int)    0–10 quality score (direction-agnostic magnitude)
        iv_skew_dir   (int)    +1 = bullish, −1 = bearish, 0 = neutral
        expiry        (str)    expiration date used (YYYY-MM-DD)
        source        (str)    always "alpaca"

    Returns None when:
        - Alpaca keys missing or alpaca-py not installed.
        - No chain data returned for the symbol.
        - Fewer than 2 qualifying contracts found.
    """
    today = date.today()
    cache_key = (symbol, today.isoformat())
    if cache_key in _cache:
        return _cache[cache_key]

    client = _get_client()
    if client is None:
        return None

    cfg = CONFIG.get("iv_skew", {})
    dte_min = cfg.get("dte_min", 7)
    dte_max = cfg.get("dte_max", 60)
    target_dte = cfg.get("target_dte", 30)
    otm_delta_target = cfg.get("otm_put_delta", -0.25)  # put delta is negative
    atm_delta_target = cfg.get("atm_call_delta", 0.50)

    try:
        from alpaca.data.requests import OptionChainRequest

        exp_min = today + timedelta(days=dte_min)
        exp_max = today + timedelta(days=dte_max)

        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=exp_min,
            expiration_date_lte=exp_max,
        )
        chain: dict = client.get_option_chain(req)

        if not chain:
            return None

        # Parse each OCC symbol to extract expiry, type, strike.
        # Alpaca's OptionsSnapshot has no .details sub-object —
        # all contract metadata is encoded in snap.symbol (OCC format).
        expiries: dict[str, list] = {}
        for occ_sym, snap in chain.items():
            parsed = _parse_occ(occ_sym)
            if parsed is None:
                continue
            exp, ctype, strike = parsed
            iv = snap.implied_volatility
            delta = snap.greeks.delta if snap.greeks else None
            if iv is None or iv <= 0 or delta is None:
                continue
            expiries.setdefault(exp, []).append(
                {
                    "delta": float(delta),
                    "iv": float(iv),
                    "strike": strike,
                    "ctype": ctype,
                }
            )

        if not expiries:
            return None

        # Pick expiry closest to target_dte
        def _dte(exp_str: str) -> int:
            return (date.fromisoformat(exp_str) - today).days

        best_expiry = min(expiries.keys(), key=lambda e: abs(_dte(e) - target_dte))
        entries = expiries[best_expiry]

        # Separate calls and puts
        calls = [e for e in entries if e["ctype"] == "call"]
        puts = [e for e in entries if e["ctype"] == "put"]

        if not calls or not puts:
            return None

        # ATM call: delta closest to +0.50
        atm_call = min(calls, key=lambda c: abs(c["delta"] - atm_delta_target))
        # OTM put:  delta closest to -0.25
        otm_put = min(puts, key=lambda p: abs(p["delta"] - otm_delta_target))

        atm_call_iv = atm_call["iv"]
        otm_put_iv = otm_put["iv"]
        skew = otm_put_iv - atm_call_iv

        iv_skew_score, iv_skew_dir = _score_skew(skew, cfg)

        result = {
            "skew": round(skew, 4),
            "otm_put_iv": round(otm_put_iv, 4),
            "atm_call_iv": round(atm_call_iv, 4),
            "iv_skew_score": iv_skew_score,
            "iv_skew_dir": iv_skew_dir,
            "expiry": best_expiry,
            "source": "alpaca",
        }
        _cache[cache_key] = result
        log.debug(
            f"iv_skew {symbol}: skew={skew:.4f} "
            f"(put={otm_put_iv:.3f} call={atm_call_iv:.3f}) "
            f"score={iv_skew_score} dir={iv_skew_dir} expiry={best_expiry}"
        )
        return result

    except Exception as exc:
        log.debug(f"iv_skew {symbol}: fetch failed — {exc}")
        return None


def _score_skew(skew: float, cfg: dict) -> tuple[int, int]:
    """
    Map raw skew value → (score: 0–10, direction: +1/−1/0).

    High positive skew (put IV >> call IV)
        → informed downside hedging → bearish signal (dir = −1).
    Low / negative skew (call IV ≥ put IV)
        → complacency / call demand → slight bullish lean (dir = +1).
    Neutral band
        → no structural edge (score = 0, dir = 0).

    Thresholds are configurable in config["iv_skew"].
    """
    hi = cfg.get("skew_bearish_hi", 0.15)
    mid = cfg.get("skew_bearish_mid", 0.10)
    lo = cfg.get("skew_bearish_lo", 0.05)
    bull = cfg.get("skew_bullish_lo", -0.03)

    if skew > hi:
        return 10, -1
    if skew > mid:
        return 7, -1
    if skew > lo:
        return 4, -1
    if skew >= bull:
        return 0, 0  # neutral band
    return 3, +1  # complacency — slight bullish lean
