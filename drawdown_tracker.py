# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  drawdown_tracker.py                        ║
# ║   Re-export of HWM / drawdown functions from risk.py         ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Convenience re-export for callers that prefer to import from drawdown_tracker.

The drawdown functions and state remain in risk.py because test files that
use `sys.modules.pop("risk"); import risk` create distinct risk module objects.
Functions defined here (via the rebinding pattern) would operate on a different
risk object than the one test assertions check — causing spurious failures.

Architecture note: if the test infrastructure is ever consolidated so that
risk is only imported once per session, these functions can be moved here.
"""

from risk import (
    HWM_STATE_FILE,
    load_hwm_state,
    save_hwm_state,
    update_equity_high_water_mark,
    check_drawdown,
    reset_drawdown_state,
    init_equity_high_water_mark_from_history,
    get_drawdown_scalar,
)

__all__ = [
    "HWM_STATE_FILE",
    "load_hwm_state",
    "save_hwm_state",
    "update_equity_high_water_mark",
    "check_drawdown",
    "reset_drawdown_state",
    "init_equity_high_water_mark_from_history",
    "get_drawdown_scalar",
]
