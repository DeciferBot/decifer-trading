# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic_validator.py                           ║
# ║   IC validation gate: reads ic_weights.json + backtest      ║
# ║   results, evaluates three gates (sample, IC, Sharpe),      ║
# ║   and persists a LiveReadinessReport to disk.               ║
# ║                                                             ║
# ║   Gate requirements (all must pass before Phase 4):         ║
# ║     1. Sample gate  — n_valid_records >= 50                 ║
# ║     2. IC gate      — mean positive IC >= 0.05,             ║
# ║                       at least 5 dims with IC > 0           ║
# ║     3. Sharpe gate  — walk-forward out-of-sample Sharpe     ║
# ║                       >= 0.8                                ║
# ║                                                             ║
# ║   Usage:                                                    ║
# ║     python ic_validator.py          — print current status  ║
# ║     python ic_validator.py --save   — persist to disk       ║
# ║                                                             ║
# ║   Files read:                                               ║
# ║     data/ic_weights.json            — weekly IC cache       ║
# ║     backtest_results/*.json         — walk-forward results  ║
# ║                                                             ║
# ║   File written:                                             ║
# ║     data/ic_validation_result.json  — gate outcome          ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("decifer.ic_validator")

# ── Constants ──────────────────────────────────────────────────────────────────

_BASE = os.path.dirname(os.path.abspath(__file__))

_DEFAULT_IC_WEIGHTS_PATH = os.path.join(_BASE, "data", "ic_weights.json")
_DEFAULT_RESULTS_DIR     = os.path.join(_BASE, "backtest_results")
_DEFAULT_VALIDATION_OUT  = os.path.join(_BASE, "data", "ic_validation_result.json")

DIMENSIONS = [
    "trend", "momentum", "squeeze", "flow", "breakout",
    "mtf", "news", "social", "reversion",
]


# ── Config helper ──────────────────────────────────────────────────────────────

def _val_cfg(key: str, default):
    """Read from CONFIG['phase_gate']['ic_validation_gate'], falling back to default."""
    try:
        from config import CONFIG
        return CONFIG.get("phase_gate", {}).get("ic_validation_gate", {}).get(key, default)
    except Exception:
        return default


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class ICHealthReport:
    """Snapshot of IC quality read from the weekly ic_weights.json cache."""
    n_records:           int           # total records loaded when IC was computed
    n_valid_records:     int           # proxy: same as n_records (cache doesn't separate)
    n_positive_dims:     int           # dimensions with raw IC > 0
    raw_ic:              dict          # dimension → float | None from cache
    mean_positive_ic:    float         # mean of positive-IC values; 0.0 if none
    using_equal_weights: bool          # True when cache flagged equal-weights fallback
    quality:             str           # "STRONG" | "MODERATE" | "WEAK" | "NO_SIGNAL"


@dataclass
class LiveReadinessReport:
    """Outcome of all three IC validation gates."""
    # Gate outcomes
    sample_gate_passed:  bool
    ic_gate_passed:      bool
    sharpe_gate_passed:  bool

    # Diagnostic data
    n_valid_records:     int
    mean_positive_ic:    float
    n_positive_dims:     int
    walkforward_sharpe:  Optional[float]
    ic_quality:          str

    # Aggregate
    failures:            list = field(default_factory=list)
    ready_for_live:      bool = False
    checked_at:          str  = ""     # ISO 8601 UTC timestamp

    def as_dict(self) -> dict:
        return asdict(self)


# ── IC health ──────────────────────────────────────────────────────────────────

def get_ic_health(ic_weights_path: Optional[str] = None) -> ICHealthReport:
    """
    Read data/ic_weights.json and classify IC quality.

    Does NOT re-run IC computation — reads the weekly cache written by
    ic_calculator.update_ic_weights().  This keeps the check fast and
    deterministic (no yfinance calls).

    Returns ICHealthReport with quality in {"STRONG","MODERATE","WEAK","NO_SIGNAL"}.
    """
    path = ic_weights_path or _DEFAULT_IC_WEIGHTS_PATH

    _empty = ICHealthReport(
        n_records=0,
        n_valid_records=0,
        n_positive_dims=0,
        raw_ic={d: None for d in DIMENSIONS},
        mean_positive_ic=0.0,
        using_equal_weights=True,
        quality="NO_SIGNAL",
    )

    if not os.path.exists(path):
        log.info("get_ic_health: cache not found at %s", path)
        return _empty

    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        log.warning("get_ic_health: failed to read cache: %s", e)
        return _empty

    raw_ic: dict = data.get("raw_ic", {})
    n_records: int = int(data.get("n_records", 0))
    using_equal: bool = bool(data.get("using_equal_weights", True))

    positive_vals = [
        float(v)
        for d in DIMENSIONS
        for v in [raw_ic.get(d)]
        if v is not None and isinstance(v, (int, float)) and float(v) > 0.0
    ]
    n_positive = len(positive_vals)
    mean_pos = sum(positive_vals) / n_positive if n_positive else 0.0

    # Quality classification
    if using_equal or n_positive == 0:
        quality = "NO_SIGNAL"
    elif mean_pos >= 0.05 and n_positive >= 5:
        quality = "STRONG"
    elif mean_pos >= 0.02 and n_positive >= 3:
        quality = "MODERATE"
    else:
        quality = "WEAK"

    return ICHealthReport(
        n_records=n_records,
        n_valid_records=n_records,
        n_positive_dims=n_positive,
        raw_ic={d: raw_ic.get(d) for d in DIMENSIONS},
        mean_positive_ic=round(mean_pos, 6),
        using_equal_weights=using_equal,
        quality=quality,
    )


# ── Walk-forward Sharpe ────────────────────────────────────────────────────────

def load_walkforward_sharpe(results_dir: Optional[str] = None) -> Optional[float]:
    """
    Scan backtest_results/ for the most recently modified JSON that contains
    report.sharpe_ratio and return that value.

    Returns None if the directory is empty, no file matches, or all JSON is malformed.
    """
    rdir = Path(results_dir or _DEFAULT_RESULTS_DIR)
    if not rdir.exists():
        log.info("load_walkforward_sharpe: results dir not found: %s", rdir)
        return None

    candidates = sorted(
        rdir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        log.info("load_walkforward_sharpe: no JSON files in %s", rdir)
        return None

    for fp in candidates:
        try:
            with open(fp) as f:
                data = json.load(f)
        except Exception as e:
            log.debug("load_walkforward_sharpe: skip %s (%s)", fp.name, e)
            continue
        sharpe = data.get("report", {}).get("sharpe_ratio")
        if sharpe is not None:
            try:
                val = float(sharpe)
                log.info("load_walkforward_sharpe: %s → sharpe=%.3f", fp.name, val)
                return val
            except (TypeError, ValueError):
                continue

    log.warning("load_walkforward_sharpe: no file with report.sharpe_ratio in %s", rdir)
    return None


# ── Gate evaluation ────────────────────────────────────────────────────────────

def check_live_readiness(config: Optional[dict] = None) -> LiveReadinessReport:
    """
    Evaluate all three IC validation gates without writing to disk.

    Gate thresholds are read from config['phase_gate']['ic_validation_gate']
    or fall back to hardcoded defaults.

    Returns LiveReadinessReport with ready_for_live=True only when all pass.
    """
    min_records  = _val_cfg("min_valid_records",      50)
    min_mean_ic  = _val_cfg("min_mean_positive_ic",  0.05)
    min_pos_dims = _val_cfg("min_positive_dims",       5)
    min_sharpe   = _val_cfg("min_walkforward_sharpe", 0.8)

    health = get_ic_health()
    sharpe = load_walkforward_sharpe()
    failures: list[str] = []

    # Gate 1 — Sample size
    sample_ok = health.n_valid_records >= min_records
    if not sample_ok:
        failures.append(
            f"SAMPLE GATE: only {health.n_valid_records} valid IC records "
            f"(need {min_records}). Run the bot longer to accumulate forward-return data."
        )

    # Gate 2 — IC quality (breadth check takes priority in the message)
    ic_ok = (
        health.mean_positive_ic >= min_mean_ic
        and health.n_positive_dims >= min_pos_dims
    )
    if not ic_ok:
        if health.n_positive_dims < min_pos_dims:
            failures.append(
                f"IC GATE: only {health.n_positive_dims} dimensions with positive IC "
                f"(need {min_pos_dims}). Signal composite lacks breadth."
            )
        else:
            failures.append(
                f"IC GATE: mean positive IC {health.mean_positive_ic:.4f} "
                f"< threshold {min_mean_ic:.4f}. Dimensions are barely predictive."
            )

    # Gate 3 — Walk-forward Sharpe
    sharpe_ok = sharpe is not None and sharpe >= min_sharpe
    if not sharpe_ok:
        if sharpe is None:
            failures.append(
                f"SHARPE GATE: no walk-forward backtest results found in backtest_results/. "
                f"Run: python backtester.py --symbols <SYMBOLS> --start <DATE> --end <DATE>"
            )
        else:
            failures.append(
                f"SHARPE GATE: walk-forward Sharpe {sharpe:.3f} "
                f"< threshold {min_sharpe:.2f}. Out-of-sample performance insufficient."
            )

    return LiveReadinessReport(
        sample_gate_passed=sample_ok,
        ic_gate_passed=ic_ok,
        sharpe_gate_passed=sharpe_ok,
        n_valid_records=health.n_valid_records,
        mean_positive_ic=health.mean_positive_ic,
        n_positive_dims=health.n_positive_dims,
        walkforward_sharpe=sharpe,
        ic_quality=health.quality,
        failures=failures,
        ready_for_live=len(failures) == 0,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Persist ────────────────────────────────────────────────────────────────────

def validate_and_persist(out_path: Optional[str] = None) -> LiveReadinessReport:
    """
    Run check_live_readiness() and atomically write the result to
    data/ic_validation_result.json (or out_path).

    phase_gate.validate() reads this file when live accounts are active.
    """
    result = check_live_readiness()
    dest = out_path or _DEFAULT_VALIDATION_OUT
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    payload = result.as_dict()

    dir_ = os.path.dirname(os.path.abspath(dest))
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    log.info(
        "IC validation persisted → ready_for_live=%s  quality=%s  sharpe=%s",
        result.ready_for_live,
        result.ic_quality,
        f"{result.walkforward_sharpe:.3f}" if result.walkforward_sharpe is not None else "None",
    )
    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

def _print_report(result: LiveReadinessReport) -> None:
    print("\n" + "=" * 60)
    print("IC VALIDATION GATE".center(60))
    print("=" * 60)
    print(f"\nChecked at:       {result.checked_at}")
    print(f"IC quality:       {result.ic_quality}")
    print(f"Valid records:    {result.n_valid_records}")
    print(f"Positive dims:    {result.n_positive_dims}/9")
    print(f"Mean positive IC: {result.mean_positive_ic:.4f}")
    sharpe_str = f"{result.walkforward_sharpe:.3f}" if result.walkforward_sharpe is not None else "N/A"
    print(f"Walk-fwd Sharpe:  {sharpe_str}")

    print("\n--- GATE STATUS ---")
    gates = [
        ("Sample gate",  result.sample_gate_passed),
        ("IC gate",      result.ic_gate_passed),
        ("Sharpe gate",  result.sharpe_gate_passed),
    ]
    for name, passed in gates:
        mark = "PASS" if passed else "FAIL"
        print(f"  {name:15s}  [{mark}]")

    print(f"\nReady for live:   {'YES' if result.ready_for_live else 'NO'}")

    if result.failures:
        print("\n--- FAILURES ---")
        for f in result.failures:
            print(f"  • {f}")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Decifer IC Validation Gate")
    parser.add_argument("--save", action="store_true",
                        help="Persist result to data/ic_validation_result.json")
    args = parser.parse_args()

    if args.save:
        result = validate_and_persist()
        print(f"Result saved to: {_DEFAULT_VALIDATION_OUT}")
    else:
        result = check_live_readiness()

    _print_report(result)
    sys.exit(0 if result.ready_for_live else 1)
