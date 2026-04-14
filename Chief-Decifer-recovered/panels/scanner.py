"""
Scanner — detects which research features have been implemented in the decifer repo.
Runs when the 'Update Dashboard' button is clicked.
Reads git history + code, updates research state files, writes feature-shipped entries.

This is the ONE exception to Chief's read-only rule: it updates its own
state/research/*.json status fields and writes state/specs/*.json for detected
features. It still never writes code, generates tests, or runs agent loops.
"""

import ast
import difflib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from config import DECIFER_REPO_PATH, RESEARCH_DIR, SPECS_DIR


# ── Feature detection signatures ────────────────────────────────────────────
# Maps research feature keywords → what to look for in the repo.
# Each entry: list of (search_type, pattern) tuples.
# search_type: "function" (AST), "git" (commit message), "string" (grep in code)

FEATURE_SIGNATURES = {
    "Multi-Timeframe Signal Confirmation": [
        ("function", "timeframe_confluence"),
        ("function", "multi_timeframe"),
        ("string", "timeframe_confluence"),
        ("string", "multi_timeframe"),
        ("git", "multi.?timeframe"),
        ("git", "timeframe.*confirm"),
        ("git", "confluence"),
    ],
    "Dynamic Volatility-Based Position Sizing": [
        ("function", "volatility_adjusted_size"),
        ("function", "vol_adjusted"),
        ("string", "volatility_adjusted"),
        ("string", "vol_ceiling"),
        ("string", "atr_cap"),
        ("git", "volatil.*siz"),
        ("git", "vol.*position"),
        ("git", "dynamic.*siz"),
    ],
    "Claude Sentiment Scoring Pipeline": [
        ("function", "sentiment_score"),
        ("function", "news_sentiment"),
        ("string", "sentiment_score"),
        ("string", "dimension.*10"),
        ("git", "sentiment.*scor"),
        ("git", "10th.*dimension"),
    ],
    "Walk-Forward Backtesting": [
        ("function", "walk_forward"),
        ("string", "walk_forward"),
        ("git", "walk.?forward"),
    ],
    "Correlation-Aware Position Management": [
        ("function", "correlation_check"),
        ("function", "correlation_guard"),
        ("string", "correlation_check"),
        ("string", "correlation_guard"),
        ("git", "correlation.*position"),
        ("git", "correlation.*guard"),
    ],
    "HMM Regime Detection": [
        ("function", "regime_detect"),
        ("function", "hmm_regime"),
        ("string", "HiddenMarkov"),
        ("string", "hmm_regime"),
        ("string", "regime_detect"),
        ("git", "hmm"),
        ("git", "regime.*detect"),
    ],
    "Order Flow Imbalance Detection": [
        ("function", "order_flow_imbalance"),
        ("function", "order_flow"),
        ("string", "order_flow_imbalance"),
        ("string", "order_flow"),
        ("git", "order.?flow"),
    ],
    "Monte Carlo Stress Testing": [
        ("function", "monte_carlo"),
        ("function", "stress_test"),
        ("string", "monte_carlo"),
        ("string", "stress_test"),
        ("git", "monte.?carlo"),
        ("git", "stress.*test"),
    ],
}


_PATTERN_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "with", "to", "of", "in", "on",
    "at", "by", "from", "via", "into", "over", "under", "per", "using",
    "aware", "based", "dynamic", "feature", "signal", "model", "data",
    "approach", "method", "technique", "detection", "management",
    "generation", "analysis", "system",
})


def _derive_fallback_patterns(feature_name: str) -> list:
    """Auto-derive search patterns from a feature title when no explicit
    signature is defined. Returns (search_type, pattern) tuples.
    Only uses words >= 5 chars that are not stopwords, capped at 3 keywords."""
    words = re.findall(r"[a-zA-Z]{5,}", feature_name)
    keywords = [w for w in words if w.lower() not in _PATTERN_STOPWORDS][:3]
    return [pat for kw in keywords for pat in [("string", kw.lower()), ("git", kw.lower())]]


def _get_repo_functions():
    """Parse all .py files in the repo and return set of function/method names."""
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return set()
    funcs = set()
    for pyfile in DECIFER_REPO_PATH.glob("**/*.py"):
        # Skip venv, __pycache__, etc.
        parts = pyfile.parts
        if any(p.startswith(".") or p in ("venv", "__pycache__", "node_modules") for p in parts):
            continue
        try:
            source = pyfile.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.add(node.name)
        except Exception:
            pass
    return funcs


def _search_code(pattern):
    """Grep for a string pattern in all .py files in the repo."""
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return False
    for pyfile in DECIFER_REPO_PATH.glob("**/*.py"):
        parts = pyfile.parts
        if any(p.startswith(".") or p in ("venv", "__pycache__", "node_modules") for p in parts):
            continue
        try:
            text = pyfile.read_text(encoding="utf-8", errors="ignore")
            if pattern.lower() in text.lower():
                return True
        except Exception:
            pass
    return False


def _search_git(pattern):
    """Search recent git commit messages for a regex pattern."""
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-50", f"--grep={pattern}", "-i", "--extended-regexp"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _check_feature(feature_name, repo_functions):
    """Check if a feature has been implemented.
    Returns (found, evidence) where found is:
      True  — signature matched in code or git history
      False — explicit signature exists but no match found
      None  — no signature defined; fallback patterns tried but result is uncertain
    """
    sigs = FEATURE_SIGNATURES.get(feature_name)  # None when key absent

    if sigs is None:
        # No explicit signature — try auto-derived fallback patterns
        for search_type, pattern in _derive_fallback_patterns(feature_name):
            if search_type == "string" and _search_code(pattern):
                return None, f"Fallback match (uncertain): code contains '{pattern}'"
            if search_type == "git" and _search_git(pattern):
                return None, f"Fallback match (uncertain): git history matches '{pattern}'"
        return None, ""

    for search_type, pattern in sigs:
        if search_type == "function":
            # Word-boundary match — prevents 'vol' matching 'evolving'
            matches = [
                f for f in repo_functions
                if re.search(r"(?<![a-z])" + re.escape(pattern.lower()) + r"(?![a-z])", f.lower())
            ]
            if matches:
                return True, f"Function found: {matches[0]}()"
        elif search_type == "string":
            if _search_code(pattern):
                return True, f"Code contains: {pattern}"
        elif search_type == "git":
            if _search_git(pattern):
                return True, f"Git history match: {pattern}"

    return False, ""


def run_scan():
    """
    Scan the decifer repo and update research/spec state files.
    Returns a summary dict: {scanned, detected, updated, details}.
    """
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return {
            "scanned": 0, "detected": 0, "updated": 0,
            "details": ["Cannot access decifer repo — check DECIFER_REPO_PATH in .env"],
        }

    # Pre-load all function names from the repo (one pass)
    repo_functions = _get_repo_functions()

    details = []
    scanned = 0
    detected = 0
    updated = 0

    # Load all research files
    if not RESEARCH_DIR.exists():
        return {"scanned": 0, "detected": 0, "updated": 0, "details": ["No research directory"]}

    for research_file in sorted(RESEARCH_DIR.glob("*.json")):
        try:
            data = json.loads(research_file.read_text())
        except Exception:
            continue

        if data.get("status") == "complete":
            details.append(f"Skipped {research_file.name} — already marked complete")
            continue

        findings = data.get("findings", [])
        any_found = False
        all_found = True
        found_features = []

        for finding in findings:
            scanned += 1
            feature_name = finding.get("feature", finding.get("title", ""))
            found, evidence = _check_feature(feature_name, repo_functions)

            if found is True:
                detected += 1
                any_found = True
                found_features.append(feature_name)
                details.append(f"FOUND: {feature_name} — {evidence}")

                # Write a spec entry for the detected feature if it doesn't exist
                _write_spec_for_feature(finding, evidence)
            elif found is None:
                all_found = False
                if evidence:
                    details.append(f"Unknown (fallback): {feature_name} — {evidence}")
                else:
                    details.append(f"Unknown (no signature): {feature_name}")
            else:
                all_found = False
                details.append(f"Not found: {feature_name}")

        # If ALL features in a research report are found, mark the whole report complete
        if all_found and findings:
            data["status"] = "complete"
            data["completed_date"] = datetime.now().strftime("%Y-%m-%d")
            research_file.write_text(json.dumps(data, indent=2))
            updated += 1
            details.append(f"Marked {research_file.name} as COMPLETE (all {len(findings)} features found)")
        elif any_found:
            # Partial — note which ones are done
            details.append(f"Partial: {len(found_features)}/{len(findings)} features found in {research_file.name}")

    return {
        "scanned": scanned,
        "detected": detected,
        "updated": updated,
        "details": details,
    }


def _write_spec_for_feature(finding, evidence):
    """Write a spec JSON file for a detected feature (if it doesn't already exist)."""
    feature_name = finding.get("feature", finding.get("title", ""))
    if not feature_name:
        return

    # Slug the feature name for a filename
    slug = re.sub(r"[^a-z0-9]+", "_", feature_name.lower()).strip("_")
    spec_path = SPECS_DIR / f"{slug}.json"

    if spec_path.exists():
        # Already have a spec — update status to complete if not already
        try:
            existing = json.loads(spec_path.read_text())
            if existing.get("status") != "complete":
                existing["status"] = "complete"
                existing["completed_date"] = datetime.now().strftime("%Y-%m-%d")
                existing["evidence"] = evidence
                spec_path.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass
        return

    spec = {
        "id": slug,
        "title": feature_name,
        "summary": finding.get("summary", ""),
        "status": "complete",
        "priority": {1: "P0", 2: "P1", 3: "P2"}.get(finding.get("tier", 2), "P2"),
        "module": finding.get("module", ""),
        "subsystem": finding.get("subsystem", ""),
        "expected_impact": finding.get("expected_impact", ""),
        "dev_days": finding.get("dev_days", 0),
        "designed_date": finding.get("date", ""),
        "completed_date": datetime.now().strftime("%Y-%m-%d"),
        "evidence": evidence,
        "source": "Auto-detected by scanner from research findings",
    }

    try:
        SPECS_DIR.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(json.dumps(spec, indent=2))
    except Exception:
        pass
