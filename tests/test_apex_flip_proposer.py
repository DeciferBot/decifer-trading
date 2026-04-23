"""
tests/test_apex_flip_proposer.py

Phase 7C.4 — flag-flip proposer / rollback tooling.

All tests exercise the audit-only proposer. No flag is ever mutated. No
config.py is ever written. Audit JSON is written only into tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import apex_flip_proposer as fp  # noqa: E402


# ── flag state reader ───────────────────────────────────────────────────────

def test_read_current_flag_state_returns_all_six_flags():
    state = fp.read_current_flag_state()
    assert set(state.keys()) == set(fp._FLAG_ACCESSOR.keys())
    # Defaults per 7C.9 invariants.
    assert state["USE_APEX_V3_SHADOW"] is False
    assert state["FINBERT_MATERIALITY_GATE_ENABLED"] is False
    assert state["TRADE_ADVISOR_ENABLED"] is True
    assert state["PM_LEGACY_OPUS_REVIEW_ENABLED"] is True
    assert state["SENTINEL_LEGACY_PIPELINE_ENABLED"] is True
    assert state["USE_LEGACY_PIPELINE"] is True


# ── argument parsing ────────────────────────────────────────────────────────

def test_parse_flag_argument_accepts_true_false_aliases():
    assert fp.parse_flag_argument("USE_APEX_V3_SHADOW=true") == ("USE_APEX_V3_SHADOW", True)
    assert fp.parse_flag_argument("USE_APEX_V3_SHADOW=FALSE") == ("USE_APEX_V3_SHADOW", False)
    assert fp.parse_flag_argument("X=1") == ("X", True)
    assert fp.parse_flag_argument("X=no") == ("X", False)


def test_parse_flag_argument_rejects_bad_inputs():
    with pytest.raises(ValueError):
        fp.parse_flag_argument("USE_APEX_V3_SHADOW")
    with pytest.raises(ValueError):
        fp.parse_flag_argument("X=maybe")


# ── expected_transition ─────────────────────────────────────────────────────

def test_expected_transition_known_and_unknown():
    assert fp.expected_transition("USE_APEX_V3_SHADOW") == (False, True)
    assert fp.expected_transition("USE_LEGACY_PIPELINE") == (True, False)
    assert fp.expected_transition("NOT_A_FLAG") is None


# ── out_of_order_warning ────────────────────────────────────────────────────

def test_out_of_order_warning_none_when_earlier_flags_flipped():
    # Flipping the 2nd flag after the 1st has been flipped → no warning.
    current = fp.read_current_flag_state()
    current["USE_APEX_V3_SHADOW"] = True
    assert fp.out_of_order_warning("FINBERT_MATERIALITY_GATE_ENABLED", True, current) is None


def test_out_of_order_warning_fires_when_earlier_flag_not_yet_flipped():
    # Defaults: USE_APEX_V3_SHADOW still False. Proposing to flip flag #3
    # forward while flag #1 has not moved → warn.
    current = fp.read_current_flag_state()
    msg = fp.out_of_order_warning("TRADE_ADVISOR_ENABLED", False, current)
    assert msg is not None and "out-of-order" in msg


def test_out_of_order_warning_skips_reverse_direction():
    # Target matches the PRE-flip value → not a forward flip → no check.
    current = fp.read_current_flag_state()
    assert fp.out_of_order_warning("USE_APEX_V3_SHADOW", False, current) is None


def test_out_of_order_warning_unknown_flag():
    assert "not in the canonical" in fp.out_of_order_warning("NOPE", True, {})


# ── evaluate_gates ──────────────────────────────────────────────────────────

def _good_report(**overrides) -> dict:
    base = {
        "shadow": {
            "total_shadow_cycles": 25,
            "apex": {
                "fallback_rate": 0.01,
                "schema_reject_rate": 0.005,
                "latency": {"p95_ms": 9_000},
            },
        },
        "divergence": {
            "events": {"by_severity": {"HIGH": 0, "MEDIUM": 1, "LOW": 3}},
            "agree_rate_cycles": 0.95,
        },
    }
    base.update(overrides)
    return base


def test_evaluate_gates_passes_on_clean_report():
    g = fp.evaluate_gates(_good_report())
    assert g["ok"] is True
    assert g["hard_blocks"] == []
    assert g["warnings"] == []


def test_evaluate_gates_blocks_on_insufficient_cycles():
    r = _good_report()
    r["shadow"]["total_shadow_cycles"] = 5
    g = fp.evaluate_gates(r)
    assert g["ok"] is False
    assert any("insufficient shadow cycles" in b for b in g["hard_blocks"])


def test_evaluate_gates_blocks_on_fallback_rate():
    r = _good_report()
    r["shadow"]["apex"]["fallback_rate"] = 0.20
    g = fp.evaluate_gates(r)
    assert g["ok"] is False
    assert any("fallback rate" in b for b in g["hard_blocks"])


def test_evaluate_gates_blocks_on_schema_reject_rate():
    r = _good_report()
    r["shadow"]["apex"]["schema_reject_rate"] = 0.10
    g = fp.evaluate_gates(r)
    assert g["ok"] is False
    assert any("schema reject rate" in b for b in g["hard_blocks"])


def test_evaluate_gates_blocks_on_p95_latency():
    r = _good_report()
    r["shadow"]["apex"]["latency"]["p95_ms"] = 45_000
    g = fp.evaluate_gates(r)
    assert g["ok"] is False
    assert any("p95 latency" in b for b in g["hard_blocks"])


def test_evaluate_gates_blocks_on_high_severity_event():
    r = _good_report()
    r["divergence"]["events"]["by_severity"]["HIGH"] = 2
    g = fp.evaluate_gates(r)
    assert g["ok"] is False
    assert any("HIGH-severity" in b for b in g["hard_blocks"])


def test_evaluate_gates_warns_on_low_agree_rate_but_does_not_block():
    r = _good_report()
    r["divergence"]["agree_rate_cycles"] = 0.80
    g = fp.evaluate_gates(r)
    assert g["ok"] is True  # still ok — soft gate only
    assert any("AGREE rate" in w for w in g["warnings"])


# ── build_proposal ──────────────────────────────────────────────────────────

def _passing_gates() -> dict:
    return fp.evaluate_gates(_good_report())


def _blocking_gates() -> dict:
    r = _good_report()
    r["shadow"]["total_shadow_cycles"] = 1
    return fp.evaluate_gates(r)


def test_build_proposal_noop_when_already_at_target():
    current = fp.read_current_flag_state()
    # TRADE_ADVISOR_ENABLED defaults True; proposing True is noop.
    p = fp.build_proposal(
        "TRADE_ADVISOR_ENABLED", True,
        current_state=current, gates=_passing_gates(),
    )
    assert p["decision"] == "noop"
    assert p["kind"] == "propose"
    assert p["hard_blocks"] == []


def test_build_proposal_allow_when_gates_pass_and_in_order():
    current = fp.read_current_flag_state()
    p = fp.build_proposal(
        "USE_APEX_V3_SHADOW", True,
        current_state=current, gates=_passing_gates(),
    )
    assert p["decision"] == "allow"
    assert p["target_value"] is True
    assert p["observed_value"] is False
    assert len(p["manual_steps"]) >= 1


def test_build_proposal_blocked_when_gates_fail():
    current = fp.read_current_flag_state()
    p = fp.build_proposal(
        "USE_APEX_V3_SHADOW", True,
        current_state=current, gates=_blocking_gates(),
    )
    assert p["decision"] == "blocked"
    assert p["hard_blocks"]


def test_build_proposal_records_out_of_order_warning():
    current = fp.read_current_flag_state()
    # Flip #3 forward while #1 still at False.
    p = fp.build_proposal(
        "TRADE_ADVISOR_ENABLED", False,
        current_state=current, gates=_passing_gates(),
    )
    assert any("out-of-order" in w for w in p["warnings"])
    # Decision is still "allow" — warnings do not block.
    assert p["decision"] == "allow"


# ── build_rollback ──────────────────────────────────────────────────────────

def test_build_rollback_inverts_target_and_is_never_blocked():
    # Simulate a prior proposal audit that targeted True.
    source = {
        "kind": "propose",
        "ts": "2026-04-24T00:00:00+00:00",
        "flag": "USE_APEX_V3_SHADOW",
        "target_value": True,
    }
    rb = fp.build_rollback(source)
    assert rb["kind"] == "rollback"
    assert rb["flag"] == "USE_APEX_V3_SHADOW"
    assert rb["source_target_value"] is True
    assert rb["target_value"] is False
    assert rb["hard_blocks"] == []
    # Default state has USE_APEX_V3_SHADOW=False; inverse=False ⇒ noop.
    assert rb["decision"] == "noop"


def test_build_rollback_allow_when_current_differs_from_inverse():
    # Prior proposal targeted False for TRADE_ADVISOR_ENABLED. Inverse=True.
    # Current state has TRADE_ADVISOR_ENABLED=True already → noop.
    # To force "allow" we simulate a source whose inverse DIFFERS from current.
    source = {"flag": "USE_APEX_V3_SHADOW", "target_value": False, "ts": "x"}
    # Inverse target = True. Current state USE_APEX_V3_SHADOW=False → allow.
    rb = fp.build_rollback(source)
    assert rb["decision"] == "allow"
    assert rb["target_value"] is True
    assert rb["hard_blocks"] == []


def test_build_rollback_rejects_source_without_flag():
    with pytest.raises(ValueError):
        fp.build_rollback({"kind": "propose"})


# ── audit writer ────────────────────────────────────────────────────────────

def test_write_audit_roundtrip(tmp_path):
    record = {"kind": "propose", "flag": "USE_APEX_V3_SHADOW", "x": 1}
    path = fp.write_audit(record, out_dir=str(tmp_path))
    p = Path(path)
    assert p.exists()
    assert p.parent == tmp_path
    assert p.name.startswith("propose_USE_APEX_V3_SHADOW_")
    assert p.suffix == ".json"
    loaded = json.loads(p.read_text())
    assert loaded["flag"] == "USE_APEX_V3_SHADOW"


# ── rendering ───────────────────────────────────────────────────────────────

def test_render_status_contains_all_flags_and_gate_observations():
    current = fp.read_current_flag_state()
    gates = _passing_gates()
    text = fp.render_status(current, gates)
    for flag in fp._FLAG_ACCESSOR:
        assert flag in text
    assert "all hard gates: OK" in text


def test_render_status_shows_blocked_hard_gates():
    current = fp.read_current_flag_state()
    text = fp.render_status(current, _blocking_gates())
    assert "BLOCKED" in text
    assert "insufficient shadow cycles" in text


def test_render_proposal_formats_decision_and_manual_steps():
    current = fp.read_current_flag_state()
    p = fp.build_proposal(
        "USE_APEX_V3_SHADOW", True,
        current_state=current, gates=_passing_gates(),
    )
    text = fp.render_proposal(p)
    assert "ALLOW" in text
    assert "config.py" in text
    assert "Manual steps" in text


# ── CLI smoke tests ─────────────────────────────────────────────────────────

def test_cli_status_smoke(tmp_path, capsys, monkeypatch):
    # Point shadow/divergence logs at non-existent files so aggregator
    # returns empty datasets deterministically.
    rc = fp.main([
        "status",
        "--shadow-log", str(tmp_path / "missing_shadow.jsonl"),
        "--divergence-log", str(tmp_path / "missing_div.jsonl"),
        "--out-dir", str(tmp_path),
        "--no-write",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Apex Flip Proposer — status" in out


def test_cli_propose_writes_audit_and_returns_blocked_on_empty_logs(tmp_path, capsys):
    rc = fp.main([
        "propose", "USE_APEX_V3_SHADOW=true",
        "--shadow-log", str(tmp_path / "missing_shadow.jsonl"),
        "--divergence-log", str(tmp_path / "missing_div.jsonl"),
        "--out-dir", str(tmp_path),
    ])
    # Empty logs ⇒ 0 cycles ⇒ hard-blocked ⇒ rc=1.
    assert rc == 1
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    # Audit file written.
    files = list(tmp_path.glob("propose_USE_APEX_V3_SHADOW_*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["decision"] == "blocked"
    assert rec["flag"] == "USE_APEX_V3_SHADOW"


def test_cli_propose_rejects_unknown_flag(tmp_path, capsys):
    rc = fp.main([
        "propose", "NOT_A_REAL_FLAG=true",
        "--shadow-log", str(tmp_path / "s.jsonl"),
        "--divergence-log", str(tmp_path / "d.jsonl"),
        "--out-dir", str(tmp_path),
        "--no-write",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown flag" in err


def test_cli_rollback_reads_prior_audit_and_writes_new(tmp_path, capsys):
    source_path = tmp_path / "propose_USE_APEX_V3_SHADOW_x.json"
    source_path.write_text(json.dumps({
        "kind": "propose",
        "ts": "2026-04-24T00:00:00+00:00",
        "flag": "USE_APEX_V3_SHADOW",
        "target_value": True,
    }))
    rc = fp.main([
        "rollback", str(source_path),
        "--out-dir", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ROLLBACK" in out
    files = list(tmp_path.glob("rollback_USE_APEX_V3_SHADOW_*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["kind"] == "rollback"
    assert rec["target_value"] is False
    assert rec["hard_blocks"] == []


def test_cli_rollback_missing_audit_file_errors(tmp_path, capsys):
    rc = fp.main([
        "rollback", str(tmp_path / "does_not_exist.json"),
        "--out-dir", str(tmp_path),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


# ── flag-invariant self-check (guard against accidental mutation) ───────────

def test_no_flag_mutated_by_running_proposer(tmp_path):
    """After exercising the full propose + rollback surface, flags are unchanged."""
    before = fp.read_current_flag_state()
    fp.main([
        "propose", "USE_APEX_V3_SHADOW=true",
        "--shadow-log", str(tmp_path / "s.jsonl"),
        "--divergence-log", str(tmp_path / "d.jsonl"),
        "--out-dir", str(tmp_path),
    ])
    source_path = tmp_path / "src.json"
    source_path.write_text(json.dumps({
        "kind": "propose", "flag": "USE_APEX_V3_SHADOW",
        "target_value": True, "ts": "x",
    }))
    fp.main([
        "rollback", str(source_path),
        "--out-dir", str(tmp_path),
    ])
    after = fp.read_current_flag_state()
    assert before == after
