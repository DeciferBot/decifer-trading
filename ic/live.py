# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/live.py                                ║
# ║   Live-trade IC tracking and comparison against historical  ║
# ║   IC.  Covers the trades-log-driven workflow from entry to  ║
# ║   milestone-gated disable recommendation.                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
from datetime import UTC, datetime

import schemas

import numpy as np

from ic.constants import (
    DIMENSIONS,
    IC_LIVE_FILE,
    IC_LIVE_HISTORY_FILE,
    LIVE_IC_MILESTONE,
    _BASE,
    _CORE_DIMENSIONS,
    _LIVE_IC_REPORT_FILE,
    _TRADES_FILE,
    log,
)
from ic.core import compute_rolling_ic
from ic.data import _load_signal_records
from ic.math import _spearman, _zscore_array


def update_live_ic(signals_log_path: str | None = None) -> dict:
    """
    Compute IC from live trades only and write to ic_weights_live.json.

    This is intentionally separate from update_ic_weights() which uses the
    historical corpus. Live IC is a small-sample real-time signal:
      - n < MIN_VALID  → raw_ic all None (not enough data yet)
      - n >= MIN_VALID → real Spearman IC from actual closed trades

    Written to:
      data/ic_weights_live.json          — latest live IC snapshot
      data/ic_weights_live_history.jsonl — append-only history for trend tracking

    Call this on the same weekly cycle as update_ic_weights().
    """
    raw_ic = compute_rolling_ic(signals_log_path)  # live-only (no historical_log_path)
    n_records = len(_load_signal_records(signals_log_path))

    record = {
        "updated": datetime.now(UTC).isoformat(),
        "raw_ic": {d: (raw_ic.get(d) if raw_ic.get(d) is not None else None) for d in DIMENSIONS},
        "n_records": n_records,
        "source": "live_trades_only",
    }

    os.makedirs(os.path.dirname(IC_LIVE_FILE), exist_ok=True)

    import tempfile

    dir_ = os.path.dirname(IC_LIVE_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
        os.replace(tmp, IC_LIVE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    with open(IC_LIVE_HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    log.info(
        "Live IC updated (n=%d): %s",
        n_records,
        ", ".join(f"{d}={raw_ic[d]:.3f}" if raw_ic.get(d) is not None else f"{d}=None" for d in DIMENSIONS),
    )
    return record


def compute_live_trade_ic(trades_path: str | None = None) -> dict:
    """
    Compute IC from our own closed trades using actual PnL as the return proxy.

    For each closed trade that has both ``signal_scores`` (non-empty) and a
    numeric ``pnl``, we derive a return proxy:

        pnl_pct = pnl / (entry_price * qty)

    Then Spearman IC is computed per dimension exactly as in compute_rolling_ic.

    Returns
    -------
    dict with keys:
      "n_trades"  — number of eligible closed trades
      "raw_ic"    — {dim: float or None}
      "timestamp" — ISO timestamp
    """
    path = trades_path or _TRADES_FILE
    try:
        with open(path) as f:
            raw = json.load(f)
        items = list(raw.values()) if isinstance(raw, dict) else raw
    except Exception as e:
        log.warning("compute_live_trade_ic: cannot load trades: %s", e)
        return {"n_trades": 0, "raw_ic": {d: None for d in DIMENSIONS}}

    eligible = []
    for t in items:
        try:
            schemas.validate_trade(t)
        except ValueError as _ve:
            log.warning("compute_live_trade_ic: skipping bad trade record: %s", _ve)
            continue
        scores = t.get("signal_scores")
        if not scores or not isinstance(scores, dict):
            continue
        pnl = t.get("pnl")
        if pnl is None:
            continue
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            continue
        entry = t.get("entry_price") or t.get("price") or 0.0
        qty = t.get("qty") or 1
        try:
            notional = float(entry) * float(qty)
            pnl_pct = pnl / notional if notional > 0 else None
        except (TypeError, ValueError):
            pnl_pct = None
        if pnl_pct is None or not np.isfinite(pnl_pct):
            continue
        eligible.append((scores, pnl_pct))

    n = len(eligible)
    if n < 10:
        log.info("compute_live_trade_ic: only %d eligible trades (need ≥10)", n)
        return {"n_trades": n, "raw_ic": {d: None for d in DIMENSIONS}, "timestamp": datetime.now(UTC).isoformat()}

    dim_scores: dict = {d: [] for d in DIMENSIONS}
    pnl_arr: list = []
    for scores, pnl_pct in eligible:
        pnl_arr.append(pnl_pct)
        for d in DIMENSIONS:
            dim_scores[d].append(float(scores.get(d, 0)))

    pnl_np = np.array(pnl_arr)
    raw_ic: dict = {}
    for d in DIMENSIONS:
        arr = np.array(dim_scores[d])
        if arr.std() < 1e-9:
            raw_ic[d] = 0.0
            continue
        try:
            raw_ic[d] = float(_spearman(_zscore_array(arr), pnl_np))
        except Exception:
            raw_ic[d] = None

    result = {
        "n_trades": n,
        "raw_ic": raw_ic,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    log.info(
        "compute_live_trade_ic: n=%d  IC=[%s]",
        n,
        ", ".join(f"{d}={raw_ic.get(d, 0):.3f}" for d in DIMENSIONS if raw_ic.get(d)),
    )
    return result


def compare_live_vs_historical_ic(
    trades_path: str | None = None,
    historical_log_path: str | None = None,
    milestone: int = LIVE_IC_MILESTONE,
) -> dict:
    """
    Compare our live-trade IC profile against historical IC.

    Returns a report dict with:
      "n_live_trades"     — eligible live trades
      "ready"             — True if n_live_trades >= milestone
      "progress_pct"      — 0-100
      "agreement_r"       — Spearman r between live IC vector and historical IC vector
                            (computed over the 9 core dims; None if not ready)
      "agreement_label"   — "STRONG" / "MODERATE" / "WEAK" / "DIVERGENT" / "PENDING"
      "dim_comparison"    — {dim: {"live": float, "hist": float, "agree": bool}}
      "recommend_disable" — True if ready and agreement_r >= 0.5
      "live_ic"           — raw live IC dict
      "hist_ic"           — raw historical IC dict
      "timestamp"         — ISO
    """
    live_result = compute_live_trade_ic(trades_path)
    n = live_result["n_trades"]
    live_ic = live_result["raw_ic"]

    # Always compute current historical IC for the comparison baseline
    hist_log = historical_log_path or os.path.join(_BASE, "data", "signals_log_historical.jsonl")
    hist_ic = compute_rolling_ic(historical_log_path=hist_log)

    report: dict = {
        "n_live_trades": n,
        "ready": n >= milestone,
        "progress_pct": round(min(n / milestone * 100, 100), 1),
        "agreement_r": None,
        "agreement_label": "PENDING",
        "dim_comparison": {},
        "recommend_disable": False,
        "live_ic": live_ic,
        "hist_ic": hist_ic,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    # Always build per-dim comparison table (useful even before milestone)
    for d in _CORE_DIMENSIONS:
        lv = live_ic.get(d)
        hv = hist_ic.get(d)
        agree = (
            lv is not None
            and hv is not None
            and np.isfinite(lv)
            and np.isfinite(hv)
            and ((lv >= 0) == (hv >= 0))  # same sign
        )
        report["dim_comparison"][d] = {
            "live": round(lv, 4) if lv is not None else None,
            "hist": round(hv, 4) if hv is not None else None,
            "agree": agree,
        }

    if n < milestone:
        log.info(
            "compare_live_vs_historical_ic: %d/%d trades — %.0f%% to milestone",
            n,
            milestone,
            report["progress_pct"],
        )
        _write_live_ic_report(report)
        return report

    # Compute vector correlation over core dims where both have finite values
    pairs = [
        (live_ic[d], hist_ic[d])
        for d in _CORE_DIMENSIONS
        if live_ic.get(d) is not None
        and hist_ic.get(d) is not None
        and np.isfinite(live_ic[d])
        and np.isfinite(hist_ic[d])
    ]
    if len(pairs) >= 3:
        live_vec = np.array([p[0] for p in pairs])
        hist_vec = np.array([p[1] for p in pairs])
        r = (
            float(_spearman(_zscore_array(live_vec), _zscore_array(hist_vec)))
            if live_vec.std() > 1e-9 and hist_vec.std() > 1e-9
            else 0.0
        )
        report["agreement_r"] = round(r, 4)
        if r >= 0.70:
            label = "STRONG"
        elif r >= 0.50:
            label = "MODERATE"
        elif r >= 0.25:
            label = "WEAK"
        else:
            label = "DIVERGENT"
        report["agreement_label"] = label
        report["recommend_disable"] = r >= 0.50

    log.info(
        "compare_live_vs_historical_ic: n=%d  r=%.3f  label=%s  recommend_disable=%s",
        n,
        report["agreement_r"] or 0.0,
        report["agreement_label"],
        report["recommend_disable"],
    )
    _write_live_ic_report(report)
    return report


def _write_live_ic_report(report: dict) -> None:
    """Atomically write the live IC report to disk."""
    import tempfile

    dir_ = os.path.dirname(_LIVE_IC_REPORT_FILE)
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(report, f, indent=2)
        os.replace(tmp, _LIVE_IC_REPORT_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def get_live_ic_progress() -> dict:
    """
    Quick read of the live IC report from disk (no computation).
    Returns {"n_live_trades": int, "progress_pct": float, ...} or a
    default stub if the report has never been written.
    """
    try:
        with open(_LIVE_IC_REPORT_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "n_live_trades": 0,
            "progress_pct": 0.0,
            "ready": False,
            "agreement_label": "PENDING",
            "recommend_disable": False,
        }
    except Exception as e:
        log.warning("get_live_ic_progress: %s", e)
        return {
            "n_live_trades": 0,
            "progress_pct": 0.0,
            "ready": False,
            "agreement_label": "PENDING",
            "recommend_disable": False,
        }
