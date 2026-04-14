"""
Code Changes panel — commits categorized by which part of the bot they touch.
Groups changes by subsystem. Cards match Research style. Clickable for details.
"""

import json
import subprocess
import re
import dash
from dash import html, dcc, Input, Output, ALL, MATCH, callback_context
import dash_bootstrap_components as dbc
from config import DECIFER_REPO_PATH


# ── Module → Subsystem mapping ───────────────────────────────────────────────

FILE_TO_SUBSYSTEM = {
    "bot.py": "Trading Core",
    "orders.py": "Trading Core",
    "smart_execution.py": "Trading Core",
    "signals.py": "Signal Generation",
    "scanner.py": "Signal Generation",
    "options_scanner.py": "Signal Generation",
    "agents.py": "AI & Learning",
    "sentinel_agents.py": "AI & Learning",
    "ml_engine.py": "AI & Learning",
    "learning.py": "AI & Learning",
    "risk.py": "Risk & Portfolio",
    "portfolio_optimizer.py": "Risk & Portfolio",
    "news.py": "News & Sentiment",
    "news_sentinel.py": "News & Sentiment",
    "social_sentiment.py": "News & Sentiment",
    "theme_tracker.py": "News & Sentiment",
    "data_collector.py": "Market Data",
    "ibkr_streaming.py": "Market Data",
    "backtester.py": "Analytics & UI",
    "dashboard.py": "Analytics & UI",
    "daily_journal.py": "Analytics & UI",
    "options.py": "Options",
    "config.py": "Configuration",
    "patch.py": "Utilities",
    "signals_integration_example.py": "Utilities",
}

SUBSYSTEM_COLORS = {
    "Trading Core": "#4dabf7",
    "Signal Generation": "#51cf66",
    "AI & Learning": "#ffd43b",
    "Risk & Portfolio": "#ff6b6b",
    "News & Sentiment": "#fcc419",
    "Market Data": "#74c0fc",
    "Analytics & UI": "#74c0fc",
    "Options": "#da77f2",
    "Configuration": "#868e96",
    "Utilities": "#868e96",
    "Tests": "#74c0fc",
    "Docs": "#868e96",
    "Multiple": "#e9ecef",
}

SUBSYSTEM_ICONS = {
    "Trading Core": "\u26A1",
    "Signal Generation": "\U0001F4CA",
    "AI & Learning": "\U0001F9E0",
    "Risk & Portfolio": "\U0001F6E1\uFE0F",
    "News & Sentiment": "\U0001F4F0",
    "Market Data": "\U0001F5C4\uFE0F",
    "Analytics & UI": "\U0001F4CA",
    "Options": "\U0001F4C8",
    "Configuration": "\u2699\uFE0F",
    "Utilities": "\U0001F527",
    "Tests": "\u2705",
    "Docs": "\U0001F4DD",
    "Multiple": "\U0001F4E6",
}


# ── Data loaders ─────────────────────────────────────────────────────────────

def _get_commits():
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return None, "Can't find the Decifer repo."
    try:
        result = subprocess.run(
            ["git", "log", "--format=%h|%an|%ar|%aI|%s", "--name-only", "-25"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None, "Couldn't read git history."

        commits = []
        current = None
        for line in result.stdout.splitlines():
            if "|" in line and line.count("|") >= 4:
                if current:
                    commits.append(current)
                parts = line.split("|", 4)
                current = {
                    "short": parts[0],
                    "author": parts[1],
                    "when": parts[2],
                    "iso_date": parts[3],
                    "message": parts[4],
                    "files": [],
                }
            elif current and line.strip():
                current["files"].append(line.strip())

        if current:
            commits.append(current)

        # Determine subsystem for each commit
        for c in commits:
            subsystems = set()
            for f in c["files"]:
                fname = f.split("/")[-1]
                if fname.startswith("test_"):
                    subsystems.add("Tests")
                elif fname in FILE_TO_SUBSYSTEM:
                    subsystems.add(FILE_TO_SUBSYSTEM[fname])
                elif fname.endswith(".md") or fname.endswith(".txt"):
                    subsystems.add("Docs")

            if len(subsystems) == 0:
                c["subsystem"] = "Other"
            elif len(subsystems) == 1:
                c["subsystem"] = subsystems.pop()
            else:
                # Use the most significant subsystem
                priority = ["Trading Core", "Signal Generation", "AI & Learning",
                            "Risk & Portfolio", "News & Sentiment", "Market Data",
                            "Options", "Analytics & UI", "Tests", "Configuration"]
                c["subsystem"] = "Multiple"
                for p in priority:
                    if p in subsystems:
                        c["subsystem"] = p
                        break
                c["all_subsystems"] = sorted(subsystems)

        return commits, None
    except Exception:
        return None, "Error reading git history."


def _get_branch_info():
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return "unknown", []
    try:
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=DECIFER_REPO_PATH, capture_output=True, text=True, timeout=5
        ).stdout.strip()
        branches = subprocess.run(
            ["git", "branch", "--sort=-committerdate"],
            cwd=DECIFER_REPO_PATH, capture_output=True, text=True, timeout=5
        ).stdout.strip().split("\n")
        branch_names = [b.strip().lstrip("* ").strip() for b in branches if b.strip()]
        return current, branch_names[:10]
    except Exception:
        return "unknown", []


def _classify_commit(msg):
    msg_lower = msg.lower()
    if msg_lower.startswith("fix") or "bug" in msg_lower:
        return "Bug Fix", "danger"
    elif msg_lower.startswith("feat") or "add " in msg_lower:
        return "Feature", "success"
    elif msg_lower.startswith("refactor") or "clean" in msg_lower:
        return "Refactor", "warning"
    elif msg_lower.startswith("test"):
        return "Tests", "info"
    elif msg_lower.startswith("doc"):
        return "Docs", "secondary"
    elif "initial" in msg_lower or "v3" in msg_lower:
        return "Release", "primary"
    else:
        return "Update", "primary"


# Maps (change_type, subsystem) → a short sentence describing the trading impact
_IMPACT_TEMPLATES = {
    ("Bug Fix",  "Trading Core"):       "A defect in order routing or execution was corrected — trades should now submit and fill more reliably.",
    ("Bug Fix",  "Signal Generation"):  "A signal calculation bug was fixed — entries and exits should now fire at the right time.",
    ("Bug Fix",  "Risk & Portfolio"):   "A risk management defect was fixed — position sizing and drawdown limits now behave correctly.",
    ("Bug Fix",  "AI & Learning"):      "An agent bug was resolved — the decision council should now reason correctly.",
    ("Bug Fix",  "News & Sentiment"):   "A news or sentiment bug was fixed — market context now feeds correctly into decisions.",
    ("Bug Fix",  "Market Data"):        "A data pipeline bug was corrected — price data and feeds are now more reliable.",
    ("Feature",  "Trading Core"):       "New order routing or execution capability added — the bot can handle more trade scenarios.",
    ("Feature",  "Signal Generation"):  "New signal dimension added — the bot can now detect additional market patterns.",
    ("Feature",  "Risk & Portfolio"):   "New risk protection added — the bot's safety guardrails are now stronger.",
    ("Feature",  "AI & Learning"):      "AI agent enhanced — the decision council is now smarter or covers more scenarios.",
    ("Feature",  "News & Sentiment"):   "New market context source added — headlines and sentiment now feed into more decisions.",
    ("Refactor", "Trading Core"):       "Order code reorganised — no behaviour change, but now easier to test and extend.",
    ("Refactor", "Signal Generation"):  "Signal code reorganised — no behaviour change, cleaner to work with.",
    ("Refactor", "Risk & Portfolio"):   "Risk code restructured — no behaviour change, clearer logic.",
    ("Update",   "Trading Core"):       "Trading core updated — order routing or execution behaviour may have changed.",
    ("Update",   "Signal Generation"):  "Signal logic updated — entry/exit logic may have been tuned.",
    ("Update",   "Risk & Portfolio"):   "Risk parameters updated — position sizing or limit thresholds may have changed.",
    ("Update",   "Configuration"):      "Config updated — thresholds, keys, or parameters have been adjusted.",
    ("Tests",    "Tests"):              "Test coverage expanded — more of the bot's behaviour is now verified.",
    ("Release",  "Multiple"):           "New release — multiple systems updated together.",
    ("Docs",     "Docs"):               "Documentation updated — design notes or strategy docs revised.",
}

def _impact_statement(change_type, subsystem, msg):
    """Return a short sentence describing what this commit achieved for the bot."""
    key = (change_type, subsystem)
    if key in _IMPACT_TEMPLATES:
        return _IMPACT_TEMPLATES[key]
    # Generic fallback using change type
    fallbacks = {
        "Bug Fix":  "A defect was corrected — behaviour should be more reliable.",
        "Feature":  "A new capability was added to the bot.",
        "Refactor": "Internal code restructured — no trading behaviour changed.",
        "Tests":    "Test coverage improved.",
        "Update":   "Code updated — check the diff for specifics.",
        "Docs":     "Documentation updated.",
        "Release":  "New version released.",
    }
    return fallbacks.get(change_type, "Codebase updated.")


# ── Card renderer ────────────────────────────────────────────────────────────

def _render_commit_card(c, index):
    """A commit card categorized by subsystem."""
    subsystem = c.get("subsystem", "Other")
    accent = SUBSYSTEM_COLORS.get(subsystem, "#868e96")
    icon = SUBSYSTEM_ICONS.get(subsystem, "\U0001F4E6")
    change_type, badge_color = _classify_commit(c["message"])
    impact = _impact_statement(change_type, subsystem, c["message"])

    msg = c["message"]
    if len(msg) > 100:
        msg = msg[:97] + "..."

    # Build expanded detail
    detail_lines = [f"Committed {c['when']} by {c['author']}."]
    if c["files"]:
        detail_lines.append(f"Changed {len(c['files'])} file{'s' if len(c['files']) != 1 else ''}:")
        for f in c["files"][:10]:
            fname = f.split("/")[-1]
            sub = FILE_TO_SUBSYSTEM.get(fname, "")
            detail_lines.append(f"  \u2022 {fname}" + (f" ({sub})" if sub else ""))
        if len(c["files"]) > 10:
            detail_lines.append(f"  ... and {len(c['files']) - 10} more.")
    if c.get("all_subsystems"):
        detail_lines.append(f"Touches: {', '.join(c['all_subsystems'])}")

    expanded = html.Div([
        html.Div(style={"borderTop": "1px solid var(--cd-border)", "margin": "10px 0"}),
        html.Div([
            html.Div(line, style={
                "fontSize": "0.75rem", "color": "var(--cd-text2)", "lineHeight": "1.6",
                "paddingLeft": "8px" if line.startswith("  ") else "0",
            }) for line in detail_lines
        ]),
        html.Div([
            dbc.Badge(f.split("/")[-1], color="secondary", className="me-1 mb-1", style={"fontSize": "0.55rem"})
            for f in c["files"][:8]
        ], className="mt-2") if c["files"] else None,
    ], id={"type": "git-detail", "index": index}, style={"display": "none"})

    return dbc.Col(
        html.Div([
            # Header: subsystem + change type
            html.Div([
                html.Span(icon, style={"marginRight": "6px", "fontSize": "0.8rem"}),
                dbc.Badge(subsystem, style={"fontSize": "0.6rem", "backgroundColor": accent, "color": "#1a1a2e", "fontWeight": "600"}, className="me-2"),
                dbc.Badge(change_type, color=badge_color, style={"fontSize": "0.55rem"}),
            ], className="mb-2"),

            # Commit message
            html.Div(msg, style={
                "fontWeight": "600", "fontSize": "0.85rem", "color": "var(--cd-text)",
                "lineHeight": "1.4", "marginBottom": "5px",
            }),

            # Impact achieved
            html.Div(impact, style={
                "fontSize": "0.68rem", "color": "var(--cd-text2)",
                "lineHeight": "1.45", "marginBottom": "7px",
                "fontStyle": "italic",
            }),

            # Author + time
            html.Div([
                html.Small(c["author"], className="text-muted me-2"),
                html.Small(c["when"], className="text-muted"),
            ]),

            # File count
            html.Div([
                html.Small(f"{len(c['files'])} file{'s' if len(c['files']) != 1 else ''}", style={"color": "var(--cd-faint)", "fontSize": "0.65rem"}),
            ], className="mt-1") if c["files"] else None,

            # Click hint
            html.Div([
                html.Small(
                    "\u25B6 Click for details",
                    id={"type": "git-hint", "index": index},
                    style={"color": "var(--cd-faint)", "fontSize": "0.6rem"},
                ),
            ], className="mt-2"),

            expanded,
        ], id={"type": "git-card", "index": index}, n_clicks=0, style={
            "backgroundColor": "var(--cd-card)", "borderRadius": "8px", "padding": "14px 16px",
            "borderLeft": f"3px solid {accent}",
            "border": "1px solid var(--cd-border)", "height": "100%", "cursor": "pointer",
        }),
        md=4, className="mb-3",
    )


# ── Main layout ──────────────────────────────────────────────────────────────

def layout():
    commits, error = _get_commits()
    current_branch, branches = _get_branch_info()

    if error:
        return html.Div([
            html.H4("Code Changes", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(error, className="text-warning"),
            html.P("Make sure DECIFER_REPO_PATH is set correctly.", className="text-muted small"),
            dcc.Interval(id="git-interval", interval=60_000, n_intervals=0),
        ])

    # Count by subsystem
    sub_counts = {}
    for c in (commits or []):
        s = c.get("subsystem", "Other")
        sub_counts[s] = sub_counts.get(s, 0) + 1

    # Filter buttons by subsystem
    filter_buttons = [
        dbc.Button(
            [f"All ", dbc.Badge(str(len(commits or [])), color="light", text_color="dark", className="ms-1")],
            id={"type": "git-filter", "index": "all"},
            n_clicks=0, color="light", size="sm", className="me-2 mb-2",
            outline=True, style={"fontSize": "0.75rem"},
        ),
    ]
    for sub, count in sorted(sub_counts.items(), key=lambda x: x[1], reverse=True):
        color = SUBSYSTEM_COLORS.get(sub, "#868e96")
        icon = SUBSYSTEM_ICONS.get(sub, "")
        filter_buttons.append(
            dbc.Button(
                [f"{icon} {sub} ", dbc.Badge(str(count), color="light", text_color="dark", className="ms-1")],
                id={"type": "git-filter", "index": sub},
                n_clicks=0, color="secondary", size="sm", className="me-2 mb-2",
                outline=True, style={"fontSize": "0.72rem"},
            )
        )

    # Branch info
    local_branches = [b for b in branches if not b.startswith("remotes/")]
    branch_section = html.Div([
        html.Small("Branch: ", className="text-muted"),
        dbc.Badge(current_branch, color="primary", className="me-2", style={"fontSize": "0.7rem"}),
        *[dbc.Badge(b, color="secondary", className="me-1", style={"fontSize": "0.6rem"})
          for b in local_branches if b != current_branch],
    ], className="mb-3")

    # Cards
    commit_cards = [_render_commit_card(c, i) for i, c in enumerate(commits or [])]

    return html.Div([
        html.H4("Code Changes", className="text-light mb-1", style={"fontWeight": "600"}),
        html.P(f"{len(commits or [])} recent commits, grouped by which part of the bot they touch.", className="text-muted small mb-3"),

        branch_section,
        html.Div(filter_buttons, className="mb-3"),

        html.Div(id="git-card-grid", children=[dbc.Row(commit_cards)]),
        dcc.Store(id="git-commits-data", data=[
            {"subsystem": c.get("subsystem", "Other"), "index": i}
            for i, c in enumerate(commits or [])
        ]),

        dcc.Interval(id="git-interval", interval=60_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("git-content", "children"),
        Input("git-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()

    # Filter buttons → hide/show cards by subsystem
    @app.callback(
        Output("git-card-grid", "children"),
        Input({"type": "git-filter", "index": ALL}, "n_clicks"),
        dash.dependencies.State("git-commits-data", "data"),
        prevent_initial_call=True,
    )
    def filter_by_subsystem(n_clicks_list, commits_data):
        if not commits_data:
            return []

        ctx = callback_context
        if not ctx.triggered:
            return dash.no_update

        # Find which filter was last clicked
        triggered_id = ctx.triggered[0]["prop_id"]
        try:
            parsed = json.loads(triggered_id.split(".")[0])
            selected = parsed["index"]
        except Exception:
            selected = "all"

        # Get fresh commits
        commits, error = _get_commits()
        if error or not commits:
            return []

        filtered = commits if selected == "all" else [
            c for c in commits if c.get("subsystem") == selected
        ]

        cards = [_render_commit_card(c, i) for i, c in enumerate(filtered)]
        return [dbc.Row(cards)]

    # Card click → toggle detail
    @app.callback(
        Output({"type": "git-detail", "index": MATCH}, "style"),
        Output({"type": "git-hint", "index": MATCH}, "children"),
        Input({"type": "git-card", "index": MATCH}, "n_clicks"),
        prevent_initial_call=True,
    )
    def toggle_git_detail(n_clicks):
        if not n_clicks:
            return {"display": "none"}, "\u25B6 Click for details"
        is_open = (n_clicks % 2) == 1
        if is_open:
            return {"display": "block"}, "\u25BC Hide details"
        else:
            return {"display": "none"}, "\u25B6 Click for details"
