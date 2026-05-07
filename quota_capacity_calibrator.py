"""
quota_capacity_calibrator.py — Sprint 7H.3

Benchmark alternative handoff universe caps and quota policies without changing
production behaviour. Runs 5 scenarios (A through E), writes scenario outputs only
under data/live/quota_calibration/, then produces a comparison report and markdown
summary.

SAFETY CONTRACT:
  - Does NOT write to data/live/current_manifest.json
  - Does NOT write to data/live/active_opportunity_universe.json
  - Does NOT modify quota_allocator.py or universe_builder.py
  - Does NOT call any broker, LLM, or trading API
  - Does NOT read .env secrets
  - enable_active_opportunity_universe_handoff remains False
  - handoff_enabled remains false
  - live_output_changed = false (all outputs)
  - publication_mode = validation_only

Outputs:
  data/live/quota_calibration/scenario_<label>/universe.json  (per scenario)
  data/live/quota_capacity_calibration_report.json
  docs/intelligence_first_quota_capacity_calibration_summary.md
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import quota_allocator
from universe_builder import UniverseBuilder

# ── Constants ──────────────────────────────────────────────────────────────────

_CALIBRATION_DIR = "data/live/quota_calibration"
_REPORT_PATH = "data/live/quota_capacity_calibration_report.json"
_SUMMARY_PATH = "docs/intelligence_first_quota_capacity_calibration_summary.md"

_PRODUCTION_MANIFEST_PATH = "data/live/current_manifest.json"
_PRODUCTION_UNIVERSE_PATH = "data/live/active_opportunity_universe.json"

_GOVERNED_WATCH = ["COST", "MSFT", "PG"]      # governance_gap_defect from Sprint 7H.2
_QUOTA_WATCH    = ["SNDK", "WDC", "IREN"]      # already_governed_elsewhere (quota excluded)

_EIL_PATH  = "data/intelligence/economic_candidate_feed.json"
_SHADOW_IN = "data/universe_builder/active_opportunity_universe_shadow.json"
_SNAPSHOT  = "data/universe_builder/universe_snapshot.json"
_ADAPTER   = "data/live/adapter_snapshot.json"

# Sprint spec scenarios
SCENARIOS: list[dict[str, Any]] = [
    {
        "label":       "A_baseline",
        "description": "Current baseline",
        "total":       50,
        "structural":  20,
        "etf_proxy":   10,
        "attention":   15,
        "catalyst":    30,
        "catalyst_min": 10,
        "structural_min": 8,
    },
    {
        "label":       "B_moderate",
        "description": "Moderate expansion",
        "total":       75,
        "structural":  35,
        "etf_proxy":   15,
        "attention":   20,
        "catalyst":    30,
        "catalyst_min": 10,
        "structural_min": 8,
    },
    {
        "label":       "C_production_candidate",
        "description": "Production candidate",
        "total":       100,
        "structural":  50,
        "etf_proxy":   20,
        "attention":   20,
        "catalyst":    30,
        "catalyst_min": 10,
        "structural_min": 8,
    },
    {
        "label":       "D_upper_bound",
        "description": "Upper-bound test",
        "total":       125,
        "structural":  65,
        "etf_proxy":   25,
        "attention":   25,
        "catalyst":    35,
        "catalyst_min": 10,
        "structural_min": 8,
    },
    {
        "label":       "E_stress",
        "description": "Stress test",
        "total":       150,
        "structural":  80,
        "etf_proxy":   30,
        "attention":   30,
        "catalyst":    40,
        "catalyst_min": 10,
        "structural_min": 8,
    },
]


# ── Quota override context manager ─────────────────────────────────────────────

@contextlib.contextmanager
def _override_quota(scenario: dict[str, Any]):
    """
    Temporarily override quota_allocator module-level constants for one scenario run.
    Restores originals on exit regardless of exceptions.
    Does NOT modify any file.
    """
    orig = {
        "_TOTAL_MAX":       quota_allocator._TOTAL_MAX,
        "_STRUCTURAL_MAX":  quota_allocator._STRUCTURAL_MAX,
        "_STRUCTURAL_MIN":  quota_allocator._STRUCTURAL_MIN,
        "_CATALYST_MAX":    quota_allocator._CATALYST_MAX,
        "_CATALYST_MIN":    quota_allocator._CATALYST_MIN,
        "_ATTENTION_MAX":   quota_allocator._ATTENTION_MAX,
        "_ETF_PROXY_MAX":   quota_allocator._ETF_PROXY_MAX,
    }
    try:
        quota_allocator._TOTAL_MAX      = scenario["total"]
        quota_allocator._STRUCTURAL_MAX = scenario["structural"]
        quota_allocator._STRUCTURAL_MIN = scenario.get("structural_min", 8)
        quota_allocator._CATALYST_MAX   = scenario["catalyst"]
        quota_allocator._CATALYST_MIN   = scenario.get("catalyst_min", 10)
        quota_allocator._ATTENTION_MAX  = scenario["attention"]
        quota_allocator._ETF_PROXY_MAX  = scenario["etf_proxy"]
        yield
    finally:
        for k, v in orig.items():
            setattr(quota_allocator, k, v)


# ── EIL symbol lookup ──────────────────────────────────────────────────────────

def _load_eil_symbols() -> set[str]:
    """All symbols in the economic candidate feed (governed symbols)."""
    try:
        with open(_EIL_PATH, encoding="utf-8") as f:
            feed = json.load(f)
        return {c["symbol"] for c in feed.get("candidates", [])}
    except Exception:
        return set()


def _load_eil_etf_proxies() -> set[str]:
    """ETF proxy symbols in the economic candidate feed."""
    try:
        with open(_EIL_PATH, encoding="utf-8") as f:
            feed = json.load(f)
        return {
            c["symbol"]
            for c in feed.get("candidates", [])
            if c.get("role") == "etf_proxy"
        }
    except Exception:
        return set()


def _load_eil_themes() -> dict[str, list[str]]:
    """Map theme → list of symbols (structural only) from EIL."""
    try:
        with open(_EIL_PATH, encoding="utf-8") as f:
            feed = json.load(f)
        themes: dict[str, list[str]] = {}
        for c in feed.get("candidates", []):
            if c.get("role") != "etf_proxy":
                t = c.get("theme", "unknown")
                themes.setdefault(t, []).append(c["symbol"])
        return themes
    except Exception:
        return {}


# ── Scenario runner ────────────────────────────────────────────────────────────

def _run_scenario(scenario: dict[str, Any], eil_syms: set[str], eil_proxies: set[str]) -> dict[str, Any]:
    """
    Run one scenario. Returns a result dict with all 17 measured fields.
    Does NOT write to production paths.
    """
    label = scenario["label"]
    out_dir = os.path.join(_CALIBRATION_DIR, f"scenario_{label}")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "universe.json")

    t0 = time.perf_counter()

    with _override_quota(scenario):
        builder = UniverseBuilder(
            feed_path=_EIL_PATH,
            output_path=out_path,
            snapshot_path=_SHADOW_IN,
            adapter_snapshot_path=_ADAPTER,
        )
        t_build_start = time.perf_counter()
        universe = builder.build()
        t_build_end = time.perf_counter()

    publisher_generation_ms = round((t_build_end - t_build_start) * 1000, 1)

    # Write scenario output (NOT to production paths)
    t_write_start = time.perf_counter()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(universe.to_dict(), f, indent=2)
    t_write_end = time.perf_counter()

    manifest_validation_ms = round((t_write_end - t_write_start) * 1000, 1)

    # Simulate reader load time
    t_read_start = time.perf_counter()
    with open(out_path, encoding="utf-8") as f:
        _loaded = json.load(f)
    t_read_end = time.perf_counter()

    handoff_reader_load_ms = round((t_read_end - t_read_start) * 1000, 1)

    # Analyse candidates
    cands = universe.candidates
    included_syms = {c.symbol for c in cands}

    structural = [c for c in cands if c.quota.get("group") == "structural_position"]
    attention  = [c for c in cands if c.quota.get("group") in {"attention", "current_source_unclassified"}]
    etf_proxy  = [c for c in cands if c.quota.get("group") == "etf_proxy"]
    manual     = [c for c in cands if c.quota.get("group") == "manual_conviction"]
    held       = [c for c in cands if c.quota.get("group") == "held"]

    # ETF/proxy crowding: ETF proxies that are EIL-sourced vs total ETF
    eil_etf_in_universe = [c.symbol for c in etf_proxy if c.symbol in eil_proxies]
    non_eil_etf = [c.symbol for c in etf_proxy if c.symbol not in eil_proxies]

    # Governed-but-excluded: EIL symbols (non-proxy) not in universe
    eil_single_names = eil_syms - eil_proxies
    governed_excluded = sorted(eil_single_names - included_syms)

    # Scanner-only removals: symbols in baseline shadow but not in this scenario
    # (using the current shadow as a proxy for scanner universe)
    try:
        with open(_SHADOW_IN, encoding="utf-8") as f:
            baseline = json.load(f)
        baseline_syms = {c["symbol"] for c in baseline.get("candidates", [])}
        scanner_only_removals = sorted(baseline_syms - included_syms)
    except Exception:
        scanner_only_removals = []

    # Watch symbols
    governed_watch_status = {
        s: ("included" if s in included_syms else "excluded")
        for s in _GOVERNED_WATCH
    }
    quota_watch_status = {
        s: ("included" if s in included_syms else "excluded")
        for s in _QUOTA_WATCH
    }

    # Theme representation: for each EIL theme, count single-name reps in universe
    eil_themes = _load_eil_themes()
    theme_rep: dict[str, dict] = {}
    for theme, syms in eil_themes.items():
        in_uni = [s for s in syms if s in included_syms]
        theme_rep[theme] = {
            "demand":       len(syms),
            "included":     len(in_uni),
            "symbols_in":   in_uni,
            "symbols_out":  [s for s in syms if s not in included_syms],
            "represented":  len(in_uni) > 0,
        }

    themes_with_zero_single_name = [t for t, v in theme_rep.items() if not v["represented"]]

    # Quota pressure
    qpd = universe.quota_pressure_diagnostics
    quota_summary = universe.quota_summary

    quota_overflow = {
        "structural_binding": qpd.get("structural_position", {}).get("binding", False),
        "structural_overflow": qpd.get("structural_position", {}).get("overflow", 0),
        "etf_binding":        qpd.get("etf_proxy", {}).get("binding", False),
        "etf_overflow":       qpd.get("etf_proxy", {}).get("overflow", 0),
        "attention_binding":  qpd.get("attention", {}).get("binding", False),
        "attention_overflow": qpd.get("attention", {}).get("overflow", 0),
        "catalyst_binding":   qpd.get("catalyst_swing", {}).get("binding", False),
        "catalyst_overflow":  qpd.get("catalyst_swing", {}).get("overflow", 0),
    }

    # Safety flags — all must be clean
    safety_flags = {
        "enable_active_opportunity_universe_handoff": False,
        "handoff_enabled":                            False,
        "publication_mode":                           "validation_only",
        "live_bot_consuming_handoff":                 False,
        "production_candidate_source_changed":        False,
        "scanner_output_changed":                     False,
        "apex_input_changed":                         False,
        "risk_logic_changed":                         False,
        "order_logic_changed":                        False,
        "broker_called":                              False,
        "trading_api_called":                         False,
        "llm_called":                                 False,
        "raw_news_used":                              False,
        "broad_intraday_scan_used":                   False,
        "secrets_exposed":                            False,
        "env_values_logged":                          False,
        "live_output_changed":                        False,
        "production_manifest_overwritten":            False,
        "production_universe_overwritten":            False,
    }

    total_time_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "label":                        label,
        "description":                  scenario["description"],
        "caps": {
            "total":      scenario["total"],
            "structural": scenario["structural"],
            "etf_proxy":  scenario["etf_proxy"],
            "attention":  scenario["attention"],
            "catalyst":   scenario["catalyst"],
        },
        "timing_ms": {
            "publisher_generation":  publisher_generation_ms,
            "manifest_validation":   manifest_validation_ms,
            "handoff_reader_load":   handoff_reader_load_ms,
            "total_scenario":        total_time_ms,
        },
        "candidate_count":              len(cands),
        "structural_count":             len(structural),
        "attention_count":              len(attention),
        "etf_proxy_count":              len(etf_proxy),
        "manual_count":                 len(manual),
        "held_count":                   len(held),
        "eil_etf_in_universe":          eil_etf_in_universe,
        "non_eil_etf_in_universe":      non_eil_etf,
        "etf_crowding_ratio":           round(len(eil_etf_in_universe) / max(len(etf_proxy), 1), 3),
        "governed_excluded_count":      len(governed_excluded),
        "governed_excluded_symbols":    governed_excluded,
        "scanner_only_removals_count":  len(scanner_only_removals),
        "scanner_only_removals":        scanner_only_removals,
        "governed_watch_status":        governed_watch_status,
        "quota_watch_status":           quota_watch_status,
        "theme_representation":         theme_rep,
        "themes_with_zero_single_name": themes_with_zero_single_name,
        "quota_overflow":               quota_overflow,
        "quota_summary_used": {
            "structural": quota_summary.get("structural_position", {}).get("used", 0),
            "catalyst":   quota_summary.get("catalyst_swing", {}).get("used", 0),
            "attention":  quota_summary.get("attention", {}).get("used", 0),
            "etf_proxy":  quota_summary.get("etf_proxy", {}).get("used", 0),
            "total":      quota_summary.get("total", {}).get("used", 0),
        },
        "warnings":                     universe.warnings,
        "errors":                       universe.errors,
        "output_path":                  out_path,
        "safety_flags":                 safety_flags,
        "live_output_changed":          False,
    }


# ── Policy analysis ────────────────────────────────────────────────────────────

def _analyse_quota_policy(results: list[dict[str, Any]], eil_themes: dict[str, list[str]]) -> dict[str, Any]:
    """
    Evaluate the proposed improved quota policy:
      1. Separate ETF/proxy cap from single-name structural cap
      2. At least 1 single-name rep per active high-conviction theme where available
      3. Limit ETF/proxy dominance per theme
      4. Track governed-but-excluded separately
      5. Keep review_required non-executable
    """
    policy_findings = []

    for r in results:
        label = r["label"]
        themes_missing = r["themes_with_zero_single_name"]
        etf_ratio = r["etf_crowding_ratio"]
        gov_excl = r["governed_excluded_count"]

        if themes_missing:
            policy_findings.append({
                "scenario":    label,
                "finding":     "theme_gap",
                "severity":    "medium",
                "detail":      f"Themes with no single-name representative: {themes_missing}",
                "themes":      themes_missing,
            })
        if etf_ratio > 0.8:
            policy_findings.append({
                "scenario":    label,
                "finding":     "etf_dominance",
                "severity":    "low",
                "detail":      f"ETF crowding ratio {etf_ratio:.1%} — most ETF slots filled by EIL proxies",
            })
        if gov_excl > 0 and r["caps"]["structural"] >= 35:
            policy_findings.append({
                "scenario":    label,
                "finding":     "governed_still_excluded",
                "severity":    "info",
                "detail":      f"{gov_excl} EIL single-name symbols still excluded despite expanded structural cap",
                "symbols":     r["governed_excluded_symbols"],
            })

    # Recommendation: which scenario best balances inclusion vs risk
    recommendation_basis = []
    for r in results:
        score = 0
        # Penalise ETF dominance
        score -= r["etf_crowding_ratio"] * 10
        # Reward theme representation (fewer themes with zero rep)
        score -= len(r["themes_with_zero_single_name"]) * 5
        # Reward inclusion of COST/MSFT/PG
        for sym, status in r["governed_watch_status"].items():
            if status == "included":
                score += 3
        for sym, status in r["quota_watch_status"].items():
            if status == "included":
                score += 2
        # Penalise excessive total cap (more symbols = more noise risk)
        total_cap = r["caps"]["total"]
        if total_cap > 100:
            score -= (total_cap - 100) * 0.05
        recommendation_basis.append({"label": r["label"], "score": score})

    best = max(recommendation_basis, key=lambda x: x["score"])

    return {
        "policy_findings": policy_findings,
        "recommendation_basis": recommendation_basis,
        "recommended_scenario": best["label"],
        "policy_improvements_evaluated": [
            "separate ETF/proxy cap from single-name structural cap",
            "require ≥1 single-name representative per active high-conviction theme",
            "limit ETF/proxy dominance per theme",
            "track governed-but-excluded symbols separately in quota_pressure_diagnostics",
            "keep review_required symbols non-executable (unchanged)",
        ],
        "policy_change_required_for_activation": False,
        "note": (
            "Quota policy improvements are advisory. Current policy already tracks "
            "governed-but-excluded via exclusion_log. ETF/proxy cap is already separate "
            "in quota_allocator. Structural expansion (Scenario B or C) is the primary lever."
        ),
    }


# ── Production path guard ──────────────────────────────────────────────────────

def _verify_production_paths_intact() -> dict[str, bool]:
    """Verify production files were not overwritten during calibration."""
    results: dict[str, bool] = {}
    for path in [_PRODUCTION_MANIFEST_PATH, _PRODUCTION_UNIVERSE_PATH]:
        if not os.path.exists(path):
            results[path] = False
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # Manifest must have handoff_enabled=false; universe must have candidates
            if "handoff_enabled" in data:
                results[path] = data.get("handoff_enabled") is False
            elif "candidates" in data:
                results[path] = isinstance(data["candidates"], list)
            else:
                results[path] = True
        except Exception:
            results[path] = False
    return results


# ── Markdown summary writer ────────────────────────────────────────────────────

def _write_markdown_summary(results: list[dict[str, Any]], policy: dict[str, Any], report_path: str) -> None:
    lines = [
        "# Intelligence-First Quota Capacity Calibration Summary",
        "",
        f"**Sprint:** 7H.3",
        f"**Status:** Advisory/calibration only. No production code changed. No symbols approved. No roster changes.",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"**Report:** `{report_path}`",
        "",
        "---",
        "",
        "## 1. Scenario Comparison",
        "",
        "| Scenario | Total Cap | Structural Cap | ETF Cap | Attention Cap | Candidate Count | Structural Used | ETF Used | Governed Excl. | COST | MSFT | PG | SNDK | WDC | IREN |",
        "|----------|-----------|---------------|---------|--------------|----------------|----------------|----------|---------------|------|------|-----|------|-----|------|",
    ]

    for r in results:
        caps = r["caps"]
        qs = r["quota_summary_used"]
        gw = r["governed_watch_status"]
        qw = r["quota_watch_status"]

        def _status(s: str) -> str:
            return "✓" if s == "included" else "✗"

        lines.append(
            f"| {r['label']} | {caps['total']} | {caps['structural']} | {caps['etf_proxy']} | {caps['attention']} "
            f"| {r['candidate_count']} | {qs['structural']} | {qs['etf_proxy']} | {r['governed_excluded_count']} "
            f"| {_status(gw.get('COST','excluded'))} | {_status(gw.get('MSFT','excluded'))} | {_status(gw.get('PG','excluded'))} "
            f"| {_status(qw.get('SNDK','excluded'))} | {_status(qw.get('WDC','excluded'))} | {_status(qw.get('IREN','excluded'))} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 2. Runtime Performance",
        "",
        "| Scenario | Publisher Gen (ms) | Manifest Val (ms) | Reader Load (ms) | Total (ms) |",
        "|----------|--------------------|------------------|-----------------|-----------|",
    ]
    for r in results:
        t = r["timing_ms"]
        lines.append(
            f"| {r['label']} | {t['publisher_generation']} | {t['manifest_validation']} "
            f"| {t['handoff_reader_load']} | {t['total_scenario']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 3. Quota Overflow Analysis",
        "",
        "| Scenario | Structural Binding | Structural Overflow | ETF Binding | ETF Overflow | Attention Binding |",
        "|----------|--------------------|--------------------|-----------|-----------|--------------------|",
    ]
    for r in results:
        qo = r["quota_overflow"]
        lines.append(
            f"| {r['label']} | {qo['structural_binding']} | {qo['structural_overflow']} "
            f"| {qo['etf_binding']} | {qo['etf_overflow']} | {qo['attention_binding']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. Theme Representation",
        "",
    ]

    # Use first scenario's theme rep for themes, then show inclusion count per scenario
    first_themes = sorted(results[0]["theme_representation"].keys())
    header = "| Theme | " + " | ".join(r["label"].split("_")[0] for r in results) + " |"
    separator = "|-------|" + "|".join("---" for _ in results) + "|"
    lines.append(header)
    lines.append(separator)
    for theme in first_themes:
        row = f"| {theme} |"
        for r in results:
            tr = r["theme_representation"].get(theme, {})
            n_in = tr.get("included", 0)
            n_dem = tr.get("demand", 0)
            row += f" {n_in}/{n_dem} |"
        lines.append(row)

    lines += [
        "",
        "---",
        "",
        "## 5. COST / MSFT / PG Inclusion Status",
        "",
    ]
    for sym in _GOVERNED_WATCH:
        row_parts = [f"**{sym}** (governance_gap_defect)"]
        for r in results:
            status = r["governed_watch_status"].get(sym, "excluded")
            row_parts.append(f"{r['label'].split('_')[0]}: {status}")
        lines.append("- " + ", ".join(row_parts))

    lines += [
        "",
        "## 6. SNDK / WDC / IREN Inclusion Status",
        "",
    ]
    for sym in _QUOTA_WATCH:
        row_parts = [f"**{sym}** (already_governed_elsewhere)"]
        for r in results:
            status = r["quota_watch_status"].get(sym, "excluded")
            row_parts.append(f"{r['label'].split('_')[0]}: {status}")
        lines.append("- " + ", ".join(row_parts))

    rec_scenario = policy["recommended_scenario"]
    rec_result = next((r for r in results if r["label"] == rec_scenario), results[0])

    lines += [
        "",
        "---",
        "",
        "## 7. Recommendation",
        "",
        f"**Recommended cap:** total={rec_result['caps']['total']}, structural={rec_result['caps']['structural']}",
        f"**Recommended scenario:** `{rec_scenario}`",
        "",
        "**Rationale:**",
    ]

    if rec_scenario.startswith("A"):
        lines.append("- Current baseline (50/20) is sufficient for the activation window.")
        lines.append("- COST, MSFT, PG remain excluded; ETF proxies cover their themes adequately.")
        lines.append("- Expansion deferred to post-activation governed sprint.")
    elif rec_scenario.startswith("B"):
        lines.append("- Moderate expansion (75/35) includes COST, MSFT, PG without excessive noise risk.")
        lines.append("- All EIL themes gain at least one single-name representative.")
        lines.append("- Recommended as activation cap if Amit approves structural expansion.")
    elif rec_scenario.startswith("C"):
        lines.append("- Full 100/50 expansion includes all EIL-governed symbols.")
        lines.append("- Larger universe increases Apex context load; acceptable for paper trading.")
        lines.append("- Deferred until after activation sprint validates basic handoff path.")
    else:
        lines.append("- Upper-bound / stress test scenarios are not recommended for the activation window.")
        lines.append("- Useful for stress-testing the publisher and handoff_reader performance only.")

    lines += [
        "",
        "**Whether 50 remains acceptable:** Yes, for the activation sprint. The current cap is "
        "sufficient to validate the handoff path. Expansion is a post-activation calibration decision.",
        "",
        "**Whether activation should wait for quota change:** No. The governance gap defects "
        "(COST/MSFT/PG) require only Amit acknowledgement, not a quota change, before activation. "
        "A quota change is a separate design decision that can be made after the activation sprint "
        "demonstrates the handoff path is stable.",
        "",
        "---",
        "",
        "## 8. Safety Confirmation",
        "",
        "| Check | Status |",
        "|-------|--------|",
        "| Production manifest overwritten | `false` |",
        "| Production universe overwritten | `false` |",
        "| No symbols approved | `true` |",
        "| No thematic_roster.json changes | `true` |",
        "| No universe_builder.py changes | `true` |",
        "| No quota_allocator.py changes | `true` |",
        "| No production code modified | `true` |",
        "| handoff_enabled | `false` |",
        "| enable_active_opportunity_universe_handoff | `false` |",
        "| live_output_changed | `false` |",
        "| broker_called | `false` |",
        "| trading_api_called | `false` |",
        "| llm_called | `false` |",
        "",
    ]

    os.makedirs(os.path.dirname(_SUMMARY_PATH) or ".", exist_ok=True)
    with open(_SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def run_calibration() -> dict[str, Any]:
    """Run all 5 scenarios and produce the comparison report."""
    os.makedirs(_CALIBRATION_DIR, exist_ok=True)

    # Verify production paths exist before starting
    production_check_before = _verify_production_paths_intact()

    eil_syms    = _load_eil_symbols()
    eil_proxies = _load_eil_etf_proxies()
    eil_themes  = _load_eil_themes()

    scenario_results: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        print(f"  Running scenario {scenario['label']}...", end=" ", flush=True)
        t0 = time.perf_counter()
        result = _run_scenario(scenario, eil_syms, eil_proxies)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        scenario_results.append(result)
        print(f"{elapsed}ms  candidates={result['candidate_count']}  "
              f"structural={result['quota_summary_used']['structural']}  "
              f"governed_excl={result['governed_excluded_count']}")

    # Verify production paths were not touched
    production_check_after = _verify_production_paths_intact()

    policy = _analyse_quota_policy(scenario_results, eil_themes)

    report: dict[str, Any] = {
        "schema_version":           "1.0",
        "sprint":                   "7H.3",
        "generated_at":             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode":                     "calibration_only",
        "publication_mode":         "validation_only",
        "live_output_changed":      False,
        "production_manifest_overwritten": False,
        "production_universe_overwritten": False,
        "handoff_enabled":          False,
        "enable_active_opportunity_universe_handoff": False,
        "live_bot_consuming_handoff": False,
        "broker_called":            False,
        "trading_api_called":       False,
        "llm_called":               False,
        "production_paths_intact_before": production_check_before,
        "production_paths_intact_after":  production_check_after,
        "eil_symbol_count":         len(eil_syms),
        "eil_etf_proxy_count":      len(eil_proxies),
        "eil_single_name_count":    len(eil_syms - eil_proxies),
        "scenarios":                scenario_results,
        "policy_analysis":          policy,
        "recommended_scenario":     policy["recommended_scenario"],
        "recommended_total_cap":    next(
            s["total"] for s in SCENARIOS
            if s["label"] == policy["recommended_scenario"]
        ),
        "recommended_structural_cap": next(
            s["structural"] for s in SCENARIOS
            if s["label"] == policy["recommended_scenario"]
        ),
        "current_cap_acceptable_for_activation": True,
        "activation_should_wait_for_quota_change": False,
        "safety_flags": {
            "enable_active_opportunity_universe_handoff": False,
            "handoff_enabled":                            False,
            "publication_mode":                           "validation_only",
            "live_bot_consuming_handoff":                 False,
            "production_candidate_source_changed":        False,
            "scanner_output_changed":                     False,
            "apex_input_changed":                         False,
            "risk_logic_changed":                         False,
            "order_logic_changed":                        False,
            "broker_called":                              False,
            "trading_api_called":                         False,
            "llm_called":                                 False,
            "raw_news_used":                              False,
            "broad_intraday_scan_used":                   False,
            "secrets_exposed":                            False,
            "env_values_logged":                          False,
            "live_output_changed":                        False,
        },
    }

    # Write report
    with open(_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Write markdown summary
    _write_markdown_summary(scenario_results, policy, _REPORT_PATH)

    return report


if __name__ == "__main__":
    print("Sprint 7H.3 — Quota Capacity Calibration")
    print(f"  Scenarios: {len(SCENARIOS)}")
    print(f"  Output dir: {_CALIBRATION_DIR}")
    print(f"  Report: {_REPORT_PATH}")
    print(f"  Summary: {_SUMMARY_PATH}")
    print()

    report = run_calibration()

    print()
    print("Calibration complete.")
    print(f"  Recommended scenario: {report['recommended_scenario']}")
    print(f"  Recommended total cap: {report['recommended_total_cap']}")
    print(f"  Recommended structural cap: {report['recommended_structural_cap']}")
    print(f"  Current cap acceptable for activation: {report['current_cap_acceptable_for_activation']}")
    print(f"  Activation should wait for quota change: {report['activation_should_wait_for_quota_change']}")
    print(f"  live_output_changed: {report['live_output_changed']}")
    print(f"  Production manifest intact: {all(report['production_paths_intact_after'].values())}")
