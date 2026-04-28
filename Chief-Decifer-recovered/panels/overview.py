"""
Overview panel — draggable-tile dashboard.
Tiles: Stats · Pillar Cards · Under the Hood · Trade Simulator
Each tile is wrapped in DashboardItem so the user can drag/resize.
Layout persists to localStorage via dash_draggable save=True.
"""

import ast
import json
import time
from pathlib import Path
from dash import html, dcc, Input, Output, State, callback_context, ALL, MATCH
import dash_bootstrap_components as dbc
try:
    import dash_draggable
    HAS_DRAGGABLE = True
except ImportError:
    HAS_DRAGGABLE = False
from config import (
    DECIFER_REPO_PATH, SPECS_DIR, SESSIONS_DIR, RESEARCH_DIR,
)


# ── Data helpers ──────────────────────────────────────────────────────────

_scan_cache: dict = {}
_scan_cache_ts: float = 0.0
_SCAN_TTL = 60  # seconds


def _scan_repo():
    global _scan_cache, _scan_cache_ts
    if _scan_cache and time.time() - _scan_cache_ts < _SCAN_TTL:
        return _scan_cache
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return None
    py_files = sorted([f for f in DECIFER_REPO_PATH.glob("*.py") if not f.name.startswith("_")])
    test_dir = DECIFER_REPO_PATH / "tests"
    test_files = list(test_dir.glob("test_*.py")) if test_dir.exists() else []
    total_lines = total_functions = total_classes = 0
    for f in py_files:
        try:
            source = f.read_text(encoding="utf-8", errors="ignore")
            total_lines += len(source.splitlines())
            tree = ast.parse(source)
            total_functions += sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
            total_classes += sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        except Exception:
            pass
    req_file = DECIFER_REPO_PATH / "requirements.txt"
    lib_count = 0
    if req_file.exists():
        try:
            libs = [l.strip() for l in req_file.read_text().splitlines() if l.strip() and not l.startswith("#")]
            lib_count = len(libs)
        except Exception:
            pass
    tested_count = 0
    for tf in test_files:
        name = tf.name.replace("test_", "").replace(".py", "")
        if any(name in pf.stem for pf in py_files):
            tested_count += 1
    specs_count = len(list(SPECS_DIR.glob("*.json"))) if SPECS_DIR.exists() else 0
    result = {
        "modules": len(py_files), "lines": total_lines,
        "functions": total_functions, "classes": total_classes,
        "libraries": lib_count, "test_files": len(test_files),
        "tested": tested_count, "proposals": specs_count,
    }
    _scan_cache = result
    _scan_cache_ts = time.time()
    return result


# ── The 6 Pillars ─────────────────────────────────────────────────────────

PILLARS = [
    {
        "id": "scan", "num": "1", "name": "Scan Market", "color": "#4dabf7",
        "icon": "🔍",
        "short": "Find stocks worth watching",
        "detail": [
            ("TradingView Screener", "Scans 3,000+ stocks in real-time. Filters by volume, momentum, and technical setups. Completely free — no API key needed."),
            ("Yahoo Finance Data", "Pulls OHLCV price data, options chains, and fundamentals via yfinance. Historical data goes back years for backtesting."),
            ("Options Scanner", "Looks for unusual options activity — volume spikes, implied volatility changes, and flow signals that suggest institutional positioning."),
            ("Finviz + News RSS", "Checks Finviz for screening overlays and Yahoo RSS for breaking news that could affect candidates."),
        ],
    },
    {
        "id": "score", "num": "2", "name": "Score Signals", "color": "#51cf66",
        "icon": "📊",
        "short": "10 dimensions of analysis",
        "detail": [
            ("Trend", "Are the moving averages aligned? Uses 50/200 MA crossover and slope."),
            ("Momentum", "How strong is the current move? RSI, MACD, and rate of change."),
            ("Squeeze", "Bollinger Band squeeze detection catches compression before expansion."),
            ("Flow", "Volume analysis — on-balance volume and accumulation/distribution."),
            ("Breakout", "Support/resistance breaks with volume confirmation."),
            ("Confluence", "How many indicators agree? More alignment = stronger signal."),
            ("News Sentiment", "Claude reads recent headlines and scores sentiment -1 to +1."),
            ("Social Buzz", "Reddit mention velocity and VADER sentiment."),
            ("Mean Reversion", "Is the stock stretched from its mean? ADF test gates this."),
        ],
    },
    {
        "id": "decide", "num": "3", "name": "AI Decision", "color": "#ffd43b",
        "icon": "🧠",
        "short": "Agents debate every trade",
        "detail": [
            ("Multi-Agent Council", "Multiple Claude-powered agents analyse the opportunity from different angles."),
            ("The Researcher", "Digs into fundamentals, recent news, sector trends, and macro conditions."),
            ("The Architect", "Designs the trade structure — entry, size, stop-loss, take-profit."),
            ("The Critic", "Tries to break the thesis. Looks for what could go wrong."),
            ("Consensus Required", "Agents must reach agreement. One strong dissent can block a trade."),
        ],
    },
    {
        "id": "risk", "num": "4", "name": "Risk Check", "color": "#ff6b6b",
        "icon": "🛡️",
        "short": "5-layer safety system",
        "detail": [
            ("Layer 1 — Market Hours", "Only trades during regular US market hours. No pre-market gambles."),
            ("Layer 2 — Daily Loss Cap", "If the bot loses more than the daily limit, it stops trading for the day."),
            ("Layer 3 — Streak Protection", "After consecutive losses, position sizes shrink automatically."),
            ("Layer 4 — Position Sizing", "Kelly criterion calculates optimal bet size. Never risks more than a fixed %."),
            ("Layer 5 — Correlation Guard", "Checks if a new trade is too correlated with existing positions."),
        ],
    },
    {
        "id": "execute", "num": "5", "name": "Execute", "color": "#74c0fc",
        "icon": "⚡",
        "short": "Orders placed via IBKR",
        "detail": [
            ("Smart Order Routing", "Places limit orders near the bid/ask spread. Minimises slippage."),
            ("Bracket Orders", "Every trade gets an automatic stop-loss and take-profit bracket."),
            ("Interactive Brokers API", "Connected to IBKR via ib_async. Currently on paper account."),
            ("Fill Monitoring", "Tracks order fills in real-time. Adjusts brackets on partial fills."),
        ],
    },
    {
        "id": "learn", "num": "6", "name": "Learn", "color": "#da77f2",
        "icon": "📈",
        "short": "Track results, adapt over time",
        "detail": [
            ("Trade Logging", "Every trade is recorded: entry, exit, P&L, signals, and conditions."),
            ("ML Pattern Recognition", "Identifies which signal combinations predict profitable trades."),
            ("Weekly Performance Review", "Automated review: win rate, average gain/loss, best/worst setups."),
            ("Capital & Equity Tracking", "Tracks the equity curve. Knows when in drawdown vs new highs."),
        ],
    },
]


# ── Module data ───────────────────────────────────────────────────────────

MODULE_CATEGORIES = {
    "Trading Core": {
        "color": "#4dabf7", "icon": "⚡",
        "modules": {
            "bot.py": ("Trading Bot Core", "The main brain. Runs the full trading loop — scan, score, decide, execute."),
            "orders.py": ("Order Management", "Creates limit orders, manages bracket orders with stop-loss and take-profit targets."),
            "smart_execution.py": ("Smart Execution", "Places orders near the bid/ask, bracket orders with automatic stops."),
        },
    },
    "Signal Generation": {
        "color": "#51cf66", "icon": "📊",
        "modules": {
            "signals.py": ("10-Dimension Signal Engine", "Scores each stock across 10 dimensions: directional, momentum, squeeze, flow, breakout, PEAD, news, short squeeze, reversion, overnight drift."),
            "scanner.py": ("TradingView Screener", "Scans 3,000+ stocks using real-time screener data."),
            "options_scanner.py": ("Options Scanner", "Finds unusual options activity — volume spikes, IV changes."),
        },
    },
    "AI & Learning": {
        "color": "#ffd43b", "icon": "🧠",
        "modules": {
            "agents.py": ("AI Agent Council", "Multiple Claude agents debate each trade opportunity."),
            "sentinel_agents.py": ("Sentinel Agents", "Watchdog agents that monitor portfolio health and flag emerging risks."),
            "ml_engine.py": ("ML Engine", "Identifies patterns across all 9 signal dimensions."),
            "learning.py": ("Learning & Adaptation", "Logs every trade result, reviews what worked, adapts signal weights."),
        },
    },
    "Risk & Portfolio": {
        "color": "#ff6b6b", "icon": "🛡️",
        "modules": {
            "risk.py": ("5-Layer Risk Management", "Session limits, daily loss caps, streak protection, position sizing, correlation checks."),
            "portfolio_optimizer.py": ("Portfolio Optimizer", "Balances the portfolio across sectors and risk levels."),
        },
    },
    "News & Sentiment": {
        "color": "#fcc419", "icon": "📰",
        "modules": {
            "news.py": ("News Feed", "Pulls financial news from Yahoo RSS and other free sources."),
            "news_sentinel.py": ("News Sentinel", "Monitors RSS feeds and runs Claude sentiment analysis on breaking news."),
            "social_sentiment.py": ("Social Sentiment", "Tracks Reddit mention velocity and VADER sentiment scores."),
            "theme_tracker.py": ("Theme Tracker", "Groups stocks by investment themes. Detects sector rotation."),
        },
    },
    "Market Data & Infrastructure": {
        "color": "#74c0fc", "icon": "🗄️",
        "modules": {
            "ibkr_streaming.py": ("IBKR Streaming", "Real-time price streaming from Interactive Brokers."),
            "backtester.py": ("Backtester", "Tests strategies against historical data before risking capital."),
            "dashboard.py": ("Trading Dashboard", "Web dashboard for monitoring live trades and bot activity."),
            "daily_journal.py": ("Daily Journal", "Automated daily trading journal with every trade and metric."),
            "options.py": ("Options Trading", "Best options contracts — right strike, right expiry, Greeks-aware."),
            "config.py": ("Configuration", "IBKR keys, risk parameters, signal thresholds, all the knobs."),
        },
    },
}


# ── Tile: Stats bar ───────────────────────────────────────────────────────

def _stat_tile(stats):
    items = [
        ("Modules",       str(stats["modules"]),                                "#4dabf7"),
        ("Lines",         f"{stats['lines']/1000:.1f}k" if stats["lines"] >= 1000 else str(stats["lines"]), "#adb5bd"),
        ("Functions",     str(stats["functions"]),                              "#51cf66"),
        ("Classes",       str(stats["classes"]),                                "#ffd43b"),
        ("Libraries",     str(stats["libraries"]),                              "#da77f2"),
        ("Proposals",     str(stats["proposals"]),                              "#74c0fc"),
        ("Test Files",    str(stats["test_files"]),                             "#63e6be"),
    ]
    pills = []
    for label, val, color in items:
        pills.append(
            html.Div([
                html.Div(val, style={"fontSize": "1.3rem", "fontWeight": "700", "color": color}),
                html.Div(label, style={
                    "fontSize": "0.55rem", "color": "var(--cd-muted)",
                    "textTransform": "uppercase", "letterSpacing": "0.5px",
                }),
            ], className="cd-stat-pill")
        )
    return html.Div([
        html.Div([
            html.Span("⚡⚡", style={"color": "var(--cd-drag-handle)", "marginRight": "8px"}),
            html.Span("Codebase Stats", style={"fontSize": "0.7rem", "fontWeight": "800",
                                               "color": "var(--cd-muted)", "letterSpacing": "1px",
                                               "textTransform": "uppercase"}),
        ], className="cd-tile-header"),
        html.Div(pills, style={
            "display": "flex", "gap": "10px", "flexWrap": "wrap", "alignItems": "center",
        }),
    ], className="cd-tile")


# ── Tile: 6 pillars ───────────────────────────────────────────────────────

def _pillars_tile():
    pillar_btns = []
    for p in PILLARS:
        pillar_btns.append(dbc.Col(
            html.Div([
                html.Div(p["icon"], style={"fontSize": "1.6rem", "marginBottom": "6px"}),
                html.Div([
                    html.Span(p["num"], style={
                        "width": "20px", "height": "20px", "borderRadius": "50%",
                        "backgroundColor": p["color"], "color": "#0a0f18",
                        "display": "inline-flex", "alignItems": "center", "justifyContent": "center",
                        "fontWeight": "800", "fontSize": "0.65rem", "marginRight": "5px",
                    }),
                    html.Span(p["name"], style={"fontWeight": "600", "fontSize": "0.78rem",
                                               "color": "var(--cd-text)"}),
                ], className="d-flex align-items-center justify-content-center mb-1"),
                html.Div(p["short"], style={"fontSize": "0.63rem", "color": "var(--cd-muted)"}),
            ], id={"type": "pillar-btn", "index": p["id"]}, n_clicks=0, className="cd-pillar"),
            md=2, xs=4, className="mb-2",
        ))

    return html.Div([
        html.Div([
            html.Span("⚡⚡", style={"color": "var(--cd-drag-handle)", "marginRight": "8px"}),
            html.Span("How Decifer Trades", style={"fontSize": "0.7rem", "fontWeight": "800",
                                                   "color": "var(--cd-muted)", "letterSpacing": "1px",
                                                   "textTransform": "uppercase"}),
            html.Span(" — click any step to expand", style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)", "marginLeft": "8px",
            }),
        ], className="cd-tile-header"),
        dbc.Row(pillar_btns, className="justify-content-center"),
        html.Div(id="pillar-detail-panel"),
    ], className="cd-tile")


def _render_pillar_detail(pillar_id):
    pillar = next((p for p in PILLARS if p["id"] == pillar_id), None)
    if not pillar:
        return html.Div()

    detail_cards = [
        dbc.Col(
            html.Div([
                html.Div(title, style={"fontWeight": "600", "fontSize": "0.82rem",
                                       "color": pillar["color"], "marginBottom": "5px"}),
                html.Div(desc, style={"fontSize": "0.72rem", "color": "var(--cd-text2)", "lineHeight": "1.5"}),
            ], style={
                "backgroundColor": "var(--cd-deep)", "borderRadius": "8px",
                "padding": "12px 14px", "borderLeft": f"3px solid {pillar['color']}",
                "height": "100%",
            }),
            md=6 if len(pillar["detail"]) <= 5 else 4, className="mb-2",
        )
        for title, desc in pillar["detail"]
    ]

    return html.Div([
        html.Div([
            html.Span(pillar["icon"], style={"fontSize": "1.2rem", "marginRight": "10px"}),
            html.Span(f"Step {pillar['num']}: {pillar['name']}",
                      style={"fontWeight": "700", "fontSize": "0.95rem", "color": pillar["color"]}),
        ], className="mb-3"),
        dbc.Row(detail_cards),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "10px", "padding": "18px",
        "border": f"1px solid {pillar['color']}40", "marginTop": "12px",
    })


# ── Tile: Under the Hood ──────────────────────────────────────────────────

def _modules_tile():
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return html.Div([
            html.Div("Under the Hood", className="cd-tile-header"),
            html.P("DECIFER_REPO_PATH not set.", className="text-warning small"),
        ], className="cd-tile")

    py_files = {f.name: f for f in DECIFER_REPO_PATH.glob("*.py") if not f.name.startswith("_")}
    category_sections = []

    # Track which files are covered by MODULE_CATEGORIES
    known_files = set()
    for cat_info in MODULE_CATEGORIES.values():
        known_files.update(cat_info["modules"].keys())

    for cat_name, cat_info in MODULE_CATEGORIES.items():
        module_cards = []
        for fname, (mod_name, mod_desc) in cat_info["modules"].items():
            lines = 0
            if fname in py_files:
                try:
                    lines = len(py_files[fname].read_text(encoding="utf-8", errors="ignore").splitlines())
                except Exception:
                    pass
            # Only render if file actually exists in repo
            if fname not in py_files:
                continue

            module_cards.append(
                dbc.Col(
                    html.Div([
                        html.Div(mod_name, style={
                            "fontWeight": "600", "fontSize": "0.77rem",
                            "color": "var(--cd-text)", "marginBottom": "3px",
                        }),
                        html.Div(mod_desc, style={
                            "fontSize": "0.67rem", "color": "var(--cd-muted)", "lineHeight": "1.4",
                        }),
                        html.Div([
                            html.Small(fname, style={"color": cat_info["color"], "fontSize": "0.6rem", "fontWeight": "600"}),
                            html.Small(f" · {lines:,} lines", style={"color": "var(--cd-faint)", "fontSize": "0.6rem"}) if lines else None,
                        ], className="mt-2"),
                    ], style={
                        "backgroundColor": "var(--cd-deep)", "borderRadius": "7px",
                        "padding": "10px 12px", "borderLeft": f"3px solid {cat_info['color']}",
                        "height": "100%",
                    }),
                    md=4, className="mb-2",
                )
            )

        if not module_cards:
            continue

        category_sections.append(html.Div([
            html.Div([
                html.Span(cat_info["icon"], style={"marginRight": "7px"}),
                html.Span(cat_name, style={"fontWeight": "600", "fontSize": "0.82rem", "color": cat_info["color"]}),
                html.Small(f" — {len(module_cards)} module{'s' if len(module_cards) != 1 else ''}", style={"color": "var(--cd-muted)", "marginLeft": "6px"}),
            ], className="mb-2"),
            dbc.Row(module_cards),
        ], className="mb-3"))

    # Dynamically surface any new .py files not yet in MODULE_CATEGORIES
    new_files = sorted(f for f in py_files if f not in known_files)
    if new_files:
        new_cards = []
        for fname in new_files:
            try:
                lines = len(py_files[fname].read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                lines = 0
            new_cards.append(dbc.Col(
                html.Div([
                    html.Div(fname, style={"fontWeight": "600", "fontSize": "0.77rem", "color": "var(--cd-text)", "marginBottom": "3px"}),
                    html.Div("New module — not yet categorised", style={"fontSize": "0.67rem", "color": "var(--cd-faint)", "lineHeight": "1.4"}),
                    html.Div([
                        html.Small(fname, style={"color": "#868e96", "fontSize": "0.6rem", "fontWeight": "600"}),
                        html.Small(f" · {lines:,} lines", style={"color": "var(--cd-faint)", "fontSize": "0.6rem"}) if lines else None,
                    ], className="mt-2"),
                ], style={
                    "backgroundColor": "var(--cd-deep)", "borderRadius": "7px",
                    "padding": "10px 12px", "borderLeft": "3px solid #868e96", "height": "100%",
                }),
                md=4, className="mb-2",
            ))
        category_sections.append(html.Div([
            html.Div([
                html.Span("🆕", style={"marginRight": "7px"}),
                html.Span("New / Uncategorised", style={"fontWeight": "600", "fontSize": "0.82rem", "color": "#868e96"}),
                html.Small(f" — {len(new_files)} module{'s' if len(new_files) != 1 else ''}", style={"color": "var(--cd-muted)", "marginLeft": "6px"}),
            ], className="mb-2"),
            dbc.Row(new_cards),
        ], className="mb-3"))

    return html.Div([
        html.Div([
            html.Span("⚡⚡", style={"color": "var(--cd-drag-handle)", "marginRight": "8px"}),
            html.Span("Under the Hood", style={"fontSize": "0.7rem", "fontWeight": "800",
                                               "color": "var(--cd-muted)", "letterSpacing": "1px",
                                               "textTransform": "uppercase"}),
        ], className="cd-tile-header"),
        *category_sections,
    ], className="cd-tile")


# ── Tile: Trade Simulator ─────────────────────────────────────────────────

SIMULATOR_GATES = [
    {
        "name": "Market Scanner",
        "question": "The scanner found 12 stocks with unusual volume and momentum today. Should we analyse them?",
        "color": "#51cf66",
        "yes_text": "Yes — run signal analysis on all 12",
        "no_text": "No — skip today",
        "no_result": "Scanner passed, but you chose to skip. The bot waits for the next scan cycle. No trades today.",
    },
    {
        "name": "Signal Scoring",
        "question": "NVDA scores 76/100 across 10 dimensions. Trend confirmed, momentum surging, news positive. Strong enough?",
        "color": "#4dabf7",
        "yes_text": "Yes — send to AI agents",
        "no_text": "No — score isn't high enough",
        "no_result": "Signal was decent but below your threshold. The bot moves on to the next candidate. Discipline preserved.",
    },
    {
        "name": "AI Agent Consensus",
        "question": "The Researcher found supporting evidence, the Architect designed the trade, but the Critic flagged earnings risk in 3 days. Consensus?",
        "color": "#ffd43b",
        "yes_text": "Yes — agents agree, proceed to risk check",
        "no_text": "No — earnings risk too high, abort",
        "no_result": "Smart call. The Critic's concern about upcoming earnings was valid — the stock dropped 8% after the report.",
    },
    {
        "name": "Risk Management",
        "question": "Position sized at 2.5% of capital. Stop-loss at -3%, take-profit at +6%. No correlation conflict. All 5 layers clear?",
        "color": "#ff6b6b",
        "yes_text": "Yes — all checks pass, execute",
        "no_text": "No — something feels off",
        "no_result": "You overrode the risk system. The bot logs the skip and moves on.",
    },
]


def _render_simulator_step(step, decisions):
    elements = []
    for i, decision in enumerate(decisions):
        gate = SIMULATOR_GATES[i]
        is_yes = decision == "yes"
        elements.append(html.Div([
            html.Div([
                html.Span(f"Gate {i+1}: {gate['name']}",
                          style={"fontWeight": "600", "color": gate["color"], "fontSize": "0.82rem"}),
                dbc.Badge("Passed" if is_yes else "Stopped",
                          color="success" if is_yes else "secondary", className="ms-2",
                          style={"fontSize": "0.6rem"}),
            ]),
        ], style={
            "backgroundColor": "var(--cd-deep)", "borderRadius": "6px", "padding": "9px 13px",
            "borderLeft": f"3px solid {gate['color'] if is_yes else 'var(--cd-border)'}",
            "opacity": "0.7" if not is_yes else "1", "marginBottom": "4px",
        }))
        if is_yes and (i < len(decisions) - 1 or step < len(SIMULATOR_GATES)):
            elements.append(html.Div(
                html.Div(style={"width": "2px", "height": "14px",
                                "backgroundColor": gate["color"], "margin": "0 0 0 20px"}),
            ))

    if decisions and decisions[-1] == "no":
        gate = SIMULATOR_GATES[len(decisions) - 1]
        elements.append(html.Div([
            html.Div("✖ Trade Not Taken", style={"fontWeight": "700", "color": "#ff6b6b",
                                                   "fontSize": "0.95rem", "marginBottom": "5px"}),
            html.P(gate["no_result"], style={"color": "var(--cd-text2)", "fontSize": "0.78rem",
                                             "lineHeight": "1.5", "marginBottom": "0"}),
        ], style={
            "backgroundColor": "var(--cd-err-bg)", "borderRadius": "10px", "padding": "14px 18px",
            "border": "1px solid #ff6b6b40", "marginTop": "8px",
        }))
        return html.Div(elements)

    if step >= len(SIMULATOR_GATES):
        elements.append(html.Div(
            html.Div(style={"width": "2px", "height": "14px",
                            "backgroundColor": "#51cf66", "margin": "0 0 0 20px"}),
        ))
        elements.append(html.Div([
            html.Div("✔ Trade Executed!", style={"fontWeight": "700", "color": "#51cf66",
                                                  "fontSize": "1.05rem", "marginBottom": "5px"}),
            html.P(
                "Limit order placed via IBKR with automatic stop-loss and take-profit brackets. "
                "Position tracked in real-time. Results logged for the learning system.",
                style={"color": "var(--cd-text2)", "fontSize": "0.78rem",
                       "lineHeight": "1.5", "marginBottom": "0"},
            ),
        ], style={
            "backgroundColor": "var(--cd-ok-bg)", "borderRadius": "10px", "padding": "14px 18px",
            "border": "1px solid #51cf66", "marginTop": "8px",
        }))
        return html.Div(elements)

    gate = SIMULATOR_GATES[step]
    if decisions:
        elements.append(html.Div(
            html.Div(style={"width": "2px", "height": "14px",
                            "backgroundColor": gate["color"], "margin": "0 0 0 20px"}),
        ))
    elements.append(html.Div([
        html.Div(f"Gate {step+1}: {gate['name']}",
                 style={"fontWeight": "700", "color": gate["color"], "fontSize": "0.92rem", "marginBottom": "6px"}),
        html.P(gate["question"], style={"color": "var(--cd-text)", "fontSize": "0.82rem",
                                        "lineHeight": "1.5", "marginBottom": "12px"}),
        html.Div([
            dbc.Button(gate["yes_text"], id={"type": "sim-yes", "index": step},
                       n_clicks=0, color="success", size="sm", className="me-2",
                       style={"fontSize": "0.73rem"}),
            dbc.Button(gate["no_text"], id={"type": "sim-no", "index": step},
                       n_clicks=0, color="outline-danger", size="sm",
                       style={"fontSize": "0.73rem"}),
        ]),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "10px", "padding": "14px 18px",
        "border": f"1px solid {gate['color']}60",
    }))
    return html.Div(elements)


def _simulator_tile():
    return html.Div([
        html.Div([
            html.Span("⚡⚡", style={"color": "var(--cd-drag-handle)", "marginRight": "8px"}),
            html.Span("Trade Decision Simulator", style={"fontSize": "0.7rem", "fontWeight": "800",
                                                         "color": "var(--cd-muted)", "letterSpacing": "1px",
                                                         "textTransform": "uppercase"}),
            html.Span("Illustrative — shows decision logic, not live trade data", style={
                "fontSize": "0.58rem", "color": "var(--cd-faint)",
                "marginLeft": "10px", "fontStyle": "italic",
            }),
        ], className="cd-tile-header"),
        html.P("Walk through every gate a trade must pass. Click to decide.",
               style={"fontSize": "0.75rem", "color": "var(--cd-muted)", "marginBottom": "14px"}),
        html.Div(id="simulator-flow", children=_render_simulator_step(0, [])),
        dbc.Button("↺ Reset", id="sim-reset-btn", n_clicks=0, color="secondary",
                   size="sm", className="mt-3", style={"fontSize": "0.72rem"}),
        dcc.Store(id="sim-state", data={"step": 0, "decisions": []}),
    ], className="cd-tile")


# ── Grid layout ───────────────────────────────────────────────────────────

def layout():
    stats = _scan_repo()

    if not stats:
        # Fallback if repo not found
        return html.Div([
            html.P(
                "Codebase stats unavailable. Set DECIFER_REPO_PATH in your .env file to connect the repo.",
                className="text-warning small",
            ),
            _pillars_tile(),
            html.Div([_simulator_tile()], style={"marginTop": "16px"}),
            dcc.Interval(id="overview-interval", interval=60_000, n_intervals=0),
        ])

    # ── Static grid (reliable, no draggable dependency) ──────────────────
    grid = html.Div([
        html.Div(_stat_tile(stats), style={"marginBottom": "16px"}),
        html.Div(_pillars_tile(), style={"marginBottom": "16px"}),
        dbc.Row([
            dbc.Col(_modules_tile(), md=8, className="mb-3"),
            dbc.Col(_simulator_tile(), md=4, className="mb-3"),
        ], className="g-3"),
    ])

    return html.Div([
        grid,
        dcc.Interval(id="overview-interval", interval=60_000, n_intervals=0),
    ])


# ── Callbacks ─────────────────────────────────────────────────────────────

def register_callbacks(app):
    @app.callback(
        Output("overview-content", "children"),
        Input("overview-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()

    # Pillar click handler
    @app.callback(
        Output("pillar-detail-panel", "children"),
        Input({"type": "pillar-btn", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def handle_pillar_click(n_clicks_list):
        ctx = callback_context
        if not ctx.triggered:
            return html.Div()
        triggered_id = ctx.triggered[0]["prop_id"]
        try:
            parsed = json.loads(triggered_id.split(".")[0])
            pillar_id = parsed["index"]
        except Exception:
            return html.Div()
        clicks = [n or 0 for n in n_clicks_list]
        if sum(clicks) == 0:
            return html.Div()
        return _render_pillar_detail(pillar_id)

    # Simulator yes/no/reset
    @app.callback(
        Output("sim-state", "data"),
        Output("simulator-flow", "children"),
        Input({"type": "sim-yes", "index": ALL}, "n_clicks"),
        Input({"type": "sim-no", "index": ALL}, "n_clicks"),
        Input("sim-reset-btn", "n_clicks"),
        State("sim-state", "data"),
        prevent_initial_call=True,
    )
    def handle_simulator(yes_clicks, no_clicks, reset_clicks, state):
        ctx = callback_context
        if not ctx.triggered:
            return state, _render_simulator_step(0, [])
        triggered_id = ctx.triggered[0]["prop_id"]
        if "sim-reset-btn" in triggered_id:
            new_state = {"step": 0, "decisions": []}
            return new_state, _render_simulator_step(0, [])
        try:
            parsed = json.loads(triggered_id.split(".")[0])
            btn_type = parsed["type"]
        except Exception:
            return state, _render_simulator_step(state["step"], state["decisions"])
        decisions = list(state.get("decisions", []))
        step = state.get("step", 0)
        if btn_type == "sim-yes":
            decisions.append("yes")
            step += 1
        elif btn_type == "sim-no":
            decisions.append("no")
            step += 1
        new_state = {"step": step, "decisions": decisions}
        return new_state, _render_simulator_step(step, decisions)
