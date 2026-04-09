"""
Regression tests for regime detection architecture (DECISIONS.md Action #9).

Guards against architectural incoherence caused by multiple regime detectors
running in parallel. VIX-proxy is the sole committed approach until the IC
Phase 2 gate (closed_trades >= 200) is met.
"""

import os
import sys
import importlib.util

import pytest

# test_ml_engine.py stubs out the entire `config` module at collection time
# (sys.modules.setdefault) with a minimal dict that lacks regime keys.
# Load the real config.py directly from disk to avoid that stub.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _load_real_config() -> dict:
    spec = importlib.util.spec_from_file_location(
        "_real_config_arch", os.path.join(_PROJECT_ROOT, "config.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.CONFIG  # type: ignore[attr-defined]

REAL_CONFIG = _load_real_config()

# RegimeClassifier import — must come after sys.modules is stable
sys.path.insert(0, _PROJECT_ROOT)
from ml_engine import RegimeClassifier  # noqa: E402

try:
    from sklearn.ensemble import RandomForestClassifier
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


def test_regime_detector_config_is_vix_proxy():
    """config['regime_detector'] must be 'vix_proxy' — changing it is a gate-guarded decision."""
    assert REAL_CONFIG["regime_detector"] == "vix_proxy", (
        "Regime detector was changed away from 'vix_proxy'. "
        "This requires IC Phase 2 gate review (closed_trades >= 200). "
        "See DECISIONS.md Action #9."
    )


def test_canonical_regime_states_declared_in_config():
    """config['regime_states'] must declare the full canonical state set."""
    states = REAL_CONFIG["regime_states"]
    assert isinstance(states, (tuple, list, frozenset))
    required = {"TRENDING_UP", "TRENDING_DOWN", "RELIEF_RALLY", "RANGE_BOUND", "CAPITULATION", "UNKNOWN"}
    assert required.issubset(set(states)), (
        f"Missing canonical regime states: {required - set(states)}"
    )


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
def test_ml_regime_classifier_is_production_locked():
    """RegimeClassifier.PRODUCTION_LOCKED must be True — it is not wired into the live pipeline."""
    assert RegimeClassifier.PRODUCTION_LOCKED is True, (
        "RegimeClassifier.PRODUCTION_LOCKED was set to False. "
        "Do not connect this to the production pipeline without gate review. "
        "See DECISIONS.md Action #9."
    )


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
def test_ml_regime_classifier_predict_raises_runtime_error():
    """predict_regime() must raise RuntimeError while PRODUCTION_LOCKED is True."""
    clf = RegimeClassifier()
    with pytest.raises(RuntimeError, match="production"):
        clf.predict_regime({"returns": 0.01, "volatility": 0.5, "volume_ma_ratio": 1.0})


def test_regime_threshold_covers_all_canonical_states(monkeypatch):
    """get_regime_threshold() must return a valid threshold for every canonical regime state."""
    import config as _config_mod
    from signals import get_regime_threshold
    # Ensure all keys get_regime_threshold reads are present (guards against stub pollution)
    _required = {
        "min_score_to_trade": 18,
        "regime_threshold_bear_offset": -3,
        "regime_threshold_choppy_offset": -6,
        "regime_threshold_panic": 99,
        "regime_threshold_bear_min": 15,
        "regime_threshold_choppy_min": 12,
    }
    for k, v in _required.items():
        monkeypatch.setitem(_config_mod.CONFIG, k, v)
    for state in REAL_CONFIG["regime_states"]:
        threshold = get_regime_threshold(state)
        assert isinstance(threshold, (int, float)), (
            f"get_regime_threshold('{state}') returned non-numeric: {threshold!r}"
        )
        assert threshold >= 0, (
            f"get_regime_threshold('{state}') returned negative threshold: {threshold}"
        )
