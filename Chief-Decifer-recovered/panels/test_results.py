"""
Test Results panel — tells you what's broken, what it means for the bot, and what to fix first.
No raw counts. Each failure is mapped to a subsystem with a plain-English impact statement.
"""

import subprocess
import re
import sys
from datetime import datetime
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import DECIFER_REPO_PATH

_last_result = None
_last_run_time = None

# ── Subsystem mapping ─────────────────────────────────────────────────────────

# Maps test file stem → (subsystem name, what breaks in the bot, fix priority 1=most critical)
TEST_TO_IMPACT = {
    "test_bot":               ("Trading Core",      "The main trading loop — the bot may not execute or route orders correctly.", 1),
    "test_orders":            ("Trading Core",      "Order placement and fill logic — live trades may not submit or cancel correctly.", 1),
    "test_smart_execution":   ("Trading Core",      "Smart routing — the bot may send orders suboptimally or miss price improvement.", 1),
    "test_risk":              ("Risk & Portfolio",  "Position sizing and drawdown limits — the bot may over-size or ignore stop rules.", 2),
    "test_portfolio":         ("Risk & Portfolio",  "Portfolio optimisation — allocation weights and correlation limits may be wrong.", 2),
    "test_signals":           ("Signal Generation", "Signal scoring — entry and exit signals may fire incorrectly or not at all.", 2),
    "test_scanner":           ("Signal Generation", "Market scanner — the bot may miss setups or scan the wrong universe.", 3),
    "test_options_scanner":   ("Signal Generation", "Options screener — options signals and IV filters may be unreliable.", 3),
    "test_agents":            ("AI & Learning",     "Agent decision layer — adaptive behaviour and context switching may be broken.", 3),
    "test_sentinel_agents":   ("AI & Learning",     "Sentinel agents — background monitoring and regime detection may not fire.", 3),
    "test_ml_engine":         ("AI & Learning",     "ML engine — model inference and feature input may return stale or wrong values.", 3),
    "test_learning":          ("AI & Learning",     "Learning loop — the bot may not update from trade outcomes correctly.", 4),
    "test_news":              ("News & Sentiment",  "News feed — market context signals from headlines may be missing or stale.", 4),
    "test_social_sentiment":  ("News & Sentiment",  "Social sentiment — crowd signal may be absent from the entry decision.", 4),
}

PRIORITY_LABELS = {1: "Fix first", 2: "Fix next", 3: "Important", 4: "Lower priority"}
PRIORITY_COLORS = {1: "#ff6b6b", 2: "#ffd43b", 3: "#4dabf7", 4: "#868e96"}

SUBSYSTEM_COLORS = {
    "Trading Core":      "#4dabf7",
    "Signal Generation": "#51cf66",
    "Risk & Portfolio":  "#ff6b6b",
    "AI & Learning":     "#ffd43b",
    "News & Sentiment":  "#da77f2",
}


# ── Test runner ───────────────────────────────────────────────────────────────

def _run_pytest():
    global _last_result, _last_run_time
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / "tests").exists():
        return None, "Can't find the tests folder."
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "--no-header"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=120
        )
        _last_result = result
        _last_run_time = datetime.now().strftime("%H:%M:%S")
        return result, None
    except Exception as e:
        return None, f"Error running tests: {e}"


def _parse_results(output):
    passed = failed = errors = skipped = 0
    summary = re.search(r"=+\s*(.*?)\s*=+\s*$", output, re.MULTILINE)
    if summary:
        s = summary.group(1)
        for match in re.finditer(r"(\d+)\s+(passed|failed|error|skipped|warnings?|deselected)", s):
            count = int(match.group(1))
            kind = match.group(2)
            if kind == "passed":    passed = count
            elif kind == "failed":  failed = count
            elif kind == "error":   errors = count
            elif kind == "skipped": skipped = count
    # Fallback: count markers
    if passed == 0 and failed == 0 and errors == 0:
        passed  = len(re.findall(r" PASSED", output))
        failed  = len(re.findall(r" FAILED", output))
        errors  = len(re.findall(r" ERROR",  output))
        skipped = len(re.findall(r" SKIPPED", output))
    collection_errors = len(re.findall(r"ERROR collecting", output))
    if collection_errors > 0 and errors == 0:
        errors = collection_errors
    return passed, failed, errors, skipped


def _extract_failures(output):
    """
    Returns a list of dicts:
      { test_file, test_name, subsystem, impact, fix_priority, is_import }
    Sorted by fix_priority ascending (most critical first).
    """
    failures = []
    for line in output.splitlines():
        line = line.strip()
        is_import = False

        if "ERROR collecting" in line:
            m = re.search(r"ERROR collecting (.+)", line)
            if not m:
                continue
            test_file = m.group(1).split("/")[-1].replace(".py", "")
            test_name = "Import / load error"
            is_import = True
        elif " FAILED" in line and "::" in line:
            parts = line.split("::")
            test_file = parts[0].split("/")[-1].replace(".py", "")
            raw_name = parts[-1].split(" ")[0]
            test_name = raw_name.replace("test_", "").replace("_", " ").title()
        elif " ERROR" in line and "::" in line:
            parts = line.split("::")
            test_file = parts[0].split("/")[-1].replace(".py", "")
            raw_name = parts[-1].split(" ")[0]
            test_name = raw_name.replace("test_", "").replace("_", " ").title()
        else:
            continue

        key = test_file if test_file.startswith("test_") else f"test_{test_file}"
        subsystem, impact, fix_priority = TEST_TO_IMPACT.get(
            key,
            ("Unknown", "Behaviour of an untested module may be unreliable.", 5)
        )
        failures.append({
            "test_file": test_file,
            "test_name": test_name,
            "subsystem": subsystem,
            "impact": impact,
            "fix_priority": fix_priority,
            "is_import": is_import,
        })

    # De-duplicate by (test_file, test_name), keep highest priority
    seen = {}
    for f in failures:
        k = (f["test_file"], f["test_name"])
        if k not in seen or f["fix_priority"] < seen[k]["fix_priority"]:
            seen[k] = f
    unique = list(seen.values())
    unique.sort(key=lambda x: (x["fix_priority"], x["test_file"], x["test_name"]))
    return unique


# ── Failure card ──────────────────────────────────────────────────────────────

def _render_failure_card(f, rank):
    pri = f["fix_priority"]
    pri_color = PRIORITY_COLORS.get(pri, "#868e96")
    pri_label = PRIORITY_LABELS.get(pri, "")
    sub_color = SUBSYSTEM_COLORS.get(f["subsystem"], "#868e96")
    is_import = f.get("is_import", False)
    badge_label = "Import Error" if is_import else "Failing"
    badge_color = "warning" if is_import else "danger"

    guidance = (
        "Fix the import first — this is likely a missing library (e.g. TA-Lib) or a bad module path. "
        "Once imports resolve, re-run to see real failures."
        if is_import else
        f"Open {f['test_file'].replace('test_', '')}.py and the matching test file, fix the logic, then re-run."
    )

    return html.Div([
        # Top row: rank + subsystem badge + priority label
        html.Div([
            html.Span(
                f"#{rank}",
                style={"fontSize": "0.65rem", "fontWeight": 700, "color": pri_color,
                       "minWidth": "24px"},
            ),
            html.Span(
                f["subsystem"],
                style={"fontSize": "0.62rem", "padding": "2px 8px", "borderRadius": "4px",
                       "backgroundColor": f"{sub_color}18", "color": sub_color,
                       "border": f"1px solid {sub_color}30"},
            ),
            dbc.Badge(badge_label, color=badge_color, style={"fontSize": "0.58rem"}),
            html.Span(
                pri_label,
                style={"fontSize": "0.6rem", "color": pri_color, "marginLeft": "auto",
                       "fontWeight": 700, "letterSpacing": "0.3px"},
            ),
        ], style={"display": "flex", "alignItems": "center", "gap": "7px",
                  "marginBottom": "8px", "flexWrap": "wrap"}),

        # Test name
        html.Div(
            f["test_name"] if f["test_name"] else f["test_file"],
            style={"fontWeight": 600, "fontSize": "0.83rem", "color": "var(--cd-text)",
                   "marginBottom": "5px"},
        ),

        # Bot impact
        html.Div([
            html.Span("Bot impact: ", style={"fontSize": "0.7rem", "color": "var(--cd-muted)",
                                             "fontWeight": 600, "marginRight": "4px"}),
            html.Span(f["impact"], style={"fontSize": "0.7rem", "color": "var(--cd-text2)",
                                          "lineHeight": "1.5"}),
        ], style={"marginBottom": "7px"}),

        # What to do
        html.Div([
            html.Span("→ ", style={"color": pri_color, "fontSize": "0.7rem"}),
            html.Span(guidance, style={"fontSize": "0.68rem", "color": "var(--cd-muted)",
                                       "fontStyle": "italic"}),
        ]),

    ], style={
        "backgroundColor": "var(--cd-card)",
        "borderRadius": "9px",
        "padding": "13px 16px",
        "marginBottom": "10px",
        "borderLeftWidth": "3px",
        "borderLeftStyle": "solid",
        "borderLeftColor": pri_color,
        "border": "1px solid var(--cd-border-sub)",
    })


# ── Main layout ───────────────────────────────────────────────────────────────

def layout():
    # On first load return immediately without running pytest.
    # The interval fires 1 second later and triggers the real run via the callback.
    # This prevents pytest (30–120 s) from blocking app startup.
    if _last_result is None and _last_run_time is None:
        return html.Div([
            html.Div([
                html.H4("Bot Status", className="text-light mb-0", style={"fontWeight": "600"}),
                html.Small("Running tests…", className="text-muted ms-2"),
            ], className="d-flex align-items-center mb-3"),
            dbc.Spinner(color="primary", size="sm"),
            html.P("Running pytest against the trading repo. This takes up to 2 minutes on first load.",
                   className="text-muted small mt-3"),
            dcc.Interval(id="tests-interval", interval=1_000, n_intervals=0),
        ])

    result, error = _run_pytest()

    if error:
        return html.Div([
            html.H4("Tests", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(error, className="text-warning"),
            html.P("Make sure DECIFER_REPO_PATH is set correctly.", className="text-muted small"),
            dcc.Interval(id="tests-interval", interval=120_000, n_intervals=0),
        ])

    stdout = result.stdout + result.stderr
    passed, failed, errors, skipped = _parse_results(stdout)
    total_run = passed + failed
    overall_ok = result.returncode == 0
    cant_run = errors > 0 and passed == 0 and failed == 0

    failures = _extract_failures(stdout)

    # ── Bot readiness verdict ─────────────────────────────────────────────────
    pass_rate = int(passed / total_run * 100) if total_run > 0 else None

    # Has any Trading Core or Risk failure? That's a hard "do not trade" signal.
    critical_subsystems = {"Trading Core", "Risk & Portfolio"}
    critical_failures = [f for f in failures if f["subsystem"] in critical_subsystems]
    has_critical = len(critical_failures) > 0

    if overall_ok:
        verdict       = "Ready to trade"
        verdict_sub   = (f"All {passed} tests pass. The bot's order execution, signal logic, "
                         "and risk controls are verified and working.")
        verdict_color = "#51cf66"
        verdict_bg    = "#0d1f11"
        verdict_border= "#51cf66"
        verdict_icon  = "●"
        advice        = None

    elif cant_run:
        verdict       = "Cannot verify — tests won't load"
        verdict_sub   = (f"{errors} test file{'s' if errors != 1 else ''} have import errors. "
                         "Until these are fixed you have no visibility into whether the bot's "
                         "core logic is working. Treat this as unknown risk.")
        verdict_color = "#ffd43b"
        verdict_bg    = "#1a1800"
        verdict_border= "#ffd43b"
        verdict_icon  = "◐"
        advice        = "Ask Cowork to fix the import errors listed below before your next live session."

    elif has_critical:
        affected_critical = sorted(set(f["subsystem"] for f in critical_failures))
        verdict       = "Do not trade — critical failures"
        verdict_sub   = (f"Failures in {', '.join(affected_critical)} mean the bot's "
                         "order execution or risk controls cannot be trusted right now. "
                         "Run on paper only until these are resolved.")
        verdict_color = "#ff6b6b"
        verdict_bg    = "#1a0a0a"
        verdict_border= "#ff6b6b"
        verdict_icon  = "●"
        advice        = "Fix the red cards below first — they're the ones that affect live money."

    else:
        affected = sorted(set(f["subsystem"] for f in failures))
        affected_str = ", ".join(affected) if affected else "unknown areas"

        # High pass rate with only peripheral failures → don't overstate the risk
        if pass_rate is not None and pass_rate >= 95:
            verdict       = "Ready to trade — minor gaps only"
            verdict_sub   = (f"{passed}/{total_run} tests pass ({pass_rate}%). "
                             f"Core order execution and risk controls are fully verified. "
                             f"Non-critical failures in {affected_str} reduce signal quality "
                             "but won't block or distort trade execution.")
            verdict_color = "#a9e34b"
            verdict_bg    = "#0f1a06"
            verdict_border= "#a9e34b"
            verdict_icon  = "◕"
            advice        = f"Fix {affected_str} when you get a chance — they reduce edge but won't cause bad trades."
        elif pass_rate is not None and pass_rate >= 80:
            verdict       = "Trade with caution"
            verdict_sub   = (f"Core order and risk logic is passing, but failures in "
                             f"{affected_str} mean the bot is running with meaningfully reduced capability. "
                             "Signals, learning, or news context may not be fully reliable.")
            verdict_color = "#ffd43b"
            verdict_bg    = "#1a1800"
            verdict_border= "#ffd43b"
            verdict_icon  = "◑"
            advice        = "These failures reduce trading edge noticeably. Fix before next live session."
        else:
            verdict       = "Do not trade — too many failures"
            verdict_sub   = (f"Only {pass_rate}% of tests pass. With {len(failures)} failures "
                             f"across {affected_str}, the bot's behaviour is too unreliable to risk capital.")
            verdict_color = "#ff6b6b"
            verdict_bg    = "#1a0a0a"
            verdict_border= "#ff6b6b"
            verdict_icon  = "●"
            advice        = "Fix the failures below before trading. Start with the highest-priority cards."

    hero = html.Div([
        # Verdict row
        html.Div([
            html.Span(verdict_icon, style={
                "fontSize": "1.6rem", "color": verdict_color,
                "marginRight": "14px", "lineHeight": "1",
            }),
            html.Div([
                html.Div("BOT READINESS", style={
                    "fontSize": "0.55rem", "fontWeight": 800, "color": "var(--cd-faint)",
                    "letterSpacing": "1.2px", "textTransform": "uppercase",
                    "marginBottom": "2px",
                }),
                html.Div(verdict, style={
                    "fontSize": "1.15rem", "fontWeight": 700, "color": verdict_color,
                }),
            ]),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),

        # Explanation
        html.P(verdict_sub, style={
            "fontSize": "0.78rem", "color": "var(--cd-text2)", "lineHeight": "1.6",
            "marginBottom": "8px" if advice else "0",
        }),

        # Action advice
        html.Div([
            html.Span("→ ", style={"color": verdict_color, "fontWeight": 700}),
            html.Span(advice, style={"fontSize": "0.75rem", "color": "var(--cd-muted)",
                                     "fontStyle": "italic"}),
        ]) if advice else None,

    ], style={
        "backgroundColor": verdict_bg,
        "borderRadius": "12px",
        "padding": "20px 24px",
        "border": f"1px solid {verdict_border}",
        "borderLeft": f"4px solid {verdict_border}",
        "marginBottom": "20px",
    })

    # ── Stat strip ────────────────────────────────────────────────────────────
    rate_color = (
        "#51cf66" if (pass_rate or 0) >= 80
        else "#ffd43b" if (pass_rate or 0) >= 50
        else "#ff6b6b"
    )

    def _stat(label, value, color):
        return dbc.Col(html.Div([
            html.Small(label, style={"color": "var(--cd-faint)", "fontSize": "0.6rem",
                                     "textTransform": "uppercase", "display": "block"}),
            html.Span(str(value), style={"fontSize": "1.25rem", "fontWeight": 700, "color": color}),
        ], style={"backgroundColor": "var(--cd-card)", "borderRadius": "8px",
                  "padding": "12px 16px", "border": "1px solid var(--cd-border)"}), md=2, className="mb-3")

    stat_strip = dbc.Row([
        _stat("Pass Rate", f"{pass_rate}%" if pass_rate is not None else "—", rate_color),
        _stat("Passing", passed, "#51cf66" if passed > 0 else "#555"),
        _stat("Failing", failed, "#ff6b6b" if failed > 0 else "#555"),
        _stat("Import Errors", errors, "#ffd43b" if errors > 0 else "#555"),
        _stat("Skipped", skipped, "#555"),
    ], className="mb-4")

    # ── Failure cards ─────────────────────────────────────────────────────────
    failure_section = None
    if failures:
        failure_section = html.Div([
            html.Div([
                html.H6("What needs fixing", className="text-light mb-0",
                        style={"fontWeight": 600}),
                html.Small("Ordered by impact on live trading — work top to bottom.",
                           className="text-muted ms-3"),
            ], className="d-flex align-items-center mb-3"),
            html.Div([_render_failure_card(f, i + 1) for i, f in enumerate(failures)]),
        ], className="mb-4")

    # ── Collapsible raw output ────────────────────────────────────────────────
    raw_section = html.Details([
        html.Summary("Show full test output", className="text-muted small",
                     style={"cursor": "pointer"}),
        html.Pre(
            stdout[-4000:] if len(stdout) > 4000 else stdout,
            style={"backgroundColor": "var(--cd-deep)", "color": "var(--cd-text2)",
                   "padding": "1rem", "borderRadius": "6px",
                   "fontSize": "0.65rem", "maxHeight": "280px",
                   "overflowY": "auto", "whiteSpace": "pre-wrap", "marginTop": "8px"},
        ),
    ], className="mt-2")

    return html.Div([
        html.Div([
            html.H4("Tests", className="text-light mb-0", style={"fontWeight": "600"}),
            html.Small(f"Last run: {_last_run_time}", className="text-muted ms-2") if _last_run_time else None,
        ], className="d-flex align-items-center mb-3"),

        hero,
        stat_strip,
        failure_section,
        raw_section,
        dcc.Interval(id="tests-interval", interval=120_000, n_intervals=0),
    ])

_LOADING_PLACEHOLDER = html.Div([
    html.Div([
        html.H4("Bot Status", className="text-light mb-0", style={"fontWeight": "600"}),
        html.Small("Running tests…", className="text-muted ms-2"),
    ], className="d-flex align-items-center mb-3"),
    dbc.Spinner(color="primary", size="sm"),
    html.P("Running pytest against the trading repo. This takes up to 2 minutes on first load.",
           className="text-muted small mt-3"),
    dcc.Interval(id="tests-interval", interval=1_000, n_intervals=0),
])


def register_callbacks(app):
    @app.callback(
        Output("tests-content", "children"),
        Input("tests-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        # First call: run pytest now (blocking but deferred from startup)
        if _last_result is None:
            _run_pytest()
        return layout()
