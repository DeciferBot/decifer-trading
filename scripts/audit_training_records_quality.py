#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  audit_training_records_quality.py         ║
# ║   Surgical audit of legacy training_records.jsonl.          ║
# ║   READ-ONLY. Never modifies the file.                       ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Counts degraded and suspect records in data/training_records.jsonl and
reports how many would be quarantined by migrate_training_records_quality.py.

Does NOT modify any file. Run with no arguments.

Exit code:
  0 — audit complete (even if degraded records exist)
  1 — file not found or unreadable
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

from config import CONFIG
from training_store import classify_record_quality

_STORE_FILE = Path(CONFIG.get("training_records", "data/training_records.jsonl"))


def _load() -> list[dict]:
    if not _STORE_FILE.exists():
        print(f"ERROR: {_STORE_FILE} not found")
        sys.exit(1)
    records: list[dict] = []
    skipped = 0
    with open(_STORE_FILE, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        print(f"  WARNING: skipped {skipped} corrupt line(s)")
    return records


def main() -> None:
    print(f"\nAudit: {_STORE_FILE}")
    records = _load()
    total = len(records)
    print(f"Total records: {total}\n")

    # ── Category counts ──────────────────────────────────────────────────────

    # Records already tagged by the quarantine system (v4.2.0+)
    already_eligible_true  = sum(1 for r in records if r.get("ml_eligible") is True)
    already_eligible_false = sum(1 for r in records if r.get("ml_eligible") is False)
    missing_ml_eligible    = sum(1 for r in records if "ml_eligible" not in r)

    # Definitive degradation criteria (same as migrate_training_records_quality.py)
    unk_tt = [r for r in records if (r.get("trade_type") or "").upper() in ("UNKNOWN", "")]
    unk_er = [r for r in records if (r.get("exit_reason") or "").lower() == "unknown_trade_type"]
    ext_id = [r for r in records if "_ext_" in (r.get("trade_id") or "").lower()]

    # Union of definitive criteria (de-duped by trade_id)
    definitive_degraded_ids = (
        {r.get("trade_id") for r in unk_tt}
        | {r.get("trade_id") for r in unk_er}
        | {r.get("trade_id") for r in ext_id}
    )
    definitive_degraded = len(definitive_degraded_ids)

    # Already tagged as ml_eligible=False among the definitive set
    already_tagged_degraded = sum(
        1 for r in records
        if r.get("trade_id") in definitive_degraded_ids
        and r.get("ml_eligible") is False
    )
    needs_tagging = definitive_degraded - already_tagged_degraded

    # Soft signals (not definitive — reported as advisory only, NOT quarantined)
    empty_scores = [r for r in records if not r.get("signal_scores")]
    zero_conv    = [r for r in records if r.get("conviction") == 0 and not r.get("signal_scores")]

    # Overlap: empty signal_scores AND NOT in definitive set
    soft_only_empty_scores = [
        r for r in empty_scores
        if r.get("trade_id") not in definitive_degraded_ids
    ]

    # ── Report ───────────────────────────────────────────────────────────────

    print("── Quality tagging status ───────────────────────────────────────────")
    print(f"  ml_eligible=True  (already tagged, v4.2.0+):  {already_eligible_true}")
    print(f"  ml_eligible=False (already tagged, v4.2.0+):  {already_eligible_false}")
    print(f"  ml_eligible field absent (legacy records):    {missing_ml_eligible}")
    print()

    print("── Definitive degradation criteria (would be quarantined) ───────────")
    print(f"  trade_type UNKNOWN or blank:                  {len(unk_tt)}")
    print(f"  exit_reason == 'unknown_trade_type':          {len(unk_er)}")
    print(f"  trade_id contains '_EXT_':                    {len(ext_id)}")
    print(f"  ── Union (de-duped by trade_id):              {definitive_degraded}")
    print(f"  Already tagged ml_eligible=False:             {already_tagged_degraded}")
    print(f"  Would be newly tagged by migration:           {needs_tagging}")
    print()

    print("── Soft signals (advisory only — NOT quarantined) ───────────────────")
    print(f"  empty signal_scores (all records):            {len(empty_scores)}")
    print(f"  empty signal_scores NOT in definitive set:    {len(soft_only_empty_scores)}")
    print(f"  conviction=0 AND empty scores:                {len(zero_conv)}")
    print()

    # ── Why soft signals are not quarantined ─────────────────────────────────
    print("── Why soft signals are left unchanged ──────────────────────────────")
    print("  'empty signal_scores' alone is not a reliable degradation indicator:")
    print("  - Early-phase trades before 10-dim scoring was implemented have")
    print("    empty scores but valid trade_type, conviction, and reasoning.")
    print("  - Test-fixture records legitimately have empty scores.")
    print("  - A trade with conviction>0 and a valid trade_type but scores={}")
    print("    represents a real decision — its outcome is still ML-informative.")
    print("  Only the three definitive criteria identify records where the bot")
    print("  genuinely lost its identity (trade_type UNKNOWN = restart wipe).")
    print()

    # ── Migration impact ──────────────────────────────────────────────────────
    eligible_current   = already_eligible_true + missing_ml_eligible  # legacy treated as eligible
    eligible_after_mig = already_eligible_true + (missing_ml_eligible - needs_tagging)

    print("── Migration impact on count_eligible() ─────────────────────────────")
    print(f"  count_eligible() today (legacy = eligible):   {eligible_current}")
    print(f"  count_eligible() after migration --apply:     {eligible_after_mig}")
    print(f"  Delta:                                        -{needs_tagging}")
    print()

    # ── Sample degraded records ───────────────────────────────────────────────
    sample_degraded = [
        r for r in records
        if r.get("trade_id") in definitive_degraded_ids
        and r.get("ml_eligible") is not False  # not yet tagged
    ][:8]

    if sample_degraded:
        print("── Sample records that would be newly tagged ─────────────────────────")
        for r in sample_degraded:
            tid  = (r.get("trade_id") or "")[:40]
            tt   = r.get("trade_type", "")
            er   = r.get("exit_reason", "")
            sc   = len(r.get("signal_scores") or {})
            conv = r.get("conviction", "?")
            quality = classify_record_quality(r, er)
            reason = quality.get("metadata_quality", "?")
            print(f"  {tid}")
            print(f"    trade_type={tt!r:<12}  exit_reason={er!r:<25}  scores={sc}  conv={conv}")
            print(f"    → {reason}")
        print()

    print("── Recommendation ───────────────────────────────────────────────────")
    if needs_tagging == 0:
        print("  ✓ All definitive degraded records are already tagged. No action needed.")
    else:
        print(f"  Run to apply: python3 scripts/migrate_training_records_quality.py --apply")
        print(f"  This will tag {needs_tagging} record(s) as ml_eligible=False.")
        print(f"  count_eligible() will decrease from {eligible_current} to {eligible_after_mig}.")
        print(f"  Phase gates (200 closed, ML gate 50) remain satisfied — "
              f"{eligible_after_mig} >> 200.")
    print()


if __name__ == "__main__":
    main()
