"""
Code Health panel — visual overview of codebase size, complexity, and quality.
Shows progress bars for module sizes, gauge for test coverage, and lint results.
"""

import subprocess
from pathlib import Path
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import DECIFER_REPO_PATH


def _count_lines(path):
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:
        return 0


def _get_module_stats():
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return None, "Can't find the Decifer repo. Check your settings."

    py_files = sorted(
        [f for f in DECIFER_REPO_PATH.glob("*.py") if not f.name.startswith("_")],
        key=lambda f: _count_lines(f),
        reverse=True
    )
    test_files = list((DECIFER_REPO_PATH / "tests").glob("*.py")) if (DECIFER_REPO_PATH / "tests").exists() else []

    modules = [{"file": f.name, "lines": _count_lines(f)} for f in py_files[:25]]
    total_src_lines = sum(m["lines"] for m in modules)
    total_test_lines = sum(_count_lines(f) for f in test_files)

    return {
        "modules": modules,
        "total_src_lines": total_src_lines,
        "total_test_lines": total_test_lines,
        "test_file_count": len(test_files),
        "src_file_count": len(py_files),
    }, None


def _get_lint_summary():
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return None
    try:
        result = subprocess.run(
            ["python", "-m", "flake8", "--count", "--statistics", "--max-line-length=120", "."],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=30
        )
        lines = (result.stdout + result.stderr).strip().split("\n")
        total = 0
        stats = []
        for line in lines[-10:]:
            if line.strip().isdigit():
                total = int(line.strip())
            elif line.strip() and line[0].isdigit():
                parts = line.strip().split()
                if len(parts) >= 2:
                    stats.append({"count": parts[0], "code": " ".join(parts[1:])})
        return {"total": total, "stats": stats[:5]}
    except Exception:
        return None


# Module descriptions for the bar chart
_MODULE_NAMES = {
    "bot.py": "Trading Bot Core",
    "dashboard.py": "Trading Dashboard",
    "orders.py": "Order Management",
    "ml_engine.py": "ML Engine",
    "portfolio_optimizer.py": "Portfolio Optimizer",
    "signals.py": "Signal Engine",
    "backtester.py": "Backtester",
    "smart_execution.py": "Smart Execution",
    "social_sentiment.py": "Social Sentiment",
    "ibkr_streaming.py": "IBKR Streaming",
    "news_sentinel.py": "News Sentinel",
    "agents.py": "AI Agent Council",
    "options.py": "Options Trading",
    "news.py": "News Feed",
    "daily_journal.py": "Daily Journal",
    "theme_tracker.py": "Theme Tracker",
    "scanner.py": "Market Scanner",
    "options_scanner.py": "Options Scanner",
    "learning.py": "Learning",
    "config.py": "Configuration",
    "risk.py": "Risk Management",
    "sentinel_agents.py": "Sentinel Agents",
    "patch.py": "Utilities",
}


PRIORITY_COLORS = {"high": "#ff6b6b", "medium": "#ffd43b", "low": "#4dabf7"}
PRIORITY_BG     = {"high": "#1a0a0a", "medium": "#1a1800", "low": "#0d1a2a"}
PRIORITY_BORDER = {"high": "#ff6b6b40", "medium": "#ffd43b40", "low": "#4dabf740"}


def _generate_recommendations(stats, lint):
    """Analyse the codebase data and return actionable recommendations."""
    recs = []
    ratio = (stats["total_test_lines"] / stats["total_src_lines"] * 100) if stats["total_src_lines"] else 0

    # Large modules that should be split
    for m in stats["modules"]:
        if m["lines"] > 1500:
            name = _MODULE_NAMES.get(m["file"], m["file"])
            recs.append({
                "priority": "high",
                "icon": "✂️",
                "title": f"Split {m['file']} — {m['lines']:,} lines",
                "detail": (f"{name} is very large. Files over 1,500 lines become hard to test, "
                           "review, and debug. A bug anywhere in the file requires reading all of it."),
                "action": f"Ask Cowork to break {m['file']} into focused sub-modules (e.g. separate data, logic, and I/O).",
            })
        elif m["lines"] > 800:
            name = _MODULE_NAMES.get(m["file"], m["file"])
            recs.append({
                "priority": "low",
                "icon": "📐",
                "title": f"{m['file']} is getting large ({m['lines']:,} lines)",
                "detail": f"{name} is approaching the threshold where splitting becomes worthwhile.",
                "action": f"Keep an eye on {m['file']} — consider splitting if it grows past 1,000 lines.",
            })

    # Test coverage
    if ratio < 25:
        recs.append({
            "priority": "high",
            "icon": "🧪",
            "title": f"Low test coverage — {ratio:.0f}% ratio",
            "detail": ("Test code is only a small fraction of source code. "
                       "Without tests, bugs in risk.py or orders.py can go undetected until a bad trade."),
            "action": "Prioritise writing tests for risk.py, orders.py, and signals.py before adding new features.",
        })
    elif ratio < 50:
        recs.append({
            "priority": "medium",
            "icon": "🧪",
            "title": f"Test coverage can be stronger — {ratio:.0f}% ratio",
            "detail": "Industry standard for financial code is 70–80%. Low coverage means regressions can hide.",
            "action": "Add tests for the highest-risk modules (risk.py, orders.py, signals.py).",
        })

    # Missing test files for critical modules
    critical_modules = ["risk.py", "orders.py", "signals.py", "bot.py"]
    if DECIFER_REPO_PATH and DECIFER_REPO_PATH.exists():
        test_dir = DECIFER_REPO_PATH / "tests"
        for mod in critical_modules:
            test_name = f"test_{mod}"
            if not (test_dir / test_name).exists():
                name = _MODULE_NAMES.get(mod, mod)
                recs.append({
                    "priority": "high",
                    "icon": "⚠️",
                    "title": f"No test file for {mod}",
                    "detail": (f"{name} has no dedicated test file. "
                               "If this module breaks, you won't know until the bot misbehaves in production."),
                    "action": f"Ask Cowork to write tests/{test_name} covering the core logic.",
                })

    # Lint issues
    if lint and lint["total"] > 100:
        recs.append({
            "priority": "high",
            "icon": "🔍",
            "title": f"{lint['total']} lint issues — clean-up needed",
            "detail": "High lint count often masks real bugs (unused variables, shadowed names, wrong types). Hard to spot the real issues.",
            "action": "Ask Cowork to run a dedicated lint clean-up session (flake8 --max-line-length=120).",
        })
    elif lint and lint["total"] > 30:
        recs.append({
            "priority": "medium",
            "icon": "🔍",
            "title": f"{lint['total']} lint issues",
            "detail": "Moderate lint count. Not blocking but worth cleaning periodically.",
            "action": "Fix lint issues in batches during refactor sessions.",
        })

    # Keep only top 5, sorted by priority
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order.get(r["priority"], 9))
    return recs[:5]


def _render_recommendations(recs):
    if not recs:
        return html.Div([
            html.Div([
                html.Span("✅", style={"fontSize": "1.2rem", "marginRight": "10px"}),
                html.Span("No significant health issues detected.", style={
                    "fontWeight": "600", "color": "#51cf66", "fontSize": "0.88rem",
                }),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={
            "backgroundColor": "#0d1f11", "borderRadius": "10px", "padding": "16px 20px",
            "border": "1px solid #51cf6640",
        })

    cards = []
    for i, r in enumerate(recs):
        color  = PRIORITY_COLORS[r["priority"]]
        bg     = PRIORITY_BG[r["priority"]]
        border = PRIORITY_BORDER[r["priority"]]
        cards.append(html.Div([
            html.Div([
                html.Span(r["icon"], style={"fontSize": "1rem", "marginRight": "8px"}),
                html.Span(r["title"], style={
                    "fontWeight": "700", "fontSize": "0.83rem", "color": color,
                }),
                html.Span(
                    r["priority"].upper(),
                    style={
                        "fontSize": "0.55rem", "fontWeight": "800", "marginLeft": "10px",
                        "padding": "2px 7px", "borderRadius": "4px",
                        "backgroundColor": f"{color}20", "color": color,
                        "letterSpacing": "0.5px",
                    }
                ),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "6px"}),
            html.Div(r["detail"], style={
                "fontSize": "0.72rem", "color": "var(--cd-text2)", "lineHeight": "1.5",
                "marginBottom": "8px",
            }),
            html.Div([
                html.Span("→ ", style={"color": color, "fontWeight": "700"}),
                html.Span(r["action"], style={
                    "fontSize": "0.70rem", "color": "var(--cd-muted)", "fontStyle": "italic",
                }),
            ]),
        ], style={
            "backgroundColor": bg, "borderRadius": "9px", "padding": "14px 16px",
            "border": f"1px solid {border}", "borderLeft": f"3px solid {color}",
            "marginBottom": "10px",
        }))

    return html.Div(cards)


def layout():
    stats, error = _get_module_stats()

    if error:
        return html.Div([
            html.H4("Code Health", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(error, className="text-warning"),
            html.P("Make sure DECIFER_REPO_PATH is set correctly in your .env file.", className="text-muted small"),
            dcc.Interval(id="health-interval", interval=120_000, n_intervals=0),
        ])

    lint = _get_lint_summary()

    # Summary cards with gauges
    ratio = (stats["total_test_lines"] / stats["total_src_lines"] * 100) if stats["total_src_lines"] else 0
    ratio_color = "success" if ratio >= 50 else "warning" if ratio >= 25 else "danger"

    summary = dbc.Row([
        dbc.Col(html.Div([
            html.Span(str(stats["src_file_count"]), style={"fontSize": "1.5rem", "fontWeight": "700", "color": "#4dabf7"}),
            html.Small(" source modules", className="text-muted d-block"),
        ], className="text-center", style={"backgroundColor": "var(--cd-card)", "borderRadius": "8px", "padding": "16px", "border": "1px solid var(--cd-border)"}), md=3),

        dbc.Col(html.Div([
            html.Span(f"{stats['total_src_lines']:,}", style={"fontSize": "1.5rem", "fontWeight": "700", "color": "var(--cd-text2)"}),
            html.Small(" lines of code", className="text-muted d-block"),
        ], className="text-center", style={"backgroundColor": "var(--cd-card)", "borderRadius": "8px", "padding": "16px", "border": "1px solid var(--cd-border)"}), md=3),

        dbc.Col(html.Div([
            html.Span(str(stats["test_file_count"]), style={"fontSize": "1.5rem", "fontWeight": "700", "color": "#51cf66"}),
            html.Small(" test files", className="text-muted d-block"),
        ], className="text-center", style={"backgroundColor": "var(--cd-card)", "borderRadius": "8px", "padding": "16px", "border": "1px solid var(--cd-border)"}), md=3),

        dbc.Col(html.Div([
            dbc.Progress(
                value=min(ratio, 100), color=ratio_color,
                style={"height": "6px", "backgroundColor": "#2a2a3e"},
                className="mb-2",
            ),
            html.Span(f"{ratio:.0f}%", style={"fontSize": "1.3rem", "fontWeight": "700", "color": f"var(--bs-{ratio_color})"}),
            html.Small(" test/code ratio", className="text-muted d-block"),
        ], className="text-center", style={"backgroundColor": "var(--cd-card)", "borderRadius": "8px", "padding": "16px", "border": "1px solid var(--cd-border)"}), md=3),
    ], className="mb-4")

    # Module size bars (visual, no raw numbers)
    max_lines = stats["modules"][0]["lines"] if stats["modules"] else 1
    module_bars = []
    for m in stats["modules"]:
        bar_pct = int((m["lines"] / max_lines) * 100)
        friendly_name = _MODULE_NAMES.get(m["file"], m["file"].replace(".py", "").replace("_", " ").title())

        # Color based on size (bigger = more attention needed)
        if m["lines"] > 1500:
            bar_color = "#ff6b6b"
        elif m["lines"] > 800:
            bar_color = "#ffd43b"
        else:
            bar_color = "#4dabf7"

        module_bars.append(
            html.Div([
                html.Div([
                    html.Span(friendly_name, className="text-light", style={"fontSize": "0.8rem", "fontWeight": "500", "minWidth": "180px", "display": "inline-block"}),
                ]),
                html.Div([
                    html.Div(style={
                        "width": f"{bar_pct}%",
                        "height": "6px",
                        "backgroundColor": bar_color,
                        "borderRadius": "3px",
                        "minWidth": "2px",
                        "transition": "width 0.3s ease",
                    }),
                ], style={"flex": "1", "margin": "0 12px", "backgroundColor": "var(--cd-stripe)", "borderRadius": "3px"}),
                html.Small(f"{m['lines']:,}", className="text-muted", style={"minWidth": "50px", "textAlign": "right", "fontSize": "0.75rem"}),
            ], className="d-flex align-items-center py-1")
        )

    # Lint section
    lint_section = None
    if lint is not None:
        if lint["total"] == 0:
            lint_section = html.Div([
                html.H6("Code Quality", className="text-light mb-2", style={"fontWeight": "600"}),
                html.Div([
                    html.Span("Clean", style={"color": "#51cf66", "fontWeight": "700", "fontSize": "1.1rem"}),
                    html.P("No linting issues found.", className="text-muted small mb-0"),
                ], style={
                    "backgroundColor": "var(--cd-ok-bg)",
                    "borderRadius": "8px",
                    "padding": "16px",
                    "border": "1px solid #51cf66",
                }),
            ])
        else:
            lint_items = []
            for s in lint.get("stats", []):
                lint_items.append(
                    html.Div([
                        dbc.Badge(s["count"], color="warning", className="me-2", style={"fontSize": "0.7rem"}),
                        html.Small(s["code"], className="text-muted"),
                    ], className="py-1")
                )
            lint_section = html.Div([
                html.H6("Code Quality", className="text-light mb-2", style={"fontWeight": "600"}),
                html.Div([
                    html.Span(f"{lint['total']} issue{'s' if lint['total'] != 1 else ''}", style={
                        "color": "#ffd43b" if lint["total"] < 50 else "#ff6b6b",
                        "fontWeight": "700", "fontSize": "1.1rem",
                    }),
                    html.Div(lint_items, className="mt-2"),
                ], style={
                    "backgroundColor": "#2a2a1a" if lint["total"] < 50 else "#2a1a1a",
                    "borderRadius": "8px",
                    "padding": "16px",
                    "border": f"1px solid {'#ffd43b' if lint['total'] < 50 else '#ff6b6b'}",
                }),
            ])

    recs = _generate_recommendations(stats, lint)

    return html.Div([
        html.H4("Code Health", className="text-light mb-3", style={"fontWeight": "600"}),
        summary,

        # ── Recommendations ─────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Span("RECOMMENDATIONS", style={
                    "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
                    "color": "var(--cd-muted)", "textTransform": "uppercase",
                }),
                html.Span(
                    f" — {len(recs)} action{'s' if len(recs) != 1 else ''} identified" if recs else " — codebase looks healthy",
                    style={"fontSize": "0.62rem", "color": "var(--cd-faint)", "marginLeft": "8px"},
                ),
            ], style={"marginBottom": "12px"}),
            _render_recommendations(recs),
        ], style={
            "backgroundColor": "var(--cd-card)",
            "border": "1px solid var(--cd-border)",
            "borderRadius": "12px",
            "padding": "20px",
            "marginBottom": "24px",
        }),

        dbc.Row([
            dbc.Col([
                html.H6("Module Sizes", className="text-light mb-2", style={"fontWeight": "600"}),
                html.P(
                    "Larger modules may benefit from being split up. Red bars are over 1,500 lines.",
                    className="text-muted small mb-3",
                ),
                html.Div(module_bars),
            ], md=8),
            dbc.Col([
                lint_section or html.P("Linter not available.", className="text-muted small"),
            ], md=4),
        ]),

        dcc.Interval(id="health-interval", interval=120_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("health-content", "children"),
        Input("health-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()
