"""
tests/test_freshness_checks.py — Tests for freshness_checks.py utility module.

Covers:
  - Intelligence file freshness: fresh, stale, missing, no timestamp
  - committed_universe freshness: fresh, warn, stale, missing
  - ic_weights freshness: fresh, stale, missing
  - Edge cases: unparseable timestamps, ImportError safety
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from freshness_checks import (
    check_committed_universe_freshness,
    check_ic_weights_freshness,
    check_intelligence_freshness,
    _age_hours,
    _parse_iso,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _write_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(UTC)


# ── _parse_iso ────────────────────────────────────────────────────────────────

def test_parse_iso_valid():
    dt = _parse_iso("2026-05-12T12:45:00Z")
    assert dt is not None
    assert dt.year == 2026


def test_parse_iso_invalid():
    assert _parse_iso("not-a-timestamp") is None
    assert _parse_iso("") is None


# ── _age_hours ────────────────────────────────────────────────────────────────

def test_age_hours_recent():
    one_hour_ago = _now() - timedelta(hours=1)
    age = _age_hours(_ts(one_hour_ago))
    assert age is not None
    assert 0.9 < age < 1.1


def test_age_hours_unparseable():
    assert _age_hours("garbage") is None


# ── check_intelligence_freshness ─────────────────────────────────────────────

class TestIntelligenceFreshness:

    def test_all_fresh(self, tmp_path):
        files = []
        for name in ["ctx.json", "theme.json", "thesis.json"]:
            p = str(tmp_path / name)
            _write_json(p, {"generated_at": _ts(_now() - timedelta(hours=1))})
            files.append(p)

        result = check_intelligence_freshness(files, max_age_hours=25.0)
        assert result["ok"] is True
        assert result["stale"] == []
        assert result["missing"] == []
        assert result["no_ts"] == []

    def test_stale_file_fails_closed(self, tmp_path):
        old_ts = _ts(_now() - timedelta(hours=30))
        fresh_ts = _ts(_now() - timedelta(hours=1))
        files = []
        for i, ts in enumerate([fresh_ts, old_ts, fresh_ts]):
            p = str(tmp_path / f"file{i}.json")
            _write_json(p, {"generated_at": ts})
            files.append(p)

        result = check_intelligence_freshness(files, max_age_hours=25.0)
        assert result["ok"] is False
        assert len(result["stale"]) == 1
        assert result["stale"][0] == files[1]

    def test_missing_file_fails_closed(self, tmp_path):
        existing = str(tmp_path / "ctx.json")
        _write_json(existing, {"generated_at": _ts(_now())})
        missing = str(tmp_path / "does_not_exist.json")

        result = check_intelligence_freshness([existing, missing], max_age_hours=25.0)
        assert result["ok"] is False
        assert missing in result["missing"]

    def test_no_timestamp_field_fails_closed(self, tmp_path):
        """File exists but has no generated_at — must not assume freshness."""
        p = str(tmp_path / "no_ts.json")
        _write_json(p, {"some_other_field": "value"})

        result = check_intelligence_freshness([p], max_age_hours=25.0)
        assert result["ok"] is False
        assert p in result["no_ts"]

    def test_all_missing(self, tmp_path):
        files = [str(tmp_path / f"missing{i}.json") for i in range(3)]
        result = check_intelligence_freshness(files, max_age_hours=25.0)
        assert result["ok"] is False
        assert len(result["missing"]) == 3

    def test_custom_max_age(self, tmp_path):
        p = str(tmp_path / "file.json")
        _write_json(p, {"generated_at": _ts(_now() - timedelta(hours=2))})

        # With 1h max — stale
        result = check_intelligence_freshness([p], max_age_hours=1.0)
        assert result["ok"] is False

        # With 3h max — fresh
        result = check_intelligence_freshness([p], max_age_hours=3.0)
        assert result["ok"] is True

    def test_default_paths_not_found_returns_failure(self):
        """Default paths don't exist in test env — must return ok=False, not raise."""
        old_cwd = os.getcwd()
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                os.chdir(td)
                result = check_intelligence_freshness()
                assert result["ok"] is False
        finally:
            os.chdir(old_cwd)


# ── check_committed_universe_freshness ────────────────────────────────────────

class TestCommittedUniverseFreshness:

    def test_fresh(self, tmp_path):
        p = str(tmp_path / "committed_universe.json")
        _write_json(p, {"refreshed_at": _ts(_now() - timedelta(days=1))})

        result = check_committed_universe_freshness(p, max_age_days=9.0, warn_age_days=7.0)
        assert result["ok"] is True
        assert result["warn"] is False
        assert result["status"] == "fresh"

    def test_warn_zone(self, tmp_path):
        p = str(tmp_path / "committed_universe.json")
        _write_json(p, {"refreshed_at": _ts(_now() - timedelta(days=8))})

        result = check_committed_universe_freshness(p, max_age_days=9.0, warn_age_days=7.0)
        assert result["ok"] is True
        assert result["warn"] is True
        assert result["status"] == "warn"

    def test_stale(self, tmp_path):
        p = str(tmp_path / "committed_universe.json")
        _write_json(p, {"refreshed_at": _ts(_now() - timedelta(days=10))})

        result = check_committed_universe_freshness(p, max_age_days=9.0, warn_age_days=7.0)
        assert result["ok"] is False
        assert result["status"] == "stale"

    def test_missing_file(self, tmp_path):
        p = str(tmp_path / "not_there.json")
        result = check_committed_universe_freshness(p)
        assert result["ok"] is False
        assert result["status"] == "missing"

    def test_no_timestamp_field(self, tmp_path):
        p = str(tmp_path / "committed_universe.json")
        _write_json(p, {"symbols": ["AAPL"]})  # no refreshed_at

        result = check_committed_universe_freshness(p)
        assert result["ok"] is False
        assert result["status"] == "no_timestamp"

    def test_age_days_reported(self, tmp_path):
        p = str(tmp_path / "committed_universe.json")
        _write_json(p, {"refreshed_at": _ts(_now() - timedelta(days=3))})

        result = check_committed_universe_freshness(p, max_age_days=9.0, warn_age_days=7.0)
        assert result["age_days"] is not None
        assert 2.9 < result["age_days"] < 3.1


# ── check_ic_weights_freshness ────────────────────────────────────────────────

class TestICWeightsFreshness:

    def test_fresh(self, tmp_path):
        p = str(tmp_path / "ic_weights.json")
        _write_json(p, {"updated": _ts(_now() - timedelta(days=5))})

        result = check_ic_weights_freshness(p, warn_age_days=14.0)
        assert result["ok"] is True
        assert result["status"] == "fresh"

    def test_warn_stale(self, tmp_path):
        p = str(tmp_path / "ic_weights.json")
        _write_json(p, {"updated": _ts(_now() - timedelta(days=15))})

        result = check_ic_weights_freshness(p, warn_age_days=14.0)
        assert result["ok"] is False
        assert result["status"] == "warn"

    def test_missing(self, tmp_path):
        p = str(tmp_path / "not_there.json")
        result = check_ic_weights_freshness(p)
        assert result["ok"] is False
        assert result["status"] == "missing"

    def test_no_updated_field(self, tmp_path):
        p = str(tmp_path / "ic_weights.json")
        _write_json(p, {"raw_ic": {"trend": 0.1}})

        result = check_ic_weights_freshness(p)
        assert result["ok"] is False
        assert result["status"] == "no_timestamp"

    def test_corrupt_json(self, tmp_path):
        p = str(tmp_path / "ic_weights.json")
        with open(p, "w") as f:
            f.write("{not valid json")

        result = check_ic_weights_freshness(p)
        assert result["ok"] is False

    def test_age_days_reported(self, tmp_path):
        p = str(tmp_path / "ic_weights.json")
        _write_json(p, {"updated": _ts(_now() - timedelta(days=7))})

        result = check_ic_weights_freshness(p, warn_age_days=14.0)
        assert result["age_days"] is not None
        assert 6.9 < result["age_days"] < 7.1


# ── Control plane status: handles missing files gracefully ───────────────────

class TestControlPlaneStatusGraceful:
    """Verify control_plane_status.py handles missing files without raising."""

    def test_build_report_all_missing(self, tmp_path, monkeypatch):
        import scripts.control_plane_status as cps

        missing = str(tmp_path / "missing.json")
        monkeypatch.setattr(cps, "_MANIFEST_PATH", missing)
        monkeypatch.setattr(cps, "_COMMITTED_UNIVERSE_PATH", missing)
        monkeypatch.setattr(cps, "_IC_WEIGHTS_PATH", missing)
        monkeypatch.setattr(cps, "_INTELLIGENCE_FILES", [missing, missing, missing])
        monkeypatch.setattr(cps, "_HEARTBEAT_FILES", {})
        monkeypatch.setattr(cps, "_LAUNCHD_PLISTS", [])

        # Must not raise
        report = cps.build_report()
        assert "overall" in report
        assert "sections" in report
        assert report["overall"] in ("OK", "WARN", "CRITICAL")

    def test_print_report_does_not_raise(self, tmp_path, monkeypatch, capsys):
        import scripts.control_plane_status as cps

        missing = str(tmp_path / "missing.json")
        monkeypatch.setattr(cps, "_MANIFEST_PATH", missing)
        monkeypatch.setattr(cps, "_COMMITTED_UNIVERSE_PATH", missing)
        monkeypatch.setattr(cps, "_IC_WEIGHTS_PATH", missing)
        monkeypatch.setattr(cps, "_INTELLIGENCE_FILES", [missing])
        monkeypatch.setattr(cps, "_HEARTBEAT_FILES", {})
        monkeypatch.setattr(cps, "_LAUNCHD_PLISTS", [])

        report = cps.build_report()
        cps.print_report(report)  # Must not raise
        captured = capsys.readouterr()
        assert "DECIFER CONTROL-PLANE STATUS" in captured.out
