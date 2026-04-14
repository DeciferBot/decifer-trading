"""
Activity panel — shows development session history in a friendly timeline.
Reads state/sessions/*.json.
"""

import json
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import SESSIONS_DIR


def _load_sessions():
    sessions = []
    if SESSIONS_DIR.exists():
        for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
            try:
                sessions.append(json.loads(f.read_text()))
            except Exception:
                pass
    return sessions[:20]


_TYPE_LABELS = {
    "bugfix": "Bug fix",
    "feature": "New feature",
    "refactor": "Refactor",
    "test": "Tests",
    "docs": "Documentation",
}
_TYPE_COLORS = {
    "bugfix": "danger",
    "feature": "success",
    "refactor": "warning",
    "test": "info",
    "docs": "secondary",
}


def _render_work_item(item):
    """A single work item as a friendly card."""
    item_type = item.get("type", "")
    component = item.get("component", "")
    summary = item.get("summary", "")
    root_cause = item.get("root_cause", "")
    tests_ok = item.get("tests_passing")
    files = item.get("files_changed", [])

    return html.Div([
        # Type badge + component
        html.Div([
            dbc.Badge(
                _TYPE_LABELS.get(item_type, item_type),
                color=_TYPE_COLORS.get(item_type, "secondary"),
                className="me-2",
                style={"fontSize": "0.65rem"},
            ),
            html.Span(component, className="text-light", style={"fontWeight": "500", "fontSize": "0.85rem"}) if component else None,
        ], className="mb-1"),

        # Summary
        html.P(summary, className="text-muted small mb-1", style={"lineHeight": "1.4"}) if summary else None,

        # Root cause (if bug fix)
        html.P([
            html.Small("Why: ", className="text-warning"),
            html.Small(root_cause, className="text-muted"),
        ], className="mb-1") if root_cause else None,

        # Tests status
        html.Div([
            html.Small(
                "Tests passing" if tests_ok else "Tests failing",
                className=f"{'text-success' if tests_ok else 'text-danger'} small",
            ),
        ], className="mb-1") if tests_ok is not None else None,

        # Files changed (summarized, not a code dump)
        html.Small(
            f"{len(files)} file{'s' if len(files) != 1 else ''} changed",
            className="text-muted",
        ) if files else None,
    ], className="py-2", style={"borderBottom": "1px solid #2a2a3e"})


def _render_session(session):
    """A full session as a timeline card."""
    date = session.get("date", session.get("session_id", "Unknown"))
    work_items = session.get("work_items", [])
    commits = session.get("git_commits", [])
    approved_by = session.get("approved_by", "")

    # Stats line
    stats = []
    if work_items:
        stats.append(f"{len(work_items)} item{'s' if len(work_items) != 1 else ''}")
    if commits:
        stats.append(f"{len(commits)} commit{'s' if len(commits) != 1 else ''}")

    return dbc.Card([
        dbc.CardHeader([
            html.Div([
                html.Div([
                    html.H6(date, className="text-light mb-0", style={"fontWeight": "600"}),
                    html.Small(" · ".join(stats), className="text-muted ms-2") if stats else None,
                ], className="d-flex align-items-center"),
                dbc.Badge(
                    f"Approved by {approved_by}",
                    color="success",
                    style={"fontSize": "0.65rem"},
                ) if approved_by else None,
            ], className="d-flex justify-content-between align-items-center"),
        ], style={"backgroundColor": "#1e1e2e", "borderBottom": "1px solid #333"}),
        dbc.CardBody([
            *[_render_work_item(item) for item in work_items],
        ] if work_items else [
            html.P("No work items recorded for this session.", className="text-muted small mb-0"),
        ], className="p-3"),
    ], className="mb-3", style={"backgroundColor": "#1a1a2e", "border": "1px solid #333", "borderRadius": "8px"})


def layout():
    sessions = _load_sessions()

    if not sessions:
        return html.Div([
            html.H4("Activity", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(
                "No development sessions recorded yet. After each approved work session, "
                "a summary will appear here as a timeline entry.",
                className="text-muted",
            ),
            dcc.Interval(id="sessions-interval", interval=30_000, n_intervals=0),
        ])

    return html.Div([
        html.H4("Activity", className="text-light mb-1", style={"fontWeight": "600"}),
        html.P(
            f"Showing the last {len(sessions)} development session{'s' if len(sessions) != 1 else ''}.",
            className="text-muted small mb-3",
        ),
        *[_render_session(s) for s in sessions],
        dcc.Interval(id="sessions-interval", interval=30_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("sessions-content", "children"),
        Input("sessions-interval", "n_intervals"),
    )
    def refresh(_):
        return layout()
