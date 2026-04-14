"""
Feature Pipeline — Kanban view.
Reads specs from state/specs/ and renders them as a 4-column board:
  Proposal  →  Backlog  →  In Progress  →  Shipped

Each card carries a ready-to-paste Code prompt so Amit can open Chief,
copy the prompt, and drop it straight into a Code session.
Proposal cards have an Approve button that moves them to Backlog.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import SPECS_DIR, BACKLOG_FILE, LIFECYCLE_LABELS, SESSIONS_DIR

STALE_IN_PROGRESS_DAYS = 3  # flag in_progress specs with no session reference after this many days


def _get_recently_active_spec_ids():
    """Return set of spec IDs referenced in sessions written in the last STALE_IN_PROGRESS_DAYS days."""
    active = set()
    if not SESSIONS_DIR.exists():
        return active
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=STALE_IN_PROGRESS_DAYS)
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue
            data = json.loads(f.read_text())
            for sid in data.get("specIds", []):
                active.add(sid)
        except Exception:
            pass
    return active

# ── Column definitions ────────────────────────────────────────────────────────

COLUMNS = [
    {
        "id": "spec_complete",
        "label": "Proposal",
        "subtitle": "Research findings awaiting approval",
        "color": "#74c0fc",
        "bg": "#131e2a",
        "border": "rgba(116,192,252,0.2)",
        "dot": "#74c0fc",
    },
    {
        "id": "backlog",
        "label": "Backlog",
        "subtitle": "Behind schedule — build later",
        "color": "var(--cd-muted)",
        "bg": "#1a1d23",
        "border": "rgba(134,142,150,0.25)",
        "dot": "#868e96",
    },
    {
        "id": "in_progress",
        "label": "Progress",
        "subtitle": "On roadmap — building now",
        "color": "#ffd43b",
        "bg": "#1e1c12",
        "border": "rgba(255,212,59,0.2)",
        "dot": "#ffd43b",
    },
    {
        "id": "future",
        "label": "Future",
        "subtitle": "Valid idea — no current timeline",
        "color": "#b197fc",
        "bg": "#17131e",
        "border": "rgba(177,151,252,0.2)",
        "dot": "#b197fc",
    },
    {
        "id": "complete",
        "label": "Shipped",
        "subtitle": "",
        "color": "#51cf66",
        "bg": "#111e15",
        "border": "rgba(81,207,102,0.2)",
        "dot": "#51cf66",
    },
]

ROADMAP_HINTS = {
    # title keywords → (hint text, suggested bucket)
    "consensus":         ("Phase A — immediate, no deps",           "in_progress"),
    "short-candidate":   ("Phase A — immediate, no deps",           "in_progress"),
    "short scan":        ("Phase A — immediate, no deps",           "in_progress"),
    "skew":              ("Phase A — immediate, no deps",           "in_progress"),
    "direction-agnostic":("Phase B — after Phase A",                "backlog"),
    "mean-reversion":    ("Phase B — after Phase A",                "backlog"),
    "signal validation": ("Phase C — needs 200+ trades",            "future"),
    "alphalens":         ("Phase C — needs 200+ trades",            "future"),
    "ic analysis":       ("Phase C — needs 200+ trades",            "future"),
    "walk-forward":      ("Phase C — needs 200+ trades",            "future"),
    "hmm":               ("Phase D — blocked on Phase C",           "future"),
    "regime detection":  ("Phase D — blocked on Phase C",           "future"),
    "weight calibration":("Phase D — blocked on Phase C",           "future"),
}


def _roadmap_hint(spec):
    """Return (hint_text, suggested_bucket) based on title/phase keywords, or (None, None)."""
    title = (spec.get("title", "") + " " + spec.get("summary", "")).lower()
    for keyword, (hint, bucket) in ROADMAP_HINTS.items():
        if keyword in title:
            return hint, bucket
    # Fall back to phase-based suggestion
    phase = spec.get("phase", "")
    if phase in ("A",):
        return "Phase A — immediate, no deps", "in_progress"
    if phase in ("B",):
        return "Phase B — after Phase A", "backlog"
    if phase in ("C", "D", "E"):
        return f"Phase {phase} — blocked/data-dependent", "future"
    return None, None


PRIORITY_STYLES = {
    "P0": {"bg": "rgba(255,107,107,0.12)", "color": "#ff6b6b", "border": "rgba(255,107,107,0.3)"},
    "P1": {"bg": "rgba(255,212,59,0.12)",  "color": "#ffd43b", "border": "rgba(255,212,59,0.3)"},
    "P2": {"bg": "rgba(116,192,252,0.12)", "color": "#74c0fc", "border": "rgba(116,192,252,0.3)"},
}

PHASE_COLORS = {
    # Existing bias-fix roadmap phases
    "A": "#f783ac",
    "B": "#da77f2",
    "C": "#74c0fc",
    "D": "#63e6be",
    "E": "#ffa94d",
    # Multi-account vision phases
    "MA1": "#20c997",   # teal  — local multi-account foundation
    "MA2": "#4dabf7",   # blue  — cloud & signal broadcasting
    "MA3": "#a9e34b",   # lime  — user interfaces
}


# ── Data loader ───────────────────────────────────────────────────────────────

def _load_specs():
    specs = []
    seen_ids = set()

    # Load individual spec files from state/specs/
    if SPECS_DIR.exists():
        for f in sorted(SPECS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                if data.get("id") and data["id"] not in seen_ids:
                    specs.append(data)
                    seen_ids.add(data["id"])
            except Exception:
                continue

    # Load bulk backlog from state/backlog.json (array of specs)
    if BACKLOG_FILE.exists():
        try:
            items = json.loads(BACKLOG_FILE.read_text())
            if isinstance(items, list):
                for data in items:
                    if data.get("id") and data["id"] not in seen_ids:
                        specs.append(data)
                        seen_ids.add(data["id"])
        except Exception:
            pass

    return specs


def _group_specs(specs):
    grouped = {col["id"]: [] for col in COLUMNS}
    for spec in specs:
        status = spec.get("status", "backlog")
        if status == "archived":
            continue
        if status not in grouped:
            status = "backlog"
        grouped[status].append(spec)
    return grouped


VALID_STATUSES = {col["id"] for col in COLUMNS}


# ── Cowork prompt builder ──────────────────────────────────────────────────────

def _build_cowork_prompt(spec, all_specs):
    """
    Generate a ready-to-paste Cowork session prompt for this feature.
    Clear enough that Amit can copy it cold and start a productive session.
    """
    title   = spec.get("title", "Feature")
    summary = spec.get("summary", "")
    approach = spec.get("approach", "")
    files   = spec.get("files_affected", []) or []
    dep_ids = spec.get("dependencies", []) or []
    phase   = spec.get("phase", "")
    pri     = spec.get("priority", "P2")
    roadmap = spec.get("roadmap_ref", "")

    dep_titles = []
    for dep_id in dep_ids:
        match = next((s.get("title", dep_id) for s in all_specs if s.get("id") == dep_id), dep_id)
        dep_titles.append(match)

    lines = [f"Build: {title}"]
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    if approach:
        lines.append(f"Approach: {approach}")
        lines.append("")
    if files:
        lines.append(f"Files to work in: {', '.join(files)}")
    if dep_titles:
        lines.append(f"Depends on (build these first if not done): {', '.join(dep_titles)}")
    lines.append(f"Phase {phase}  ·  Priority {pri}")
    if roadmap:
        lines.append(f"Roadmap ref: {roadmap}")
    lines.append("")
    lines.append(
        "When done: write or update tests for the changed logic, "
        "record a session summary in state/sessions/, "
        "and confirm end-to-end on a paper-trade run."
    )
    return "\n".join(lines)


# ── Card renderer ─────────────────────────────────────────────────────────────

def _render_card(spec, all_specs, recently_active_ids=None):
    status = spec.get("status", "backlog")
    col = next((c for c in COLUMNS if c["id"] == status), COLUMNS[0])
    pri = spec.get("priority", "P2")
    pri_style = PRIORITY_STYLES.get(pri, PRIORITY_STYLES["P2"])
    phase = spec.get("phase", "")
    phase_color = PHASE_COLORS.get(phase, "#868e96")
    spec_id = spec.get("id", "")
    hint_text, _hint_bucket = _roadmap_hint(spec)

    # Stale in-progress badge
    stale_badge = None
    if status == "in_progress" and recently_active_ids is not None:
        if spec_id not in recently_active_ids:
            stale_badge = html.Span(
                "Stale — no recent session",
                style={
                    "fontSize": "0.55rem", "padding": "2px 8px", "borderRadius": "4px",
                    "backgroundColor": "rgba(255,212,59,0.12)", "color": "#ffd43b",
                    "border": "1px solid rgba(255,212,59,0.35)", "fontWeight": "600",
                }
            )

    # Resolve dependency names
    dep_ids = spec.get("dependencies", []) or []
    dep_names = []
    for dep_id in dep_ids:
        match = next((s.get("title", dep_id) for s in all_specs if s.get("id") == dep_id), dep_id)
        dep_names.append(match)

    # Dates footer
    dates = []
    if spec.get("designed_date"):
        dates.append(html.Span(f"Designed {spec['designed_date']}", style={"color": "var(--cd-faint)", "fontSize": "0.58rem"}))
    if spec.get("started_date"):
        dates.append(html.Span(f"Started {spec['started_date']}", style={"color": "#ffd43b88", "fontSize": "0.58rem"}))
    if spec.get("completed_date"):
        dates.append(html.Span(f"Shipped {spec['completed_date']}", style={"color": "#51cf6688", "fontSize": "0.58rem"}))

    # Branch badge
    branch_badge = None
    if spec.get("branch"):
        branch_label = spec["branch"].replace("feat/", "").replace("fix/", "")
        branch_badge = html.Span(
            branch_label,
            style={
                "fontSize": "0.55rem", "padding": "1px 7px", "borderRadius": "3px",
                "backgroundColor": "rgba(81,207,102,0.1)", "color": "#51cf66",
                "fontFamily": "monospace", "border": "1px solid rgba(81,207,102,0.2)",
            }
        )

    # Dependencies
    dep_badges = [
        html.Span(
            dep,
            style={
                "fontSize": "0.56rem", "padding": "1px 6px", "borderRadius": "3px",
                "backgroundColor": "rgba(255,255,255,0.04)", "color": "var(--cd-muted)",
                "border": "1px solid var(--cd-border-sub)", "marginRight": "3px",
            }
        )
        for dep in dep_names
    ]

    return html.Div([
        # Top badge row
        html.Div([
            html.Span(
                pri,
                style={
                    "fontSize": "0.58rem", "fontWeight": 700,
                    "padding": "2px 8px", "borderRadius": "4px",
                    "backgroundColor": pri_style["bg"],
                    "color": pri_style["color"],
                    "border": f"1px solid {pri_style['border']}",
                    "letterSpacing": "0.5px", "marginRight": "5px",
                }
            ),
            html.Span(
                f"Phase {phase}",
                style={
                    "fontSize": "0.56rem", "padding": "2px 7px", "borderRadius": "3px",
                    "backgroundColor": f"{phase_color}15",
                    "color": phase_color,
                    "border": f"1px solid {phase_color}30",
                }
            ),
            branch_badge or html.Span(),
            stale_badge or html.Span(),
        ], style={"display": "flex", "alignItems": "center", "gap": "4px", "marginBottom": "8px", "flexWrap": "wrap"}),

        # Title
        html.Div(
            spec.get("title", "Untitled"),
            style={
                "fontWeight": 600, "fontSize": "0.83rem", "color": "var(--cd-text)",
                "lineHeight": "1.35", "marginBottom": "6px",
            }
        ),

        # Summary
        html.Div(
            spec.get("summary", ""),
            style={
                "fontSize": "0.7rem", "color": "var(--cd-muted)",
                "lineHeight": "1.5", "marginBottom": "8px",
            }
        ),

        # Dependencies
        html.Div(
            [html.Span("Needs: ", style={"fontSize": "0.58rem", "color": "var(--cd-faint)", "marginRight": "3px"})]
            + dep_badges
        , style={"marginBottom": "6px", "display": "flex", "flexWrap": "wrap", "alignItems": "center"}
        ) if dep_names else None,

        # Dates — interleave separator dots between items
        html.Div(
            [item for pair in zip(dates, [html.Span("  ·  ", style={"color": "var(--cd-faint)", "fontSize": "0.58rem"})] * len(dates)) for item in pair][:-1]
            if len(dates) > 1 else (dates if dates else []),
            style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}
        ) if dates else None,

        # Routing buttons — only on Proposal cards
        *([
            html.Div([
                # Roadmap hint
                *([html.Div(
                    f"📍 {hint_text}",
                    style={
                        "fontSize": "0.58rem", "color": "#74c0fc",
                        "backgroundColor": "rgba(116,192,252,0.08)",
                        "border": "1px solid rgba(116,192,252,0.2)",
                        "borderRadius": "4px", "padding": "3px 8px",
                        "marginBottom": "7px",
                    }
                )] if hint_text else []),
                # 3 route buttons
                html.Div([
                    html.Button(
                        "🔨 Progress",
                        id={"type": "kanban-route-btn", "index": f"{spec_id}__in_progress"},
                        n_clicks=0,
                        style={
                            "fontSize": "0.62rem", "fontWeight": 700,
                            "padding": "4px 10px", "borderRadius": "5px", "cursor": "pointer",
                            "border": "1px solid rgba(255,212,59,0.4)",
                            "backgroundColor": "rgba(255,212,59,0.08)", "color": "#ffd43b",
                        }
                    ),
                    html.Button(
                        "📋 Backlog",
                        id={"type": "kanban-route-btn", "index": f"{spec_id}__backlog"},
                        n_clicks=0,
                        style={
                            "fontSize": "0.62rem", "fontWeight": 700,
                            "padding": "4px 10px", "borderRadius": "5px", "cursor": "pointer",
                            "border": "1px solid rgba(134,142,150,0.4)",
                            "backgroundColor": "rgba(134,142,150,0.08)", "color": "#868e96",
                        }
                    ),
                    html.Button(
                        "🔮 Future",
                        id={"type": "kanban-route-btn", "index": f"{spec_id}__future"},
                        n_clicks=0,
                        style={
                            "fontSize": "0.62rem", "fontWeight": 700,
                            "padding": "4px 10px", "borderRadius": "5px", "cursor": "pointer",
                            "border": "1px solid rgba(177,151,252,0.4)",
                            "backgroundColor": "rgba(177,151,252,0.08)", "color": "#b197fc",
                        }
                    ),
                ], style={"display": "flex", "gap": "6px", "flexWrap": "wrap"}),
            ], style={"marginTop": "10px"})
        ] if status == "spec_complete" else []),

        # Code prompt — copy and paste into a session to start building this feature
        html.Details([
            html.Summary(
                "📋 Code prompt",
                style={"fontSize": "0.62rem", "color": "#4dabf7", "cursor": "pointer",
                       "marginTop": "10px", "userSelect": "none"},
            ),
            html.Div([
                html.Pre(
                    _build_cowork_prompt(spec, all_specs),
                    style={
                        "backgroundColor": "var(--cd-deep)",
                        "color": "#c5d8f0",
                        "fontSize": "0.64rem",
                        "lineHeight": "1.6",
                        "padding": "10px 12px",
                        "borderRadius": "6px",
                        "whiteSpace": "pre-wrap",
                        "wordBreak": "break-word",
                        "marginTop": "6px",
                        "border": "1px solid rgba(77,171,247,0.15)",
                        "userSelect": "all",
                    }
                ),
                html.Small(
                    "Select all text above and copy into Code to start this session.",
                    style={"color": "var(--cd-faint)", "fontSize": "0.58rem", "display": "block",
                           "marginTop": "4px"},
                ),
            ]),
        ], style={"marginTop": "4px"}),

    ], style={
        "backgroundColor": "var(--cd-card)",
        "borderRadius": "9px",
        "padding": "13px 15px",
        "marginBottom": "9px",
        "borderLeft": f"3px solid {col['color']}",
        "border": "1px solid var(--cd-border-sub)",
        "borderLeftWidth": "3px",
        "borderLeftColor": col["color"],
        "borderLeftStyle": "solid",
    })


# ── Column renderer ───────────────────────────────────────────────────────────

def _render_column(col, specs, all_specs, recently_active_ids=None):
    cards = [_render_card(spec, all_specs, recently_active_ids) for spec in specs]

    # WIP limit warning for In Progress column
    wip_warning = None
    if col["id"] == "in_progress" and len(specs) > 2:
        wip_warning = html.Div(
            f"⚠ {len(specs)} items in progress — WIP limit exceeded",
            style={
                "fontSize": "0.6rem", "color": "#ffd43b",
                "backgroundColor": "rgba(255,212,59,0.08)",
                "border": "1px solid rgba(255,212,59,0.25)",
                "borderRadius": "5px", "padding": "4px 10px",
                "marginBottom": "10px", "textAlign": "center",
            }
        )

    empty_state = html.Div(
        "Nothing here yet",
        style={
            "padding": "28px 16px", "textAlign": "center",
            "fontSize": "0.72rem", "color": "var(--cd-faint)",
            "border": "1px dashed rgba(255,255,255,0.06)",
            "borderRadius": "8px",
        }
    ) if not specs else None

    subtitle = col.get("subtitle", "")

    return html.Div([
        # Column header
        html.Div([
            html.Div([
                html.Span(style={
                    "width": "8px", "height": "8px", "borderRadius": "50%",
                    "backgroundColor": col["dot"], "display": "inline-block",
                    "marginRight": "8px",
                }),
                html.Span(col["label"], style={
                    "fontWeight": 700, "fontSize": "0.78rem",
                    "color": "var(--cd-text)", "letterSpacing": "0.3px",
                }),
            ], style={"display": "flex", "alignItems": "center"}),
            html.Span(
                str(len(specs)),
                style={
                    "fontSize": "0.62rem", "fontWeight": 700,
                    "padding": "2px 9px", "borderRadius": "10px",
                    "backgroundColor": f"{col['color']}18",
                    "color": col["color"],
                }
            ),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "marginBottom": "4px",
        }),

        # Column subtitle
        html.Div(subtitle, style={
            "fontSize": "0.58rem", "color": "var(--cd-faint)",
            "marginBottom": "12px", "lineHeight": "1.3",
        }) if subtitle else None,

        # WIP warning
        wip_warning,

        # Cards
        empty_state or html.Div(cards),

    ], style={
        "flex": "1",
        "minWidth": "240px",
        "maxWidth": "340px",
        "backgroundColor": col["bg"],
        "borderRadius": "12px",
        "padding": "16px 14px",
        "border": f"1px solid {col['border']}",
    })


# ── Next session hero ─────────────────────────────────────────────────────────

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
STATUS_ORDER   = {"in_progress": 0, "spec_complete": 1, "backlog": 2}
PHASE_ORDER    = {"MA1": 0, "MA2": 1, "MA3": 2,
                  "A": 3, "B": 4, "C": 5, "D": 6, "E": 7}


def _pick_next_spec(specs):
    """Return the single most actionable spec for the next Cowork session.

    Ordering: status first (in-progress beats ready beats backlog),
    then priority (P0 beats P1 etc.), then phase (A before B before E)
    so earlier-phase work is always preferred over later-phase work at
    equal priority — Phase E features never jump the queue.
    """
    candidates = [s for s in specs if s.get("status") not in ("complete", "future")]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (
        STATUS_ORDER.get(s.get("status", "backlog"), 9),
        PHASE_ORDER.get(s.get("phase", "Z"), 9),   # phase before priority — earlier phases always come first
        PRIORITY_ORDER.get(s.get("priority", "P2"), 9),
    ))
    return candidates[0]


def _render_next_session_hero(specs, all_specs):
    """Full-width hero at the top of the Pipeline tab showing what to build next."""
    spec = _pick_next_spec(specs)
    if not spec:
        return None

    prompt_text = _build_cowork_prompt(spec, all_specs)
    pri         = spec.get("priority", "P2")
    phase       = spec.get("phase", "")
    status      = spec.get("status", "backlog")
    pri_style   = PRIORITY_STYLES.get(pri, PRIORITY_STYLES["P2"])
    phase_color = PHASE_COLORS.get(phase, "#868e96")

    status_labels = {
        "in_progress":  ("In Progress", "#ffd43b"),
        "spec_complete": ("Ready to build", "#74c0fc"),
        "backlog":       ("Up next",        "#868e96"),
    }
    status_label, status_color = status_labels.get(status, ("Queued", "#868e96"))

    return html.Div([

        # Label row
        html.Div([
            html.Span("▶  NEXT IN PIPELINE", style={
                "fontSize": "0.6rem", "fontWeight": 800, "color": "#4dabf7",
                "letterSpacing": "1.2px", "textTransform": "uppercase",
            }),
            html.Span("from tracked specs · for strategic recommendation see Brain", style={
                "fontSize": "0.58rem", "color": "var(--cd-faint)", "marginLeft": "10px",
            }),
            html.Span(status_label, style={
                "fontSize": "0.6rem", "padding": "2px 9px", "borderRadius": "4px",
                "backgroundColor": f"{status_color}18", "color": status_color,
                "border": f"1px solid {status_color}30", "fontWeight": 600,
            }),
        ], style={"display": "flex", "alignItems": "center", "gap": "12px",
                  "marginBottom": "12px"}),

        # Feature name + badges
        html.Div([
            html.Div([
                html.Span(pri, style={
                    "fontSize": "0.62rem", "fontWeight": 700,
                    "padding": "2px 9px", "borderRadius": "4px",
                    "backgroundColor": pri_style["bg"], "color": pri_style["color"],
                    "border": f"1px solid {pri_style['border']}", "marginRight": "8px",
                }),
                html.Span(f"Phase {phase}", style={
                    "fontSize": "0.6rem", "padding": "2px 8px", "borderRadius": "4px",
                    "backgroundColor": f"{phase_color}15", "color": phase_color,
                    "border": f"1px solid {phase_color}30",
                }),
            ], style={"marginBottom": "6px"}),
            html.Div(spec.get("title", ""), style={
                "fontSize": "1.05rem", "fontWeight": 700, "color": "var(--cd-text)",
                "marginBottom": "4px",
            }),
            html.Div(spec.get("summary", ""), style={
                "fontSize": "0.75rem", "color": "var(--cd-muted)", "lineHeight": "1.5",
                "marginBottom": "16px",
            }),
        ]),

        # Cowork prompt — immediately visible, no accordion
        html.Div([
            html.Div([
                html.Span("CODE PROMPT", style={
                    "fontSize": "0.55rem", "fontWeight": 800, "color": "#4dabf7",
                    "letterSpacing": "1px", "textTransform": "uppercase",
                }),
                html.Span(" — select all the text below, copy it, and paste it into Code to start this session",
                          style={"fontSize": "0.6rem", "color": "var(--cd-faint)", "marginLeft": "6px"}),
            ], style={"marginBottom": "6px"}),
            html.Pre(
                prompt_text,
                style={
                    "backgroundColor": "var(--cd-deep)",
                    "color": "#c5d8f0",
                    "fontSize": "0.72rem",
                    "lineHeight": "1.7",
                    "padding": "14px 16px",
                    "borderRadius": "6px",
                    "whiteSpace": "pre-wrap",
                    "wordBreak": "break-word",
                    "margin": "0",
                    "border": "1px solid rgba(77,171,247,0.2)",
                    "userSelect": "all",
                    "cursor": "text",
                }
            ),
            html.Div([
                html.Span("💡 Tip: click inside the box above, then ", style={"fontSize": "0.62rem", "color": "var(--cd-faint)"}),
                html.Span("Cmd+A", style={"fontSize": "0.6rem", "fontFamily": "monospace",
                                          "backgroundColor": "var(--cd-card)", "color": "#74c0fc",
                                          "padding": "1px 5px", "borderRadius": "3px"}),
                html.Span(" + ", style={"fontSize": "0.62rem", "color": "var(--cd-faint)"}),
                html.Span("Cmd+C", style={"fontSize": "0.6rem", "fontFamily": "monospace",
                                          "backgroundColor": "var(--cd-card)", "color": "#74c0fc",
                                          "padding": "1px 5px", "borderRadius": "3px"}),
                html.Span(" to copy, then paste into Code.", style={"fontSize": "0.62rem", "color": "var(--cd-faint)"}),
            ], style={"marginTop": "8px"}),
        ], style={
            "backgroundColor": "var(--cd-deep)",
            "borderRadius": "8px",
            "padding": "14px 16px",
            "border": "1px solid rgba(77,171,247,0.12)",
        }),

    ], style={
        "backgroundColor": "var(--cd-card2)",
        "borderRadius": "12px",
        "padding": "20px 24px",
        "border": "1px solid rgba(77,171,247,0.25)",
        "borderLeft": "4px solid #4dabf7",
        "marginBottom": "24px",
    })


# ── Stats bar ─────────────────────────────────────────────────────────────────

def _render_stats(specs, grouped):
    total = len(specs)
    shipped = len(grouped.get("complete", []))
    in_prog = len(grouped.get("in_progress", []))
    proposed = len(grouped.get("spec_complete", []))
    backlog = len(grouped.get("backlog", []))

    pct_done = int((shipped / total * 100) if total else 0)
    pct_prog = int((in_prog / total * 100) if total else 0)
    pct_prop = int((proposed / total * 100) if total else 0)

    future = len(grouped.get("future", []))

    stat_items = [
        ("Total", total, "#e9ecef"),
        ("Shipped", shipped, "#51cf66"),
        ("Progress", in_prog, "#ffd43b"),
        ("Proposal", proposed, "#74c0fc"),
        ("Backlog", backlog, "#868e96"),
        ("Future", future, "#b197fc"),
    ]

    return html.Div([
        # Stat pills
        html.Div([
            html.Div([
                html.Div(str(val), style={"fontSize": "1.25rem", "fontWeight": 700, "color": color}),
                html.Div(label, style={"fontSize": "0.57rem", "color": "var(--cd-faint)", "letterSpacing": "0.4px", "textTransform": "uppercase"}),
            ], style={
                "padding": "9px 16px", "borderRadius": "8px",
                "backgroundColor": "var(--cd-card2)", "border": "1px solid var(--cd-border-sub)",
                "textAlign": "center", "minWidth": "72px",
            })
            for label, val, color in stat_items
        ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "alignItems": "center"}),

        # Progress bar
        html.Div([
            html.Div(
                f"Pipeline: {shipped}/{total} shipped · {pct_done}% done",
                style={"fontSize": "0.6rem", "color": "var(--cd-faint)", "marginBottom": "5px"}
            ),
            html.Div([
                html.Div(style={"width": f"{pct_done}%", "backgroundColor": "#51cf66", "height": "100%", "borderRadius": "3px 0 0 3px" if pct_prog + pct_prop > 0 else "3px"}),
                html.Div(style={"width": f"{pct_prog}%", "backgroundColor": "#ffd43b", "height": "100%"}),
                html.Div(style={"width": f"{pct_prop}%", "backgroundColor": "#74c0fc", "height": "100%"}),
            ], style={
                "height": "7px", "borderRadius": "4px",
                "backgroundColor": "var(--cd-stripe)", "display": "flex", "overflow": "hidden",
                "minWidth": "180px",
            }),
        ], style={"display": "flex", "flexDirection": "column", "justifyContent": "center", "flex": "1", "minWidth": "200px"}),

    ], style={"display": "flex", "gap": "20px", "alignItems": "center", "marginBottom": "20px", "flexWrap": "wrap"})


# ── Main layout ───────────────────────────────────────────────────────────────

def layout():
    specs = _load_specs()
    grouped = _group_specs(specs)

    if not specs:
        return html.Div([
            html.H4("Feature Pipeline", className="text-light mb-2", style={"fontWeight": 600}),
            html.P(
                "No feature specs yet. Ask Code to write spec files to state/specs/ "
                "and they'll appear here as Kanban cards.",
                className="text-muted",
            ),
            dcc.Interval(id="kanban-interval", interval=30_000, n_intervals=0),
        ])

    # Phase summary row — only show phases that actually have features
    phases_with_features = sorted(
        set(s.get("phase", "?") for s in specs if s.get("phase")),
        key=lambda p: PHASE_ORDER.get(p, 99)
    )
    phase_pills = [
        html.Span(
            f"Phase {p}",
            style={
                "fontSize": "0.62rem", "padding": "3px 10px", "borderRadius": "5px",
                "backgroundColor": f"{PHASE_COLORS.get(p, '#868e96')}18",
                "color": PHASE_COLORS.get(p, "#868e96"),
                "border": f"1px solid {PHASE_COLORS.get(p, '#868e96')}30",
                "marginRight": "6px",
            }
        ) for p in phases_with_features
    ]

    next_hero = _render_next_session_hero(specs, specs)

    # ── Phase group filter buttons ─────────────────────────────────────────
    MA_PHASES    = {"MA1", "MA2", "MA3"}
    BIAS_PHASES  = {"A", "B", "C", "D", "E"}

    def _filter_btn(label, value, active_val, color):
        active = (value == active_val)
        return html.Button(
            label,
            id={"type": "kanban-filter-btn", "index": value},
            n_clicks=0,
            style={
                "fontSize": "0.68rem", "fontWeight": 700,
                "padding": "4px 14px", "borderRadius": "6px", "cursor": "pointer",
                "border": f"1px solid {color}40",
                "backgroundColor": f"{color}22" if active else "var(--cd-card2)",
                "color": color if active else "var(--cd-muted)",
                "transition": "all 0.15s",
            }
        )

    filter_bar = html.Div([
        html.Span("Focus:", style={
            "fontSize": "0.62rem", "color": "var(--cd-faint)",
            "fontWeight": 700, "letterSpacing": "0.5px", "marginRight": "8px",
            "textTransform": "uppercase",
        }),
        _filter_btn("All",            "all",   "all",  "#868e96"),
        _filter_btn("Multi-Account",  "ma",    "all",  "#20c997"),
        _filter_btn("Bias Fix",       "bias",  "all",  "#f783ac"),
    ], id="kanban-filter-bar", style={
        "display": "flex", "alignItems": "center", "gap": "6px",
        "marginBottom": "16px",
    })

    # Stale detection for in-progress cards
    recently_active_ids = _get_recently_active_spec_ids()

    # Kanban board and filtered stats (default = all)
    kanban_board = html.Div(
        [_render_column(col, grouped[col["id"]], specs, recently_active_ids) for col in COLUMNS],
        id="kanban-board",
        style={
            "display": "flex", "gap": "14px",
            "overflowX": "auto", "paddingBottom": "12px",
            "alignItems": "flex-start",
        },
    )

    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.H4("Feature Pipeline", className="text-light mb-0", style={"fontWeight": 600}),
                html.Div(
                    phase_pills,
                    style={"display": "flex", "alignItems": "center", "flexWrap": "wrap", "marginTop": "4px"},
                ),
            ]),
        ], style={"marginBottom": "16px"}),

        # Next session hero — the most important thing on this page
        next_hero,

        # Phase group filter
        filter_bar,

        # Stats bar
        html.Div(id="kanban-stats-container", children=_render_stats(specs, grouped)),

        # Kanban board
        kanban_board,

        dcc.Store(id="kanban-filter-store", data="all"),
        dcc.Interval(id="kanban-interval", interval=30_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("kanban-content", "children"),
        Input("kanban-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()

    # Filter button clicks → update board + filter store
    from dash import callback_context, ALL as DALL, MATCH as DMATCH, no_update
    MA_PHASES   = {"MA1", "MA2", "MA3"}
    BIAS_PHASES = {"A", "B", "C", "D", "E"}
    FILTER_COLORS = {"all": "#868e96", "ma": "#20c997", "bias": "#f783ac"}

    @app.callback(
        Output("kanban-filter-store", "data"),
        Output("kanban-filter-bar", "children"),
        Output("kanban-board", "children"),
        Output("kanban-stats-container", "children"),
        Input({"type": "kanban-filter-btn", "index": DALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def apply_filter(n_clicks_list):
        ctx = callback_context
        if not ctx.triggered:
            return no_update, no_update, no_update, no_update

        triggered_id = ctx.triggered[0]["prop_id"]
        import json as _json
        try:
            active = _json.loads(triggered_id.split(".")[0])["index"]
        except Exception:
            active = "all"

        # Reload specs and apply filter
        specs = _load_specs()

        if active == "ma":
            filtered = [s for s in specs if s.get("phase", "") in MA_PHASES]
        elif active == "bias":
            filtered = [s for s in specs if s.get("phase", "") in BIAS_PHASES]
        else:
            filtered = specs

        grouped = _group_specs(filtered)

        def _btn(label, value):
            is_active = (value == active)
            color = FILTER_COLORS[value]
            return html.Button(
                label,
                id={"type": "kanban-filter-btn", "index": value},
                n_clicks=0,
                style={
                    "fontSize": "0.68rem", "fontWeight": 700,
                    "padding": "4px 14px", "borderRadius": "6px", "cursor": "pointer",
                    "border": f"1px solid {color}40",
                    "backgroundColor": f"{color}22" if is_active else "var(--cd-card2)",
                    "color": color if is_active else "var(--cd-muted)",
                }
            )

        new_filter_bar = [
            html.Span("Focus:", style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)",
                "fontWeight": 700, "letterSpacing": "0.5px", "marginRight": "8px",
                "textTransform": "uppercase",
            }),
            _btn("All",           "all"),
            _btn("Multi-Account", "ma"),
            _btn("Bias Fix",      "bias"),
        ]

        recently_active_ids = _get_recently_active_spec_ids()
        new_board = [_render_column(col, grouped[col["id"]], filtered, recently_active_ids) for col in COLUMNS]
        new_stats = _render_stats(filtered, grouped)

        return active, new_filter_bar, new_board, new_stats

    # Route buttons → write chosen status to spec file, refresh board
    # Button index format: "{spec_id}__{bucket}"  e.g. "feat-mtf-gate__in_progress"
    from dash import ALL as DALL2
    @app.callback(
        Output("kanban-content", "children", allow_duplicate=True),
        Input({"type": "kanban-route-btn", "index": DALL2}, "n_clicks"),
        prevent_initial_call=True,
    )
    def route_proposal(n_clicks_list):
        from dash import callback_context as ctx2, no_update
        if not ctx2.triggered or not any(n_clicks_list):
            return no_update
        triggered_id = ctx2.triggered[0]["prop_id"]
        import json as _json2
        try:
            raw_index = _json2.loads(triggered_id.split(".")[0])["index"]
            # index is "{spec_id}__{bucket}"
            parts = raw_index.rsplit("__", 1)
            if len(parts) != 2:
                return no_update
            spec_id, bucket = parts
            if bucket not in VALID_STATUSES:
                return no_update
        except Exception:
            return no_update

        # Find the spec file and write the new status
        if SPECS_DIR.exists():
            for f in SPECS_DIR.glob("*.json"):
                try:
                    data = _json2.loads(f.read_text())
                    if data.get("id") == spec_id:
                        data["status"] = bucket
                        f.write_text(_json2.dumps(data, indent=2))
                        break
                except Exception:
                    pass

        return layout()
