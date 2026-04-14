# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic_calculator.py                          ║
# ║   Re-export shim for the `ic/` package.                      ║
# ║                                                              ║
# ║   The IC subsystem was split into ic/ (constants, math,     ║
# ║   data, core, storage, monitoring, live) in April 2026.     ║
# ║   This file preserves all public and private names at the   ║
# ║   `ic_calculator.<name>` path so every external import,     ║
# ║   test mock, and dashboard filename reference keeps working ║
# ║   unchanged.                                                 ║
# ║                                                              ║
# ║   DO NOT add new logic here — extend the relevant submodule ║
# ║   in ic/ instead.  If you add a new public name, re-export  ║
# ║   it from this file.                                         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

# Preserve standard-library bindings at module level so existing references
# like `ic_calculator.datetime`, `ic_calculator.json`, etc. continue to resolve.
import json
import logging
import os
from datetime import UTC, datetime, timedelta

import numpy as np

# Shared logger — same name as before the split, so log consumers are unaffected.
log = logging.getLogger("decifer.ic_calculator")

# ── Explicit re-exports (NOT `import *` — that would drop underscore names) ────

from ic.constants import (
    DIMENSIONS,
    EQUAL_WEIGHTS,
    IC_HISTORY_FILE,
    IC_LIVE_FILE,
    IC_LIVE_HISTORY_FILE,
    IC_WEIGHTS_FILE,
    LIVE_IC_MILESTONE,
    MIN_VALID,
    ROLLING_WINDOW,
    SIGNALS_LOG_FILE,
    _BASE,
    _CORE_DIMENSIONS,
    _LIVE_IC_REPORT_FILE,
    _N,
    _TRADES_FILE,
    _ic_cfg,
)
from ic.core import compute_rolling_ic, normalize_ic_weights
from ic.data import _fetch_forward_returns_batch, _load_signal_records
from ic.live import (
    _write_live_ic_report,
    compare_live_vs_historical_ic,
    compute_live_trade_ic,
    get_live_ic_progress,
    update_live_ic,
)
from ic.math import _spearman, _zscore_array
from ic.monitoring import (
    _check_ic_auto_disable,
    check_ic_divergence,
    get_short_quality_score,
    get_system_ic_health,
)
from ic.storage import (
    get_current_weights,
    get_ic_weight_history,
    update_ic_weights,
)
