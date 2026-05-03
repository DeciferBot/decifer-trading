#!/usr/bin/env python3
"""
phase1_session_report.py
Reads signals_log.jsonl, trade_events.jsonl, and training_records.jsonl
and produces the Phase 1 Post-Session Confirmation Report.

Run after 2-3 post-Phase 1 sessions have completed:
    python3 scripts/phase1_session_report.py

Reports are dated by entry timestamp. Only events AFTER Phase 1 commit
(2026-05-03T13:34:44Z) are included.
"""
import json
import sys
import os
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHASE1_COMMIT_TS = "2026-05-03T13:34:44"   # UTC — Phase 1 commit boundary

SIGNALS_LOG   = os.path.join(REPO, "data", "signals_log.jsonl")
TRADE_EVENTS  = os.path.join(REPO, "data", "trade_events.jsonl")
TRAINING      = os.path.join(REPO, "data", "training_records.jsonl")

GATE_TAGS = [
    "2of3_signal_gate",
    "short_flow_squeeze_gate",
    "score_zero_swing_position",
    "intraday_max_concurrent",
    "news_alone_swing_block",
    "news_alone_swing_block_shadow",
    "swing_short_bearish_regime_gate",
    "position_long_only",
    "position_equity_only",
    "catalyst_score_floor",
]

STRUCTURAL_REGIMES = {
    "TRENDING_UP", "TRENDING_DOWN", "RANGE_BOUND",
    "BULL_VOLATILE", "BEAR_VOLATILE", "PANIC",
    "MOMENTUM_SURGE", "RECOVERY", "DISTRIBUTION",
    "NEUTRAL", "UNKNOWN",
}
# These are session_character labels — should NOT appear in entry_regime after Phase 1
SESSION_CHAR_VALUES = {
    "MOMENTUM_BULL", "TRENDING_BULL", "BULL_TRENDING", "AGGRESSIVE_BULL",
    "RELIEF_RALLY_FADING", "TRENDING_BEAR", "CHOPPY", "WHIPSAW",
}

SIG_THRESHOLD = 5


def _is_post_phase1(ts_str: str) -> bool:
    if not ts_str:
        return False
    ts = ts_str[:19].replace("T", " ")
    return ts > PHASE1_COMMIT_TS[:19].replace("T", " ")


def _combo_label(flow, squeeze, momentum, threshold=SIG_THRESHOLD):
    fires = [
        ("flow", flow >= threshold),
        ("squeeze", squeeze >= threshold),
        ("momentum", momentum >= threshold),
    ]
    passing = [name for name, ok in fires if ok]
    if len(passing) == 3:
        return "all_three"
    if len(passing) == 2:
        pair = tuple(sorted(passing))
        labels = {
            ("flow", "squeeze"): "flow+squeeze",
            ("flow", "momentum"): "flow+momentum",
            ("momentum", "squeeze"): "squeeze+momentum",
        }
        return labels.get(pair, "+".join(pair))
    return f"only_{passing[0]}" if passing else "none"


def load_signals():
    signals = []
    with open(SIGNALS_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
                if _is_post_phase1(s.get("ts", "")):
                    signals.append(s)
            except Exception:
                pass
    return signals


def load_trade_events():
    events = []
    with open(TRADE_EVENTS) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if _is_post_phase1(e.get("ts", "")):
                    events.append(e)
            except Exception:
                pass
    return events


def load_training():
    records = []
    with open(TRAINING) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = r.get("open_time") or r.get("ts") or r.get("fill_time") or ""
                if _is_post_phase1(ts):
                    records.append(r)
            except Exception:
                pass
    return records


def analyse_signals(signals):
    print(f"\n{'='*70}")
    print("SECTION 3: SESSION-LEVEL SIGNAL FLOW")
    print(f"{'='*70}")

    by_day = defaultdict(list)
    for s in signals:
        day = (s.get("ts") or "")[:10]
        by_day[day].append(s)

    if not by_day:
        print("  NO POST-PHASE 1 SIGNALS FOUND")
        print(f"  Last signal ts: check {SIGNALS_LOG}")
        return

    for day in sorted(by_day):
        day_sigs = by_day[day]
        scores = [s.get("score", 0) or 0 for s in day_sigs]
        above40 = [s for s in day_sigs if (s.get("score", 0) or 0) >= 40]
        cycles = set(s.get("scan_id", "") for s in day_sigs)

        print(f"\n  Session: {day}")
        print(f"    Scan cycles:          {len(cycles)}")
        print(f"    Total signals:        {len(day_sigs)}")
        print(f"    Score >= 40:          {len(above40)} ({100*len(above40)/max(len(day_sigs),1):.1f}%)")
        print(f"    Score = 0:            {sum(1 for sc in scores if sc == 0)}")

        # Direction breakdown
        dirs = Counter((s.get("direction") or "LONG").upper() for s in day_sigs)
        print(f"    LONG signals:         {dirs.get('LONG', 0)}")
        print(f"    SHORT signals:        {dirs.get('SHORT', 0)}")


def analyse_gate_tags(events, training):
    """Gate rejection tags from trade_events and gate reasons from training records."""
    print(f"\n{'='*70}")
    print("SECTION 4: GATE REJECTION BY TAG")
    print(f"{'='*70}")

    # From trade_events: look for gate rejection events
    rejected = [e for e in events if (e.get("event_type") or "").upper() in ("REJECTED", "REJECT", "GATE_REJECT")]
    tag_counter = Counter()
    for e in rejected:
        reason = e.get("reason") or e.get("gate_reason") or ""
        for tag in GATE_TAGS:
            if tag in reason:
                tag_counter[tag] += 1
                break
        else:
            tag_counter["other"] += 1

    # Also scan training records for rejected entries
    rejected_tr = [r for r in training if (r.get("gate_result") or "").upper() == "REJECT"]
    for r in rejected_tr:
        reason = r.get("gate_reason") or ""
        for tag in GATE_TAGS:
            if tag in reason:
                tag_counter[tag] += 1

    if not tag_counter and not rejected:
        print("  No gate rejection events found in post-Phase 1 events.")
        print("  (Rejection tags are logged by entry_gate.py at INFO level —")
        print("   check decifer.log for 'entry_gate:' strings if this is unexpected)")
    else:
        print(f"  Total rejection events: {sum(tag_counter.values())}")
        for tag, count in sorted(tag_counter.items(), key=lambda x: -x[1]):
            print(f"    entry_gate:{tag}  →  {count}")


def analyse_intraday_combinations(signals):
    """INTRADAY survivor 2-of-3 combination breakdown."""
    print(f"\n{'='*70}")
    print("SECTION 4b: INTRADAY SIGNAL COMBINATION BREAKDOWN (score >= 40)")
    print(f"{'='*70}")

    above40 = [s for s in signals if (s.get("score", 0) or 0) >= 40 and s.get("score_breakdown")]
    combo_counter = Counter()
    combo_scores = defaultdict(list)

    for s in above40:
        bd = s.get("score_breakdown") or {}
        if not isinstance(bd, dict):
            continue
        flow = float(bd.get("flow", 0) or 0)
        squeeze = float(bd.get("squeeze", 0) or 0)
        momentum = float(bd.get("momentum", 0) or 0)
        label = _combo_label(flow, squeeze, momentum)
        combo_counter[label] += 1
        combo_scores[label].append(s.get("score", 0) or 0)

    total = sum(combo_counter.values())
    for combo in ["all_three", "flow+squeeze", "flow+momentum", "squeeze+momentum",
                  "only_flow", "only_squeeze", "only_momentum", "none"]:
        count = combo_counter.get(combo, 0)
        if count == 0:
            continue
        scores_for_combo = combo_scores[combo]
        avg_score = sum(scores_for_combo) / len(scores_for_combo) if scores_for_combo else 0
        blocked = combo in ("only_flow", "only_squeeze", "only_momentum", "none")
        tag = " ← BLOCKED by 2-of-3 gate" if blocked else ""
        print(f"  {combo:<22} {count:>5}  ({100*count/max(total,1):5.1f}%)  avg_score={avg_score:.1f}{tag}")


def analyse_accepted_entries(training):
    """Section 1: Accepted entries by trade type."""
    print(f"\n{'='*70}")
    print("SECTION 3b: ACCEPTED ENTRIES BY TRADE TYPE")
    print(f"{'='*70}")

    if not training:
        print("  NO POST-PHASE 1 TRAINING RECORDS FOUND")
        return

    by_type = defaultdict(list)
    for r in training:
        tt = (r.get("trade_type") or "UNKNOWN").upper()
        by_type[tt].append(r)

    for tt in sorted(by_type):
        records = by_type[tt]
        directions = Counter((r.get("direction") or "LONG").upper() for r in records)
        print(f"  {tt}: {len(records)} entries  (LONG={directions.get('LONG',0)} SHORT={directions.get('SHORT',0)})")


def analyse_swing_catalyst(training):
    """Section 5: SWING catalyst field population."""
    print(f"\n{'='*70}")
    print("SECTION 5: SWING CATALYST SHADOW HEALTH")
    print(f"{'='*70}")

    swings = [r for r in training if (r.get("trade_type") or "").upper() == "SWING"]
    if not swings:
        print("  No SWING entries in post-Phase 1 training records.")
        return

    with_type = [r for r in swings if r.get("catalyst_type") and str(r.get("catalyst_type")).lower() not in ("none", "null", "")]
    with_score = [r for r in swings if r.get("catalyst_score") and float(r.get("catalyst_score") or 0) > 0]

    print(f"  SWING entries (post-Phase 1):  {len(swings)}")
    print(f"  catalyst_type populated:       {len(with_type)}/{len(swings)} ({100*len(with_type)/len(swings):.1f}%)")
    print(f"  catalyst_score populated:      {len(with_score)}/{len(swings)} ({100*len(with_score)/len(swings):.1f}%)")

    structural_cats = {
        "earnings", "earnings_beat", "earnings_surprise", "pead",
        "upgrade", "sector", "overnight_drift",
    }
    structural_ok = [r for r in with_type if str(r.get("catalyst_type", "")).lower() in structural_cats]
    print(f"  Structural catalyst type:      {len(structural_ok)}/{len(with_type)} of those populated")

    # Gate 6 shadow would-have-blocked
    would_block = [r for r in swings if not (
        str(r.get("catalyst_type", "")).lower() in structural_cats
        or r.get("recent_upgrade")
        or r.get("insider_net_sentiment") == "BUYING"
        or (r.get("congressional_sentiment") or "").upper() == "BUYING"
    )]
    print(f"  Gate 6 shadow would-block:     {len(would_block)}/{len(swings)}")

    pct = 100 * len(with_type) / len(swings)
    gate_ready = pct >= 80
    print(f"\n  Catalyst field population:  {pct:.1f}%")
    print(f"  Gate 6 enable threshold:    80%")
    print(f"  Gate 6 ready to enable:     {'YES — but await Amit approval' if gate_ready else 'NO — keep shadow mode'}")


def analyse_entry_regime(training):
    """Section 6: entry_regime verification."""
    print(f"\n{'='*70}")
    print("SECTION 6: ENTRY REGIME VERIFICATION")
    print(f"{'='*70}")

    if not training:
        print("  No post-Phase 1 training records to check.")
        return

    missing = [r for r in training if not r.get("entry_regime") or r.get("entry_regime") == "MISSING"]
    contaminated = [r for r in training if r.get("entry_regime") in SESSION_CHAR_VALUES]
    structural_ok = [r for r in training
                     if r.get("entry_regime")
                     and r.get("entry_regime") != "MISSING"
                     and r.get("entry_regime") not in SESSION_CHAR_VALUES]

    print(f"  Post-Phase 1 closed trades:          {len(training)}")
    print(f"  entry_regime = MISSING:              {len(missing)}")
    print(f"  entry_regime = session_character:    {len(contaminated)}")
    print(f"  entry_regime = structural label:     {len(structural_ok)}")

    if contaminated:
        print(f"\n  ⚠️  CONTAMINATION DETECTED — session_character values in entry_regime:")
        for r in contaminated:
            print(f"      {r.get('symbol','?')}: {r.get('entry_regime')} (trade_type={r.get('trade_type')})")
    elif structural_ok:
        values = Counter(r.get("entry_regime") for r in structural_ok)
        print(f"\n  ✅ entry_regime structural values seen:")
        for v, c in values.most_common():
            print(f"      {v}: {c}")
    else:
        print("\n  ⚠️  All records still MISSING — Change 9 has not produced a closed trade yet")


def analyse_timeout(events, training):
    """Section 7: timeout behaviour."""
    print(f"\n{'='*70}")
    print("SECTION 7: EXIT TIMEOUT BEHAVIOUR (Change 15)")
    print(f"{'='*70}")

    # Look for scalp_timeout events in trade_events
    timeout_exits = [e for e in events if "scalp_timeout" in str(e.get("reason", "") or "")]
    losing_exits = [e for e in timeout_exits if (e.get("pnl") or 0) < 0]
    profitable_exits = [e for e in timeout_exits if (e.get("pnl") or 0) >= 0]

    # Also check training records
    timeout_records = [r for r in training if "scalp_timeout" in str(r.get("exit_reason", "") or r.get("reason", "") or "")]

    print(f"  scalp_timeout events in trade log:   {len(timeout_exits)}")
    print(f"    losing timeouts (EXIT):            {len(losing_exits)}")
    print(f"    profitable timeouts (unexpected):  {len(profitable_exits)}")
    print(f"  scalp_timeout in closed records:     {len(timeout_records)}")

    if profitable_exits:
        print("\n  ⚠️  PROFITABLE TIMEOUT EXITS DETECTED — should be PM reviews, not exits:")
        for e in profitable_exits:
            print(f"      {e.get('symbol','?')}: pnl={e.get('pnl')}")


def analyse_errors(events, training):
    """Section 8: runtime errors."""
    print(f"\n{'='*70}")
    print("SECTION 8: ERRORS AND UNEXPECTED BEHAVIOUR")
    print(f"{'='*70}")

    # Look for error events
    error_events = [e for e in events if (e.get("event_type") or "").upper() in ("ERROR", "EXCEPTION")]
    missing_field = [r for r in training if not r.get("score") and not r.get("trade_type")]

    # Gate violations: accepted trade with score < 40
    gate_violations = [r for r in training
                       if (r.get("score") or 0) < 40 and
                       (r.get("trade_type") or "").upper() == "INTRADAY"]

    print(f"  Error events in trade log:     {len(error_events)}")
    print(f"  Records missing score/type:    {len(missing_field)}")
    print(f"  Gate violations (INTRADAY <40): {len(gate_violations)}")

    if gate_violations:
        print("\n  ⚠️  GATE VIOLATIONS — INTRADAY entries with score < 40:")
        for r in gate_violations:
            print(f"      {r.get('symbol','?')}: score={r.get('score')} direction={r.get('direction')}")


def recommendation(signals, training):
    """Section 9: Go/No-Go recommendation."""
    print(f"\n{'='*70}")
    print("SECTION 9: GO / NO-GO RECOMMENDATION")
    print(f"{'='*70}")

    intraday = [r for r in training if (r.get("trade_type") or "").upper() == "INTRADAY"]
    swings = [r for r in training if (r.get("trade_type") or "").upper() == "SWING"]
    n_sessions = len(set((r.get("open_time") or "")[:10] for r in training if r.get("open_time")))

    intraday_per_session = len(intraday) / max(n_sessions, 1)
    swing_cat_populated = sum(1 for r in swings if r.get("catalyst_type") and r["catalyst_type"] not in ("none","null",""))
    cat_pct = 100 * swing_cat_populated / max(len(swings), 1)

    print(f"\n  Sessions with post-Phase 1 entries:  {n_sessions}")
    print(f"  INTRADAY entries/session avg:        {intraday_per_session:.1f}  (target: ≤5)")
    print(f"  SWING catalyst field population:     {cat_pct:.1f}%  (target for Gate 6: ≥80%)")

    print(f"\n  ┌─────────────────────────────────────────────────────────┐")
    print(f"  │ Keep Phase 1 as-is:          {'YES' if True else 'NO'}                              │")
    print(f"  │ Relax any gate:              NO — observe first           │")
    print(f"  │ Keep Gate 6 in shadow:       YES — cat_pct={cat_pct:5.1f}%          │")
    print(f"  │ Start Phase 2:               NO — per Amit instruction    │")
    print(f"  └─────────────────────────────────────────────────────────┘")


def main():
    print("\n" + "="*70)
    print("PHASE 1 POST-SESSION CONFIRMATION REPORT")
    print(f"Phase 1 commit boundary: {PHASE1_COMMIT_TS} UTC")
    print(f"Report generated:        {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("="*70)

    signals  = load_signals()
    events   = load_trade_events()
    training = load_training()

    print(f"\nPost-Phase 1 records found:")
    print(f"  Signals:        {len(signals)}")
    print(f"  Trade events:   {len(events)}")
    print(f"  Training recs:  {len(training)}")

    if len(signals) == 0 and len(events) == 0 and len(training) == 0:
        print("\n⚠️  NO POST-PHASE 1 DATA FOUND.")
        print("   Market opens Monday 2026-05-04 at 09:30 EDT.")
        print("   Start the bot at market open and re-run this script after 2-3 sessions.")
        return

    analyse_signals(signals)
    analyse_accepted_entries(training)
    analyse_gate_tags(events, training)
    analyse_intraday_combinations(signals)
    analyse_swing_catalyst(training)
    analyse_entry_regime(training)
    analyse_timeout(events, training)
    analyse_errors(events, training)
    recommendation(signals, training)

    print(f"\n{'='*70}")
    print("END OF REPORT")
    print("="*70)


if __name__ == "__main__":
    main()
