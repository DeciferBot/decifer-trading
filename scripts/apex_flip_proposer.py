#!/usr/bin/env python3
"""
apex_flip_proposer.py — Phase 7C.4 audit-only flip / rollback proposer.

Purpose
───────
Provide a safe, reviewable, NON-EXECUTING tool that:
  1. Inspects the current Phase 7 flag state (via safety_overlay).
  2. Aggregates the most recent shadow artifacts (apex_shadow_log.jsonl +
     apex_divergence_log.jsonl) via scripts/apex_shadow_report.py.
  3. Evaluates the Phase 7B hard-cutover gates against that roll-up.
  4. For a requested flag flip, emits a PROPOSAL (allow / block / warn) with
     per-gate reasoning and the exact manual steps the operator must take to
     apply the change to config.py (or to roll it back).
  5. Writes an audit record to data/apex_flip_audit/<kind>_<UTCTS>.json so the
     operator review and any later rollback have a complete, time-stamped
     paper trail.

This tool NEVER mutates config.py, NEVER mutates the in-process CONFIG dict,
and NEVER flips a flag. It only reads shadow logs, reads current flag state,
evaluates gates, and writes a JSON audit record + human-readable proposal.

Why audit-only
──────────────
Per Phase 7C.4 constraint #6: runtime config reload into the live CONFIG dict
has not been verified safe/deterministic (CONFIG is a shared mutable dict read
by many modules on every cycle). Until that verification is done, the
proposer is explicitly propose-only: it computes what SHOULD happen and tells
the operator exactly what to change by hand. The operator reviews the audit
record, edits config.py's `safety_overlay` section, restarts the process, and
the new flag takes effect cleanly.

Rollback
────────
Rollback is ALWAYS allowed. A --rollback <audit_file> invocation reads a
prior proposal audit, computes the inverse flag change, and writes a
rollback audit record with the same manual-steps format. Gates are NOT
re-evaluated for rollbacks (rollback = safety action, never blocked).

Flip order (canonical)
──────────────────────
The Phase 7 master plan flipped flags in this order, one at a time, with a
shadow observation window between each. All five flags are now at their
post-cutover values. USE_APEX_V3_SHADOW and FINBERT_MATERIALITY_GATE_ENABLED
remain as live operational flags; the other three were removed at cleanup.

    1. USE_APEX_V3_SHADOW               : False → True   (observation on) ✓
    2. FINBERT_MATERIALITY_GATE_ENABLED : False → True   (news gate cutover) ✓

The proposer enforces this order: it will WARN when a flip is proposed out
of order. It does not strictly block — the operator retains authority — but
the audit record clearly records the out-of-order warning for review.

Gate thresholds (from Phase 7B)
───────────────────────────────
  fallback_rate_max       : 0.05   (hard)   ≤5% of shadow cycles may fallback
  schema_reject_rate_max  : 0.02   (hard)   ≤2% of cycles may schema-reject
  p95_latency_ms_max      : 30_000 (hard)   ≤30s p95 end-to-end
  agree_rate_min          : 0.90   (soft)   ≥90% agree is a directional benchmark
  min_shadow_cycles       : 20     (hard)   ≥20 shadow cycles observed
  no_unresolved_high      : true   (hard)   zero HIGH-severity divergences

Usage
─────
  # Show current flag state + latest gate snapshot + recommendation.
  python3 scripts/apex_flip_proposer.py status

  # Propose a specific flip. Writes an audit record; no config mutation.
  python3 scripts/apex_flip_proposer.py propose USE_APEX_V3_SHADOW=true

  # Produce a rollback proposal from a prior audit record.
  python3 scripts/apex_flip_proposer.py rollback data/apex_flip_audit/propose_<ts>.json

  # Override windowing (e.g. narrow to today's shadow data).
  python3 scripts/apex_flip_proposer.py propose ... --since 2026-04-24
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Re-use the shadow aggregator. scripts/apex_shadow_report.py is
# import-safe (no runtime module imports).
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import apex_shadow_report as _shadow  # noqa: E402


_AUDIT_DIR_DEFAULT = "data/apex_flip_audit"

# ── Canonical flip order + expected transitions ─────────────────────────────

FLIP_SEQUENCE: list[tuple[str, bool, bool]] = [
    ("USE_APEX_V3_SHADOW",               False, True),
    ("FINBERT_MATERIALITY_GATE_ENABLED", False, True),
]

_FLAG_ACCESSOR = {
    "USE_APEX_V3_SHADOW":               "should_run_apex_shadow",
    "FINBERT_MATERIALITY_GATE_ENABLED": "finbert_materiality_gate_enabled",
}


# ── Gate thresholds (Phase 7B) ──────────────────────────────────────────────

GATE_THRESHOLDS: dict[str, Any] = {
    "fallback_rate_max":      0.05,
    "schema_reject_rate_max": 0.02,
    "p95_latency_ms_max":     30_000,
    "agree_rate_min":         0.90,
    "min_shadow_cycles":      20,
    "no_unresolved_high":     True,
}


# ── Flag state ──────────────────────────────────────────────────────────────

def read_current_flag_state() -> dict[str, bool]:
    """Read every Phase 7 flag via safety_overlay. Safe, read-only."""
    import safety_overlay
    state: dict[str, bool] = {}
    for flag, accessor in _FLAG_ACCESSOR.items():
        fn = getattr(safety_overlay, accessor, None)
        state[flag] = bool(fn()) if callable(fn) else False
    return state


# ── Gate evaluation ─────────────────────────────────────────────────────────

def evaluate_gates(report: dict, thresholds: dict = GATE_THRESHOLDS) -> dict:
    """
    Apply Phase 7B gates to a shadow-report dict (from apex_shadow_report).
    Returns {"ok": bool, "hard_blocks": [...], "warnings": [...], "observations": {...}}.
    """
    sh = report.get("shadow") or {}
    dv = report.get("divergence") or {}
    apex = sh.get("apex") or {}
    lat = apex.get("latency") or {}

    hard_blocks: list[str] = []
    warnings: list[str] = []

    total_cycles = sh.get("total_shadow_cycles", 0)
    min_cycles = thresholds["min_shadow_cycles"]
    if total_cycles < min_cycles:
        hard_blocks.append(
            f"insufficient shadow cycles: {total_cycles} < {min_cycles}"
        )

    fb_rate = apex.get("fallback_rate")
    if fb_rate is not None and fb_rate > thresholds["fallback_rate_max"]:
        hard_blocks.append(
            f"fallback rate {fb_rate:.2%} > {thresholds['fallback_rate_max']:.2%}"
        )

    sr_rate = apex.get("schema_reject_rate")
    if sr_rate is not None and sr_rate > thresholds["schema_reject_rate_max"]:
        hard_blocks.append(
            f"schema reject rate {sr_rate:.2%} > {thresholds['schema_reject_rate_max']:.2%}"
        )

    p95 = lat.get("p95_ms")
    if p95 is not None and p95 > thresholds["p95_latency_ms_max"]:
        hard_blocks.append(
            f"p95 latency {p95:.0f}ms > {thresholds['p95_latency_ms_max']}ms"
        )

    # HIGH-severity divergence presence (unresolved in this window = presence).
    sev = (dv.get("events") or {}).get("by_severity") or {}
    high_count = sev.get("HIGH", 0)
    if thresholds["no_unresolved_high"] and high_count > 0:
        hard_blocks.append(
            f"{high_count} HIGH-severity divergence event(s) unresolved"
        )

    # Soft gate: AGREE rate (directional benchmark only — warn, do not block).
    agree = dv.get("agree_rate_cycles")
    if agree is not None and agree < thresholds["agree_rate_min"]:
        warnings.append(
            f"AGREE rate {agree:.2%} < benchmark {thresholds['agree_rate_min']:.2%} "
            "(directional only; not a hard block)"
        )

    return {
        "ok": not hard_blocks,
        "hard_blocks": hard_blocks,
        "warnings": warnings,
        "observations": {
            "total_shadow_cycles": total_cycles,
            "fallback_rate": fb_rate,
            "schema_reject_rate": sr_rate,
            "p95_latency_ms": p95,
            "high_severity_events": high_count,
            "agree_rate_cycles": agree,
        },
    }


# ── Proposal / rollback logic ───────────────────────────────────────────────

def parse_flag_argument(arg: str) -> tuple[str, bool]:
    """Parse 'FLAG=true' or 'FLAG=false' → (FLAG, bool). Case-insensitive value."""
    if "=" not in arg:
        raise ValueError(f"bad flip spec {arg!r}: expected FLAG=true|false")
    name, _, val = arg.partition("=")
    name = name.strip()
    val_norm = val.strip().lower()
    if val_norm not in ("true", "false", "1", "0", "yes", "no"):
        raise ValueError(f"bad flip value {val!r}: expected true/false")
    return name, val_norm in ("true", "1", "yes")


def expected_transition(flag: str) -> tuple[bool, bool] | None:
    for f, frm, to in FLIP_SEQUENCE:
        if f == flag:
            return (frm, to)
    return None


def out_of_order_warning(
    flag: str,
    target_value: bool,
    current_state: dict[str, bool],
) -> str | None:
    """
    If any earlier flag in FLIP_SEQUENCE is still in its pre-flip state while
    this flag is being flipped forward, return a warning string.
    Pure function — no I/O.
    """
    trans = expected_transition(flag)
    if trans is None:
        return f"{flag} is not in the canonical FLIP_SEQUENCE"
    _, expected_to = trans
    if target_value != expected_to:
        # Flipping backwards vs canonical direction = skip the check
        # (rollbacks handle that path explicitly).
        return None
    for earlier_flag, _, earlier_to in FLIP_SEQUENCE:
        if earlier_flag == flag:
            break
        if bool(current_state.get(earlier_flag)) != bool(earlier_to):
            return (
                f"out-of-order flip: {earlier_flag} is still at its pre-flip "
                f"value (expected {earlier_to}, observed "
                f"{current_state.get(earlier_flag)}). Flip earlier flags first."
            )
    return None


def build_proposal(
    flag: str,
    target_value: bool,
    *,
    current_state: dict[str, bool],
    gates: dict,
) -> dict:
    """Assemble a proposal record. Pure; writes nothing."""
    observed = current_state.get(flag)
    no_op = bool(observed) == bool(target_value)

    ooo = out_of_order_warning(flag, target_value, current_state)
    warnings = list(gates.get("warnings") or [])
    if ooo:
        warnings.append(ooo)

    # Decision rule:
    #   - no-op (already at target)           → "noop"
    #   - any hard gate failure               → "blocked"
    #   - otherwise                           → "allow" (with any warnings)
    if no_op:
        decision = "noop"
    elif not gates["ok"]:
        decision = "blocked"
    else:
        decision = "allow"

    manual_steps = _manual_steps(flag, target_value, kind="propose")
    return {
        "kind": "propose",
        "ts": datetime.now(UTC).isoformat(),
        "flag": flag,
        "target_value": bool(target_value),
        "observed_value": observed,
        "decision": decision,
        "hard_blocks": list(gates.get("hard_blocks") or []),
        "warnings": warnings,
        "gate_observations": gates.get("observations") or {},
        "current_flag_state": current_state,
        "manual_steps": manual_steps,
    }


def build_rollback(
    source_audit: dict,
) -> dict:
    """Given a prior proposal audit, compute its inverse. Rollback is never blocked."""
    flag = source_audit.get("flag")
    if not flag:
        raise ValueError("source audit is missing 'flag'")
    # Inverse target: if the prior proposal targeted True, rollback targets False.
    prior_target = bool(source_audit.get("target_value"))
    inverse_target = not prior_target
    # Current state is reread live — rollback always reflects reality now.
    current = read_current_flag_state()
    observed = current.get(flag)
    no_op = bool(observed) == bool(inverse_target)
    return {
        "kind": "rollback",
        "ts": datetime.now(UTC).isoformat(),
        "flag": flag,
        "source_audit_ts": source_audit.get("ts"),
        "source_target_value": prior_target,
        "target_value": inverse_target,
        "observed_value": observed,
        "decision": "noop" if no_op else "allow",
        "hard_blocks": [],  # rollback is always allowed
        "warnings": [],
        "current_flag_state": current,
        "manual_steps": _manual_steps(flag, inverse_target, kind="rollback"),
    }


def _manual_steps(flag: str, target_value: bool, *, kind: str) -> list[str]:
    value_str = "True" if target_value else "False"
    return [
        f"Open config.py and locate the CONFIG['safety_overlay'] dict.",
        f"Set CONFIG['safety_overlay']['{flag}'] = {value_str}",
        "Save config.py.",
        "Stop any running bot processes (bot.py, bot_trading, bot_sentinel).",
        "Restart the bot so the new flag value is read by safety_overlay.flag().",
        (
            f"After {'flip' if kind == 'propose' else 'rollback'}, verify:\n"
            f"    python3 -c \"from safety_overlay import "
            f"{_FLAG_ACCESSOR.get(flag, 'flag')}; "
            f"print({_FLAG_ACCESSOR.get(flag, 'flag')}())\""
        ),
        "Watch the next 10+ shadow cycles and re-run scripts/apex_shadow_report.py "
        "before any further flip.",
    ]


# ── Audit writer ────────────────────────────────────────────────────────────

def write_audit(
    record: dict,
    out_dir: str = _AUDIT_DIR_DEFAULT,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    kind = record.get("kind", "audit")
    flag = record.get("flag", "flag")
    path = os.path.join(out_dir, f"{kind}_{flag}_{stamp}.json")
    with open(path, "w") as fh:
        json.dump(record, fh, indent=2, default=str)
    return path


# ── Text rendering ──────────────────────────────────────────────────────────

def render_status(current: dict[str, bool], gates: dict) -> str:
    lines: list[str] = []
    lines.append("━━━ Apex Flip Proposer — status ━━━")
    lines.append(f"generated: {datetime.now(UTC).isoformat()}")
    lines.append("")
    lines.append("Current Phase 7 flag state:")
    for flag, _, expected_to in FLIP_SEQUENCE:
        val = current.get(flag)
        flipped = bool(val) == bool(expected_to)
        marker = "✔ flipped" if flipped else "· pre-flip"
        lines.append(f"  {marker}  {flag:<35} = {val}")
    lines.append("")
    lines.append("Gate evaluation (latest shadow window):")
    obs = gates.get("observations") or {}
    for k, v in obs.items():
        lines.append(f"  {k:<25} {v}")
    lines.append("")
    if gates["ok"]:
        lines.append("  → all hard gates: OK")
    else:
        lines.append("  → hard gates: BLOCKED")
        for b in gates.get("hard_blocks") or []:
            lines.append(f"     • {b}")
    for w in gates.get("warnings") or []:
        lines.append(f"  ⚠ warn: {w}")
    return "\n".join(lines)


def render_proposal(proposal: dict) -> str:
    lines: list[str] = []
    kind = proposal["kind"].upper()
    lines.append(f"━━━ Apex Flip Proposer — {kind} ━━━")
    lines.append(f"ts:             {proposal['ts']}")
    lines.append(f"flag:           {proposal['flag']}")
    lines.append(f"observed:       {proposal['observed_value']}")
    lines.append(f"target:         {proposal['target_value']}")
    lines.append(f"decision:       {proposal['decision'].upper()}")
    if proposal.get("hard_blocks"):
        lines.append("hard blocks:")
        for b in proposal["hard_blocks"]:
            lines.append(f"  ✗ {b}")
    if proposal.get("warnings"):
        lines.append("warnings:")
        for w in proposal["warnings"]:
            lines.append(f"  ⚠ {w}")
    lines.append("")
    if proposal["decision"] in ("allow", "noop"):
        lines.append("Manual steps to apply (audit-only — this tool does NOT mutate config.py):")
    else:
        lines.append("DO NOT apply. Manual steps listed only for reference:")
    for i, s in enumerate(proposal.get("manual_steps") or [], 1):
        lines.append(f"  {i}. {s}")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _load_and_evaluate_gates(args) -> tuple[dict, dict]:
    """Load shadow + divergence, build a shadow report, evaluate gates."""
    sh = _shadow.filter_by_date(
        _shadow.load_jsonl(args.shadow_log), args.since, args.until,
    )
    dv = _shadow.filter_by_date(
        _shadow.load_jsonl(args.divergence_log), args.since, args.until,
    )
    report = _shadow.build_report(sh, dv, since=args.since, until=args.until)
    gates = evaluate_gates(report)
    return report, gates


def cmd_status(args) -> int:
    current = read_current_flag_state()
    _report, gates = _load_and_evaluate_gates(args)
    print(render_status(current, gates))
    return 0


def cmd_propose(args) -> int:
    flag, target_value = parse_flag_argument(args.spec)
    if flag not in _FLAG_ACCESSOR:
        print(f"ERROR: unknown flag {flag!r}. Allowed: {sorted(_FLAG_ACCESSOR)}",
              file=sys.stderr)
        return 2
    current = read_current_flag_state()
    _report, gates = _load_and_evaluate_gates(args)
    proposal = build_proposal(
        flag, target_value, current_state=current, gates=gates,
    )
    print(render_proposal(proposal))
    if not args.no_write:
        path = write_audit(proposal, out_dir=args.out_dir)
        print(f"\naudit: {path}")
    return 0 if proposal["decision"] in ("allow", "noop") else 1


def cmd_rollback(args) -> int:
    src_path = Path(args.audit_file)
    if not src_path.exists():
        print(f"ERROR: audit file not found: {src_path}", file=sys.stderr)
        return 2
    source = json.loads(src_path.read_text())
    rollback = build_rollback(source)
    print(render_proposal(rollback))
    if not args.no_write:
        path = write_audit(rollback, out_dir=args.out_dir)
        print(f"\naudit: {path}")
    return 0


def _common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--shadow-log", default="data/apex_shadow_log.jsonl")
    ap.add_argument("--divergence-log", default="data/apex_divergence_log.jsonl")
    ap.add_argument("--since", default=None, help="UTC date filter (YYYY-MM-DD)")
    ap.add_argument("--until", default=None, help="UTC date filter (YYYY-MM-DD)")
    ap.add_argument("--out-dir", default=_AUDIT_DIR_DEFAULT)
    ap.add_argument("--no-write", action="store_true",
                    help="Render proposal text but do not write audit artifact.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apex flag flip / rollback proposer (audit-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_status = sub.add_parser("status", help="Print flag state + gate snapshot")
    _common_args(ap_status)
    ap_status.set_defaults(func=cmd_status)

    ap_prop = sub.add_parser("propose", help="Propose a flag flip (audit-only)")
    ap_prop.add_argument("spec", help="FLAG=true|false")
    _common_args(ap_prop)
    ap_prop.set_defaults(func=cmd_propose)

    ap_rb = sub.add_parser("rollback", help="Build a rollback proposal from a prior audit")
    ap_rb.add_argument("audit_file", help="Path to a prior propose_*.json audit")
    _common_args(ap_rb)
    ap_rb.set_defaults(func=cmd_rollback)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
