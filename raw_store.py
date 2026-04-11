# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  raw_store.py                               ║
# ║   Immutable OHLCV storage layer.                             ║
# ║                                                              ║
# ║   Contract:                                                  ║
# ║   • validate() is a hard gate — raises DataQualityError      ║
# ║   • write()    is append-only, keep="first" (canonical)      ║
# ║   • read()     raises FileNotFoundError if not collected yet  ║
# ║   • No features are stored here — OHLCV only                 ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("decifer.raw_store")

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "data" / "raw"
RAW_INTRADAY_DIR = RAW_DIR / "intraday"
RAW_DAILY_DIR = RAW_DIR / "daily"

REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}

_TIMEFRAME_DIRS = {
    "5m": RAW_INTRADAY_DIR,
    "1d": RAW_DAILY_DIR,
}
_TIMEFRAME_SUFFIX = {
    "5m": "_5m",
    "1d": "_1d",
}
_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


class DataQualityError(Exception):
    """Raised when incoming OHLCV data fails the validation gate."""


def ensure_dirs():
    """Create raw storage directories if they don't exist."""
    RAW_INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DAILY_DIR.mkdir(parents=True, exist_ok=True)


def _path(symbol: str, timeframe: str) -> Path:
    d = _TIMEFRAME_DIRS.get(timeframe)
    if d is None:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Use '5m' or '1d'.")
    return d / f"{symbol}{_TIMEFRAME_SUFFIX[timeframe]}.parquet"


def validate(df: pd.DataFrame, symbol: str = "") -> None:
    """
    Hard validation gate. Raises DataQualityError on schema or range violations.

    Zero-volume bars are warned but NOT rejected — they are valid for halted
    or thinly traded stocks and pre-market sessions.
    """
    label = f"[{symbol}] " if symbol else ""

    if df is None or df.empty:
        raise DataQualityError(f"{label}DataFrame is None or empty")

    # Required columns
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise DataQualityError(f"{label}Missing required columns: {missing}")

    # High >= Low
    bad_hl = (df["high"] < df["low"]).sum()
    if bad_hl > 0:
        raise DataQualityError(f"{label}{bad_hl} bars where high < low")

    # Close > 0
    bad_close = (df["close"] <= 0).sum()
    if bad_close > 0:
        raise DataQualityError(f"{label}{bad_close} bars with close <= 0")

    # No duplicate index in incoming data
    dupes = df.index.duplicated().sum()
    if dupes > 0:
        raise DataQualityError(
            f"{label}{dupes} duplicate index entries in incoming data — "
            "deduplicate before calling write()"
        )

    # Zero-volume: warn but allow
    zero_vol = (df["volume"] == 0).sum()
    if zero_vol > 0:
        log.warning(
            f"{label}{zero_vol}/{len(df)} bars have zero volume — "
            "stored as-is (halted/pre-market bars are valid)"
        )


def write(symbol: str, timeframe: str, df: pd.DataFrame) -> Path:
    """
    Validate then append df to the raw store for this symbol/timeframe.

    - Raises DataQualityError if validation fails.
    - Raises on any IO or concat failure — no silent exception swallowing.
    - keep="first": the first fetch is canonical. When incoming data has
      different values for an existing timestamp, the stored value is kept
      and a WARNING is logged.

    Returns the path written to.
    """
    ensure_dirs()
    validate(df, symbol)

    # Store OHLCV only — no features in raw store
    store_cols = [c for c in _OHLCV_COLS if c in df.columns]
    incoming = df[store_cols].copy()

    path = _path(symbol, timeframe)

    if path.exists():
        existing = pd.read_parquet(path)  # intentional: raises on corrupt file

        # Detect value conflicts before dedup
        overlap = incoming.index.intersection(existing.index)
        if len(overlap) > 0:
            shared_cols = [c for c in store_cols if c in existing.columns]
            ex_slice = existing.loc[overlap, shared_cols].sort_index().round(4)
            in_slice = incoming.loc[overlap, shared_cols].sort_index().round(4)
            conflicts = (ex_slice.values != in_slice.values).any(axis=1)
            n_conflicts = int(conflicts.sum())
            if n_conflicts > 0:
                log.warning(
                    f"[{symbol}] {n_conflicts} overlapping bar(s) have different values "
                    f"in incoming data — keeping stored values (first fetch is canonical)"
                )

        combined = pd.concat([existing, incoming])
        combined = combined[~combined.index.duplicated(keep="first")]
        combined = combined.sort_index()
    else:
        combined = incoming.sort_index()

    combined.to_parquet(path)
    log.debug(f"[{symbol}] raw/{timeframe}: {len(combined):,} bars → {path.name}")
    return path


def read(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Read raw OHLCV for a symbol/timeframe.
    Raises FileNotFoundError if the symbol has not been collected yet.
    """
    path = _path(symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No raw data for {symbol}/{timeframe}. Run collect_all() first."
        )
    return pd.read_parquet(path)
