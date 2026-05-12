"""
tests/test_trade_quality_report.py — Unit tests for scripts/trade_quality_report.py.

Fixture-based only. No live file I/O. No dependency on actual data files.
No import of any trading runtime module.
"""
from __future__ import annotations

import json
import pathlib
import sys
import textwrap
from datetime import date, datetime, timezone

import pytest

# ── resolve script path without touching sys.path globally ───────────────────
_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import trade_quality_report as tqr  # noqa: E402  (must come after sys.path fix)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pos(symbol, score, qty=100, entry=50.0, open_time_str=None, trade_type="POSITION"):
    p = {
        "symbol": symbol,
        "entry_score": score,
        "score": score,
        "qty": qty,
        "entry": entry,
        "trade_type": trade_type,
    }
    if open_time_str:
        p["open_time"] = open_time_str
    return p


def _training_rec(symbol, score, pnl=0.0, ts_close="2026-05-11T12:00:00+00:00", entry_score=None):
    return {
        "symbol": symbol,
        "score": score,
        "entry_score": entry_score,
        "pnl": pnl,
        "ts_close": ts_close,
    }


def _apex_candidate(symbol, score, selected=False, scanner_tier=None, pru=False,
                    cycle_id="c1", ts="2026-05-11T10:00:00Z"):
    r = {
        "record_type": "apex_candidate",
        "symbol": symbol,
        "raw_score": score,
        "apex_cap_score": score,
        "apex_decision": "selected" if selected else "not_selected",
        "cycle_id": cycle_id,
        "ts": ts,
    }
    if scanner_tier is not None:
        r["scanner_tier"] = scanner_tier
    if pru:
        r["pru"] = True
    return r


def _skip_record(skipped_sym, skipped_score, sel_sym, sel_score, gap,
                 origin_path="normal_path", scanner_tier="A",
                 ts="2026-05-11T10:00:00Z"):
    return {
        "record_type": "high_score_skip",
        "symbol": skipped_sym,
        "effective_score": skipped_score,
        "selected_lower_symbol": sel_sym,
        "selected_lower_score": sel_score,
        "score_gap": gap,
        "origin_path": origin_path,
        "scanner_tier": scanner_tier,
        "ts": ts,
    }


def _blank_dq() -> tqr.DataQuality:
    return tqr.DataQuality()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Capital deployment sequence: chronological order + cumulative notional
# ─────────────────────────────────────────────────────────────────────────────

def test_section1_chronological_order_and_cumulative_notional():
    positions = [
        _pos("AAPL", 85, qty=10, entry=200.0, open_time_str="2026-05-11T14:00:00+00:00"),
        _pos("NVDA", 75, qty=5,  entry=400.0, open_time_str="2026-05-11T10:00:00+00:00"),
        _pos("TSM",  39, qty=20, entry=150.0, open_time_str="2026-05-11T09:00:00+00:00"),
        _pos("WDC",  27, qty=8,  entry=50.0,  open_time_str="2026-05-11T11:00:00+00:00"),
        _pos("MSFT", 63, qty=3,  entry=300.0, open_time_str="2026-05-11T16:00:00+00:00"),
    ]
    nlv = 100_000.0
    lines, data = tqr.section_1(positions, nlv)

    seq = data["sequence"]
    assert len(seq) == 5

    # Verify chronological order
    symbols_in_order = [r["symbol"] for r in seq]
    assert symbols_in_order == ["TSM", "NVDA", "WDC", "AAPL", "MSFT"], \
        f"Expected chronological order, got {symbols_in_order}"

    # Verify cumulative notional is monotonically increasing
    cums = [r["cum_notional"] for r in seq]
    for i in range(1, len(cums)):
        assert cums[i] > cums[i - 1], \
            f"Cumulative notional not increasing at step {i}: {cums}"

    # TSM first: 20 * 150 = 3000
    assert seq[0]["notional"] == pytest.approx(3000.0)
    assert seq[0]["cum_notional"] == pytest.approx(3000.0)

    # Final cum = sum of all notionals
    expected_total = (20*150) + (5*400) + (8*50) + (10*200) + (3*300)
    assert data["final_cum_notional"] == pytest.approx(expected_total)

    # Exposure % for first entry = 3000/100_000 * 100 = 3.0%
    assert seq[0]["cum_exp_pct"] == pytest.approx(3.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Filled vs blocked score gap: outscores_15 flag triggers correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_section2_outscores_15_flag():
    # Book with one low-score position (avg ~30)
    positions = [_pos("WDC", 30, qty=10, entry=50.0)]
    # Score index: a blocked candidate scored 55
    sym_score_index = {"ASML": 55.0}
    margin_blocks = [{
        "ts": None,
        "symbol": "ASML",
        "deployed": 1_000_000,
        "new_position": 50_000,
        "total_pct": 111.0,
        "limit_pct": 110.0,
        "block_reason": "margin_cap",
    }]
    spread_blocked = set()

    lines, data = tqr.section_2(margin_blocks, positions, sym_score_index, spread_blocked)

    # Book avg should be ~30; ASML score 55 → gap = +25 → outscores_15 = 1
    assert data["unique_blocked"] == 1
    assert data["book_avg_score"] == pytest.approx(30.0)
    assert data["outscores_15_count"] == 1
    assert data["outscores_20_count"] == 1  # gap=25 > 20
    assert data["rows"][0]["symbol"] == "ASML"
    assert data["rows"][0]["outscores_15"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Spread-blocked symbols excluded from Section 2 block counting
# ─────────────────────────────────────────────────────────────────────────────

def test_section2_spread_block_not_counted_as_margin_block():
    # TSLA was spread-blocked (not margin-blocked) — NOT in margin_blocks list
    positions = [_pos("NVDA", 75, qty=10, entry=200.0)]
    sym_score_index = {"TSLA": 80.0}
    margin_blocks = []          # No margin blocks — TSLA was spread-blocked
    spread_blocked = {"TSLA"}   # Spread-blocked (distinct reason)

    lines, data = tqr.section_2(margin_blocks, positions, sym_score_index, spread_blocked)

    # No margin blocks → unique_blocked = 0; outscores counts = 0
    assert data["unique_blocked"] == 0
    assert data["outscores_15_count"] == 0
    assert data["outscores_20_count"] == 0
    # TSLA was spread-blocked, not margin-blocked: must NOT appear in block rows
    assert all(r.get("symbol") != "TSLA" for r in data.get("rows", []))


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — High-score displacement: ranked by score_gap descending, top shown first
# ─────────────────────────────────────────────────────────────────────────────

def test_section3_high_score_skips_ranked_by_gap():
    apex_records = [
        _skip_record("ASML", 75, "NBIS", 40, gap=35),
        _skip_record("GS",   68, "ONDS", 45, gap=23),
        _skip_record("TSLA", 60, "WDC",  55, gap=5),
    ]
    pru_syms: set[str] = set()

    lines, data = tqr.section_3(apex_records, pru_syms)

    assert data["total_skips"] == 3
    # Top-ranked by gap: ASML(35) > GS(23) > TSLA(5)
    text = "\n".join(lines)
    asml_pos = text.index("ASML")
    gs_pos = text.index("GS")
    tsla_pos = text.index("TSLA")
    assert asml_pos < gs_pos < tsla_pos, \
        "ASML (gap=35) should appear before GS (gap=23) before TSLA (gap=5)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — PRU/discovery displacement watch: fires correctly; no tier-led language
# ─────────────────────────────────────────────────────────────────────────────

def test_section3_pru_displacement_watch_fires_and_no_tier_language():
    # NBIS is PRU-sourced (selected lower), ASML is normal-path (skipped), gap=35
    apex_records = [
        _apex_candidate("NBIS", 40, selected=False, scanner_tier="D"),
        _skip_record(
            skipped_sym="ASML", skipped_score=75,
            sel_sym="NBIS", sel_score=40,
            gap=35,
            origin_path="normal_path",
            scanner_tier="A",   # skipped candidate is normal-path
        ),
    ]
    pru_syms = {"NBIS"}   # NBIS is the PRU/discovery symbol

    lines, data = tqr.section_3(apex_records, pru_syms)

    assert data["pru_displacement_watch_count"] >= 1, \
        "PRU_SOURCE_DISPLACEMENT_WATCH should fire when gap>15 and selected is PRU"

    text = "\n".join(lines)
    # Must flag it
    assert "PRU_SOURCE_DISPLACEMENT_WATCH" in text

    # Must NOT use tier-led recommendation language
    forbidden = ["Tier D allocation", "tier-led", "allocate to Tier D", "Tier D priority"]
    for phrase in forbidden:
        assert phrase not in text, \
            f"Tier-led language '{phrase}' found — architecture is not tier-led"

    # Must include "Diagnostic flag only"
    assert "Diagnostic flag only" in text or "diagnostic" in text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Score bucket distribution: all 4 buckets, counts, P&L aggregation
# ─────────────────────────────────────────────────────────────────────────────

def test_section4_score_buckets():
    positions = [
        _pos("A", 20),   # QUESTIONABLE (<35)
        _pos("B", 40),   # LOW (35-49)
        _pos("C", 60),   # MEDIUM (50-64)
        _pos("D", 70),   # HIGH (65+)
        _pos("E", 80),   # HIGH (65+)
    ]
    training = [
        _training_rec("X", 20, pnl=-100.0),  # QUESTIONABLE
        _training_rec("Y", 42, pnl=200.0),   # LOW
        _training_rec("Z", 58, pnl=150.0),   # MEDIUM
        _training_rec("W", 70, pnl=300.0),   # HIGH
        _training_rec("V", 75, pnl=-50.0),   # HIGH
    ]
    lines, data = tqr.section_4(positions, training)

    ob = data["open_buckets"]
    assert ob["QUESTIONABLE (<35)"]["count"] == 1
    assert ob["LOW (35-49)"]["count"] == 1
    assert ob["MEDIUM (50-64)"]["count"] == 1
    assert ob["HIGH (65+)"]["count"] == 2

    cb = data["closed_buckets"]
    assert cb["QUESTIONABLE (<35)"]["count"] == 1
    assert cb["QUESTIONABLE (<35)"]["total_pnl"] == pytest.approx(-100.0)
    assert cb["LOW (35-49)"]["count"] == 1
    assert cb["LOW (35-49)"]["total_pnl"] == pytest.approx(200.0)
    assert cb["HIGH (65+)"]["count"] == 2
    assert cb["HIGH (65+)"]["total_pnl"] == pytest.approx(250.0)
    assert cb["HIGH (65+)"]["wins"] == 1
    assert cb["HIGH (65+)"]["losses"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — ETF overlap: XLK score 26 + NVDA → flag fires; no overlap → no flag
# ─────────────────────────────────────────────────────────────────────────────

def test_section5_etf_overlap_flag_fires_and_clears():
    # Case A: XLK (score 26) held alongside NVDA — flag must fire
    positions_with_overlap = [
        _pos("XLK",  26),
        _pos("NVDA", 75),
    ]
    lines_a, data_a = tqr.section_5(positions_with_overlap)
    assert data_a["etf_overlap_flags"] == 1
    assert "XLK" in data_a["flagged_etfs"]
    assert "XLK" in data_a["flagged_below_35"]

    # Case B: IWM at high score (75) — IWM has empty overlap list → no flag
    positions_clean = [_pos("IWM", 75)]
    lines_b, data_b = tqr.section_5(positions_clean)
    assert data_b["etf_overlap_flags"] == 0

    # Case C: XLK held at score 60 (≥50) with NVDA — no flag (score ≥ 50 threshold)
    positions_high_etf = [
        _pos("XLK",  60),
        _pos("NVDA", 75),
    ]
    lines_c, data_c = tqr.section_5(positions_high_etf)
    assert data_c["etf_overlap_flags"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Field name normalisation: training score used when entry_score is None
# ─────────────────────────────────────────────────────────────────────────────

def test_training_score_normalises_none_entry_score():
    # entry_score = None, score = 65 → training_score should return 65
    rec_no_entry_score = {"entry_score": None, "score": 65}
    assert tqr.training_score(rec_no_entry_score) == pytest.approx(65.0)

    # entry_score = 0 (falsy), score = 72 → training_score should return 72
    rec_zero_entry_score = {"entry_score": 0, "score": 72}
    assert tqr.training_score(rec_zero_entry_score) == pytest.approx(72.0)

    # entry_score present and non-zero → use it
    rec_with_entry_score = {"entry_score": 55, "score": 72}
    assert tqr.training_score(rec_with_entry_score) == pytest.approx(55.0)

    # Both missing → None
    rec_empty = {"entry_score": None, "score": None}
    assert tqr.training_score(rec_empty) is None

    # Position score: similar logic
    pos_no_score = {"entry_score": None, "score": 44}
    assert tqr.position_score(pos_no_score) == pytest.approx(44.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — --since filter: records outside date range are excluded
# ─────────────────────────────────────────────────────────────────────────────

def test_load_jsonl_since_filter(tmp_path):
    jsonl_path = tmp_path / "test.jsonl"
    # Record on target date — include
    rec_in  = {"symbol": "AAPL", "score": 70, "ts": "2026-05-11T10:00:00+00:00"}
    # Record before target date — exclude
    rec_out = {"symbol": "NVDA", "score": 75, "ts": "2026-05-10T10:00:00+00:00"}
    jsonl_path.write_text(
        json.dumps(rec_in) + "\n" + json.dumps(rec_out) + "\n",
        encoding="utf-8",
    )

    dq = _blank_dq()
    since = date(2026, 5, 11)
    records = tqr.load_jsonl(jsonl_path, dq, since)

    assert len(records) == 1
    assert records[0]["symbol"] == "AAPL"

    # Records with no timestamp field are always included
    rec_no_ts = {"symbol": "MSFT", "score": 60}
    jsonl_path.write_text(
        json.dumps(rec_in) + "\n" + json.dumps(rec_no_ts) + "\n",
        encoding="utf-8",
    )
    records2 = tqr.load_jsonl(jsonl_path, dq, since)
    assert len(records2) == 2
    syms = {r["symbol"] for r in records2}
    assert "AAPL" in syms and "MSFT" in syms


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — Session verdict thresholds: all 5 verdicts fire at correct boundaries
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_verdict_all_branches():
    # INSUFFICIENT_DATA — book_avg is None
    assert tqr.compute_verdict(None, 0, 0, 0, 0, False) == "INSUFFICIENT_DATA"

    # CAPITAL_SEQUENCING_FAILURE — outscores_20 >= 3 AND low_qual_open >= 3
    assert tqr.compute_verdict(
        book_avg=50, below_35=3, outscores_15=4, outscores_20=3,
        low_qual_open=3, etf_below_35_flag=False
    ) == "CAPITAL_SEQUENCING_FAILURE"

    # Not failure because low_qual_open < 3 (only 2), even though outscores_20 = 3
    v = tqr.compute_verdict(
        book_avg=50, below_35=2, outscores_15=4, outscores_20=3,
        low_qual_open=2, etf_below_35_flag=False
    )
    assert v != "CAPITAL_SEQUENCING_FAILURE"

    # WEAK_ENTRIES_DETECTED — below_35 >= 2
    assert tqr.compute_verdict(
        book_avg=60, below_35=2, outscores_15=0, outscores_20=0,
        low_qual_open=2, etf_below_35_flag=False
    ) == "WEAK_ENTRIES_DETECTED"

    # WEAK_ENTRIES_DETECTED — book_avg < 50
    assert tqr.compute_verdict(
        book_avg=45, below_35=0, outscores_15=0, outscores_20=0,
        low_qual_open=0, etf_below_35_flag=False
    ) == "WEAK_ENTRIES_DETECTED"

    # WEAK_ENTRIES_DETECTED — etf_below_35_flag True
    assert tqr.compute_verdict(
        book_avg=55, below_35=0, outscores_15=0, outscores_20=0,
        low_qual_open=0, etf_below_35_flag=True
    ) == "WEAK_ENTRIES_DETECTED"

    # SEQUENCING_PRESSURE — book_avg >= 50, outscores_15 >= 1, below_35 <= 2
    assert tqr.compute_verdict(
        book_avg=55, below_35=1, outscores_15=1, outscores_20=0,
        low_qual_open=1, etf_below_35_flag=False
    ) == "SEQUENCING_PRESSURE"

    # CAPITAL_DEPLOYED_WELL — book_avg >= 55, below_35 <= 1, outscores_15 == 0, no etf flag
    assert tqr.compute_verdict(
        book_avg=65, below_35=0, outscores_15=0, outscores_20=0,
        low_qual_open=0, etf_below_35_flag=False
    ) == "CAPITAL_DEPLOYED_WELL"


# ─────────────────────────────────────────────────────────────────────────────
# Test 11 — Missing files: graceful degradation without crashes
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_files_degrade_gracefully(tmp_path):
    dq = _blank_dq()
    since = date(2026, 5, 11)

    # load_jsonl on missing path → empty list + dq.missing_files populated
    missing = tmp_path / "nonexistent.jsonl"
    records = tqr.load_jsonl(missing, dq, since)
    assert records == []
    assert any("nonexistent.jsonl" in f for f in dq.missing_files)

    # load_positions on missing path → empty list + dq.missing_files populated
    dq2 = _blank_dq()
    missing_pos = tmp_path / "positions.json"
    positions = tqr.load_positions(missing_pos, dq2)
    assert positions == []
    assert any("positions.json" in f for f in dq2.missing_files)

    # section_1 with empty positions → INSUFFICIENT DATA in output
    lines, data = tqr.section_1([], nlv=None)
    assert any("INSUFFICIENT DATA" in l or "INSUFFICIENT_DATA" in l for l in lines)

    # parse_margin_blocks on missing file → empty list
    dq3 = _blank_dq()
    missing_log = tmp_path / "decifer.log"
    blocks = tqr.parse_margin_blocks(missing_log, since, dq3)
    assert blocks == []


# ─────────────────────────────────────────────────────────────────────────────
# Test 12 — Malformed JSONL: bad lines counted and skipped, not fatal
# ─────────────────────────────────────────────────────────────────────────────

def test_malformed_jsonl_skipped_not_fatal(tmp_path):
    jsonl_path = tmp_path / "audit.jsonl"
    good = {"record_type": "apex_candidate", "symbol": "AAPL", "score": 70,
            "ts": "2026-05-11T10:00:00+00:00"}
    jsonl_path.write_text(
        json.dumps(good) + "\n"
        + "{this is not json\n"
        + "another bad line {{{\n"
        + json.dumps({"symbol": "NVDA", "score": 75, "ts": "2026-05-11T11:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )

    dq = _blank_dq()
    since = date(2026, 5, 11)
    records = tqr.load_jsonl(jsonl_path, dq, since)

    # Only the 2 valid records returned
    assert len(records) == 2
    # Malformed count is tracked
    assert dq.malformed_lines.get("audit.jsonl", 0) == 2

    # section_9 surfaces the malformed count
    positions: list[dict] = []
    training: list[dict] = []
    lines, data = tqr.section_9(dq, positions, training, [])
    issues_text = "\n".join(data["issues"])
    assert "Malformed lines" in issues_text or "malformed" in issues_text.lower()
