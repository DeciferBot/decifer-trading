#!/usr/bin/env python3
"""
build_brain.py — Decifer Living Brain Generator

Covers every Python file in the project. Three core outputs per run:

  Bug Radar   — cross-module risks most likely to cause real loss or failure
  Build Next  — highest-alpha features, grounded in actual code state
  Kill List   — files adding complexity without serving any north star pillar

Architecture:
  1. AST parse all .py files  → imports, functions, classes, line count
  2. git blob hash lookup      → cache key (only re-annotate changed files)
  3. Claude Sonnet annotation  → per-file: group, desc, good/bad/ugly, pillars, bug risk
  4. Claude Opus synthesis     → cross-file: bug radar, build next, kill list
  5. HTML generation           → decifer-brain.html (vis-network graph + three panels)

Usage:
    python build_brain.py           # normal run (uses cache)
    python build_brain.py --force   # re-annotate all files
    python build_brain.py --fast    # skip Opus synthesis (Sonnet only)
    python build_brain.py --no-open # don't auto-open browser

Cache:  brain_cache.json  (git-blob keyed — re-annotates only changed files)
Output: decifer-brain.html
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic

# Load .env so ANTHROPIC_API_KEY is available (mirrors config.py pattern)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)
except ImportError:
    pass

# ─── Config ───────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent
CACHE_FILE   = ROOT / "brain_cache.json"
OUTPUT_FILE  = ROOT / "decifer-brain.html"
NORTH_STAR   = ROOT / "chief-decifer/state/specs/feat-north-star.json"
BACKLOG      = ROOT / "chief-decifer/state/backlog.json"

MODEL_ANNOTATE   = "claude-sonnet-4-6"
MODEL_SYNTHESIZE = "claude-opus-4-6"
MAX_WORKERS      = 5   # parallel Sonnet calls

EXCLUDE_DIRS  = {".claude", "__pycache__", "tests", ".git", "logs", "docs", "roadmap"}
EXCLUDE_FILES = {"build_brain.py"}   # don't annotate the brain generator itself

GROUPS = {
    "orchestration": {"label": "Orchestration",     "color": "#6366f1"},
    "data":          {"label": "Data / Scanning",    "color": "#06b6d4"},
    "signal":        {"label": "Signal Engine",      "color": "#f59e0b"},
    "intelligence":  {"label": "Intelligence",       "color": "#8b5cf6"},
    "execution":     {"label": "Execution",          "color": "#ef4444"},
    "risk":          {"label": "Risk",               "color": "#f97316"},
    "learning":      {"label": "Learning",           "color": "#10b981"},
    "infra":         {"label": "Infrastructure",     "color": "#64748b"},
    "zombie":        {"label": "Zombie / No Pillar", "color": "#7f1d1d"},
}


# ─── File discovery ────────────────────────────────────────────────────────────
def get_py_files() -> list[Path]:
    files = []
    for entry in sorted(ROOT.rglob("*.py")):
        rel   = entry.relative_to(ROOT)
        parts = rel.parts
        if any(p in EXCLUDE_DIRS for p in parts[:-1]):
            continue
        if entry.name in EXCLUDE_FILES:
            continue
        try:
            if entry.stat().st_size < 50:   # skip empty/stub files
                continue
        except OSError:
            continue
        files.append(entry)
    return files


# ─── Git blob hashes (cache keys) ─────────────────────────────────────────────
def get_blob_hashes() -> dict[str, str]:
    """Returns {relative_path: blob_hash} for every tracked file."""
    hashes: dict[str, str] = {}
    try:
        out = subprocess.run(
            ["git", "ls-files", "-s", "--full-name"],
            capture_output=True, text=True, cwd=ROOT, timeout=10
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                hashes[parts[3]] = parts[1]
    except Exception:
        pass
    return hashes


def file_cache_key(filepath: Path, blob_hashes: dict[str, str]) -> str:
    rel = str(filepath.relative_to(ROOT))
    if rel in blob_hashes:
        return blob_hashes[rel]
    # Fallback for untracked files: use mtime + size
    st = filepath.stat()
    return f"mtime-{int(st.st_mtime)}-{st.st_size}"


# ─── AST analysis ─────────────────────────────────────────────────────────────
def ast_analyze(filepath: Path) -> dict:
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {"lines": 0, "imports": [], "functions": [], "classes": [],
                "docstring": "", "source_preview": ""}

    lines = source.count("\n") + 1
    result: dict = {
        "lines": lines,
        "imports": [],
        "functions": [],
        "classes": [],
        "docstring": "",
        "source_preview": source[:4000],
    }

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return result

    # Module docstring
    if (tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(getattr(tree.body[0], "value", None), ast.Constant)):
        doc = tree.body[0].value.value
        if isinstance(doc, str):
            result["docstring"] = doc[:600]

    # Top-level imports, functions, classes
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            result["imports"].append(node.module.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append(node.name)
        elif isinstance(node, ast.ClassDef):
            result["classes"].append(node.name)

    result["imports"] = sorted(set(result["imports"]))
    return result


# ─── Claude Sonnet annotation ──────────────────────────────────────────────────
ANNOTATION_PROMPT = """\
You are analyzing a Python file in Decifer — a reasoning-based auto trader.

NORTH STAR: A system that enters with a stated thesis, monitors that thesis against evolving \
market conditions, exits when the thesis breaks (not when a price level is hit), records the \
reason for every decision, and learns from the correlation between reasoning and outcomes.

FIVE PILLARS (assign pillars[] to the numbers this file *directly* serves):
1. Entry with thesis    — every entry has a stated, falsifiable reason
2. Hold with monitoring — open positions re-evaluated against thesis each scan cycle
3. Exit with reason     — exits driven by thesis breach, not price levels
4. Record everything    — every decision has a logged, structured reason
5. Learn from outcomes  — logged reasons compared against outcomes; patterns reinforced

FILE: {filename}
LINES: {lines}
DOCSTRING: {docstring}
FUNCTIONS: {functions}
CLASSES: {classes}
IMPORTS: {imports}

SOURCE (first 3000 chars):
---
{source}
---

STRICT RULES for your JSON output:
- NEVER include code snippets, variable names with quotes, or backticks inside string values.
- All string values must use plain prose only — no double-quotes inside strings.
- Use apostrophes (') inside strings if you need quotes, never double-quotes.
- Respond with ONLY a valid JSON object — no markdown fences, no commentary, nothing else.

{{
  "group": "orchestration|data|signal|intelligence|execution|risk|learning|infra|zombie",
  "desc": "1-2 plain-prose sentences about what this file does in the live trading system",
  "fn": ["top 5 public functions or classes — most important first — with () suffix"],
  "good": ["up to 3 specific things done well with consequences"],
  "bad": ["up to 3 real problems with real failure-mode consequences — plain prose only"],
  "ugly": ["up to 2 tech-debt items or historical issues — plain prose only"],
  "cfg": ["config keys or env vars this file reads"],
  "files": ["data files read or written"],
  "pillars": [1, 2],
  "kill_candidate": false,
  "kill_reason": null,
  "bug_risk": "critical|high|medium|low",
  "bug_risk_reason": "specific failure mode that could cause real loss or system failure"
}}"""


def _sanitize_json_strings(text: str) -> str:
    """Replace literal control chars (newlines, tabs) inside JSON strings with spaces.
    Control chars are valid in JSON only when escaped — raw newlines in strings break parsing."""
    result = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == "\\":
            result.append(ch)
            escape_next = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string and ch in ("\n", "\r", "\t"):
            result.append(" ")   # replace illegal control char with space
        else:
            result.append(ch)
    return "".join(result)


def _extract_json(text: str) -> dict:
    """Extract and parse JSON from model output, tolerating markdown fences and raw newlines."""
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # Find outermost { ... }
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    # Sanitize control chars inside strings
    text = _sanitize_json_strings(text)
    return json.loads(text)


FALLBACK_PROMPT = """\
Analyze this Python file and return ONLY a JSON object. \
No code, no backticks, no double-quotes inside string values (use apostrophes if needed).

File: {filename} ({lines} lines)
Functions: {functions}
Imports: {imports}

Return exactly this JSON (fill in the values):
{{"group":"orchestration","desc":"plain prose description","fn":[],"good":[],"bad":[],\
"ugly":[],"cfg":[],"files":[],"pillars":[],"kill_candidate":false,"kill_reason":null,\
"bug_risk":"low","bug_risk_reason":"plain prose"}}

Valid groups: orchestration data signal intelligence execution risk learning infra zombie"""


def annotate_file(client: anthropic.Anthropic, filepath: Path, ast_data: dict) -> dict:
    _fallback = {
        "group": "infra",
        "desc": "(annotation unavailable)",
        "fn": ast_data["functions"][:5],
        "good": [], "bad": [], "ugly": [],
        "cfg": [], "files": [],
        "pillars": [],
        "kill_candidate": False, "kill_reason": None,
        "bug_risk": "low", "bug_risk_reason": "annotation unavailable",
    }

    def _call(prompt: str, max_tokens: int) -> dict:
        r = client.messages.create(
            model=MODEL_ANNOTATE,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_json(r.content[0].text)

    # First attempt — full prompt
    prompt = ANNOTATION_PROMPT.format(
        filename=filepath.name,
        lines=ast_data["lines"],
        docstring=(ast_data["docstring"] or "(none)")[:300],
        functions=", ".join(ast_data["functions"][:20]) or "(none)",
        classes=", ".join(ast_data["classes"][:10]) or "(none)",
        imports=", ".join(ast_data["imports"][:30]) or "(none)",
        source=ast_data["source_preview"][:3000],
    )
    try:
        return _call(prompt, 800)
    except Exception:
        pass  # try fallback

    # Fallback — minimal prompt, no source
    fallback = FALLBACK_PROMPT.format(
        filename=filepath.name,
        lines=ast_data["lines"],
        functions=", ".join(ast_data["functions"][:15]) or "(none)",
        imports=", ".join(ast_data["imports"][:20]) or "(none)",
    )
    try:
        return _call(fallback, 400)
    except Exception:
        pass

    # Nuclear fallback — system prompt enforces JSON mode
    try:
        r = client.messages.create(
            model=MODEL_ANNOTATE,
            max_tokens=300,
            system='You output only valid RFC 8259 JSON. No markdown. No prose. No code snippets in string values. Use only plain text descriptions.',
            messages=[{"role": "user", "content":
                f'Return a JSON object for Python file "{filepath.name}" ({ast_data["lines"]} lines). '
                f'Functions: {", ".join(ast_data["functions"][:10]) or "none"}. '
                'Fields: group (pick one: orchestration data signal intelligence execution risk learning infra zombie), '
                'desc (1 sentence), fn (list), good (list), bad (list), ugly (list), '
                'cfg (list), files (list), pillars (list of 1-5), kill_candidate (bool), '
                'kill_reason (str or null), bug_risk (critical/high/medium/low), bug_risk_reason (str).'}],
        )
        return _extract_json(r.content[0].text)
    except Exception as exc:
        print(f"  [!] annotation failed for {filepath.name}: {exc}")
        return _fallback


# ─── Claude Opus synthesis ────────────────────────────────────────────────────
SYNTHESIS_PROMPT = """\
You are the senior architect of Decifer — a reasoning-based auto trader.

NORTH STAR: Enter with thesis → monitor thesis → exit when thesis breaks → \
record every reason → learn from outcomes.

BACKLOG STATUS:
{backlog_summary}

ALL MODULE ANNOTATIONS ({count} files, {total_lines:,} total lines):
{summaries}

Synthesize the above into three components. Respond with ONLY valid JSON — no markdown.
{{
  "bug_radar": [
    {{
      "rank": 1,
      "title": "<short title>",
      "files": ["<file1.py>", "<file2.py>"],
      "risk": "<specific failure scenario that could cause real loss or system failure — \
be precise about the mechanism>",
      "severity": "<critical|high|medium>"
    }}
  ],
  "build_next": [
    {{
      "rank": 1,
      "feature": "<feature name>",
      "backlog_item": "<BACK-011 or null>",
      "why": "<why this generates the most alpha or unblocks the most downstream work>",
      "files_needed": ["<files to create or extend>"],
      "code_gap": "<what specifically is missing in the code right now>"
    }}
  ],
  "kill_list": [
    {{
      "rank": 1,
      "file": "<filename.py>",
      "lines": <line count>,
      "reason": "<specific reason it serves no north star pillar>",
      "safe_to_delete": <true|false>,
      "caveat": "<if not safe: what to verify first. null if safe>"
    }}
  ]
}}

Rules:
- bug_radar: up to 8 items, ordered by severity. Focus on CROSS-MODULE risks, not per-file issues.
- build_next: up to 5 items. Ground each in the backlog phases and actual code gaps observed.
- kill_list: up to 12 items. Files that add complexity, noise, or drift without serving a pillar.
  Include dashboard_v2.py if a v1 still exists, version-suffixed files, unused entrypoints, etc."""


def synthesize(client: anthropic.Anthropic, nodes: list[dict],
               total_lines: int, fast: bool) -> dict | None:
    if fast:
        print("  [skip] synthesis (--fast mode)")
        return None

    # Load backlog summary
    backlog_summary = "(unavailable)"
    if BACKLOG.exists():
        try:
            bl = json.loads(BACKLOG.read_text())
            phases = bl.get("wip_policy", {}).get("phases", {})
            items  = bl.get("items", [])
            pending = [i for i in items if i.get("status") == "pending"]
            shipped = [i for i in items if i.get("status") == "shipped"]
            backlog_summary = (
                f"Shipped: {len(shipped)} items. Pending: {len(pending)} items.\n"
                + "\n".join(f"  Phase {k}: {v}" for k, v in phases.items())
                + "\nPending items:\n"
                + "\n".join(f"  {i['id']}: {i['title']}" for i in pending[:15])
            )
        except Exception:
            pass

    # Build per-file summaries (compact, not full source)
    summaries = []
    for nd in nodes:
        pillars = nd.get("pillars", [])
        summary = (
            f"{nd['label']} ({nd.get('lines', 0)} lines, group={nd['group']}, "
            f"bug_risk={nd.get('bug_risk','?')}, pillars={pillars}, "
            f"kill_candidate={nd.get('kill_candidate', False)})\n"
            f"  desc: {nd.get('desc', '')}\n"
            f"  bad:  {'; '.join(nd.get('bad', []))}\n"
            f"  bug:  {nd.get('bug_risk_reason', '')}"
        )
        summaries.append(summary)

    prompt = SYNTHESIS_PROMPT.format(
        backlog_summary=backlog_summary,
        count=len(nodes),
        total_lines=total_lines,
        summaries="\n\n".join(summaries),
    )

    print(f"  Calling Opus for synthesis ({len(nodes)} files)...")
    try:
        response = client.messages.create(
            model=MODEL_SYNTHESIZE,
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        # Debug: show stop reason
        if response.stop_reason != "end_turn":
            print(f"  [!] synthesis stop_reason={response.stop_reason} — output may be truncated")
        return _extract_json(raw)
    except Exception as exc:
        print(f"  [!] synthesis failed: {exc}")
        return None


# ─── Edge construction ────────────────────────────────────────────────────────
def build_edges(nodes: list[dict], ast_map: dict[str, dict]) -> list[dict]:
    """Build import-dependency edges between project files."""
    # Map stem → node id
    stem_to_id = {nd["id"]: nd["id"] for nd in nodes}   # id is the stem
    all_stems  = {nd["id"] for nd in nodes}

    edges = []
    seen: set[tuple[str, str]] = set()
    for nd in nodes:
        ast_data = ast_map.get(nd["label"], {})
        for imp in ast_data.get("imports", []):
            if imp in all_stems and imp != nd["id"]:
                key = (nd["id"], imp)
                if key not in seen:
                    seen.add(key)
                    edges.append({"from": nd["id"], "to": imp})
    return edges


# ─── HTML generation ──────────────────────────────────────────────────────────
def render_html(nodes: list[dict], edges: list[dict],
                synthesis: dict | None, generated_at: str) -> str:

    nodes_js    = json.dumps(nodes, indent=2, ensure_ascii=False)
    edges_js    = json.dumps(edges, indent=2, ensure_ascii=False)
    groups_js   = json.dumps(GROUPS, indent=2)
    synth_js    = json.dumps(synthesis or {}, indent=2, ensure_ascii=False)
    total_files = len(nodes)
    total_lines = sum(n.get("lines", 0) for n in nodes)
    kill_count  = sum(1 for n in nodes if n.get("kill_candidate"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Decifer Brain</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --bg: #0d1117; --surface: #161b22; --surface2: #21262d;
  --border: #30363d; --text: #c9d1d9; --muted: #8b949e;
  --blue: #58a6ff; --green: #3fb950; --red: #f85149;
  --yellow: #d29922; --orange: #f97316; --purple: #8b5cf6;
}}
html, body {{ height: 100%; background: var(--bg); color: var(--text);
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 13px; }}
#app {{ display: flex; flex-direction: column; height: 100%; }}

/* Header */
header {{ display: flex; align-items: center; gap: 12px; padding: 10px 16px;
          background: var(--surface); border-bottom: 1px solid var(--border);
          flex-shrink: 0; }}
.logo {{ font-size: 15px; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 6px; }}
.accent {{ color: var(--blue); }}
.hstats {{ display: flex; gap: 16px; margin-left: auto; }}
.hstat {{ font-size: 11px; color: var(--muted); }}
.hstat b {{ color: var(--text); }}
.hctrls {{ display: flex; gap: 8px; }}
.hbtn {{ padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px;
          background: var(--surface2); color: var(--muted); cursor: pointer; font-size: 11px; }}
.hbtn:hover {{ color: var(--text); border-color: var(--blue); }}
.gen-time {{ font-size: 10px; color: #484f58; }}

/* Main layout */
#main {{ display: flex; flex: 1; overflow: hidden; }}

/* Sidebar */
#sidebar {{ width: 240px; flex-shrink: 0; background: var(--surface);
            border-right: 1px solid var(--border); overflow-y: auto;
            display: flex; flex-direction: column; gap: 0; }}
.sb-section {{ border-bottom: 1px solid var(--border); }}
.sb-head {{ padding: 10px 14px; font-size: 10px; font-weight: 600;
             text-transform: uppercase; letter-spacing: .6px; color: var(--muted);
             display: flex; align-items: center; justify-content: space-between;
             cursor: pointer; user-select: none; }}
.sb-head:hover {{ color: var(--text); }}
.sb-head .toggle {{ font-size: 9px; transition: transform .2s; }}
.sb-head.collapsed .toggle {{ transform: rotate(-90deg); }}
.sb-body {{ padding: 8px 14px 12px; }}
.sb-body.collapsed {{ display: none; }}

/* Search */
#search {{ width: 100%; padding: 6px 10px; background: var(--surface2);
           border: 1px solid var(--border); border-radius: 6px; color: var(--text);
           font-size: 12px; outline: none; }}
#search:focus {{ border-color: var(--blue); }}
#search::placeholder {{ color: #484f58; }}

/* Filter items */
.fi {{ display: flex; align-items: center; gap: 7px; padding: 3px 0; }}
.fi-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
.fi-lbl {{ flex: 1; font-size: 11px; color: var(--muted); cursor: pointer; }}
.fi input:checked ~ .fi-lbl {{ color: var(--text); }}
.fi-cnt {{ font-size: 10px; color: #484f58; font-family: monospace; }}
.fi input {{ width: 13px; height: 13px; cursor: pointer; accent-color: var(--blue); }}

/* Synthesis panels */
.syn-item {{ margin-bottom: 10px; padding: 8px 10px;
             background: var(--surface2); border-radius: 6px; border-left: 3px solid var(--border); }}
.syn-item.critical {{ border-left-color: #f85149; }}
.syn-item.high {{ border-left-color: #f97316; }}
.syn-item.medium {{ border-left-color: #d29922; }}
.syn-item.kill {{ border-left-color: #7f1d1d; }}
.syn-item.next {{ border-left-color: var(--blue); }}
.syn-rank {{ font-size: 9px; color: var(--muted); font-family: monospace; text-transform: uppercase; }}
.syn-title {{ font-size: 11px; font-weight: 600; color: var(--text); margin: 3px 0; }}
.syn-body {{ font-size: 10px; color: var(--muted); line-height: 1.55; }}
.syn-files {{ display: flex; flex-wrap: wrap; gap: 3px; margin-top: 4px; }}
.syn-file {{ font-family: monospace; font-size: 9px; padding: 1px 5px;
              background: #161b22; border: 1px solid var(--border); border-radius: 3px;
              color: var(--blue); cursor: pointer; }}
.syn-file:hover {{ border-color: var(--blue); }}
.syn-lines {{ font-family: monospace; font-size: 9px; color: #484f58; margin-top: 2px; }}
.syn-empty {{ font-size: 11px; color: #484f58; font-style: italic; padding: 4px 0; }}
.sev-badge {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9px;
               font-weight: 600; text-transform: uppercase; letter-spacing: .4px; margin-left: 4px; }}
.sev-critical {{ background: rgba(248,81,73,.15); color: #f85149; }}
.sev-high {{ background: rgba(249,115,22,.15); color: #f97316; }}
.sev-medium {{ background: rgba(210,153,34,.15); color: #d29922; }}

/* Canvas */
#net {{ flex: 1; background: var(--bg); }}

/* Detail panel */
#detail {{ width: 300px; flex-shrink: 0; background: var(--surface);
           border-left: 1px solid var(--border); display: none; flex-direction: column; }}
#detail.open {{ display: flex; }}
.d-header {{ padding: 12px 14px; border-bottom: 1px solid var(--border);
              display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }}
.d-close {{ background: none; border: none; color: var(--muted); cursor: pointer; font-size: 18px; line-height: 1; }}
.d-close:hover {{ color: var(--text); }}
#dcontent {{ flex: 1; overflow-y: auto; padding: 14px; }}
.d-file {{ font-family: monospace; font-size: 14px; font-weight: 600; color: var(--blue); margin-bottom: 4px; }}
.d-tag {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 10px;
           font-weight: 500; margin-bottom: 5px; }}
.d-lines {{ font-size: 10px; color: var(--muted); font-family: monospace; margin-bottom: 10px; }}
.d-desc {{ font-size: 11px; line-height: 1.65; color: var(--text); padding: 10px 11px;
            background: var(--surface2); border-radius: 6px; border-left: 3px solid var(--border);
            margin-bottom: 14px; }}
.d-pillars {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 12px; }}
.d-pillar {{ font-size: 9px; padding: 2px 6px; border-radius: 3px; font-weight: 500;
              background: rgba(16,185,129,.12); color: #3fb950; border: 1px solid rgba(16,185,129,.25); }}
.d-kill-banner {{ background: rgba(127,29,29,.15); border: 1px solid rgba(127,29,29,.4);
                   border-radius: 6px; padding: 8px 10px; margin-bottom: 12px;
                   font-size: 10px; color: #fca5a5; line-height: 1.5; }}
.ksec {{ margin-bottom: 12px; }}
.ksec h4 {{ font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px;
             margin-bottom: 6px; display: flex; align-items: center; gap: 5px; }}
.ksec ul {{ list-style: none; display: flex; flex-direction: column; gap: 3px; }}
.ksec li {{ font-size: 10px; line-height: 1.5; padding: 5px 8px; border-radius: 5px;
             display: flex; gap: 6px; align-items: flex-start; }}
.kd {{ width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; margin-top: 3px; }}
.good h4 {{ color: var(--green); }} .good li {{ background: rgba(63,185,80,.07); }} .good .kd {{ background: var(--green); }}
.bad  h4 {{ color: var(--red);   }} .bad  li {{ background: rgba(248,81,73,.07); }} .bad  .kd {{ background: var(--red);   }}
.ugly h4 {{ color: var(--yellow);}} .ugly li {{ background: rgba(210,153,34,.07);}} .ugly .kd {{ background: var(--yellow);}}
.cfg  h4 {{ color: var(--blue);  }} .cfg  li {{ background: rgba(88,166,255,.07); font-family: monospace; font-size: 9px; }} .cfg  .kd {{ background: var(--blue);  }}
.fn   h4 {{ color: var(--muted); }} .fn   li {{ background: var(--surface2); font-family: monospace; font-size: 9px; }} .fn   .kd {{ background: #484f58; }}
.fl   h4 {{ color: var(--muted); }} .fl   li {{ background: #1c2128; font-size: 10px; }} .fl   .kd {{ background: var(--border); }}
.bug-risk {{ display: inline-block; margin-bottom: 12px; }}
.divider {{ border: none; border-top: 1px solid var(--border); margin: 12px 0; }}
::-webkit-scrollbar {{ width: 4px; height: 4px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}
</style>
</head>
<body>
<div id="app">

<header>
  <div class="logo">&#129504; <span class="accent">Decifer</span> Brain</div>
  <div class="hstats">
    <div class="hstat"><b>{total_files}</b> files</div>
    <div class="hstat"><b>{total_lines:,}</b> lines</div>
    <div class="hstat"><b>{kill_count}</b> zombie</div>
  </div>
  <div class="hctrls">
    <button class="hbtn" onclick="fitAll()">Fit</button>
    <button class="hbtn" id="phys-btn" onclick="togglePhysics()">&#9889; Physics</button>
  </div>
  <div class="gen-time">Generated {generated_at}</div>
</header>

<div id="main">

<!-- ── Sidebar ─────────────────────────────────── -->
<div id="sidebar">

  <div class="sb-section">
    <div class="sb-body" style="padding-top:10px">
      <input type="text" id="search" placeholder="Search files, descriptions...">
    </div>
  </div>

  <div class="sb-section">
    <div class="sb-head" onclick="toggleSection('filters')">
      Layers <span class="toggle">&#9660;</span>
    </div>
    <div class="sb-body" id="filters-body"></div>
  </div>

  <div class="sb-section">
    <div class="sb-head" onclick="toggleSection('radar')">
      &#128308; Bug Radar <span class="toggle">&#9660;</span>
    </div>
    <div class="sb-body" id="radar-body">
      <div class="syn-empty">Loading...</div>
    </div>
  </div>

  <div class="sb-section">
    <div class="sb-head" onclick="toggleSection('next')">
      &#128312; Build Next <span class="toggle">&#9660;</span>
    </div>
    <div class="sb-body" id="next-body">
      <div class="syn-empty">Loading...</div>
    </div>
  </div>

  <div class="sb-section">
    <div class="sb-head" onclick="toggleSection('kill')">
      &#128465; Kill List <span class="toggle">&#9660;</span>
    </div>
    <div class="sb-body" id="kill-body">
      <div class="syn-empty">Loading...</div>
    </div>
  </div>

</div>
<!-- ────────────────────────────────────────────── -->

<div id="net"></div>

<div id="detail">
  <div class="d-header">
    <span style="font-size:11px;color:var(--muted)">File Detail</span>
    <button class="d-close" onclick="closeDetail()">&#215;</button>
  </div>
  <div id="dcontent"></div>
</div>

</div><!-- #main -->
</div><!-- #app -->

<script>
// ── Data ─────────────────────────────────────────────────────────────────────
const GROUPS    = {groups_js};
const NODES     = {nodes_js};
const EDGES     = {edges_js};
const SYNTHESIS = {synth_js};

// ── Vis-network setup ─────────────────────────────────────────────────────────
const GC = {{}};
Object.entries(GROUPS).forEach(([k,v]) => GC[k] = v.color);

function nodeSize(lines) {{
  return Math.max(12, Math.min(44, 12 + (lines / 3000) * 32));
}}
function lightenHex(hex, amt) {{
  const n = parseInt(hex.replace('#',''), 16);
  const r = Math.min(255, (n>>16) + amt);
  const g = Math.min(255, ((n>>8)&0xff) + amt);
  const b = Math.min(255, (n&0xff) + amt);
  return `rgb(${{r}},${{g}},${{b}})`;
}}

const visNodes = new vis.DataSet(NODES.map(n => ({{
  id: n.id,
  label: n.label,
  color: {{
    background: GC[n.group] || '#64748b',
    border:     GC[n.group] || '#64748b',
    highlight: {{ background: '#ffffff', border: GC[n.group] || '#64748b' }},
    hover:     {{ background: lightenHex(GC[n.group] || '#64748b', 40), border: GC[n.group] }},
  }},
  font: {{ color:'#fff', size:11, face:'-apple-system,sans-serif' }},
  size: nodeSize(n.lines || 200),
  borderWidth: n.kill_candidate ? 3 : 2,
  borderDashes: n.kill_candidate ? [4,3] : false,
  borderWidthSelected: 4,
  shadow: {{ enabled:true, color:'rgba(0,0,0,0.4)', size:8 }},
}})));

const visEdges = new vis.DataSet(EDGES.map((e,i) => ({{
  id: i,
  from: e.from, to: e.to,
  color: {{ color:'#30363d', highlight:'#ffffff', hover:'#58a6ff', opacity:0.7 }},
  arrows: {{ to:{{ enabled:true, scaleFactor:0.45 }} }},
  width: 1.2,
  smooth: {{ type:'curvedCW', roundness:0.1 }},
}})));

let physOn = false;
const network = new vis.Network(
  document.getElementById('net'),
  {{ nodes: visNodes, edges: visEdges }},
  {{
    physics: {{
      enabled: true,
      barnesHut: {{
        gravitationalConstant: -9000,
        centralGravity: 0.25,
        springLength: 180,
        springConstant: 0.03,
        damping: 0.09,
      }},
      stabilization: {{ iterations: 500, fit: true }},
    }},
    interaction: {{ hover:true, tooltipDelay:80, zoomView:true, dragView:true }},
    layout: {{ improvedLayout: true }},
    nodes: {{ shape:'dot' }},
    edges: {{ width:1.2 }},
  }}
);
network.once('stabilizationIterationsDone', () => {{
  network.setOptions({{ physics:{{ enabled:false }} }});
  physOn = false;
}});
network.on('click', params => {{
  if (params.nodes.length) {{
    const nd = NODES.find(n => n.id === params.nodes[0]);
    if (nd) showDetail(nd);
  }} else {{
    closeDetail();
  }}
}});

// ── Detail panel ──────────────────────────────────────────────────────────────
const PILLAR_NAMES = ['','Entry with thesis','Hold with monitoring','Exit with reason','Record everything','Learn from outcomes'];

function showDetail(nd) {{
  document.getElementById('detail').classList.add('open');
  const gc = GC[nd.group] || '#64748b';
  const gl = GROUPS[nd.group]?.label || nd.group;

  const list = (arr) => (arr||[]).map(x =>
    `<li><div class="kd"></div><span>${{esc(x)}}</span></li>`
  ).join('');
  const sec = (cls, icon, title, arr) => arr && arr.length
    ? `<div class="ksec ${{cls}}"><h4>${{icon}} ${{title}}</h4><ul>${{list(arr)}}</ul></div>`
    : '';

  const pillarsHtml = (nd.pillars||[]).length
    ? `<div class="d-pillars">${{(nd.pillars||[]).map(p =>
        `<span class="d-pillar">Pillar ${{p}}: ${{esc(PILLAR_NAMES[p]||'')}}</span>`
      ).join('')}}</div>`
    : '';

  const killBanner = nd.kill_candidate
    ? `<div class="d-kill-banner">&#128465; Kill candidate — ${{esc(nd.kill_reason||'')}}</div>`
    : '';

  const bugBadge = nd.bug_risk
    ? `<div class="bug-risk"><span class="sev-badge sev-${{nd.bug_risk}}">${{nd.bug_risk}} risk</span>
       <span style="font-size:10px;color:var(--muted);margin-left:6px">${{esc(nd.bug_risk_reason||'')}}</span></div>`
    : '';

  document.getElementById('dcontent').innerHTML = `
    <div class="d-file">${{esc(nd.label)}}</div>
    <div class="d-tag" style="background:${{gc}}22;color:${{gc}};border:1px solid ${{gc}}44">${{gl}}</div>
    ${{nd.lines ? `<div class="d-lines">${{nd.lines.toLocaleString()}} lines</div>` : ''}}
    <div class="d-desc">${{esc(nd.desc||'')}}</div>
    ${{pillarsHtml}}
    ${{killBanner}}
    ${{bugBadge}}
    ${{sec('fn','&#9881;','Key Functions', nd.fn)}}
    <hr class="divider">
    ${{sec('good','&#10003;','Good', nd.good)}}
    ${{sec('bad','&#10007;','Bad', nd.bad)}}
    ${{sec('ugly','&#9888;','Ugly / Debt', nd.ugly)}}
    <hr class="divider">
    ${{sec('cfg','&#9881;','Config Keys', nd.cfg)}}
    ${{sec('fl','&#128193;','Data Files', nd.files)}}
  `;
}}
function closeDetail() {{
  document.getElementById('detail').classList.remove('open');
}}
function esc(s) {{
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}}

// ── Sidebar filters ───────────────────────────────────────────────────────────
const fc = document.getElementById('filters-body');
Object.entries(GROUPS).forEach(([key, grp]) => {{
  const count = NODES.filter(n => n.group === key).length;
  if (!count) return;
  const div = document.createElement('div');
  div.className = 'fi';
  div.innerHTML = `
    <input type="checkbox" id="f-${{key}}" checked>
    <div class="fi-dot" style="background:${{grp.color}}"></div>
    <label class="fi-lbl" for="f-${{key}}">${{grp.label}}</label>
    <span class="fi-cnt">${{count}}</span>
  `;
  div.querySelector('input').addEventListener('change', e => toggleGroup(key, e.target.checked));
  fc.appendChild(div);
}});

function toggleGroup(group, visible) {{
  const ids = NODES.filter(n => n.group === group).map(n => n.id);
  ids.forEach(id => visNodes.update({{ id, hidden: !visible }}));
  const hidden = new Set(visNodes.get().filter(n => n.hidden).map(n => n.id));
  visEdges.get().forEach(e => {{
    visEdges.update({{ id: e.id, hidden: hidden.has(e.from) || hidden.has(e.to) }});
  }});
}}

// ── Synthesis panels ──────────────────────────────────────────────────────────
function renderSynthesis() {{
  if (!SYNTHESIS || Object.keys(SYNTHESIS).length === 0) {{
    ['radar-body','next-body','kill-body'].forEach(id => {{
      document.getElementById(id).innerHTML =
        '<div class="syn-empty">Run without --fast to enable Opus synthesis.</div>';
    }});
    return;
  }}

  // Bug Radar
  const radarBody = document.getElementById('radar-body');
  const radar = SYNTHESIS.bug_radar || [];
  if (!radar.length) {{
    radarBody.innerHTML = '<div class="syn-empty">No risks identified.</div>';
  }} else {{
    radarBody.innerHTML = radar.map(item => `
      <div class="syn-item ${{item.severity||'medium'}}">
        <div class="syn-rank">#${{item.rank}} <span class="sev-badge sev-${{item.severity}}">${{item.severity}}</span></div>
        <div class="syn-title">${{esc(item.title)}}</div>
        <div class="syn-body">${{esc(item.risk)}}</div>
        <div class="syn-files">${{(item.files||[]).map(f =>
          `<span class="syn-file" onclick="focusFile('${{f}}')">${{esc(f)}}</span>`
        ).join('')}}</div>
      </div>
    `).join('');
  }}

  // Build Next
  const nextBody = document.getElementById('next-body');
  const next = SYNTHESIS.build_next || [];
  if (!next.length) {{
    nextBody.innerHTML = '<div class="syn-empty">No build recommendations.</div>';
  }} else {{
    nextBody.innerHTML = next.map(item => `
      <div class="syn-item next">
        <div class="syn-rank">#${{item.rank}} ${{item.backlog_item ? `— ${{esc(item.backlog_item)}}` : ''}}</div>
        <div class="syn-title">${{esc(item.feature)}}</div>
        <div class="syn-body">${{esc(item.why)}}</div>
        ${{item.code_gap ? `<div class="syn-body" style="margin-top:4px;color:#484f58">Gap: ${{esc(item.code_gap)}}</div>` : ''}}
        <div class="syn-files">${{(item.files_needed||[]).map(f =>
          `<span class="syn-file" onclick="focusFile('${{f}}')">${{esc(f)}}</span>`
        ).join('')}}</div>
      </div>
    `).join('');
  }}

  // Kill List
  const killBody = document.getElementById('kill-body');
  const kill = SYNTHESIS.kill_list || [];
  if (!kill.length) {{
    killBody.innerHTML = '<div class="syn-empty">No kill candidates.</div>';
  }} else {{
    killBody.innerHTML = kill.map(item => `
      <div class="syn-item kill">
        <div class="syn-rank">#${{item.rank}} ${{item.safe_to_delete
          ? '<span class="sev-badge" style="background:rgba(127,29,29,.2);color:#fca5a5">safe to delete</span>'
          : '<span class="sev-badge" style="background:rgba(210,153,34,.15);color:#d29922">verify first</span>'
        }}</div>
        <div class="syn-title"><span class="syn-file" onclick="focusFile('${{esc(item.file)}}')">${{esc(item.file)}}</span></div>
        <div class="syn-lines">${{(item.lines||0).toLocaleString()}} lines recovered</div>
        <div class="syn-body">${{esc(item.reason)}}</div>
        ${{item.caveat ? `<div class="syn-body" style="margin-top:3px;color:#d29922">&#9888; ${{esc(item.caveat)}}</div>` : ''}}
      </div>
    `).join('');
  }}
}}

function focusFile(filename) {{
  const stem = filename.replace(/\\.py$/, '');
  const nd = NODES.find(n => n.id === stem || n.label === filename);
  if (!nd) return;
  network.focus(nd.id, {{ scale: 1.4, animation: {{ duration: 600, easingFunction: 'easeInOutQuad' }} }});
  network.selectNodes([nd.id]);
  showDetail(nd);
}}

renderSynthesis();

// ── Search ────────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', e => {{
  const q = e.target.value.toLowerCase().trim();
  NODES.forEach(nd => {{
    const match = !q
      || nd.label.toLowerCase().includes(q)
      || (nd.desc||'').toLowerCase().includes(q)
      || (nd.good||[]).some(x => x.toLowerCase().includes(q))
      || (nd.bad||[]).some(x => x.toLowerCase().includes(q))
      || (nd.ugly||[]).some(x => x.toLowerCase().includes(q))
      || (nd.fn||[]).some(x => x.toLowerCase().includes(q));
    const gc = GC[nd.group] || '#64748b';
    visNodes.update({{
      id: nd.id,
      color: match
        ? {{ background: gc, border: gc,
             highlight: {{ background:'#fff', border:gc }},
             hover: {{ background:lightenHex(gc,40), border:gc }} }}
        : {{ background:'#1a1d2e', border:'#21262d',
             highlight: {{ background:'#1a1d2e', border:'#21262d' }} }},
      font: {{ color: match ? '#fff' : '#30363d', size:11 }},
    }});
  }});
}});

// ── Collapsible sections ──────────────────────────────────────────────────────
function toggleSection(name) {{
  const head = document.querySelector(`[onclick="toggleSection('${{name}}')"]`);
  const body = document.getElementById(name + '-body') ||
               document.getElementById('filters-body');
  if (!body) return;
  const isOpen = !body.classList.contains('collapsed');
  body.classList.toggle('collapsed', isOpen);
  head.classList.toggle('collapsed', isOpen);
}}

// ── Controls ──────────────────────────────────────────────────────────────────
function fitAll() {{
  network.fit({{ animation:{{ duration:600, easingFunction:'easeInOutQuad' }} }});
}}
function togglePhysics() {{
  physOn = !physOn;
  network.setOptions({{ physics:{{ enabled:physOn }} }});
  document.getElementById('phys-btn').textContent = physOn ? '&#9889; Physics ON' : '&#9889; Physics';
}}
</script>
</body>
</html>"""


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    args = set(sys.argv[1:])
    force   = "--force"   in args
    fast    = "--fast"    in args
    no_open = "--no-open" in args

    client = anthropic.Anthropic()

    # 1. Discover files
    py_files = get_py_files()
    print(f"Found {len(py_files)} Python files")

    # 2. Load cache
    cache: dict[str, dict] = {}
    if CACHE_FILE.exists() and not force:
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}

    # 3. Git blob hashes
    blob_hashes = get_blob_hashes()

    # 4. AST analyze all files
    print("AST analysis...")
    ast_map: dict[str, dict] = {}   # filename -> ast_data
    for fp in py_files:
        ast_map[fp.name] = ast_analyze(fp)

    # 5. Determine which files need annotation
    to_annotate: list[tuple[Path, dict, str]] = []
    for fp in py_files:
        key = file_cache_key(fp, blob_hashes)
        if key not in cache:
            to_annotate.append((fp, ast_map[fp.name], key))

    cached_count = len(py_files) - len(to_annotate)
    print(f"Annotating {len(to_annotate)} files (cache hit: {cached_count})")

    # 6. Annotate in parallel
    def _annotate(args_tuple):
        fp, ast_data, cache_key = args_tuple
        print(f"  Sonnet → {fp.name}")
        annotation = annotate_file(client, fp, ast_data)
        return cache_key, fp.name, annotation

    if to_annotate:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_annotate, t) for t in to_annotate]
            for fut in as_completed(futures):
                try:
                    key, fname, ann = fut.result()
                    cache[key] = {"filename": fname, **ann}
                except Exception as exc:
                    print(f"  [!] worker error: {exc}")

    # Save cache
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))

    # 7. Assemble nodes
    nodes: list[dict] = []
    for fp in py_files:
        key  = file_cache_key(fp, blob_hashes)
        ann  = cache.get(key, {})
        stem = fp.stem

        # Ensure kill_candidate = True if group is zombie
        if ann.get("group") == "zombie" and not ann.get("kill_candidate"):
            ann["kill_candidate"] = True

        node: dict = {
            "id":            stem,
            "label":         fp.name,
            "group":         ann.get("group", "infra"),
            "lines":         ast_map[fp.name]["lines"],
            "desc":          ann.get("desc", ""),
            "fn":            ann.get("fn", ast_map[fp.name]["functions"][:5]),
            "good":          ann.get("good", []),
            "bad":           ann.get("bad", []),
            "ugly":          ann.get("ugly", []),
            "cfg":           ann.get("cfg", []),
            "files":         ann.get("files", []),
            "pillars":       ann.get("pillars", []),
            "kill_candidate":ann.get("kill_candidate", False),
            "kill_reason":   ann.get("kill_reason"),
            "bug_risk":      ann.get("bug_risk", "low"),
            "bug_risk_reason":ann.get("bug_risk_reason", ""),
        }
        nodes.append(node)

    # 8. Build edges
    edges = build_edges(nodes, ast_map)
    print(f"Built {len(edges)} dependency edges")

    # 9. Opus synthesis
    total_lines = sum(n["lines"] for n in nodes)
    print("Running Opus synthesis...")
    synthesis = synthesize(client, nodes, total_lines, fast)

    # 10. Render HTML
    print("Rendering HTML...")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = render_html(nodes, edges, synthesis, generated_at)
    OUTPUT_FILE.write_text(html, encoding="utf-8")

    kill_count = sum(1 for n in nodes if n.get("kill_candidate"))
    print(f"\nDone — {OUTPUT_FILE.name}")
    print(f"  {len(nodes)} files, {total_lines:,} lines, {len(edges)} edges, {kill_count} zombie")

    if not no_open:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
