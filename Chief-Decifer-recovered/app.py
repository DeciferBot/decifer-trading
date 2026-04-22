"""
Chief Decifer — read-only monitoring dashboard for the Decifer Trading system.
Runs on port 8181. Reads everything from the single sacred path:
  $DECIFER_REPO_PATH/chief-decifer/state/
    ├── sessions/   — session logs written by Cowork
    ├── research/   — research findings (from researcher.py and Cowork)
    ├── specs/      — feature specs written by Cowork
    ├── backlog.json — canonical Phase A–E backlog
    ├── vision.json  — product north-star
    └── internal/   — Chief-only compute artifacts (catalyst, analysis, activity)

Chief NEVER writes code, generates tests, or runs autonomous agent loops.
"""

import subprocess
import threading
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output
try:
    import dash_draggable
    HAS_DRAGGABLE = True
except ImportError:
    HAS_DRAGGABLE = False

from config import (
    PORT, REFRESH_INTERVAL_MS, EDGAR_POLL_INTERVAL, CATALYST_SCREEN_INTERVAL,
    OPTIONS_ANOMALY_INTERVAL, SENTIMENT_SCORER_INTERVAL,
    STATE_DIR, SESSIONS_DIR, RESEARCH_DIR, SPECS_DIR, INTERNAL_DIR, CATALYST_DIR,
)

# Ensure required state directories exist on every startup (they're gitignored).
for _d in (SESSIONS_DIR, RESEARCH_DIR, SPECS_DIR, INTERNAL_DIR, CATALYST_DIR,
           INTERNAL_DIR / "docs", STATE_DIR / "analysis"):
    _d.mkdir(parents=True, exist_ok=True)

# ── Version (always read from source — never cached at import time) ────────────
def _get_version() -> str:
    """Read __version__ and __codename__ directly from version.py so the dashboard
    always reflects the current release without needing a restart."""
    import importlib.util, sys
    from pathlib import Path
    vpath = Path(__file__).parent.parent / "version.py"
    spec = importlib.util.spec_from_file_location("_decifer_version", vpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return f"v{mod.__version__} · {mod.__codename__}"
from panels import (
    overview, pipeline, research, git_history, test_results,
    code_health, blueprint, kanban, product_map, brain, catalyst,
    knowledge_graph,
)
from panels import status_strip
from panels.scanner import run_scan

# ── Researcher background state ────────────────────────────────────────────
# Shared dict updated by the background thread; read by the poll callback.
_researcher = {
    "state":   "idle",   # idle | running | done | error
    "output":  "",
    "thread":  None,
}

RESEARCHER_SCRIPT = Path(__file__).parent / "researcher.py"


def _run_researcher_bg():
    """Run researcher.py in a background thread; update _researcher when done."""
    _researcher["state"]  = "running"
    _researcher["output"] = ""
    try:
        result = subprocess.run(
            ["python", str(RESEARCHER_SCRIPT)],
            cwd=str(RESEARCHER_SCRIPT.parent),
            capture_output=True, text=True, timeout=300,
        )
        _researcher["output"] = (result.stdout + result.stderr).strip()
        _researcher["state"]  = "done" if result.returncode == 0 else "error"
    except subprocess.TimeoutExpired:
        _researcher["output"] = "Researcher timed out after 5 minutes."
        _researcher["state"]  = "error"
    except Exception as e:
        _researcher["output"] = str(e)
        _researcher["state"]  = "error"


# ── App initialisation ─────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
    ],
    title="Chief Decifer",
    suppress_callback_exceptions=True,
)

server = app.server  # Expose Flask server for production use


# ── Tab helper ────────────────────────────────────────────────────────────

def _tab(label, icon, content_id, tab_id, initial_content):
    # dbc.Tab label must be a plain string in dbc >= 2.x
    return dbc.Tab(
        html.Div(initial_content, id=content_id),
        label=label,
        tab_id=tab_id,
        label_style={"fontSize": "0.8rem", "padding": "8px 14px"},
        active_label_style={"color": "#4dabf7", "fontWeight": "700", "fontSize": "0.8rem"},
    )


# ── Layout ─────────────────────────────────────────────────────────────────
# Defined as a function so Dash re-evaluates panel layouts on every page load,
# preventing stale content when panels are updated and Chief is restarted.

def serve_layout():
    return dbc.Container([

        # ── Theme store (persists across sessions) ─────────────────────────────
        dcc.Store(id="theme-store", storage_type="local", data="dark"),

        # ── Header ─────────────────────────────────────────────────────────────
        html.Div([
            # Left: logo
            html.Div([
                html.Span(className="cd-logo-dot"),
                html.Span("CHIEF DECIFER", className="cd-logo"),
                html.Span(
                    " · Decifer Trading Monitor",
                    style={"fontSize": "0.7rem", "color": "var(--cd-muted)", "marginLeft": "8px"},
                ),
                html.Span(
                    _get_version(),
                    style={
                        "fontSize": "0.65rem", "color": "#4dabf7",
                        "marginLeft": "10px", "fontFamily": "monospace",
                        "background": "rgba(77,171,247,0.1)", "borderRadius": "6px",
                        "padding": "2px 7px",
                    },
                ),
            ], style={"display": "flex", "alignItems": "center"}),

            # Right: theme toggle + refresh + research + timestamp
            html.Div([
                html.Span(id="last-updated", style={
                    "fontSize": "0.62rem", "color": "var(--cd-muted)", "marginRight": "12px",
                }),
                dbc.Button(
                    [html.I(className="bi bi-arrow-clockwise me-1"), "Refresh"],
                    id="refresh-btn",
                    color="outline-primary",
                    size="sm",
                    className="me-2",
                    n_clicks=0,
                    style={"fontSize": "0.72rem", "borderRadius": "16px", "padding": "3px 12px"},
                ),
                dbc.Button(
                    [html.I(className="bi bi-search me-1", id="research-btn-icon"), "Research"],
                    id="research-btn",
                    color="outline-success",
                    size="sm",
                    className="me-2",
                    n_clicks=0,
                    style={"fontSize": "0.72rem", "borderRadius": "16px", "padding": "3px 12px"},
                ),
                # Theme toggle button
                html.Button(
                    [html.I(className="bi bi-moon-stars me-1", id="theme-icon"), "Dark"],
                    id="theme-btn",
                    className="cd-theme-btn",
                    n_clicks=0,
                ),
            ], style={"display": "flex", "alignItems": "center"}),
        ], className="cd-header"),

        # ── Status strip ───────────────────────────────────────────────────────
        status_strip.layout(),

        html.Div(style={
            "borderBottom": "1px solid var(--cd-border)",
            "marginBottom": "16px", "marginTop": "10px",
        }),

        # ── Main tabs ──────────────────────────────────────────────────────────
        dbc.Tabs([
            _tab("Brain",            "cpu",              "brain-content",    "tab-brain",    brain.layout()),
            _tab("Risks",            "shield-exclamation","risks-content",   "tab-risks",    brain.risks_layout()),
            _tab("Catalyst Alerts",  "lightning-charge", "catalyst-content", "tab-catalyst", catalyst.layout()),
            _tab("Feature Pipeline", "kanban",           "kanban-content",   "tab-kanban",   kanban.layout()),
            _tab("Research",         "lightbulb",        "research-content", "tab-research", research.layout()),
            _tab("Bot Status",       "check2-circle",    "tests-content",    "tab-tests",    test_results.layout()),
            _tab("Activity",         "activity",         "pipeline-content", "tab-pipeline", pipeline.layout()),
            _tab("Product Map",      "map",              "map-content",      "tab-map",      product_map.layout()),
            _tab("Commits",          "git-commit",       "git-content",      "tab-git",      git_history.layout()),
            _tab("Code Health",      "heart-pulse",      "health-content",   "tab-health",   code_health.layout()),
            _tab("Overview",         "house",            "overview-content", "tab-overview", overview.layout()),
            _tab("Architecture",     "diagram-3",        "blueprint-content","tab-blueprint",blueprint.layout()),
            _tab("Knowledge Graph",  "diagram-2",        "kg-content",       "tab-kg",       knowledge_graph.layout()),
        ], id="main-tabs", active_tab="tab-brain",
           className="mb-3",
           style={"borderBottom": "1px solid var(--cd-border)"}),

        # ── Scan result banner ─────────────────────────────────────────────────
        html.Div(id="scan-result-banner", style={"display": "none"}),

        # ── Research result banner ──────────────────────────────────────────────
        html.Div(id="research-result-banner", style={"display": "none"}),

        # ── Hidden stores & dummy outputs ─────────────────────────────────────
        dcc.Store(id="scan-complete", data=0),
        dcc.Store(id="research-trigger", data=0),
        html.Div(id="theme-applier", style={"display": "none"}),

        # ── Brain tab persistent state (outside brain-content so interval doesn't reset them) ──
        dcc.Store(id="brain-opp-idx", data=-1),
        dcc.Store(id="brain-risk-idx", data=-1),
        dcc.Store(id="brain-chat-history", storage_type="local", data=[]),
        dcc.Store(id="brain-rec-skip", storage_type="local", data=0),

        # ── Global refresh ticker ──────────────────────────────────────────────
        dcc.Interval(id="global-interval", interval=REFRESH_INTERVAL_MS, n_intervals=0),

        # ── Researcher poll ticker (enabled only while research is running) ────
        dcc.Interval(id="research-poll", interval=2000, disabled=True, n_intervals=0),

        # ── Brain rerun poll ticker (enabled only while analyse.py is running) ──
        dcc.Interval(id="brain-rerun-poll", interval=3000, disabled=True, n_intervals=0),

        # ── Brain chat poll ticker (enabled only while a chat API call is in-flight) ──
        dcc.Interval(id="brain-chat-poll", interval=1500, disabled=True, n_intervals=0),

    ], fluid=True, className="px-4", id="main-container")

app.layout = serve_layout


# ── Clientside callbacks (theme toggle) ────────────────────────────────────

# Callback 1: Toggle theme value on button click
app.clientside_callback(
    """
    function(n_clicks, current_theme) {
        if (!n_clicks || n_clicks === 0) {
            return current_theme || 'dark';
        }
        return (current_theme || 'dark') === 'dark' ? 'light' : 'dark';
    }
    """,
    Output("theme-store", "data"),
    Input("theme-btn", "n_clicks"),
    dash.dependencies.State("theme-store", "data"),
)

# Callback 2: Apply body class when theme changes (side effect, dummy output)
app.clientside_callback(
    """
    function(theme) {
        if (theme === 'light') {
            document.body.classList.add('light-mode');
        } else {
            document.body.classList.remove('light-mode');
        }
        return theme;
    }
    """,
    Output("theme-applier", "children"),
    Input("theme-store", "data"),
)


# ── Theme button label update ──────────────────────────────────────────────

@app.callback(
    Output("theme-btn", "children"),
    Input("theme-store", "data"),
)
def update_theme_btn(theme):
    if theme == "light":
        return [html.I(className="bi bi-sun me-1"), "Light"]
    return [html.I(className="bi bi-moon-stars me-1"), "Dark"]


# ── Panel callbacks ─────────────────────────────────────────────────────────

overview.register_callbacks(app)
pipeline.register_callbacks(app)
research.register_callbacks(app)
git_history.register_callbacks(app)
test_results.register_callbacks(app)
code_health.register_callbacks(app)
blueprint.register_callbacks(app)
kanban.register_callbacks(app)
status_strip.register_callbacks(app)
product_map.register_callbacks(app)
brain.register_callbacks(app)
catalyst.register_callbacks(app)
knowledge_graph.register_callbacks(app)


# ── Scan on refresh button ─────────────────────────────────────────────────

@app.callback(
    Output("scan-result-banner", "children"),
    Output("scan-result-banner", "style"),
    Output("scan-complete", "data"),
    Input("refresh-btn", "n_clicks"),
    prevent_initial_call=True,
)
def run_scan_on_click(n_clicks):
    from dash import callback_context
    if not callback_context.triggered:
        return "", {"display": "none"}, dash.no_update

    result = run_scan()

    if result["detected"] == 0 and result["scanned"] == 0:
        msg = "Scan complete — no research features to check."
        color = "secondary"
    elif result["detected"] > 0:
        msg = f"Scan complete — {result['detected']}/{result['scanned']} features detected"
        if result["updated"] > 0:
            msg += f", {result['updated']} report(s) marked shipped"
        color = "success"
    else:
        msg = f"Scan complete — checked {result['scanned']} features, none detected yet"
        color = "info"

    banner = dbc.Alert(
        [
            html.I(className="bi bi-check-circle me-2") if result["detected"] > 0
            else html.I(className="bi bi-info-circle me-2"),
            msg,
        ],
        color=color,
        dismissable=True,
        className="mb-3 py-2",
        style={"fontSize": "0.8rem"},
    )
    return banner, {"display": "block"}, n_clicks


# ── Research button — launch background researcher ─────────────────────────

@app.callback(
    Output("research-btn", "children"),
    Output("research-btn", "disabled"),
    Output("research-poll", "disabled"),
    Output("research-trigger", "data"),
    Input("research-btn", "n_clicks"),
    prevent_initial_call=True,
)
def start_research(n_clicks):
    if _researcher["state"] == "running":
        # Already running — ignore double-click
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    # Start background thread
    t = threading.Thread(target=_run_researcher_bg, daemon=True)
    _researcher["thread"] = t
    _researcher["state"]  = "running"
    t.start()

    return (
        [html.I(className="bi bi-hourglass-split me-1"), "Researching…"],
        True,    # disable button while running
        False,   # enable poll interval
        n_clicks,
    )


# ── Research poll — check background thread, show result when done ──────────

@app.callback(
    Output("research-result-banner", "children"),
    Output("research-result-banner", "style"),
    Output("research-btn", "children", allow_duplicate=True),
    Output("research-btn", "disabled", allow_duplicate=True),
    Output("research-poll", "disabled", allow_duplicate=True),
    Input("research-poll", "n_intervals"),
    prevent_initial_call=True,
)
def poll_research(_n):
    state = _researcher["state"]

    if state == "running":
        # Still running — keep polling, no banner update yet
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, False

    if state == "idle":
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, True

    # Done or error
    output = _researcher["output"]
    _researcher["state"] = "idle"  # reset for next run

    if state == "done":
        # Extract topic and quick wins from output if present
        topic_line = next((l for l in output.splitlines() if "Research topic:" in l), "")
        topic = topic_line.split("Research topic:")[-1].strip() if topic_line else "Research"
        wins  = [l.strip().lstrip("•").strip() for l in output.splitlines() if l.strip().startswith("•")]
        win_text = "  ·  ".join(wins[:3]) if wins else ""
        msg   = f"Research complete — {topic}"
        if win_text:
            msg += f"  |  Quick wins: {win_text}"
        color = "success"
        icon  = html.I(className="bi bi-check-circle me-2")
    else:
        first_error = next((l for l in output.splitlines() if "ERROR" in l or "error" in l.lower()), output[:120])
        msg   = f"Research failed — {first_error}"
        color = "danger"
        icon  = html.I(className="bi bi-exclamation-circle me-2")

    banner = dbc.Alert(
        [icon, msg],
        color=color,
        dismissable=True,
        className="mb-3 py-2",
        style={"fontSize": "0.8rem"},
    )
    btn_label = [html.I(className="bi bi-search me-1"), "Research"]

    return banner, {"display": "block"}, btn_label, False, True  # re-enable button, stop polling


# ── Last-updated timestamp ─────────────────────────────────────────────────

@app.callback(
    Output("last-updated", "children"),
    Input("global-interval", "n_intervals"),
    Input("refresh-btn", "n_clicks"),
)
def update_timestamp(_n, _clicks):
    from datetime import datetime
    return f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}"


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # auto_runner disabled — CatalystEngine in the Decifer bot now owns all
    # scoring runners (fundamental screen, EDGAR monitor, options anomaly,
    # sentiment scorer). Chief Decifer reads the same candidates files at
    # chief-decifer/state/internal/catalyst/ — dashboard panels unchanged.
    # from signals.auto_runner import start as start_auto_runner
    # start_auto_runner(
    #     screen_interval=CATALYST_SCREEN_INTERVAL,
    #     edgar_interval=EDGAR_POLL_INTERVAL,
    #     options_interval=OPTIONS_ANOMALY_INTERVAL,
    #     sentiment_interval=SENTIMENT_SCORER_INTERVAL,
    # )
    print(f"\n\u26A1 Chief Decifer starting on http://localhost:{PORT}\n")
    app.run(debug=False, host="0.0.0.0", port=PORT)
