"""
Product Map panel — visual overview of what Decifer Trading is and how it works.
Crystal-clear layout: anyone can look at this and understand what is being built.

Shows:
  1. The 6-stage trading pipeline (top row — the engine)
  2. Shared infrastructure supporting all stages
  3. Phase E features currently in the backlog
  4. Live IBKR portfolio summary (if connected)
"""

import json
import subprocess
import re
from pathlib import Path
from datetime import datetime
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import BACKLOG_FILE, SPECS_DIR, DECIFER_REPO_PATH, SESSIONS_DIR, RESEARCH_DIR


# ── Pipeline stage definitions ────────────────────────────────────────────

STAGES = [
    {
        "num": "1", "name": "Scan", "color": "#4dabf7",
        "icon": "🔍", "tagline": "Find candidates",
        "modules": ["scanner.py", "options_scanner.py", "data_collector.py"],
        "how": "3,000+ stocks screened via TradingView + yfinance in real-time",
    },
    {
        "num": "2", "name": "Score", "color": "#51cf66",
        "icon": "📊", "tagline": "9-dimension analysis",
        "modules": ["signals.py", "news_sentinel.py", "social_sentiment.py"],
        "how": "Trend · Momentum · Squeeze · Flow · Breakout · Confluence · News · Social · Mean Reversion",
    },
    {
        "num": "3", "name": "Decide", "color": "#ffd43b",
        "icon": "🧠", "tagline": "AI agent council",
        "modules": ["agents.py", "sentinel_agents.py", "ml_engine.py"],
        "how": "3 Claude agents debate: Researcher · Architect · Critic — must reach consensus",
    },
    {
        "num": "4", "name": "Risk", "color": "#ff6b6b",
        "icon": "🛡️", "tagline": "5-layer safety",
        "modules": ["risk.py", "portfolio_optimizer.py"],
        "how": "Market hours · Daily loss cap · Streak protection · Position sizing · Correlation guard",
    },
    {
        "num": "5", "name": "Execute", "color": "#74c0fc",
        "icon": "⚡", "tagline": "IBKR order routing",
        "modules": ["orders.py", "smart_execution.py", "ibkr_streaming.py"],
        "how": "Limit orders near spread · Automatic bracket (stop + target) · Real-time fill tracking",
    },
    {
        "num": "6", "name": "Learn", "color": "#da77f2",
        "icon": "📈", "tagline": "Adapt over time",
        "modules": ["learning.py", "backtester.py", "daily_journal.py"],
        "how": "ML patterns from trade results · Weekly reviews · Equity curve tracking · Signal weight tuning",
    },
]

INFRASTRUCTURE = [
    ("bot.py", "Trading Bot Core", "The main orchestration loop — runs all 6 stages on a schedule", "#4dabf7"),
    ("config.py", "Configuration", "API keys, risk parameters, IBKR account settings — all in one place", "#868e96"),
    ("dashboard.py", "Trading Dashboard", "Live web dashboard for monitoring bot activity (port 8080)", "#74c0fc"),
    ("theme_tracker.py", "Theme Tracker", "Groups stocks by investment themes, detects sector rotation", "#fcc419"),
    ("options.py", "Options Module", "Options chain analysis, Greeks calculation, contract selection", "#da77f2"),
    ("news.py", "News Feed", "Yahoo RSS + financial news aggregation for all candidates", "#fcc419"),
    ("patch.py", "Utilities", "Shared helpers and patching utilities", "#868e96"),
]


# ── Data loaders ──────────────────────────────────────────────────────────

def _load_backlog():
    items = []
    seen = set()
    if BACKLOG_FILE.exists():
        try:
            raw = json.loads(BACKLOG_FILE.read_text())
            if isinstance(raw, list):
                for d in raw:
                    if d.get("id") and d["id"] not in seen:
                        items.append(d)
                        seen.add(d["id"])
        except Exception:
            pass
    if SPECS_DIR.exists():
        for f in sorted(SPECS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                if d.get("id") and d["id"] not in seen:
                    items.append(d)
                    seen.add(d["id"])
            except Exception:
                pass
    return items


# ── Renderers ─────────────────────────────────────────────────────────────

def _stage_card(stage):
    module_pills = [
        html.Div(m, className="cd-map-module")
        for m in stage["modules"]
    ]

    return html.Div([
        # Number + icon header
        html.Div([
            html.Span(
                stage["num"],
                style={
                    "width": "22px", "height": "22px", "borderRadius": "50%",
                    "backgroundColor": stage["color"], "color": "#0a0f18",
                    "display": "inline-flex", "alignItems": "center", "justifyContent": "center",
                    "fontWeight": "800", "fontSize": "0.7rem", "marginRight": "6px",
                    "flexShrink": "0",
                }
            ),
            html.Span(stage["icon"], style={"fontSize": "1rem", "marginRight": "6px"}),
            html.Span(stage["name"], style={
                "fontWeight": "700", "fontSize": "0.85rem", "color": stage["color"],
            }),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "6px"}),

        # Tagline
        html.Div(stage["tagline"], style={
            "fontSize": "0.68rem", "color": "var(--cd-muted)",
            "marginBottom": "10px", "fontStyle": "italic",
        }),

        # Modules
        html.Div(module_pills, style={"marginBottom": "10px"}),

        # How it works
        html.Div(stage["how"], style={
            "fontSize": "0.62rem", "color": "var(--cd-text2)",
            "lineHeight": "1.5", "borderTop": "1px solid var(--cd-border)",
            "paddingTop": "8px", "marginTop": "4px",
        }),
    ], className="cd-map-stage")


def _infra_row():
    pills = []
    for fname, friendly, desc, color in INFRASTRUCTURE:
        pills.append(
            dbc.Col(
                html.Div([
                    html.Span(friendly, style={
                        "fontWeight": "600", "fontSize": "0.72rem", "color": color,
                        "display": "block", "marginBottom": "2px",
                    }),
                    html.Span(fname, style={
                        "fontSize": "0.58rem", "color": "var(--cd-muted)",
                        "fontFamily": "monospace",
                    }),
                    html.Div(desc, style={
                        "fontSize": "0.62rem", "color": "var(--cd-text2)",
                        "lineHeight": "1.4", "marginTop": "4px",
                    }),
                ], style={
                    "backgroundColor": "var(--cd-deep)",
                    "border": "1px solid var(--cd-border-sub)",
                    "borderLeft": f"3px solid {color}",
                    "borderRadius": "7px",
                    "padding": "9px 12px",
                    "height": "100%",
                }),
                md=3, sm=6, className="mb-3",
            )
        )
    return dbc.Row(pills)


def _phase_e_section(backlog):
    phase_e = [s for s in backlog if s.get("phase") == "E"]
    if not phase_e:
        return None

    STATUS_COLORS = {
        "complete": "#51cf66", "in_progress": "#ffd43b",
        "spec_complete": "#74c0fc", "backlog": "#868e96", "blocked": "#ff6b6b",
    }
    STATUS_LABELS = {
        "complete": "✓ Shipped", "in_progress": "● In Progress",
        "spec_complete": "◎ Ready", "backlog": "○ Queued", "blocked": "✕ Blocked",
    }

    cards = []
    for spec in phase_e:
        status = spec.get("status", "backlog")
        color = STATUS_COLORS.get(status, "#868e96")
        label = STATUS_LABELS.get(status, status)
        pri = spec.get("priority", "P2")

        cards.append(
            dbc.Col(
                html.Div([
                    # Status dot + priority
                    html.Div([
                        html.Span(label, style={
                            "fontSize": "0.6rem", "fontWeight": "700", "color": color,
                        }),
                        html.Span(f" · {pri}", style={
                            "fontSize": "0.58rem", "color": "var(--cd-muted)",
                        }),
                    ], style={"marginBottom": "5px"}),

                    # Title
                    html.Div(spec.get("title", ""), style={
                        "fontWeight": "600", "fontSize": "0.78rem",
                        "color": "var(--cd-text)", "lineHeight": "1.3",
                        "marginBottom": "4px",
                    }),

                    # Summary
                    html.Div(spec.get("summary", ""), style={
                        "fontSize": "0.63rem", "color": "var(--cd-muted)",
                        "lineHeight": "1.4",
                    }),

                    # Files
                    html.Div([
                        html.Span(f, style={
                            "fontSize": "0.55rem", "fontFamily": "monospace",
                            "color": "#74c0fc", "marginRight": "5px",
                        })
                        for f in (spec.get("files_affected") or [])[:3]
                    ], style={"marginTop": "6px"}),
                ], style={
                    "backgroundColor": "var(--cd-deep)",
                    "border": "1px solid var(--cd-border-sub)",
                    "borderLeft": f"3px solid {color}",
                    "borderRadius": "8px",
                    "padding": "11px 13px",
                    "height": "100%",
                }),
                md=4, sm=6, className="mb-3",
            )
        )

    return html.Div([
        html.Div([
            html.Span("Phase E — Multi-Account Features", style={
                "fontWeight": "700", "fontSize": "0.9rem", "color": "#ffa94d",
            }),
            html.Small(f"  {len(phase_e)} features", style={"color": "var(--cd-muted)", "marginLeft": "8px"}),
        ], className="mb-3"),
        dbc.Row(cards),
    ], style={
        "backgroundColor": "var(--cd-card)",
        "border": "1px solid var(--cd-border)",
        "borderRadius": "12px",
        "padding": "20px",
        "marginTop": "24px",
    })


def _overall_stats(backlog):
    total = len(backlog)
    shipped = sum(1 for s in backlog if s.get("status") == "complete")
    in_prog = sum(1 for s in backlog if s.get("status") == "in_progress")
    in_back = sum(1 for s in backlog if s.get("status") == "backlog")
    pct = int(shipped / total * 100) if total else 0

    pills = [
        (str(total), "Total Features", "var(--cd-text)"),
        (str(shipped), "Shipped", "#51cf66"),
        (str(in_prog), "In Progress", "#ffd43b"),
        (str(in_back), "Queued", "var(--cd-muted)"),
        (f"{pct}%", "Done", "#51cf66" if pct >= 50 else "#ffd43b"),
    ]

    return html.Div([
        *[
            html.Div([
                html.Div(val, style={
                    "fontSize": "1.3rem", "fontWeight": "700", "color": color,
                }),
                html.Div(label, style={
                    "fontSize": "0.58rem", "color": "var(--cd-muted)",
                    "textTransform": "uppercase", "letterSpacing": "0.4px",
                }),
            ], style={
                "backgroundColor": "var(--cd-deep)",
                "border": "1px solid var(--cd-border-sub)",
                "borderRadius": "8px",
                "padding": "10px 16px",
                "textAlign": "center",
                "minWidth": "72px",
            })
            for val, label, color in pills
        ],
        # Progress bar
        html.Div([
            html.Div(
                f"{shipped}/{total} features shipped",
                style={"fontSize": "0.58rem", "color": "var(--cd-muted)", "marginBottom": "4px"},
            ),
            html.Div([
                html.Div(style={
                    "width": f"{pct}%",
                    "backgroundColor": "#51cf66",
                    "height": "100%",
                    "borderRadius": "4px",
                    "transition": "width 0.5s ease",
                }),
            ], style={
                "height": "8px", "backgroundColor": "var(--cd-deep)",
                "borderRadius": "4px", "overflow": "hidden", "minWidth": "120px",
            }),
        ], style={"display": "flex", "flexDirection": "column", "justifyContent": "center", "flex": "1", "minWidth": "160px"}),
    ], style={"display": "flex", "gap": "10px", "alignItems": "center", "flexWrap": "wrap", "marginBottom": "24px"})


# ── Multi-Account Vision Roadmap Strip ────────────────────────────────────

def _ma_roadmap_strip(backlog):
    """Visual 3-phase roadmap for the multi-account platform build."""
    MA_PHASES = [
        {
            "id": "MA1",
            "label": "Phase 1",
            "name": "Local Multi-Account",
            "color": "#20c997",
            "desc": "Foundation — run all accounts locally before touching the cloud.",
        },
        {
            "id": "MA2",
            "label": "Phase 2",
            "name": "Cloud & Broadcasting",
            "color": "#4dabf7",
            "desc": "Brain on cloud. WebSocket signals. Per-user Docker containers.",
        },
        {
            "id": "MA3",
            "label": "Phase 3",
            "name": "User Interfaces",
            "color": "#a9e34b",
            "desc": "Web dashboard, authentication, setup screen, Telegram bot.",
        },
    ]

    def _phase_card(p):
        phase_specs = [s for s in backlog if s.get("phase") == p["id"]]
        total   = len(phase_specs)
        shipped = sum(1 for s in phase_specs if s.get("status") == "complete")
        in_prog = sum(1 for s in phase_specs if s.get("status") == "in_progress")
        pct     = int(shipped / total * 100) if total else 0
        color   = p["color"]

        if shipped == total and total > 0:
            status_label, status_color = "Complete", "#51cf66"
        elif in_prog > 0:
            status_label, status_color = "In Progress", "#ffd43b"
        else:
            status_label, status_color = "Queued", "var(--cd-muted)"

        return html.Div([
            # Phase label + status
            html.Div([
                html.Span(p["label"], style={
                    "fontSize": "0.6rem", "fontWeight": 800, "letterSpacing": "1px",
                    "color": color, "textTransform": "uppercase",
                }),
                html.Span(status_label, style={
                    "fontSize": "0.55rem", "padding": "1px 8px", "borderRadius": "4px",
                    "backgroundColor": f"{status_color}18", "color": status_color,
                    "border": f"1px solid {status_color}30", "marginLeft": "8px",
                    "fontWeight": 600,
                }),
            ], style={"marginBottom": "6px", "display": "flex", "alignItems": "center"}),

            # Phase name
            html.Div(p["name"], style={
                "fontSize": "0.88rem", "fontWeight": 700, "color": "var(--cd-text)",
                "marginBottom": "4px",
            }),

            # Description
            html.Div(p["desc"], style={
                "fontSize": "0.68rem", "color": "var(--cd-muted)",
                "lineHeight": "1.5", "marginBottom": "10px",
            }),

            # Progress bar
            html.Div([
                html.Div([
                    html.Div(style={
                        "width": f"{pct}%", "height": "100%",
                        "backgroundColor": color, "borderRadius": "3px",
                        "transition": "width 0.5s ease",
                    }),
                ], style={
                    "height": "5px", "backgroundColor": "var(--cd-deep)",
                    "borderRadius": "3px", "overflow": "hidden", "flex": "1",
                }),
                html.Span(f"{shipped}/{total}", style={
                    "fontSize": "0.6rem", "color": color,
                    "fontWeight": 700, "marginLeft": "8px", "whiteSpace": "nowrap",
                }),
            ], style={"display": "flex", "alignItems": "center"}),

        ], style={
            "flex": "1", "minWidth": "200px",
            "backgroundColor": "var(--cd-card2)",
            "border": f"1px solid {color}30",
            "borderTop": f"3px solid {color}",
            "borderRadius": "10px",
            "padding": "14px 16px",
        })

    # Arrow between phases
    arrow = html.Div("→", style={
        "color": "var(--cd-faint)", "fontSize": "1.2rem",
        "alignSelf": "center", "padding": "0 4px", "flexShrink": 0,
    })

    row = []
    for i, p in enumerate(MA_PHASES):
        row.append(_phase_card(p))
        if i < len(MA_PHASES) - 1:
            row.append(arrow)

    return html.Div([
        html.Div([
            html.Span("THE VISION", style={
                "fontSize": "0.6rem", "fontWeight": 800, "letterSpacing": "1.5px",
                "color": "var(--cd-muted)", "textTransform": "uppercase",
            }),
            html.Span(" — multi-account platform build plan", style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)", "marginLeft": "8px",
            }),
        ], style={"marginBottom": "12px"}),
        html.Div(row, style={
            "display": "flex", "gap": "8px",
            "alignItems": "stretch", "flexWrap": "wrap",
        }),
    ], style={
        "backgroundColor": "var(--cd-card)",
        "border": "1px solid var(--cd-border)",
        "borderRadius": "12px",
        "padding": "20px",
        "marginBottom": "24px",
    })


# ── Smart Recommendations ─────────────────────────────────────────────────

def _quick_test_summary():
    """Run pytest with minimal output to get a fast pass/fail count."""
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / "tests").exists():
        return None
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=no", "--no-header"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        m = re.search(r"(\d+) passed", output)
        passed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) failed", output)
        failed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) error", output)
        errors = int(m.group(1)) if m else 0
        return {"passed": passed, "failed": failed, "errors": errors,
                "ok": result.returncode == 0}
    except Exception:
        return None


def _recent_commit_summary():
    """Return the most recent commit message and how long ago it was."""
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "log", "--format=%ar|%s", "-3"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in result.stdout.strip().splitlines() if "|" in l]
        commits = []
        for line in lines:
            when, msg = line.split("|", 1)
            commits.append({"when": when.strip(), "msg": msg.strip()})
        return commits
    except Exception:
        return None


def _next_feature(backlog):
    """Return the highest-priority non-complete feature."""
    STATUS_ORDER = {"in_progress": 0, "spec_complete": 1, "backlog": 2}
    PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    PHASE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
    candidates = [s for s in backlog if s.get("status") != "complete"]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (
        STATUS_ORDER.get(s.get("status", "backlog"), 9),
        PHASE_ORDER.get(s.get("phase", "Z"), 9),   # phase before priority
        PRIORITY_ORDER.get(s.get("priority", "P2"), 9),
    ))
    return candidates[0]


def _latest_research():
    """Return the most recent research finding title."""
    if not RESEARCH_DIR.exists():
        return None
    files = sorted(RESEARCH_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        data = json.loads(files[0].read_text())
        return {"topic": data.get("topic", ""), "date": data.get("date", "")}
    except Exception:
        return None


def _build_recommendations(backlog, tests, commits, research):
    """Synthesise data from all sources into prioritised recommendations."""
    recs = []

    # ── Test health ──────────────────────────────────────────────────────
    if tests:
        total = tests["passed"] + tests["failed"]
        if tests["errors"] > 0 and total == 0:
            recs.append({
                "priority": "critical",
                "color": "#ff6b6b",
                "bg": "#1a0a0a",
                "icon": "🔴",
                "title": "Tests can't load — unknown risk",
                "body": (f"{tests['errors']} test file(s) have import errors. "
                         "You have no visibility into whether the bot's core logic is working."),
                "action": "Fix import errors in the test suite before your next session.",
            })
        elif tests["failed"] > 0:
            rate = int(tests["passed"] / total * 100) if total else 0
            if rate < 80:
                recs.append({
                    "priority": "high",
                    "color": "#ff6b6b",
                    "bg": "#1a0a0a",
                    "icon": "🔴",
                    "title": f"Test failures need attention — {rate}% passing",
                    "body": (f"{tests['failed']} test(s) failing. "
                             "Depending on which subsystems are affected, this could mean "
                             "order logic or risk controls are not verified."),
                    "action": "Open the Tests tab to see which systems are affected and fix in priority order.",
                })
            else:
                recs.append({
                    "priority": "medium",
                    "color": "#ffd43b",
                    "bg": "#1a1800",
                    "icon": "🟡",
                    "title": f"Minor test failures — {rate}% passing",
                    "body": (f"{tests['failed']} non-critical test(s) failing. "
                             "Core trading logic is likely fine but some subsystems are unverified."),
                    "action": "Fix when convenient. Check the Tests tab for details.",
                })
        elif tests["ok"]:
            recs.append({
                "priority": "good",
                "color": "#51cf66",
                "bg": "#0d1f11",
                "icon": "🟢",
                "title": f"All {tests['passed']} tests passing — bot is verified",
                "body": "Order execution, signal logic, and risk controls are all passing their tests.",
                "action": None,
            })

    # ── Pipeline state ───────────────────────────────────────────────────
    in_progress = [s for s in backlog if s.get("status") == "in_progress"]
    ready       = [s for s in backlog if s.get("status") == "spec_complete"]
    complete    = [s for s in backlog if s.get("status") == "complete"]

    if in_progress:
        f = in_progress[0]
        recs.append({
            "priority": "info",
            "color": "#ffd43b",
            "bg": "#1a1800",
            "icon": "🔨",
            "title": f"In progress: {f.get('title', '')}",
            "body": f.get("summary", ""),
            "action": "Continue in your next Cowork session — check the Pipeline tab for the prompt.",
        })
    elif ready:
        f = _next_feature(backlog)
        if f:
            recs.append({
                "priority": "info",
                "color": "#4dabf7",
                "bg": "#0d1a2a",
                "icon": "▶️",
                "title": f"Ready to build: {f.get('title', '')}",
                "body": f"Phase {f.get('phase', '?')} · {f.get('priority', 'P2')} — {f.get('summary', '')}",
                "action": "Open the Pipeline tab, copy the Cowork prompt, and start a new session.",
            })

    if complete:
        pct = int(len(complete) / len(backlog) * 100) if backlog else 0
        if pct >= 50:
            recs.append({
                "priority": "good",
                "color": "#51cf66",
                "bg": "#0d1f11",
                "icon": "🏁",
                "title": f"Roadmap is {pct}% complete — {len(complete)}/{len(backlog)} features shipped",
                "body": "Good momentum. The bullish bias fix is well underway.",
                "action": None,
            })

    # ── Research ─────────────────────────────────────────────────────────
    if research:
        recs.append({
            "priority": "info",
            "color": "#da77f2",
            "bg": "#180e20",
            "icon": "🔬",
            "title": f"Research available: {research['topic']}",
            "body": f"New findings from {research['date']} are ready to review.",
            "action": "Check the Research tab before planning your next feature.",
        })
    elif not research:
        recs.append({
            "priority": "low",
            "color": "#868e96",
            "bg": "var(--cd-card2)",
            "icon": "📋",
            "title": "No research findings yet",
            "body": "The scheduled research task hasn't run yet, or no findings have been saved.",
            "action": "Set up the scheduled research task, or ask Cowork to research a roadmap topic.",
        })

    # ── Recent activity ───────────────────────────────────────────────────
    if commits:
        latest = commits[0]
        recs.append({
            "priority": "info",
            "color": "#74c0fc",
            "bg": "#0d1826",
            "icon": "📝",
            "title": f"Last commit: {latest['msg'][:70]}{'…' if len(latest['msg']) > 70 else ''}",
            "body": f"Committed {latest['when']}.",
            "action": "Check Code Changes tab for the full recent history.",
        })

    # Deduplicate and order: critical → high → info → good → low
    ORDER = {"critical": 0, "high": 1, "info": 2, "good": 3, "low": 4}
    recs.sort(key=lambda r: ORDER.get(r["priority"], 9))
    return recs[:6]


def _render_recommendations_panel(recs):
    if not recs:
        return None

    cards = []
    for r in recs:
        action_el = html.Div([
            html.Span("→ ", style={"color": r["color"], "fontWeight": "700"}),
            html.Span(r["action"], style={
                "fontSize": "0.70rem", "color": "var(--cd-muted)", "fontStyle": "italic",
            }),
        ], style={"marginTop": "6px"}) if r.get("action") else None

        cards.append(
            dbc.Col(
                html.Div([
                    html.Div([
                        html.Span(r["icon"], style={"fontSize": "1rem", "marginRight": "8px"}),
                        html.Span(r["title"], style={
                            "fontWeight": "700", "fontSize": "0.82rem", "color": r["color"],
                            "lineHeight": "1.3",
                        }),
                    ], style={"display": "flex", "alignItems": "flex-start", "marginBottom": "6px"}),
                    html.Div(r["body"], style={
                        "fontSize": "0.71rem", "color": "var(--cd-text2)",
                        "lineHeight": "1.5", "marginBottom": "2px",
                    }) if r.get("body") else None,
                    action_el,
                ], style={
                    "backgroundColor": r["bg"],
                    "borderRadius": "9px",
                    "padding": "14px 16px",
                    "border": f"1px solid {r['color']}30",
                    "borderLeft": f"3px solid {r['color']}",
                    "height": "100%",
                }),
                md=6, className="mb-3",
            )
        )

    return html.Div([
        html.Div([
            html.Span("CHIEF'S ANALYSIS", style={
                "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
                "color": "var(--cd-muted)", "textTransform": "uppercase",
            }),
            html.Span(" — based on live tests, pipeline, and code", style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)", "marginLeft": "8px",
            }),
        ], style={"marginBottom": "14px"}),
        dbc.Row(cards),
    ], style={
        "backgroundColor": "var(--cd-card)",
        "border": "1px solid var(--cd-border)",
        "borderRadius": "12px",
        "padding": "20px",
        "marginBottom": "28px",
    })


# ── Main layout ───────────────────────────────────────────────────────────

def layout():
    backlog = _load_backlog()

    # Gather live data for recommendations
    tests    = _quick_test_summary()
    commits  = _recent_commit_summary()
    research = _latest_research()
    recs     = _build_recommendations(backlog, tests, commits, research)
    recs_panel = _render_recommendations_panel(recs)

    # Pipeline flow
    stage_cards = [_stage_card(s) for s in STAGES]
    arrows = [html.Div("→", className="cd-map-arrow") for _ in range(len(STAGES) - 1)]

    # Interleave arrows between stages
    pipeline_row = []
    for i, card in enumerate(stage_cards):
        pipeline_row.append(card)
        if i < len(arrows):
            pipeline_row.append(arrows[i])

    return html.Div([

        # Hero title
        html.Div([
            html.Div([
                html.Div("DECIFER TRADING", style={
                    "fontSize": "0.65rem", "fontWeight": "800",
                    "letterSpacing": "2px", "color": "var(--cd-muted)",
                    "textTransform": "uppercase", "marginBottom": "4px",
                }),
                html.H4(
                    "What We're Building",
                    style={
                        "fontWeight": "800", "color": "var(--cd-text)",
                        "marginBottom": "4px", "fontSize": "1.3rem",
                    },
                ),
                html.P(
                    "A multi-account AI trading platform. One shared brain scans the market, "
                    "scores opportunities, and debates them with Claude agents. Each user gets "
                    "their own isolated container, their own IBKR account, and their own "
                    "strategy settings — all visible from a web dashboard and Telegram. "
                    "One brain. Every person controls their own execution.",
                    style={
                        "fontSize": "0.82rem", "color": "var(--cd-text2)",
                        "lineHeight": "1.6", "maxWidth": "700px", "marginBottom": "0",
                    },
                ),
            ]),
            html.Div([
                html.Div("🏦", style={"fontSize": "2.5rem"}),
                html.Div("IBKR", style={
                    "fontSize": "0.6rem", "color": "#4dabf7", "fontWeight": "700",
                    "textAlign": "center",
                }),
                html.Div("Paper Account", style={
                    "fontSize": "0.55rem", "color": "var(--cd-muted)", "textAlign": "center",
                }),
            ], style={"textAlign": "center"}),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "marginBottom": "24px", "flexWrap": "wrap", "gap": "16px",
        }),

        # Chief's live analysis
        recs_panel,

        # Feature stats
        _overall_stats(backlog) if backlog else None,

        # Multi-account vision roadmap
        _ma_roadmap_strip(backlog) if backlog else None,

        # Section label
        html.Div([
            html.Span("THE TRADING ENGINE", style={
                "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
                "color": "var(--cd-muted)", "textTransform": "uppercase",
            }),
            html.Span(" — 6 stages, runs in sequence every cycle", style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)", "marginLeft": "8px",
            }),
        ], style={"marginBottom": "12px"}),

        # 6-stage pipeline (scrollable on small screens)
        html.Div(
            pipeline_row,
            style={
                "display": "flex",
                "gap": "8px",
                "overflowX": "auto",
                "paddingBottom": "8px",
                "alignItems": "stretch",
                "marginBottom": "28px",
            }
        ),

        # Infrastructure section
        html.Div([
            html.Div([
                html.Span("SHARED INFRASTRUCTURE", style={
                    "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
                    "color": "var(--cd-muted)", "textTransform": "uppercase",
                }),
                html.Span(" — modules used across all stages", style={
                    "fontSize": "0.62rem", "color": "var(--cd-faint)", "marginLeft": "8px",
                }),
            ], style={"marginBottom": "12px"}),
            _infra_row(),
        ], style={
            "backgroundColor": "var(--cd-card)",
            "border": "1px solid var(--cd-border)",
            "borderRadius": "12px",
            "padding": "20px",
            "marginBottom": "4px",
        }),

        # Phase E backlog
        _phase_e_section(backlog) if backlog else None,

        dcc.Interval(id="map-interval", interval=60_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("map-content", "children"),
        Input("map-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _sc):
        return layout()
