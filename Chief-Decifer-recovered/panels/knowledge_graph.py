"""
Knowledge Graph panel — visualises the graphify codebase knowledge graph.

Shows the 5,696-node / 11,159-edge graph built by `graphify update`.
Default view: community-level overview (one node per community, sized by member count).
Click a community node to drill into its members.
Rebuild button re-runs `graphify update` in a background thread.
Cache: graph.json is loaded once and held for 24 h; stale on rebuild.
"""

import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
import dash_cytoscape as cyto
from dash import Input, Output, State, callback_context, html, dcc

from config import DECIFER_REPO_PATH

# ── Paths ──────────────────────────────────────────────────────────────────────

GRAPH_JSON = DECIFER_REPO_PATH / "graphify-out" / "graph.json"
GRAPH_REPORT = DECIFER_REPO_PATH / "graphify-out" / "GRAPH_REPORT.md"

# ── Cache (24 h TTL) ───────────────────────────────────────────────────────────

_CACHE_TTL = 24 * 60 * 60  # 24 hours in seconds

_cache: dict | None = None
_cache_ts: float = 0.0
_cache_lock = threading.Lock()

# ── Rebuild state ──────────────────────────────────────────────────────────────

_rebuild: dict = {"running": False, "msg": "", "ts": 0.0}


def _load_graph() -> dict | None:
    """Load graph.json with 24 h TTL. Returns None if file missing."""
    global _cache, _cache_ts
    with _cache_lock:
        now = time.time()
        if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
            return _cache
        if not GRAPH_JSON.exists():
            return None
        try:
            data = json.loads(GRAPH_JSON.read_text())
            _cache = data
            _cache_ts = now
            return data
        except Exception:
            return None


def _invalidate_cache():
    global _cache, _cache_ts
    with _cache_lock:
        _cache = None
        _cache_ts = 0.0


def _run_rebuild():
    """Background thread: run `graphify update` and invalidate cache when done."""
    _rebuild["running"] = True
    _rebuild["msg"] = ""
    try:
        result = subprocess.run(
            ["graphify", "update", str(DECIFER_REPO_PATH)],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            _rebuild["msg"] = "Rebuild complete"
        else:
            _rebuild["msg"] = f"Rebuild failed: {result.stderr[:120]}"
        _invalidate_cache()
    except Exception as e:
        _rebuild["msg"] = f"Error: {e}"
    finally:
        _rebuild["running"] = False
        _rebuild["ts"] = time.time()


# ── Graph data helpers ─────────────────────────────────────────────────────────

def _is_rationale(node: dict) -> bool:
    """True if the node is a docstring/comment node, not actual code."""
    label = node.get("label", "")
    file_type = node.get("file_type", "")
    if file_type == "rationale":
        return True
    # Rationale nodes have long prose labels (not identifiers)
    if len(label) > 60 and " " in label:
        return True
    return False


def _dominant_module(nodes: list[dict]) -> str:
    """Return the most common non-test source file name for a community."""
    from collections import Counter
    files: Counter = Counter()
    for n in nodes:
        sf = n.get("source_file", "")
        if not sf:
            continue
        name = sf.split("/")[-1].replace(".py", "")
        if name.startswith("test_") or name.startswith("conftest"):
            continue
        files[name] += 1
    if files:
        return files.most_common(1)[0][0]
    # Fall back to test file name if community is test-only
    files2: Counter = Counter()
    for n in nodes:
        sf = n.get("source_file", "")
        if sf:
            files2[sf.split("/")[-1].replace(".py", "")] += 1
    return files2.most_common(1)[0][0] if files2 else "unknown"


def _top_code_labels(nodes: list[dict], n: int = 4) -> str:
    """Return top N readable function/class labels from a community."""
    labels = []
    for node in nodes:
        if _is_rationale(node):
            continue
        label = node.get("label", "")
        # Skip file-level nodes and very short/generic names
        if label.endswith(".py") or label in (".__init__()", ".update()", ".get()"):
            continue
        if label and len(label) > 3:
            labels.append(label)
        if len(labels) >= n:
            break
    return ", ".join(labels)


def _community_elements(data: dict) -> list[dict]:
    """One cytoscape node per community, named by dominant module."""
    nodes = data.get("nodes", [])
    links = data.get("links", [])

    # Group nodes by community
    by_community: dict[int, list[dict]] = {}
    for node in nodes:
        c = node.get("community", 0)
        by_community.setdefault(c, []).append(node)

    # Build cytoscape elements
    elements = []
    for c, members in sorted(by_community.items()):
        module = _dominant_module(members)
        count = len(members)
        top_labels = _top_code_labels(members)
        elements.append({
            "data": {
                "id": f"comm_{c}",
                "label": f"{module}\n({count})",
                "community": c,
                "count": count,
                "members": top_labels or module,
                "module": module,
            }
        })

    # Edges between communities (deduplicated)
    id_to_comm = {n["id"]: n.get("community", 0) for n in nodes}
    seen_edges: set[tuple] = set()
    for lnk in links:
        src_c = id_to_comm.get(lnk.get("source"), -1)
        tgt_c = id_to_comm.get(lnk.get("target"), -1)
        if src_c == tgt_c or src_c < 0 or tgt_c < 0:
            continue
        key = (min(src_c, tgt_c), max(src_c, tgt_c))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        elements.append({
            "data": {
                "source": f"comm_{src_c}",
                "target": f"comm_{tgt_c}",
            }
        })

    return elements


_DRILL_CAP = 300  # max nodes to render in community drill-down


def _community_detail_elements(data: dict, community_id: int) -> tuple[list[dict], bool]:
    """
    Nodes + intra-community edges for one community.
    Returns (elements, truncated) — truncated=True when capped at _DRILL_CAP.
    """
    nodes = data.get("nodes", [])
    links = data.get("links", [])

    # Filter out docstring/rationale nodes — show code only
    all_members = [
        n for n in nodes
        if n.get("community") == community_id and not _is_rationale(n)
    ]
    truncated = len(all_members) > _DRILL_CAP

    # Sort by degree (most-connected first) so the cap keeps the interesting nodes
    if truncated:
        node_ids = {n["id"] for n in all_members}
        degree: dict[str, int] = {}
        for lnk in links:
            if lnk.get("source") in node_ids:
                degree[lnk["source"]] = degree.get(lnk["source"], 0) + 1
            if lnk.get("target") in node_ids:
                degree[lnk["target"]] = degree.get(lnk["target"], 0) + 1
        all_members = sorted(all_members, key=lambda n: degree.get(n["id"], 0), reverse=True)
        all_members = all_members[:_DRILL_CAP]

    members = {n["id"] for n in all_members}

    elements = []
    for n in all_members:
        label = n.get("label") or n.get("id", "")
        # Use source file basename as group hint in tooltip
        sf = n.get("source_file", "")
        file_name = sf.split("/")[-1] if sf else ""
        loc = n.get("source_location", "")
        elements.append({
            "data": {
                "id": n["id"],
                "label": str(label)[:35],
                "src": f"{file_name} {loc}".strip(),
                "file_type": n.get("file_type", "code"),
                "is_test": "1" if file_name.startswith("test_") else "0",
            }
        })

    for lnk in links:
        if lnk.get("source") in members and lnk.get("target") in members:
            elements.append({
                "data": {
                    "source": lnk["source"],
                    "target": lnk["target"],
                    "edge_type": lnk.get("type", ""),
                }
            })

    return elements, truncated


# ── Cytoscape stylesheets ──────────────────────────────────────────────────────

_STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "font-size": "11px",
            "font-weight": "600",
            "color": "#ffffff",
            "background-color": "#1971c2",
            "width": "mapData(count, 1, 800, 55, 120)",
            "height": "mapData(count, 1, 800, 55, 120)",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "100px",
            "border-width": 2,
            "border-color": "#4dabf7",
            "cursor": "pointer",
        }
    },
    {
        "selector": "node:hover",
        "style": {
            "background-color": "#4dabf7",
            "border-color": "#ffffff",
            "border-width": 3,
        }
    },
    {
        "selector": "node:selected",
        "style": {
            "background-color": "#e03131",
            "border-color": "#ff6b6b",
            "border-width": 3,
        }
    },
    {
        "selector": "edge",
        "style": {
            "width": 1.5,
            "line-color": "#2c2c3e",
            "target-arrow-color": "#2c2c3e",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "opacity": 0.5,
        }
    },
]

_DETAIL_STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "font-size": "10px",
            "color": "#ffffff",
            "background-color": "#1971c2",
            "width": 48,
            "height": 48,
            "text-valign": "bottom",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "90px",
            "text-margin-y": 6,
            "border-width": 2,
            "border-color": "#4dabf7",
            "cursor": "pointer",
        }
    },
    {
        # Test nodes — muted so production code stands out
        "selector": "node[is_test='1']",
        "style": {
            "background-color": "#2f6219",
            "border-color": "#40c057",
            "opacity": 0.65,
            "font-size": "9px",
        }
    },
    {
        "selector": "node:hover",
        "style": {
            "background-color": "#4dabf7",
            "border-color": "#ffffff",
            "border-width": 3,
            "opacity": 1,
        }
    },
    {
        "selector": "node:selected",
        "style": {
            "background-color": "#e03131",
            "border-color": "#ff6b6b",
            "border-width": 3,
            "opacity": 1,
        }
    },
    {
        "selector": "edge",
        "style": {
            "width": 1,
            "line-color": "#2c2c3e",
            "target-arrow-color": "#2c2c3e",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "opacity": 0.4,
        }
    },
]


# ── Stats bar ──────────────────────────────────────────────────────────────────

def _stats_bar(data: dict | None) -> html.Div:
    if data is None:
        return html.Div("No graph built yet — click Rebuild to generate.", className="text-warning small")

    nodes = data.get("nodes", [])
    links = data.get("links", [])
    communities = len({n.get("community") for n in nodes})

    mtime = ""
    if GRAPH_JSON.exists():
        ts = GRAPH_JSON.stat().st_mtime
        mtime = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

    return dbc.Row([
        dbc.Col(html.Span([html.Strong(f"{len(nodes):,}"), " nodes"], className="me-3")),
        dbc.Col(html.Span([html.Strong(f"{len(links):,}"), " edges"], className="me-3")),
        dbc.Col(html.Span([html.Strong(f"{communities}"), " communities"], className="me-3")),
        dbc.Col(html.Span(f"Built: {mtime}", className="text-muted"), className="ms-auto text-end"),
    ], className="small align-items-center")


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    data = _load_graph()

    return html.Div([

        # ── Header bar ────────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col(html.H5("Knowledge Graph", className="mb-0"), width="auto", className="align-self-center"),
            dbc.Col(_stats_bar(data), className="align-self-center"),
            dbc.Col([
                dbc.Button(
                    [html.I(className="bi bi-arrow-clockwise me-1"), "Rebuild"],
                    id="kg-rebuild-btn", color="secondary", size="sm", className="me-2"
                ),
                dbc.Button(
                    [html.I(className="bi bi-arrow-left me-1"), "All Communities"],
                    id="kg-back-btn", color="outline-secondary", size="sm",
                    style={"display": "none"}
                ),
            ], width="auto", className="ms-auto"),
        ], className="mb-3 align-items-center"),

        # ── Status bar ─────────────────────────────────────────────────────────
        html.Div(id="kg-status", className="small text-muted mb-2"),

        # ── View label ────────────────────────────────────────────────────────
        html.Div(id="kg-view-label", children="Community overview — click a node to expand",
                 className="small text-secondary mb-2"),

        # ── Cytoscape graph ───────────────────────────────────────────────────
        cyto.Cytoscape(
            id="kg-cytoscape",
            layout={
                "name": "cose",
                "animate": False,
                "randomize": False,
                "nodeRepulsion": 800000,
                "idealEdgeLength": 180,
                "nodeOverlap": 40,
                "gravity": 40,
                "numIter": 1000,
                "initialTemp": 300,
                "coolingFactor": 0.95,
                "minTemp": 1.0,
            },
            style={"width": "100%", "height": "680px",
                   "background": "#0d1117", "borderRadius": "8px"},
            elements=_community_elements(data) if data else [],
            stylesheet=_STYLESHEET,
            minZoom=0.05,
            maxZoom=8,
            responsive=True,
        ),

        # ── Node detail panel ─────────────────────────────────────────────────
        html.Div(id="kg-node-detail", className="mt-3"),

        # ── Hidden state ──────────────────────────────────────────────────────
        dcc.Store(id="kg-view-mode", data="communities"),  # "communities" | "community:<id>"
        dcc.Interval(id="kg-rebuild-poll", interval=2000, disabled=True, n_intervals=0),
        dcc.Interval(id="kg-refresh-interval", interval=_CACHE_TTL * 1000, n_intervals=0),

    ], className="px-1")


# ── Callbacks ──────────────────────────────────────────────────────────────────

def register_callbacks(app: dash.Dash) -> None:

    # ── Rebuild button → start background job ─────────────────────────────────
    @app.callback(
        Output("kg-status", "children", allow_duplicate=True),
        Output("kg-rebuild-poll", "disabled", allow_duplicate=True),
        Input("kg-rebuild-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def start_rebuild(_n):
        if _rebuild["running"]:
            return "Already rebuilding…", False
        t = threading.Thread(target=_run_rebuild, daemon=True)
        t.start()
        return [html.I(className="bi bi-arrow-clockwise me-1 spin"), "Rebuilding graph…"], False

    # ── Poll rebuild status ───────────────────────────────────────────────────
    @app.callback(
        Output("kg-status", "children", allow_duplicate=True),
        Output("kg-rebuild-poll", "disabled", allow_duplicate=True),
        Input("kg-rebuild-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def poll_rebuild(_n):
        if _rebuild["running"]:
            return [html.I(className="bi bi-arrow-clockwise me-1 spin"), "Rebuilding…"], False
        msg = _rebuild.get("msg", "")
        color = "text-success" if "complete" in msg else "text-danger"
        return html.Span(msg, className=color), True  # stop polling

    # ── Community node click → drill into community ───────────────────────────
    @app.callback(
        Output("kg-cytoscape", "elements"),
        Output("kg-cytoscape", "stylesheet"),
        Output("kg-cytoscape", "layout"),
        Output("kg-view-mode", "data"),
        Output("kg-view-label", "children"),
        Output("kg-back-btn", "style"),
        Input("kg-cytoscape", "tapNodeData"),
        Input("kg-back-btn", "n_clicks"),
        State("kg-view-mode", "data"),
        prevent_initial_call=True,
    )
    def handle_node_click(node_data, _back, view_mode):
        ctx = callback_context
        trigger = ctx.triggered[0]["prop_id"] if ctx.triggered else ""
        data = _load_graph()
        show_back = {"display": "inline-block"}
        hide_back = {"display": "none"}

        # Back button → return to community overview
        if "kg-back-btn" in trigger or node_data is None:
            if data is None:
                return [], _STYLESHEET, {"name": "cose"}, "communities", "No graph data", hide_back
            return (
                _community_elements(data),
                _STYLESHEET,
                {"name": "cose", "animate": False, "randomize": True},
                "communities",
                "Community overview — click a node to expand",
                hide_back,
            )

        # Community node clicked → drill in
        if view_mode == "communities" and node_data and "community" in node_data:
            comm_id = int(node_data["community"])
            if data is None:
                return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update
            elements, truncated = _community_detail_elements(data, comm_id)
            count = node_data.get("count", "?")
            label = f"Community {comm_id} — {count} nodes  |  click Back to return"
            if truncated:
                label += f"  ⚠ showing top {_DRILL_CAP} by degree"
            return (
                elements,
                _DETAIL_STYLESHEET,
                {
                    "name": "cose",
                    "animate": False,
                    "randomize": False,
                    "nodeRepulsion": 600000,
                    "idealEdgeLength": 120,
                    "nodeOverlap": 30,
                    "gravity": 60,
                    "numIter": 1000,
                },
                f"community:{comm_id}",
                label,
                show_back,
            )

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    # ── Node click → detail panel ─────────────────────────────────────────────
    @app.callback(
        Output("kg-node-detail", "children"),
        Input("kg-cytoscape", "tapNodeData"),
        State("kg-view-mode", "data"),
    )
    def show_node_detail(node_data, view_mode):
        if not node_data:
            return ""
        if view_mode == "communities":
            members = node_data.get("members", "")
            count = node_data.get("count", 0)
            comm = node_data.get("community", "?")
            return dbc.Alert([
                html.Strong(f"Community {comm}"),
                html.Span(f"  •  {count} nodes", className="text-muted ms-2"),
                html.Br(),
                html.Small(f"Top members: {members}", className="text-secondary"),
            ], color="dark", className="py-2 px-3 mb-0")
        else:
            src = node_data.get("src", "")
            label = node_data.get("label", node_data.get("id", ""))
            return dbc.Alert([
                html.Strong(label),
                html.Br(),
                html.Small(src, className="text-secondary font-monospace"),
            ], color="dark", className="py-2 px-3 mb-0")

    # ── 24 h auto-refresh: reload graph elements ──────────────────────────────
    @app.callback(
        Output("kg-cytoscape", "elements", allow_duplicate=True),
        Input("kg-refresh-interval", "n_intervals"),
        State("kg-view-mode", "data"),
        prevent_initial_call=True,
    )
    def auto_refresh(_n, view_mode):
        _invalidate_cache()
        data = _load_graph()
        if data is None:
            return []
        if view_mode == "communities":
            return _community_elements(data)
        if view_mode and view_mode.startswith("community:"):
            comm_id = int(view_mode.split(":")[1])
            elements, _ = _community_detail_elements(data, comm_id)
            return elements
        return _community_elements(data)
