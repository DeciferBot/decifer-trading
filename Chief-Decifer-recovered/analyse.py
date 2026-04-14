#!/usr/bin/env python3
"""
Chief Decifer — Product Brain Analysis
=======================================
Gathers all available project data (research, sessions, specs, git history,
code health) and calls the Claude API to reason over it and produce a
structured product roadmap aligned to the vision.

Output: state/analysis/latest.json

Run manually:   python analyse.py
Scheduled:      via Cowork scheduled task (daily)

Requires:
  - ANTHROPIC_API_KEY in .env
  - DECIFER_REPO_PATH in .env (optional but adds git/code context)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
# Import STATE_DIR from config so analyse.py writes to the same sacred state
# directory the dashboard reads from (chief-decifer/state/, per CLAUDE.md data
# contract). Previously BASE_DIR/"state" pointed at Chief-Decifer-recovered/state/
# which the dashboard does NOT read, so output was invisible.

from config import STATE_DIR  # noqa: E402

VISION_FILE      = STATE_DIR / "vision.json"
OPERATIONAL_FILE = STATE_DIR / "operational_state.json"
SPECS_DIR        = STATE_DIR / "specs"
BACKLOG_FILE     = STATE_DIR / "backlog.json"
SESSIONS_DIR     = STATE_DIR / "sessions"
RESEARCH_DIR     = STATE_DIR / "research"
ANALYSIS_DIR     = STATE_DIR / "analysis"
OUTPUT_FILE      = ANALYSIS_DIR / "latest.json"

DECIFER_REPO  = Path(os.getenv("DECIFER_REPO_PATH", "")).expanduser()
API_KEY       = os.getenv("ANTHROPIC_API_KEY", "")
MODEL         = "claude-sonnet-4-6"
MAX_TOKENS    = 16000


# ── Data collectors ────────────────────────────────────────────────────────────

def _load_vision():
    if VISION_FILE.exists():
        return json.loads(VISION_FILE.read_text())
    return {"statement": "Not set.", "current_stage": "unknown"}


def _load_operational_state():
    if OPERATIONAL_FILE.exists():
        try:
            return json.loads(OPERATIONAL_FILE.read_text())
        except Exception:
            return {}
    return {}


def _load_specs():
    specs = []
    seen  = set()
    if SPECS_DIR.exists():
        for f in sorted(SPECS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                if d.get("id") and d["id"] not in seen:
                    specs.append(d)
                    seen.add(d["id"])
            except Exception:
                pass
    if BACKLOG_FILE.exists():
        try:
            items = json.loads(BACKLOG_FILE.read_text())
            if isinstance(items, list):
                for d in items:
                    if d.get("id") and d["id"] not in seen:
                        specs.append(d)
                        seen.add(d["id"])
        except Exception:
            pass
    return specs


def _load_sessions(limit=5):
    if not SESSIONS_DIR.exists():
        return []
    files = sorted(SESSIONS_DIR.glob("*.json"), reverse=True)[:limit]
    sessions = []
    for f in files:
        try:
            sessions.append(json.loads(f.read_text()))
        except Exception:
            pass
    return sessions


def _load_research(limit=8):
    if not RESEARCH_DIR.exists():
        return []
    files = sorted(RESEARCH_DIR.glob("*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    research = []
    for f in files:
        try:
            research.append(json.loads(f.read_text()))
        except Exception:
            pass
    return research


def _git_log(n=20):
    if not DECIFER_REPO or not (DECIFER_REPO / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "log", "--format=%h|%ar|%s", f"-{n}"],
            cwd=DECIFER_REPO, capture_output=True, text=True, timeout=10,
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            if "|" in line:
                parts = line.split("|", 2)
                commits.append({"hash": parts[0], "when": parts[1], "msg": parts[2]})
        return commits
    except Exception:
        return []


def _test_summary():
    if not DECIFER_REPO or not (DECIFER_REPO / "tests").exists():
        return None
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "--tb=line", "-q", "--no-header"],
            cwd=DECIFER_REPO, capture_output=True, text=True, timeout=90,
        )
        output = result.stdout + result.stderr
        passed = failed = errors = 0
        # Find the final summary line (last line matching ===...===)
        summary_line = ""
        for line in reversed(output.splitlines()):
            if re.match(r"=+\s+\d+", line):
                summary_line = line
                break
        for match in re.finditer(r"(\d+)\s+(passed|failed|error)", summary_line):
            n, k = int(match.group(1)), match.group(2)
            if k == "passed":  passed = n
            elif k == "failed": failed = n
            elif k == "error":  errors = n
        # Include first failing test details (not full output — keep token budget tight)
        failures = []
        for line in output.splitlines():
            if "FAILED" in line or "ERROR" in line:
                failures.append(line.strip())
            if len(failures) >= 10:
                break
        return {
            "passed": passed, "failed": failed, "errors": errors,
            "pass_rate_pct": int(passed / (passed + failed) * 100) if (passed + failed) > 0 else None,
            "failures": failures,
        }
    except Exception:
        return None


def _code_summary():
    if not DECIFER_REPO or not DECIFER_REPO.exists():
        return None
    py_files = [f for f in DECIFER_REPO.glob("*.py") if not f.name.startswith("_")]
    total_lines = 0
    module_sizes = []
    for f in py_files:
        try:
            lines = len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
            total_lines += lines
            module_sizes.append({"file": f.name, "lines": lines})
        except Exception:
            pass
    module_sizes.sort(key=lambda x: x["lines"], reverse=True)
    test_dir = DECIFER_REPO / "tests"
    test_files = list(test_dir.glob("test_*.py")) if test_dir.exists() else []
    return {
        "total_src_lines": total_lines,
        "module_count": len(py_files),
        "test_file_count": len(test_files),
        "largest_modules": module_sizes[:8],
    }


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(vision, operational, specs, sessions, research, commits, tests, code):

    def _json_block(label, data, indent=2):
        return f"\n### {label}\n```json\n{json.dumps(data, indent=indent, default=str)}\n```\n"

    sections = []

    # Vision
    sections.append(f"""## VISION — The End State We Are Building Toward

{vision.get('statement', 'Not set.')}

Current stage: {vision.get('current_stage', 'unknown')}

Stages to completion:
{chr(10).join(f"  {'→' if s == vision.get('current_stage') else '  '} {s}" for s in vision.get('stages', []))}
""")

    # Operational state — what is disabled, what has been deliberately decided, what tradeoffs are accepted
    if operational:
        op_lines = []
        disabled = operational.get("disabled_features", [])
        if disabled:
            op_lines.append("### Disabled features (DO NOT raise as risks — they are gated off):")
            for d in disabled:
                op_lines.append(f"  - {d.get('feature','')}  [{d.get('config_key','')}={d.get('value','')}, {d.get('source','')}]")
                op_lines.append(f"    why: {d.get('why','')}")
                if d.get('implication_for_chief'):
                    op_lines.append(f"    implication: {d.get('implication_for_chief')}")
        decisions = operational.get("recent_decisions", [])
        if decisions:
            op_lines.append("\n### Recent deliberate decisions (DO NOT reclassify as risks — they were chosen):")
            for dec in decisions:
                op_lines.append(f"  - {dec.get('decision','')}")
                op_lines.append(f"    context: {dec.get('context','')}")
                if dec.get('implication_for_chief'):
                    op_lines.append(f"    implication: {dec.get('implication_for_chief')}")
        tradeoffs = operational.get("accepted_tradeoffs", [])
        if tradeoffs:
            op_lines.append("\n### Accepted tradeoffs (DO NOT flag in isolation — they are known and accepted):")
            for t in tradeoffs:
                op_lines.append(f"  - {t.get('tradeoff','')}")
                op_lines.append(f"    reason: {t.get('reason','')}")
                if t.get('implication_for_chief'):
                    op_lines.append(f"    implication: {t.get('implication_for_chief')}")
        if op_lines:
            sections.append("## OPERATIONAL STATE — Ground truth for what is running, what is disabled, and what has been decided\n\n"
                            + "\n".join(op_lines) + "\n")

    # Specs / pipeline
    complete  = [s for s in specs if s.get("status") == "complete"]
    active    = [s for s in specs if s.get("status") in ("in_progress", "spec_complete")]
    backlog   = [s for s in specs if s.get("status") == "backlog"]

    sections.append(f"""## FEATURE PIPELINE

Shipped ({len(complete)} features):
{chr(10).join(f"  ✓ [{s.get('phase','')}] {s.get('title','')} — {s.get('summary','')[:80]}" for s in complete)}

Active ({len(active)} features):
{chr(10).join(f"  ● [{s.get('phase','')} {s.get('priority','')}] {s.get('title','')} — {s.get('summary','')[:80]}" for s in active) or '  (none)'}

Backlog ({len(backlog)} features):
{chr(10).join(f"  ○ [{s.get('phase','')} {s.get('priority','')}] {s.get('title','')} — {s.get('summary','')[:80]}" for s in backlog)}
""")

    # Research
    if research:
        research_lines = []
        for r in research:
            topic = r.get("topic", "")
            date  = r.get("date", "")
            synth = r.get("synthesis", "")[:200]
            research_lines.append(f"  [{date}] {topic}\n    {synth}")
        sections.append("## RESEARCH FINDINGS\n\n" + "\n\n".join(research_lines) + "\n")

    # Sessions
    if sessions:
        session_lines = []
        for s in sessions:
            date  = s.get("date", "")
            items = s.get("work_items", [])
            summaries = [f"    - {i.get('type','')}: {i.get('summary','')[:100]}" for i in items[:5]]
            session_lines.append(f"  [{date}]\n" + "\n".join(summaries))
        sections.append("## RECENT DEV SESSIONS\n\n" + "\n\n".join(session_lines) + "\n")
    else:
        sections.append("## RECENT DEV SESSIONS\n\n  (No session logs yet)\n")

    # Git
    if commits:
        git_lines = [f"  {c['when']:>15}  {c['msg'][:80]}" for c in commits[:15]]
        sections.append("## RECENT COMMITS\n\n" + "\n".join(git_lines) + "\n")

    # Tests
    if tests:
        sections.append(f"""## TEST HEALTH

Pass rate: {tests.get('pass_rate_pct')}%  ({tests['passed']} passed, {tests['failed']} failed, {tests['errors']} errors)
Failing tests:
{chr(10).join('  ' + f for f in tests.get('failures', [])) or '  (none)'}
""")

    # Code
    if code:
        large = [m for m in code.get("largest_modules", []) if m["lines"] > 500]
        sections.append(f"""## CODE HEALTH

Total source lines: {code['total_src_lines']:,}
Source modules: {code['module_count']}
Test files: {code['test_file_count']}
Largest modules: {', '.join(f"{m['file']} ({m['lines']}L)" for m in large)}
""")

    context = "\n".join(sections)

    prompt = f"""You are Chief Decifer — the product brain for a trading bot project called Decifer.

You think and act like a Chief Product Officer. Your job is to read everything below, reason across it all, and produce an ordered list of the most important things to do next — grounded entirely in the data. You are the decision-making layer between raw project state and actual development work.

Do NOT hallucinate features, research, or capabilities that aren't in the data. Every recommendation must be traceable to something you can see below.

{context}

---

Produce your analysis as a JSON object with exactly these keys.

CRITICAL — RISKS SCOPE: The `risks` list is scoped to two categories ONLY:
  1. "alpha"      — risks to generating alpha: signal quality, edge decay, overfitting, regime blind spots, data contamination, scoring bias, skew feedback loops, incorrect position sizing, anything that degrades the statistical edge of the strategy.
  2. "execution"  — risks to the bot not working: order submission failures, broker disconnects, data feed outages, silent crashes, reconciliation drift, untested code paths in the trading hot path, state corruption, anything that stops the bot from running or causes it to act on bad state.

Do NOT output risks about: regulatory/compliance, "trading for friends and family", legal exposure, user trust, business model, fundraising, hiring, product-market fit, or any risk that is not technical/strategic to the bot itself.

CRITICAL — RISK BAR (every risk must clear all four, otherwise it is not a risk):
  1. EVIDENCED — there is a concrete signal in the data you can see: a failing test, an error log, an observed bug, a measurable divergence, a commit fixing the same thing repeatedly. Speculation does NOT qualify. "This could fail" or "this might cause" is NOT evidence.
  2. NOT TIME-RESOLVABLE — if the fix is "wait for more trades / run more cycles / accumulate data", it is a MILESTONE, not a risk. Only broken code or structural problems that time will not fix count as risks. Example: "IC unmeasured" is not a risk — the IC tracker needs trade volume. "IC calculator has a bug producing wrong output" IS a risk.
  3. NOT ABOUT A DISABLED COMPONENT — if the feature is gated off in OPERATIONAL STATE above, it cannot be a live risk. Legacy-cleanup commits for disabled features are not evidence of ongoing exposure.
  4. NOT A DECIDED TRADEOFF — if the change or configuration appears in OPERATIONAL STATE under "recent decisions" or "accepted tradeoffs", it was chosen deliberately. Do not re-raise it as a risk. If worth flagging at all, use `observations`.

If you cannot name a real, specific, actionable risk that clears ALL FOUR bars, return an empty `risks` list. Speculative, generic, or re-litigating risks are worse than no risks.

The `observations` list is where softer material goes — things worth Amit watching but that do not clear the risk bar. Recent behavioral changes to monitor, distribution shifts, noticed patterns, things that might become risks if they worsen. Keep it to 0–5 items. Each observation must be specific (name the file/metric/behavior), not generic commentary.

CRITICAL: The `recommended_actions` list is the most important output. It must:
- Be ordered from highest to lowest priority (item 0 = do this NOW)
- Contain 6–10 items that span the full range of PM work: fixing bugs, building pipeline features, promoting research findings to the pipeline, writing missing specs, making explicit decisions, cleaning up debt, validating assumptions
- Each action must be SPECIFIC. Name the exact feature, bug, research finding, or spec. Do not write vague actions like "improve testing" — write "Add integration tests for the order execution path in orders.py which has no test coverage"
- The `why_now` must explain the actual reasoning: what is blocked, what is at risk, what does this unlock
- The `cowork_prompt` must be a complete, ready-to-paste session brief — include enough context that someone could start the session cold
- Valid types: "build" (implement a feature from pipeline/backlog), "fix" (repair a bug, broken test, or broken tooling), "promote" (evaluate a research finding and move it into the spec pipeline), "spec" (write a missing spec for something that should be in the pipeline), "housekeeping" (dedup, refactor, cleanup), "validate" (verify an assumption, run an experiment, check a result), "decision" (a choice that must be made before work can proceed)

{{
  "generated_at": "<ISO timestamp>",
  "vision_statement": "<1-2 sentences, your distilled understanding of where Decifer is going>",
  "current_stage_assessment": "<Where Decifer actually is right now, and what that means for what to build next. 2-3 sentences.>",
  "executive_summary": "<3-5 sentences synthesising the most important things you see across all data. What is the state of the project, what's working, what needs attention?>",
  "recommended_actions": [
    {{
      "action": "<specific, concrete title of what to do — name the exact thing>",
      "type": "build|fix|promote|spec|housekeeping|validate|decision",
      "why_now": "<why this is the most important thing at this moment — what it unblocks, what risk it reduces, why it comes before the next item>",
      "source": "<where this came from: pipeline item title, research topic, git observation, test result, vision gap, etc.>",
      "cowork_prompt": "<complete session brief ready to paste — include context, goal, and specific tasks>"
    }}
  ],
  "product_roadmap": [
    {{
      "phase_name": "<name>",
      "goal": "<what this phase achieves>",
      "rationale": "<why this phase must happen before the next one>",
      "features": ["<feature or work item>"],
      "exit_criteria": "<how you know this phase is done>",
      "estimated_scope": "small|medium|large"
    }}
  ],
  "risks": [
    {{
      "risk": "<specific risk — must clear all four bars: evidenced, not time-resolvable, not about a disabled component, not a decided tradeoff>",
      "category": "alpha|execution",
      "severity": "high|medium|low",
      "evidence": "<the concrete signal that proves this is real: failing test name, error log snippet, commit pattern, measurable divergence — NOT 'could' or 'might'>",
      "mitigation": "<concrete action to reduce this risk>"
    }}
  ],
  "observations": [
    {{
      "observation": "<specific thing worth watching — name the file/metric/behavior. For material that does not clear the risk bar.>",
      "why_watch": "<why this is worth monitoring; what would elevate it to a risk>"
    }}
  ],
  "roadmap_gaps": "<What is the roadmap missing? What features or capabilities are implied by the vision but not yet in any spec or backlog?>"
}}

Return only the JSON object. No preamble, no explanation outside the JSON."""

    return prompt


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    print("Chief Decifer — Product Brain Analysis")
    print("=" * 42)
    print(f"Gathering data...")

    vision      = _load_vision()
    operational = _load_operational_state()
    specs       = _load_specs()
    sessions    = _load_sessions()
    research    = _load_research()
    commits     = _git_log()
    tests       = _test_summary()
    code        = _code_summary()

    print(f"  vision:       {'✓' if vision.get('statement') != 'Not set.' else '✗ not set'}")
    print(f"  operational:  {'✓ (' + str(len(operational.get('disabled_features',[]))) + ' disabled, ' + str(len(operational.get('recent_decisions',[]))) + ' decisions, ' + str(len(operational.get('accepted_tradeoffs',[]))) + ' tradeoffs)' if operational else '✗ not set'}")
    print(f"  specs:     {len(specs)} features")
    print(f"  sessions:  {len(sessions)} logged")
    print(f"  research:  {len(research)} findings")
    print(f"  commits:   {len(commits)}")
    print(f"  tests:     {'✓ ' + str(tests.get('pass_rate_pct','?')) + '% pass' if tests else '✗ not available'}")
    print(f"  code:      {'✓' if code else '✗ not available'}")

    prompt = _build_prompt(vision, operational, specs, sessions, research, commits, tests, code)
    tokens_estimate = len(prompt) // 4
    print(f"\nPrompt size: ~{tokens_estimate:,} tokens")
    print(f"Calling {MODEL}...")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=API_KEY)
        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic",
              file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR calling Claude API: {e}", file=sys.stderr)
        sys.exit(1)

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        print("Raw response saved to state/analysis/raw_error.txt")
        (ANALYSIS_DIR / "raw_error.txt").write_text(raw)
        sys.exit(1)

    # Stamp it
    analysis["generated_at"]  = datetime.now(tz=timezone.utc).isoformat()
    analysis["model"]         = MODEL
    analysis["data_sources"]  = {
        "specs":    len(specs),
        "sessions": len(sessions),
        "research": len(research),
        "commits":  len(commits),
        "tests":    tests is not None,
        "code":     code is not None,
    }

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    # Archive previous analysis
    if OUTPUT_FILE.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        OUTPUT_FILE.rename(ANALYSIS_DIR / f"analysis_{ts}.json")

    OUTPUT_FILE.write_text(json.dumps(analysis, indent=2, default=str))
    print(f"\n✓ Analysis written to {OUTPUT_FILE}")
    print(f"\nExecutive summary:\n{analysis.get('executive_summary', '(not generated)')}")


if __name__ == "__main__":
    run()
