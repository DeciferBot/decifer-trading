# tests/test_worker_cli.py
# Classification: test only
#
# Validates standalone worker behaviour for universe_committed and universe_promoter.
#
# Spec coverage (per standalone_universe_workers task spec):
#   1.  committed worker CLI importable without bot.py
#   2.  promoter worker CLI importable without bot.py
#   3.  committed worker run-once path exists and works
#   4.  promoter worker run-once path exists and works
#   5.  controlled failure exits non-zero (exit 1)
#   6.  evidence JSONL written on success
#   7.  evidence JSONL written on controlled failure
#   8.  no order module imported by standalone workers
#   9.  no risk module imported by standalone workers
#   10. no broker/execution module imported by standalone workers
#   11. worker commands do not trigger live trading
#       (verified by checking banned modules + no IBKR import in isolation check)
#
# No Alpaca API calls — all I/O is mocked via unittest.mock.patch.
# Import isolation tests run in subprocess to avoid test-session contamination.

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Banned module set — these must never be imported by universe workers.
# Checked via subprocess so the test session's own imports don't pollute the
# result (other test files may import risk, orders_core, etc. before these
# tests run, inflating sys.modules in the shared interpreter).
# ---------------------------------------------------------------------------

_BANNED_PREFIXES = (
    "bot_trading",
    "bot_ibkr",
    "bot.",
    "orders_core",
    "orders_options",
    "orders_state",
    "orders_guards",
    "risk",
    "risk_gates",
    "apex_orchestrator",
    "market_intelligence",
    "guardrails",
    "entry_gate",
    "execution_agent",
    "smart_execution",
    "ibkr",
)

# Absolute path to the repo root (parent of tests/) — needed for subprocess cwd.
import os as _os
_REPO_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))

_CHECK_SCRIPT = """\
import sys, json
BANNED = {banned!r}
import {module}
found = [m for m in sys.modules
         if any(m == p or m.startswith(p + ".") for p in BANNED)]
print(json.dumps(found))
"""


# ---------------------------------------------------------------------------
# Fixtures: minimal mock payloads
# ---------------------------------------------------------------------------

_MOCK_ASSETS = [
    {"symbol": "AAPL", "exchange": "NASDAQ", "fractionable": True, "shortable": True},
    {"symbol": "NVDA", "exchange": "NASDAQ", "fractionable": True, "shortable": True},
    {"symbol": "MSFT", "exchange": "NASDAQ", "fractionable": True, "shortable": True},
]

_MOCK_SNAPS = {
    "AAPL": {"prior_close": 200.0, "prev_volume": 50_000_000, "price": 200.0,
             "gap_pct": 0.02, "minute_volume": 10_000},
    "NVDA": {"prior_close": 800.0, "prev_volume": 30_000_000, "price": 800.0,
             "gap_pct": 0.03, "minute_volume": 8_000},
    "MSFT": {"prior_close": 400.0, "prev_volume": 20_000_000, "price": 400.0,
             "gap_pct": 0.01, "minute_volume": 3_000},
}

_MOCK_COMMITTED = ["AAPL", "NVDA", "MSFT"]


# ===========================================================================
# A. Import isolation — workers must not drag in execution modules.
# Each test runs in a fresh subprocess to avoid test-session contamination.
# ===========================================================================


def _check_imports(module_name: str) -> list[str]:
    """Return list of banned modules pulled in by importing module_name."""
    import subprocess
    script = _CHECK_SCRIPT.format(
        banned=list(_BANNED_PREFIXES),
        module=module_name,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Subprocess failed when importing {module_name}:\n{result.stderr}"
    )
    import json as _json
    return _json.loads(result.stdout.strip())


def test_universe_committed_does_not_import_bot_modules():
    """Importing universe_committed in a fresh interpreter must not pull in execution modules."""
    banned = _check_imports("universe_committed")
    assert not banned, (
        f"universe_committed imported banned modules: {banned}. "
        "Universe workers must never import execution logic."
    )


def test_universe_promoter_does_not_import_bot_modules():
    """Importing universe_promoter in a fresh interpreter must not pull in execution modules."""
    banned = _check_imports("universe_promoter")
    assert not banned, (
        f"universe_promoter imported banned modules: {banned}. "
        "Universe workers must never import execution logic."
    )


# ===========================================================================
# B. CLI smoke — _main() exits 0 on success
# ===========================================================================


def test_committed_main_returns_zero_on_success(tmp_path, monkeypatch):
    """_main() should return 0 when refresh_committed_universe succeeds."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
         patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
        code = _main(["--run-once"])

    assert code == 0, f"Expected exit 0, got {code}"


def test_promoter_main_returns_zero_on_success(tmp_path, monkeypatch):
    """_main() should return 0 when run_promoter succeeds."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=_MOCK_COMMITTED), \
         patch("universe_promoter.fetch_snapshots_batched", return_value=_MOCK_SNAPS), \
         patch("universe_promoter._catalyst_score_for", return_value=0.0):
        code = _main(["--run-once"])

    assert code == 0, f"Expected exit 0, got {code}"


# ===========================================================================
# C. Failure exit codes — _main() returns 1 on controlled failure
# ===========================================================================


def test_committed_main_returns_one_when_alpaca_returns_no_assets(tmp_path, monkeypatch):
    """Empty asset list from Alpaca → _main() must return 1 (not raise)."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=[]):
        code = _main([])

    assert code == 1, f"Expected exit 1 on empty Alpaca response, got {code}"


def test_committed_main_returns_one_on_exception(tmp_path, monkeypatch):
    """Exception inside refresh_committed_universe → _main() returns 1."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities",
               side_effect=RuntimeError("simulated Alpaca connection error")):
        code = _main([])

    assert code == 1


def test_promoter_main_returns_one_when_committed_universe_empty(tmp_path, monkeypatch):
    """Empty committed universe → run_promoter returns [] → _main() returns 1."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=[]):
        code = _main([])

    assert code == 1


def test_promoter_main_returns_one_on_exception(tmp_path, monkeypatch):
    """Exception inside run_promoter → _main() returns 1."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe",
               side_effect=ConnectionError("simulated API failure")):
        code = _main([])

    assert code == 1


# ===========================================================================
# D. Heartbeat evidence files — written on both success and failure
# ===========================================================================


def test_committed_heartbeat_written_on_success(tmp_path, monkeypatch):
    """Successful run must write data/heartbeats/universe_committed_worker.json."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
         patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
        _main([])

    hb_path = tmp_path / "data" / "heartbeats" / "universe_committed_worker.json"
    assert hb_path.exists(), "Heartbeat file not written on success"
    hb = json.loads(hb_path.read_text())
    assert hb["status"] == "success"
    assert hb["worker"] == "universe_committed_worker"
    assert hb["count"] > 0
    assert hb["live_output_changed"] is False
    assert hb["broker_called"] is False
    assert hb["order_placed"] is False
    assert hb["last_success_at"] is not None


def test_committed_heartbeat_written_on_failure(tmp_path, monkeypatch):
    """Failed run must still write heartbeat with status='fail'."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=[]):
        _main([])

    hb_path = tmp_path / "data" / "heartbeats" / "universe_committed_worker.json"
    assert hb_path.exists(), "Heartbeat file not written on failure"
    hb = json.loads(hb_path.read_text())
    assert hb["status"] == "fail"
    assert hb["last_success_at"] is None
    assert hb["live_output_changed"] is False
    assert hb["order_placed"] is False


def test_promoter_heartbeat_written_on_success(tmp_path, monkeypatch):
    """Successful promoter run must write data/heartbeats/universe_promoter_worker.json."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=_MOCK_COMMITTED), \
         patch("universe_promoter.fetch_snapshots_batched", return_value=_MOCK_SNAPS), \
         patch("universe_promoter._catalyst_score_for", return_value=0.0):
        _main([])

    hb_path = tmp_path / "data" / "heartbeats" / "universe_promoter_worker.json"
    assert hb_path.exists(), "Heartbeat file not written on success"
    hb = json.loads(hb_path.read_text())
    assert hb["status"] == "success"
    assert hb["worker"] == "universe_promoter_worker"
    assert hb["count"] > 0
    assert hb["live_output_changed"] is False
    assert hb["order_placed"] is False
    assert hb["last_success_at"] is not None


def test_promoter_heartbeat_written_on_failure(tmp_path, monkeypatch):
    """Failed promoter run must still write heartbeat with status='fail'."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=[]):
        _main([])

    hb_path = tmp_path / "data" / "heartbeats" / "universe_promoter_worker.json"
    assert hb_path.exists()
    hb = json.loads(hb_path.read_text())
    assert hb["status"] == "fail"
    assert hb["last_success_at"] is None
    assert hb["order_placed"] is False


# ===========================================================================
# E. Idempotency — running twice is safe
# ===========================================================================


def test_committed_is_idempotent(tmp_path, monkeypatch):
    """Running refresh twice overwrites the output file cleanly."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    for _ in range(2):
        with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
             patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
            code = _main([])
        assert code == 0

    out = tmp_path / "data" / "committed_universe.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["count"] == len(_MOCK_ASSETS)


def test_promoter_is_idempotent(tmp_path, monkeypatch):
    """Running promoter twice overwrites daily_promoted.json cleanly."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    for _ in range(2):
        with patch("universe_promoter.load_committed_universe", return_value=_MOCK_COMMITTED), \
             patch("universe_promoter.fetch_snapshots_batched", return_value=_MOCK_SNAPS), \
             patch("universe_promoter._catalyst_score_for", return_value=0.0):
            code = _main([])
        assert code == 0

    out = tmp_path / "data" / "daily_promoted.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["count"] == len(_MOCK_COMMITTED)


# ===========================================================================
# F. Output artifact is written
# ===========================================================================


def test_committed_writes_output_artifact(tmp_path, monkeypatch):
    """data/committed_universe.json must exist after a successful run."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
         patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
        _main([])

    out = tmp_path / "data" / "committed_universe.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "refreshed_at" in payload
    assert payload["count"] == len(_MOCK_ASSETS)


def test_promoter_writes_output_artifact(tmp_path, monkeypatch):
    """data/daily_promoted.json must exist after a successful promoter run."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=_MOCK_COMMITTED), \
         patch("universe_promoter.fetch_snapshots_batched", return_value=_MOCK_SNAPS), \
         patch("universe_promoter._catalyst_score_for", return_value=0.0):
        _main([])

    out = tmp_path / "data" / "daily_promoted.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "promoted_at" in payload
    assert payload["count"] == len(_MOCK_COMMITTED)


# ===========================================================================
# G. Heartbeat safety flags are always correct
# ===========================================================================


def test_heartbeat_safety_flags_are_always_false(tmp_path, monkeypatch):
    """live_output_changed, broker_called, order_placed must always be False in heartbeat."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    # Test on success path
    with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
         patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
        _main([])

    hb_path = tmp_path / "data" / "heartbeats" / "universe_committed_worker.json"
    hb = json.loads(hb_path.read_text())
    assert hb["live_output_changed"] is False
    assert hb["broker_called"] is False
    assert hb["order_placed"] is False

    # Test on failure path (overwrite heartbeat)
    with patch("universe_committed.get_all_tradable_equities", return_value=[]):
        _main([])

    hb = json.loads(hb_path.read_text())
    assert hb["live_output_changed"] is False
    assert hb["broker_called"] is False
    assert hb["order_placed"] is False


# ===========================================================================
# H. JSONL evidence file — written on success AND failure (spec items 6 + 7)
# ===========================================================================

_EVIDENCE_PATH_REL = "data/runtime/universe_worker_evidence.jsonl"


def _load_evidence_records(tmp_path) -> list[dict]:
    p = tmp_path / _EVIDENCE_PATH_REL
    if not p.exists():
        return []
    records = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def test_committed_evidence_jsonl_written_on_success(tmp_path, monkeypatch):
    """Successful committed run must append a record to the evidence JSONL."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
         patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
        code = _main([])

    assert code == 0
    records = _load_evidence_records(tmp_path)
    assert records, "Evidence JSONL not written on success"
    latest = records[-1]
    assert latest["worker_name"] == "universe_committed_worker"
    assert latest["success"] is True
    assert latest["failure_reason"] is None
    assert latest["output_artifact_path"] == "data/committed_universe.json"
    assert latest["run_mode"] == "run_once"
    assert latest["source"] == "standalone_cli"
    assert latest["live_output_changed"] is False
    assert latest["broker_called"] is False
    assert latest["order_placed"] is False


def test_committed_evidence_jsonl_written_on_failure(tmp_path, monkeypatch):
    """Failed committed run must append a failure record to the evidence JSONL."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=[]):
        code = _main([])

    assert code == 1
    records = _load_evidence_records(tmp_path)
    assert records, "Evidence JSONL not written on failure"
    latest = records[-1]
    assert latest["worker_name"] == "universe_committed_worker"
    assert latest["success"] is False
    assert latest["failure_reason"] is not None
    assert latest["live_output_changed"] is False
    assert latest["order_placed"] is False


def test_promoter_evidence_jsonl_written_on_success(tmp_path, monkeypatch):
    """Successful promoter run must append a record to the evidence JSONL."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=_MOCK_COMMITTED), \
         patch("universe_promoter.fetch_snapshots_batched", return_value=_MOCK_SNAPS), \
         patch("universe_promoter._catalyst_score_for", return_value=0.0):
        code = _main([])

    assert code == 0
    records = _load_evidence_records(tmp_path)
    assert records, "Evidence JSONL not written on success"
    latest = records[-1]
    assert latest["worker_name"] == "universe_promoter_worker"
    assert latest["success"] is True
    assert latest["failure_reason"] is None
    assert latest["output_artifact_path"] == "data/daily_promoted.json"
    assert latest["live_output_changed"] is False
    assert latest["order_placed"] is False


def test_promoter_evidence_jsonl_written_on_failure(tmp_path, monkeypatch):
    """Failed promoter run must append a failure record to the evidence JSONL."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from universe_promoter import _main

    with patch("universe_promoter.load_committed_universe", return_value=[]):
        code = _main([])

    assert code == 1
    records = _load_evidence_records(tmp_path)
    assert records, "Evidence JSONL not written on failure"
    latest = records[-1]
    assert latest["worker_name"] == "universe_promoter_worker"
    assert latest["success"] is False
    assert latest["failure_reason"] is not None
    assert latest["order_placed"] is False


def test_evidence_jsonl_is_append_only(tmp_path, monkeypatch):
    """Multiple runs must accumulate records in JSONL — not overwrite."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    # Run twice — evidence should have 2 records
    for _ in range(2):
        with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
             patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
            _main([])

    records = _load_evidence_records(tmp_path)
    assert len(records) >= 2, f"Expected ≥2 records for 2 runs, got {len(records)}"


def test_evidence_record_has_required_schema(tmp_path, monkeypatch):
    """Every evidence record must contain all required spec fields."""
    monkeypatch.chdir(tmp_path)

    from universe_committed import _main

    with patch("universe_committed.get_all_tradable_equities", return_value=_MOCK_ASSETS), \
         patch("universe_committed.fetch_snapshots_batched", return_value=_MOCK_SNAPS):
        _main([])

    records = _load_evidence_records(tmp_path)
    assert records
    r = records[-1]

    # All fields required by spec
    required_fields = [
        "worker_name", "started_at", "finished_at", "duration_seconds",
        "success", "failure_reason", "output_artifact_path",
        "output_artifact_exists", "output_artifact_mtime",
        "output_artifact_age_seconds", "run_mode", "git_branch", "source",
        "live_output_changed", "broker_called", "order_placed",
    ]
    for field in required_fields:
        assert field in r, f"Evidence record missing required field: {field!r}"


def test_evidence_worker_evidence_module_no_banned_imports():
    """worker_evidence.py itself must not import any banned execution module."""
    banned = _check_imports("worker_evidence")
    assert not banned, (
        f"worker_evidence imported banned modules: {banned}. "
        "Evidence module must be fully isolated from execution logic."
    )
