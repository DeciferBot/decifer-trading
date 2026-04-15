"""
Blueprint panel — deep technical view of the codebase architecture.
Shows subsystems, module groupings, library dependencies, and import graph.
All data is read dynamically from the decifer-trading repo.
"""

import ast
import re
from pathlib import Path
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import DECIFER_REPO_PATH


# ── Module categorization ────────────────────────────────────────────────────

SUBSYSTEMS = {
    "Trading Core": {
        "color": "#4dabf7",
        "desc": "Main trading loop, order execution, and smart routing",
        "modules": ["bot.py", "orders.py", "smart_execution.py"],
    },
    "Signal Generation": {
        "color": "#51cf66",
        "desc": "Market scanning, technical signals, and options screening",
        "modules": ["options_scanner.py", "scanner.py", "signals.py"],
    },
    "Risk Management": {
        "color": "#ff6b6b",
        "desc": "Position sizing, drawdown limits, portfolio optimization",
        "modules": ["portfolio_optimizer.py", "risk.py"],
    },
    "AI & Learning": {
        "color": "#ffd43b",
        "desc": "Claude-powered agents, ML prediction, learning loops",
        "modules": ["agents.py", "learning.py", "ml_engine.py", "sentinel_agents.py"],
    },
    "Market Data": {
        "color": "#74c0fc",
        "desc": "Historical data collection, real-time IBKR streaming",
        "modules": ["data_collector.py", "ibkr_streaming.py"],
    },
    "News & Sentiment": {
        "color": "#fcc419",
        "desc": "News feeds, sentiment analysis, social media, theme tracking",
        "modules": ["news.py", "news_sentinel.py", "social_sentiment.py", "theme_tracker.py"],
    },
    "Options": {
        "color": "#da77f2",
        "desc": "Greeks calculation, options chain analysis",
        "modules": ["options.py"],
    },
    "Analytics & UI": {
        "color": "#74c0fc",
        "desc": "Web dashboard, backtesting engine, trade journaling",
        "modules": ["backtester.py", "daily_journal.py", "dashboard.py"],
    },
    "Configuration": {
        "color": "var(--cd-muted)",
        "desc": "Central configuration — IBKR, API keys, risk params",
        "modules": ["config.py"],
    },
    "Other": {
        "color": "var(--cd-muted)",
        "desc": "Utility and auxiliary modules",
        "modules": ["patch.py", "signals_integration_example.py"],
    },
}


# ── Data helpers ─────────────────────────────────────────────────────────────

def _count_lines(path):
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:
        return 0


def _analyse_file(path):
    """Count functions, classes, and extract imports from a Python file."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return {"functions": 0, "classes": 0, "local_imports": [], "external_imports": []}

    functions = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    classes = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))

    local_imports = set()
    external_imports = set()

    # Get list of local module names
    local_modules = set()
    if DECIFER_REPO_PATH and DECIFER_REPO_PATH.exists():
        for f in DECIFER_REPO_PATH.glob("*.py"):
            if not f.name.startswith("_"):
                local_modules.add(f.stem)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in local_modules:
                    local_imports.add(name + ".py")
                else:
                    external_imports.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split(".")[0]
                if name in local_modules:
                    local_imports.add(name + ".py")
                else:
                    external_imports.add(name)

    # Filter out stdlib
    stdlib = {
        "os", "sys", "json", "re", "time", "datetime", "math", "random",
        "pathlib", "logging", "collections", "functools", "itertools",
        "typing", "abc", "enum", "dataclasses", "subprocess", "threading",
        "concurrent", "asyncio", "io", "csv", "copy", "hashlib", "uuid",
        "warnings", "traceback", "inspect", "textwrap", "string", "decimal",
        "fractions", "operator", "contextlib", "unittest", "pprint",
        "argparse", "configparser", "shutil", "glob", "tempfile",
        "socket", "http", "urllib", "email", "html", "xml",
    }
    external_imports -= stdlib

    return {
        "functions": functions,
        "classes": classes,
        "local_imports": sorted(local_imports),
        "external_imports": sorted(external_imports),
    }


def _load_requirements():
    """Read requirements.txt and return list of {name, version, desc}."""
    req_file = DECIFER_REPO_PATH / "requirements.txt"
    if not req_file.exists():
        return []

    libs = []
    try:
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Parse "package>=1.0" or "package==1.0" or just "package"
            match = re.match(r'^([a-zA-Z0-9_-]+)\s*([><=!~]+.*)?', line)
            if match:
                name = match.group(1)
                version = match.group(2) or ""
                libs.append({"name": name, "version": version.strip()})
    except Exception:
        pass

    return libs


# Library descriptions (plain English)
LIB_DESCRIPTIONS = {
    "ib_async": "Interactive Brokers API",
    "anthropic": "Claude API for 4-agent pipeline + news sentiment",
    "yfinance": "Free market data — OHLCV, options chains, fundamentals",
    "pandas": "DataFrames",
    "numpy": "Numerical computation",
    "pyarrow": "Parquet file format for historical ML training data",
    "TA-Lib": "Technical indicators — requires C library: brew install ta-lib",
    "py_vollib": "Black-Scholes Greeks",
    "scikit-learn": "RandomForest + GradientBoosting for trade prediction",
    "joblib": "Model persistence — save/load trained .pkl models",
    "statsmodels": "ADF test for mean-reversion gating in REVERSION dimension",
    "nltk": "VADER sentiment analysis",
    "requests": "HTTP requests for news feeds, Reddit API, ApexWisdom",
    "schedule": "Task scheduling",
    "colorama": "Terminal color output",
    "pytz": "Timezone handling",
    "dash": "Web framework for dashboards",
    "plotly": "Interactive charts",
    "flask": "Web framework",
}


# ── Scan functions ───────────────────────────────────────────────────────────

def _full_scan():
    """Do a complete scan of the repo for the blueprint."""
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return None

    py_files = sorted([f for f in DECIFER_REPO_PATH.glob("*.py") if not f.name.startswith("_")])
    test_dir = DECIFER_REPO_PATH / "tests"
    test_files = list(test_dir.glob("test_*.py")) if test_dir.exists() else []

    # Analyse each file
    file_data = {}
    total_lines = 0
    total_functions = 0
    total_classes = 0

    for f in py_files:
        lines = _count_lines(f)
        analysis = _analyse_file(f)
        file_data[f.name] = {
            "lines": lines,
            "functions": analysis["functions"],
            "classes": analysis["classes"],
            "local_imports": analysis["local_imports"],
            "external_imports": analysis["external_imports"],
        }
        total_lines += lines
        total_functions += analysis["functions"]
        total_classes += analysis["classes"]

    # Check which modules have tests
    tested_modules = set()
    for tf in test_files:
        name = tf.name.replace("test_", "").replace(".py", "")
        for pf in py_files:
            if name == pf.stem or name in pf.stem:
                tested_modules.add(pf.name)

    # Libraries
    libraries = _load_requirements()

    # Build import graph (who imports whom)
    import_graph = {}
    for fname, data in file_data.items():
        for imported in data["local_imports"]:
            if imported not in import_graph:
                import_graph[imported] = []
            import_graph[imported].append(fname)

    # Which modules use which library
    lib_users = {}
    for fname, data in file_data.items():
        for ext in data["external_imports"]:
            ext_lower = ext.lower().replace("-", "_").replace(".", "_")
            if ext_lower not in lib_users:
                lib_users[ext_lower] = []
            lib_users[ext_lower].append(fname)

    return {
        "files": file_data,
        "total_lines": total_lines,
        "total_functions": total_functions,
        "total_classes": total_classes,
        "module_count": len(py_files),
        "test_count": len(test_files),
        "tested_modules": tested_modules,
        "libraries": libraries,
        "import_graph": import_graph,
        "lib_users": lib_users,
    }


# ── Renderers ────────────────────────────────────────────────────────────────

def _render_stat_bar(scan):
    """Product Blueprint summary stats."""
    items = [
        (str(scan["module_count"]), "Modules", "#4dabf7"),
        (f"{scan['total_lines']/1000:.1f}k" if scan["total_lines"] >= 1000 else str(scan["total_lines"]), "Lines", "#adb5bd"),
        (str(scan["total_functions"]), "Functions", "#51cf66"),
        (str(scan["total_classes"]), "Classes", "#ffd43b"),
        (str(len(scan["libraries"])), "Libraries", "#da77f2"),
        (f"{len(scan['tested_modules'])}/{scan['module_count']}", "Tested", "#ff6b6b" if len(scan["tested_modules"]) == 0 else "#51cf66"),
    ]

    cards = []
    for value, label, color in items:
        cards.append(dbc.Col(
            html.Div([
                html.Span(value, style={"fontSize": "1.5rem", "fontWeight": "700", "color": color, "display": "block"}),
                html.Small(label, style={"color": "var(--cd-muted)", "fontSize": "0.7rem", "textTransform": "uppercase"}),
            ], className="text-center", style={
                "backgroundColor": "var(--cd-card)",
                "borderRadius": "8px",
                "padding": "14px 8px",
                "border": "1px solid var(--cd-border)",
            }),
            className="mb-3",
        ))

    return html.Div([
        html.Div([
            html.H5("Product Blueprint", className="text-light mb-0", style={"fontWeight": "600"}),
        ], className="d-flex justify-content-between align-items-center mb-3"),
        dbc.Row(cards),
    ], className="mb-4")


def _render_subsystems(scan):
    """Architecture subsystem cards with module listings."""
    subsystem_cards = []

    for name, info in SUBSYSTEMS.items():
        modules_in_group = [m for m in info["modules"] if m in scan["files"]]
        if not modules_in_group:
            continue

        group_lines = sum(scan["files"][m]["lines"] for m in modules_in_group)
        group_fns = sum(scan["files"][m]["functions"] for m in modules_in_group)

        # Module rows
        module_rows = []
        for m in modules_in_group:
            fd = scan["files"][m]
            is_tested = m in scan["tested_modules"]
            module_rows.append(
                html.Div([
                    html.Div([
                        html.Span(m, className="text-light", style={"fontWeight": "500", "fontSize": "0.8rem"}),
                    ]),
                    html.Div([
                        html.Small(f"{fd['lines']} lines", className="text-muted me-2"),
                        html.Small(f"{fd['functions']} fn", className="text-muted me-2"),
                        html.Small(
                            "tested" if is_tested else "untested",
                            className="text-success" if is_tested else "text-danger",
                            style={"fontSize": "0.7rem"},
                        ),
                    ]),
                ], className="d-flex justify-content-between align-items-center py-1",
                   style={"borderBottom": "1px solid var(--cd-border)"})
            )

        subsystem_cards.append(dbc.Col(
            dbc.Card([
                dbc.CardBody([
                    # Header
                    html.Div([
                        html.Span(
                            "\u25CF ",
                            style={"color": info["color"], "fontSize": "0.6rem"},
                        ),
                        html.Span(name, className="text-light", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                        html.Span(
                            f"  {group_lines} lines \u00B7 {group_fns} fn",
                            className="text-muted", style={"fontSize": "0.7rem"},
                        ),
                    ], className="mb-2"),
                    html.P(info["desc"], className="text-muted small mb-2", style={"fontSize": "0.7rem", "lineHeight": "1.4"}),
                    html.Div(module_rows),
                ], className="p-3"),
            ], style={
                "backgroundColor": "var(--cd-stripe)",
                "border": "1px solid var(--cd-border)",
                "borderRadius": "8px",
                "height": "100%",
            }),
            md=3, className="mb-3",
        ))

    return html.Div([
        html.H5("Architecture — Subsystems", className="text-light mb-3", style={"fontWeight": "600"}),
        dbc.Row(subsystem_cards),
    ], className="mb-4", style={
        "backgroundColor": "var(--cd-card2)",
        "borderRadius": "10px",
        "padding": "20px",
        "border": "1px solid var(--cd-border)",
    })


def _render_libraries(scan):
    """Libraries & Dependencies section."""
    libs = scan["libraries"]
    if not libs:
        return html.Div()

    lib_rows = []
    for lib in libs:
        name = lib["name"]
        version = lib["version"]
        desc = LIB_DESCRIPTIONS.get(name, "")

        # Find which modules use this library
        name_normalized = name.lower().replace("-", "_").replace(".", "_")
        users = scan["lib_users"].get(name_normalized, [])
        # Also check common aliases
        aliases = {
            "ib_async": ["ib_async", "ib_insync"],
            "scikit_learn": ["sklearn"],
            "ta_lib": ["talib"],
            "py_vollib": ["py_vollib"],
        }
        for alias_list in aliases.values():
            for alias in alias_list:
                if alias == name_normalized:
                    for a in alias_list:
                        users.extend(scan["lib_users"].get(a, []))

        users = sorted(set(users))

        lib_rows.append(
            html.Div([
                html.Div([
                    html.Div([
                        html.Span(name, className="text-info", style={"fontWeight": "600", "fontSize": "0.8rem"}),
                        html.Small(f" {version}", className="text-muted") if version else None,
                    ]),
                    html.Small(desc, className="text-muted d-block", style={"fontSize": "0.7rem"}) if desc else None,
                ], style={"minWidth": "250px"}),
                html.Div([
                    html.Small(", ".join(users) if users else "", className="text-muted", style={"fontSize": "0.7rem"}),
                ], className="text-end"),
            ], className="d-flex justify-content-between align-items-start py-2",
               style={"borderBottom": "1px solid var(--cd-border)"})
        )

    return html.Div([
        html.H5("Libraries & Dependencies", className="text-light mb-3", style={"fontWeight": "600"}),
        html.Div(lib_rows),
    ], className="mb-4", style={
        "backgroundColor": "var(--cd-card2)",
        "borderRadius": "10px",
        "padding": "20px",
        "border": "1px solid var(--cd-border)",
    })


def _render_import_graph(scan):
    """Dependency graph — which modules are imported by the most others."""
    graph = scan["import_graph"]
    if not graph:
        return html.Div()

    # Sort by number of importers (most-imported first)
    sorted_graph = sorted(graph.items(), key=lambda x: len(x[1]), reverse=True)

    rows = []
    for module, importers in sorted_graph[:15]:
        count = len(importers)
        bar_pct = min(count * 10, 100)

        rows.append(
            html.Div([
                html.Div([
                    html.Span(module, className="text-info", style={"fontWeight": "600", "fontSize": "0.8rem"}),
                    html.Small(f" — imported by {count}", className="text-muted"),
                ], className="mb-1"),
                html.Div([
                    dbc.Progress(
                        value=bar_pct, color="info",
                        style={"height": "4px", "backgroundColor": "var(--cd-stripe)", "width": "120px"},
                        className="d-inline-flex me-2",
                    ),
                    html.Small(
                        ", ".join(sorted(importers)),
                        className="text-muted",
                        style={"fontSize": "0.65rem"},
                    ),
                ], className="d-flex align-items-center"),
            ], className="py-2", style={"borderBottom": "1px solid var(--cd-border)"})
        )

    return html.Div([
        html.H5("Dependency Graph (top importers)", className="text-light mb-3", style={"fontWeight": "600"}),
        html.Div(rows),
    ], style={
        "backgroundColor": "var(--cd-card2)",
        "borderRadius": "10px",
        "padding": "20px",
        "border": "1px solid var(--cd-border)",
    })


# ── Main layout ──────────────────────────────────────────────────────────────

def layout():
    scan = _full_scan()

    if not scan:
        return html.Div([
            html.H4("Blueprint", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(
                "Can't find the Decifer Trading repo. Check that DECIFER_REPO_PATH is set correctly in your .env file.",
                className="text-warning",
            ),
            dcc.Interval(id="blueprint-interval", interval=120_000, n_intervals=0),
        ])

    return html.Div([
        _render_subsystems(scan),

        dbc.Row([
            dbc.Col(_render_libraries(scan), md=7),
            dbc.Col(_render_import_graph(scan), md=5),
        ]),

        dcc.Interval(id="blueprint-interval", interval=120_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("blueprint-content", "children"),
        Input("blueprint-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()
