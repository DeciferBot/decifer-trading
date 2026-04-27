"""
tests/test_apex_shadow_report.py — Phase 7C.2 coverage for the shadow metrics
roll-up script (scripts/apex_shadow_report.py).

Targeted unit tests only — no runtime surfaces. Constructs synthetic shadow
and divergence JSONL files in a tmp_path, runs the aggregator, and asserts
the key report fields match.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make scripts/ importable.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

import apex_shadow_report as rpt  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _shadow_entry(
    *,
    ts: str = "2026-04-24T12:00:00+00:00",
    trigger_type: str = "SCAN_CYCLE",
    would: int = 1,
    rejected: int = 0,
    latency_ms: int | None = 1200,
    error: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    meta: dict = {
        "latency_ms": latency_ms,
        "attempts": 1,
        "input_tokens": 2000,
        "output_tokens": 400,
        "cache_read_tokens": 1500,
        "cache_creation_tokens": 0,
        "model": model,
    }
    if error is not None:
        meta["error"] = error
    return {
        "ts": ts,
        "trigger_type": trigger_type,
        "trigger_context": None,
        "decision": {"_meta": meta, "new_entries": [], "portfolio_actions": []},
        "would_dispatch": [{"symbol": f"S{i}"} for i in range(would)],
        "rejected": [{"symbol": f"R{i}", "reason": "AVOID"} for i in range(rejected)],
        "note": "shadow",
        "apex_meta": meta,
    }


def _divergence_entry(
    *,
    ts: str = "2026-04-24T12:00:00+00:00",
    trigger_type: str = "SCAN_CYCLE",
    events: list[dict] | None = None,
    legacy_entries: int = 0,
    apex_entries: int = 0,
) -> dict:
    return {
        "ts": ts,
        "cycle_id": "c1",
        "trigger_type": trigger_type,
        "legacy": {
            "new_entries": [{"symbol": f"L{i}"} for i in range(legacy_entries)],
            "portfolio_actions": [],
        },
        "apex": {
            "new_entries": [{"symbol": f"A{i}"} for i in range(apex_entries)],
            "portfolio_actions": [],
        },
        "events": events or [{"category": "AGREE", "severity": "LOW", "symbol": None, "detail": {}}],
    }


# ── load_jsonl ──────────────────────────────────────────────────────────────

def test_load_jsonl_missing_returns_empty(tmp_path: Path):
    assert rpt.load_jsonl(str(tmp_path / "nope.jsonl")) == []


def test_load_jsonl_skips_corrupt_lines(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    with p.open("w") as fh:
        fh.write('{"a": 1}\n')
        fh.write("not json\n")
        fh.write('{"b": 2}\n')
    out = rpt.load_jsonl(str(p))
    assert out == [{"a": 1}, {"b": 2}]


# ── filter_by_date ──────────────────────────────────────────────────────────

def test_filter_by_date_inclusive_bounds():
    recs = [
        {"ts": "2026-04-20T10:00:00+00:00"},
        {"ts": "2026-04-22T10:00:00+00:00"},
        {"ts": "2026-04-24T10:00:00+00:00"},
    ]
    got = rpt.filter_by_date(recs, since="2026-04-22", until="2026-04-24")
    assert len(got) == 2
    assert got[0]["ts"].startswith("2026-04-22")
    assert got[1]["ts"].startswith("2026-04-24")


def test_filter_by_date_no_bounds_passes_through():
    recs = [{"ts": "2026-04-20T10:00:00+00:00"}]
    assert rpt.filter_by_date(recs, since=None, until=None) == recs


# ── percentile ──────────────────────────────────────────────────────────────

def test_percentile_empty_is_none():
    assert rpt.percentile([], 50) is None


def test_percentile_nearest_rank():
    vs = [100.0, 200.0, 300.0, 400.0, 500.0]
    assert rpt.percentile(vs, 50) == 300.0
    # p95 of 5 points → index round(0.95*4)=4 → 500
    assert rpt.percentile(vs, 95) == 500.0
    assert rpt.percentile(vs, 0) == 100.0
    assert rpt.percentile(vs, 100) == 500.0


# ── aggregate_shadow ────────────────────────────────────────────────────────

def test_aggregate_shadow_counts_and_rates():
    recs = [
        _shadow_entry(latency_ms=1000, would=2, rejected=1),
        _shadow_entry(latency_ms=2000, would=1, rejected=0),
        _shadow_entry(latency_ms=3000, would=0, rejected=0, error="apex_call_error: boom"),
        _shadow_entry(latency_ms=4000, would=0, rejected=0, error="schema_validation: bad"),
    ]
    sh = rpt.aggregate_shadow(recs)

    assert sh["total_shadow_cycles"] == 4
    assert sh["apex"]["fallback_count"] == 1
    assert sh["apex"]["schema_reject_count"] == 1
    assert sh["apex"]["fallback_rate"] == 0.25
    assert sh["apex"]["schema_reject_rate"] == 0.25
    # semantic rejections: 1 rejection out of (3 would_dispatch + 1 rejected) = 4 entries
    assert sh["apex"]["semantic_rejection_count"] == 1
    assert sh["apex"]["semantic_rejection_rate"] == 0.25

    lat = sh["apex"]["latency"]
    assert lat["n"] == 4
    assert lat["p50_ms"] == 3000.0  # nearest-rank
    assert lat["p95_ms"] == 4000.0
    assert lat["max_ms"] == 4000.0


def test_aggregate_shadow_empty_input_safe():
    sh = rpt.aggregate_shadow([])
    assert sh["total_shadow_cycles"] == 0
    assert sh["apex"]["fallback_rate"] is None
    assert sh["apex"]["schema_reject_rate"] is None
    assert sh["apex"]["semantic_rejection_rate"] is None
    assert sh["apex"]["latency"]["p50_ms"] is None


# ── aggregate_divergence ────────────────────────────────────────────────────

def test_aggregate_divergence_event_counts_and_agree_rate():
    recs = [
        _divergence_entry(events=[
            {"category": "AGREE", "severity": "LOW", "symbol": None, "detail": {}},
        ]),
        _divergence_entry(events=[
            {"category": "SIZING_DRIFT", "severity": "LOW", "symbol": "AAPL", "detail": {}},
            {"category": "DIRECTION_CONFLICT", "severity": "HIGH", "symbol": "MSFT", "detail": {}},
        ], legacy_entries=2, apex_entries=1),
        _divergence_entry(events=[
            {"category": "APEX_FALLBACK", "severity": "HIGH", "symbol": None, "detail": {}},
        ]),
    ]
    dv = rpt.aggregate_divergence(recs)

    assert dv["total_divergence_records"] == 3
    assert dv["agree_cycles"] == 1
    assert dv["agree_rate_cycles"] == round(1 / 3, 4)

    cats = dv["events"]["by_category"]
    assert cats["AGREE"] == 1
    assert cats["SIZING_DRIFT"] == 1
    assert cats["DIRECTION_CONFLICT"] == 1
    assert cats["APEX_FALLBACK"] == 1

    sev = dv["events"]["by_severity"]
    assert sev["LOW"] == 2
    assert sev["HIGH"] == 2

    assert dv["entries"]["legacy_new_entries_total"] == 2
    assert dv["entries"]["apex_new_entries_total"] == 1


def test_aggregate_divergence_empty_safe():
    dv = rpt.aggregate_divergence([])
    assert dv["total_divergence_records"] == 0
    assert dv["agree_rate_cycles"] is None
    assert dv["events"]["total"] == 0


# ── End-to-end: build_report + render_text ─────────────────────────────────

def test_build_report_combines_both_sources():
    shadow = [_shadow_entry(latency_ms=1200, would=1)]
    divergence = [_divergence_entry()]
    report = rpt.build_report(shadow, divergence, since="2026-04-01", until="2026-04-30")
    assert report["shadow"]["total_shadow_cycles"] == 1
    assert report["divergence"]["total_divergence_records"] == 1
    assert report["filters"] == {"since": "2026-04-01", "until": "2026-04-30"}
    assert report["report_ts"]  # non-empty


def test_render_text_has_every_required_section():
    shadow = [_shadow_entry()]
    divergence = [_divergence_entry()]
    text = rpt.render_text(rpt.build_report(shadow, divergence))

    # Per 7C.2 spec — every listed metric must appear in the text artifact.
    assert "Apex Shadow Report" in text
    assert "total cycles" in text
    assert "fallback" in text.lower()
    assert "schema reject" in text.lower()
    assert "semantic-rejection" in text.lower()
    assert "latency p50/p95" in text
    assert "by severity" in text
    assert "by category" in text
    assert "entries per side" in text


# ── CLI smoke ───────────────────────────────────────────────────────────────

def test_main_writes_artifact(tmp_path: Path, capsys):
    sh_path = tmp_path / "shadow.jsonl"
    dv_path = tmp_path / "div.jsonl"
    out_dir = tmp_path / "reports"
    _write_jsonl(sh_path, [_shadow_entry()])
    _write_jsonl(dv_path, [_divergence_entry()])

    rc = rpt.main([
        "--shadow-log", str(sh_path),
        "--divergence-log", str(dv_path),
        "--out-dir", str(out_dir),
    ])
    assert rc == 0

    files = list(out_dir.iterdir())
    names = sorted(f.suffix for f in files)
    assert names == [".json", ".txt"]

    json_file = next(f for f in files if f.suffix == ".json")
    data = json.loads(json_file.read_text())
    assert data["shadow"]["total_shadow_cycles"] == 1
    assert data["divergence"]["total_divergence_records"] == 1


def test_main_no_write_flag(tmp_path: Path, capsys):
    sh_path = tmp_path / "shadow.jsonl"
    dv_path = tmp_path / "div.jsonl"
    _write_jsonl(sh_path, [])
    _write_jsonl(dv_path, [])
    rc = rpt.main([
        "--shadow-log", str(sh_path),
        "--divergence-log", str(dv_path),
        "--no-write",
    ])
    assert rc == 0
    # --no-write must not create any output dir
    assert not (tmp_path / "data").exists()
