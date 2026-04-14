"""
Catalyst Alerts Panel
======================
Read-only dashboard panel displaying:

  - Top M&A target candidates ranked by composite catalyst_score
  - Active options anomaly flags (OTM call spikes, IV compression, P/C skew)
  - Recent SEC EDGAR events (13D/13G/Form 4) with watchlist cross-reference
  - APLS retrospective note — what signals were detectable before the announcement

Data sources (all read-only from state/catalyst/):
  candidates_YYYY-MM-DD.json   — written by signals/catalyst_screen.py
  edgar_events.json            — written by signals/edgar_monitor.py
  options_snapshots.jsonl      — written by signals/options_anomaly.py
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import CATALYST_DIR

EDGAR_FILE = CATALYST_DIR / "edgar_events.json"

_CATALYST_TTL = 30  # seconds
_candidates_cache: tuple[list, str] = ([], "")
_candidates_ts: float = 0.0
_edgar_cache: list = []
_edgar_ts: float = 0.0


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_candidates() -> tuple[list[dict], str]:
    """Load most recent candidates file. Returns (candidates, date_str)."""
    global _candidates_cache, _candidates_ts
    if _candidates_ts and time.time() - _candidates_ts < _CATALYST_TTL:
        return _candidates_cache
    if not CATALYST_DIR.exists():
        return [], ""
    files = sorted(CATALYST_DIR.glob("candidates_*.json"), reverse=True)
    if not files:
        return [], ""
    try:
        payload = json.loads(files[0].read_text())
        date_str = payload.get("date", files[0].stem.replace("candidates_", ""))
        result = payload.get("candidates", []), date_str
    except Exception:
        result = [], ""
    _candidates_cache = result
    _candidates_ts = time.time()
    return result


def _load_edgar_events() -> list[dict]:
    global _edgar_cache, _edgar_ts
    if _edgar_ts and time.time() - _edgar_ts < _CATALYST_TTL:
        return _edgar_cache
    if not EDGAR_FILE.exists():
        return []
    try:
        result = json.loads(EDGAR_FILE.read_text())
    except Exception:
        result = []
    _edgar_cache = result
    _edgar_ts = time.time()
    return result


# ── Score bar helper ──────────────────────────────────────────────────────────

def _score_bar(score: float, max_score: float = 10.0, color: str = "#4dabf7") -> html.Div:
    pct = min(100, round(score / max_score * 100))
    return html.Div([
        html.Div(style={
            "width":           f"{pct}%",
            "height":          "4px",
            "backgroundColor": color,
            "borderRadius":    "2px",
            "transition":      "width 0.3s ease",
        }),
    ], style={
        "width":           "100%",
        "height":          "4px",
        "backgroundColor": "var(--cd-border)",
        "borderRadius":    "2px",
        "marginTop":       "4px",
    })


def _score_color(score: float, max_score: float = 10.0) -> str:
    pct = score / max_score
    if pct >= 0.7:
        return "#ff6b6b"   # hot — red/amber
    elif pct >= 0.4:
        return "#ffd43b"   # warm — yellow
    return "#4dabf7"       # cool — blue


# ── Candidate row ─────────────────────────────────────────────────────────────

def _render_candidate_row(c: dict, rank: int) -> html.Div:
    ticker        = c.get("ticker", "?")
    name          = c.get("name", ticker)
    sector        = c.get("sector", "")
    f_score       = c.get("fundamental_score", 0)
    o_score       = c.get("options_anomaly_score", 0)
    e_score       = c.get("edgar_score", 0)
    s_score       = c.get("sentiment_score", 0.0)
    cat_score     = c.get("catalyst_score", 0)
    flags         = c.get("flags", [])
    opt_flags     = c.get("options_anomaly_flags", [])
    edgar_events  = c.get("edgar_events", [])
    sent_flags    = c.get("sentiment_flags", [])
    screened_at   = c.get("screened_at", "")

    score_color = _score_color(cat_score)
    has_anomaly = o_score > 0 or e_score > 0

    # Anomaly badges
    anomaly_badges = []
    for flag in opt_flags:
        if flag and "No " not in flag:
            anomaly_badges.append(dbc.Badge(
                flag[:45] + ("…" if len(flag) > 45 else ""),
                style={
                    "fontSize": "0.58rem",
                    "backgroundColor": "#ffd43b20",
                    "color": "#ffd43b",
                    "marginRight": "4px",
                    "marginBottom": "3px",
                },
            ))
    for ev in edgar_events:
        anomaly_badges.append(dbc.Badge(
            ev[:45] + ("…" if len(ev) > 45 else ""),
            style={
                "fontSize": "0.58rem",
                "backgroundColor": "#ff6b6b20",
                "color": "#ff6b6b",
                "marginRight": "4px",
                "marginBottom": "3px",
            },
        ))
    # Sentiment badge — show only if scored (flags contain Claude/FinBERT results)
    sent_label = next((f for f in sent_flags if f in ("Bullish", "Bearish", "Neutral")), None)
    if sent_label:
        sent_color = {"Bullish": "#51cf66", "Bearish": "#ff6b6b", "Neutral": "#868e96"}.get(sent_label, "#868e96")
        anomaly_badges.append(dbc.Badge(
            f"Senti: {sent_label}",
            style={
                "fontSize": "0.58rem",
                "backgroundColor": sent_color + "20",
                "color": sent_color,
                "marginRight": "4px",
                "marginBottom": "3px",
            },
        ))

    return html.Div([
        # Left: rank + ticker + name
        html.Div([
            html.Span(f"#{rank}", style={
                "fontSize": "0.65rem", "color": "var(--cd-muted)",
                "width": "24px", "display": "inline-block",
                "fontWeight": "600",
            }),
            html.Div([
                html.Div([
                    html.Span(ticker, style={
                        "fontWeight": "700", "fontSize": "0.9rem",
                        "color": score_color, "marginRight": "8px",
                    }),
                    html.Span(name[:35] + ("…" if len(name) > 35 else ""), style={
                        "fontSize": "0.78rem", "color": "var(--cd-text2)",
                    }),
                    dbc.Badge("LIVE SIGNAL", style={
                        "fontSize": "0.55rem",
                        "backgroundColor": "#ff6b6b",
                        "color": "white",
                        "marginLeft": "8px",
                        "animation": "pulse 1.5s infinite",
                    }) if has_anomaly else None,
                ], className="d-flex align-items-center flex-wrap"),
                html.Div([
                    dbc.Badge(sector, style={
                        "fontSize": "0.58rem", "backgroundColor": "#74c0fc20",
                        "color": "#74c0fc", "marginRight": "4px",
                    }) if sector else None,
                    *[dbc.Badge(f, style={
                        "fontSize": "0.58rem", "backgroundColor": "var(--cd-deep)",
                        "color": "var(--cd-muted)", "marginRight": "4px",
                        "border": "1px solid var(--cd-border)",
                    }) for f in flags[:3]],
                ], className="d-flex flex-wrap mt-1"),
                html.Div(anomaly_badges, className="d-flex flex-wrap mt-1") if anomaly_badges else None,
            ]),
        ], className="d-flex align-items-start", style={"flex": "1"}),

        # Right: score breakdown
        html.Div([
            html.Div([
                html.Span(f"{cat_score}", style={
                    "fontSize": "1.1rem", "fontWeight": "800", "color": score_color,
                }),
                html.Span("/10", style={
                    "fontSize": "0.65rem", "color": "var(--cd-muted)", "marginLeft": "1px",
                }),
            ]),
            _score_bar(cat_score, color=score_color),
            html.Div([
                html.Span(f"F:{f_score}/5", style={
                    "fontSize": "0.6rem", "color": "var(--cd-muted)", "marginRight": "5px",
                }),
                html.Span(f"O:{o_score}/10", style={
                    "fontSize": "0.6rem",
                    "color": "#ffd43b" if o_score > 0 else "var(--cd-muted)",
                    "marginRight": "5px",
                }),
                html.Span(f"E:{e_score}/10", style={
                    "fontSize": "0.6rem",
                    "color": "#ff6b6b" if e_score > 0 else "var(--cd-muted)",
                    "marginRight": "5px",
                }),
                html.Span(f"S:{s_score}/10", style={
                    "fontSize": "0.6rem",
                    "color": "#51cf66" if s_score > 5.5 else ("#ff6b6b" if s_score < 4.5 and s_score > 0 else "var(--cd-muted)"),
                }),
            ], className="d-flex mt-1"),
        ], style={"textAlign": "right", "minWidth": "75px"}),

    ], className="d-flex align-items-start justify-content-between", style={
        "padding":         "12px 14px",
        "borderRadius":    "8px",
        "backgroundColor": "var(--cd-card)",
        "border":          f"1px solid {score_color}40" if has_anomaly else "1px solid var(--cd-border)",
        "borderLeft":      f"3px solid {score_color}",
        "marginBottom":    "8px",
    })


# ── EDGAR events table ────────────────────────────────────────────────────────

def _render_edgar_table(events: list[dict]) -> html.Div:
    if not events:
        return html.P(
            "No EDGAR events loaded. Run edgar_monitor.py to poll SEC feeds.",
            style={"fontSize": "0.8rem", "color": "var(--cd-muted)"},
        )

    # Show only the 20 most recent, watchlist hits first
    sorted_events = sorted(
        events[:40],
        key=lambda e: (not e.get("on_watchlist", False), e.get("updated", "")),
        reverse=False,
    )[:20]

    rows = []
    for ev in sorted_events:
        ticker      = ev.get("ticker") or "—"
        form_type   = ev.get("form_type", "")
        company     = ev.get("company_name", "")[:40]
        updated     = ev.get("updated", "")[:10]
        on_watchlist = ev.get("on_watchlist", False)

        form_color = {
            "SC 13D": "#ff6b6b",
            "SC 13G": "#ffd43b",
            "4":      "#74c0fc",
        }.get(form_type, "#868e96")

        rows.append(html.Tr([
            html.Td(
                dbc.Badge("★ WATCHLIST", style={
                    "fontSize": "0.55rem", "backgroundColor": "#ff6b6b",
                    "color": "white",
                }) if on_watchlist else html.Span("", style={"width": "70px", "display": "inline-block"}),
                style={"verticalAlign": "middle", "paddingRight": "8px"},
            ),
            html.Td(html.Span(ticker, style={
                "fontWeight": "700", "fontSize": "0.8rem",
                "color": "#ff6b6b" if on_watchlist else "var(--cd-text)",
            }), style={"verticalAlign": "middle"}),
            html.Td(dbc.Badge(form_type, style={
                "fontSize": "0.6rem",
                "backgroundColor": form_color + "20",
                "color": form_color,
            }), style={"verticalAlign": "middle", "padding": "6px 8px"}),
            html.Td(company, style={
                "fontSize": "0.75rem", "color": "var(--cd-text2)",
                "verticalAlign": "middle",
            }),
            html.Td(updated, style={
                "fontSize": "0.7rem", "color": "var(--cd-muted)",
                "verticalAlign": "middle",
            }),
        ], style={
            "backgroundColor": "#ff6b6b08" if on_watchlist else "transparent",
            "borderBottom": "1px solid var(--cd-border)",
        }))

    return html.Table(
        [html.Thead(html.Tr([
            html.Th("", style={"width": "85px"}),
            html.Th("Ticker", style={"fontSize": "0.68rem", "color": "var(--cd-muted)", "fontWeight": "600"}),
            html.Th("Form",   style={"fontSize": "0.68rem", "color": "var(--cd-muted)", "fontWeight": "600"}),
            html.Th("Company",style={"fontSize": "0.68rem", "color": "var(--cd-muted)", "fontWeight": "600"}),
            html.Th("Date",   style={"fontSize": "0.68rem", "color": "var(--cd-muted)", "fontWeight": "600"}),
        ])),
        html.Tbody(rows)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )


# ── APLS retrospective card ───────────────────────────────────────────────────

def _apls_retrospective() -> html.Div:
    return html.Div([
        html.Div([
            html.Span("Case Study: APLS — ", style={
                "fontWeight": "700", "fontSize": "0.85rem", "color": "#ffd43b",
            }),
            html.Span("Biogen acquisition at 140% premium (2026-03-31)", style={
                "fontSize": "0.8rem", "color": "var(--cd-text2)",
            }),
        ], className="mb-2"),
        html.Div([
            html.Div([
                _signal_row("✅", "Low EV/Revenue", "Screener fundamental — detectable with yfinance"),
                _signal_row("✅", "Net cash + 15%+ revenue growth (Syfovre trajectory)", "yfinance fundamentals"),
                _signal_row("⚡", "Unusual OTM call volume in prior days", "~25% of M&A events show pre-announcement options anomalies (academic research)"),
                _signal_row("📋", "13D/13G filing velocity", "SEC EDGAR RSS — now monitored by edgar_monitor.py"),
                _signal_row("❓", "Strategic fit (drug portfolio + acquirer pipeline gap)", "Requires biotech domain knowledge — hard to automate"),
            ]),
        ]),
        html.Div(
            "3 of 5 detectable signals are now covered by this Catalyst Layer.",
            style={"fontSize": "0.72rem", "color": "#51cf66", "marginTop": "8px", "fontWeight": "600"},
        ),
    ], style={
        "padding": "14px 16px", "borderRadius": "8px",
        "backgroundColor": "var(--cd-deep)",
        "border": "1px solid #ffd43b30",
        "borderLeft": "3px solid #ffd43b",
        "marginBottom": "18px",
    })


def _signal_row(icon: str, label: str, detail: str) -> html.Div:
    return html.Div([
        html.Span(icon, style={"marginRight": "8px", "fontSize": "0.85rem"}),
        html.Span(label, style={
            "fontWeight": "600", "fontSize": "0.8rem", "color": "var(--cd-text)",
            "marginRight": "6px",
        }),
        html.Span(detail, style={
            "fontSize": "0.73rem", "color": "var(--cd-muted)",
        }),
    ], className="d-flex align-items-start mb-1")


# ── Section header ────────────────────────────────────────────────────────────

def _section_header(title: str, subtitle: str = "", badge: str = "") -> html.Div:
    return html.Div([
        html.Div([
            html.Span(title, style={
                "fontWeight": "700", "fontSize": "0.88rem", "color": "var(--cd-text)",
            }),
            dbc.Badge(badge, style={
                "fontSize": "0.58rem", "backgroundColor": "#4dabf720",
                "color": "#4dabf7", "marginLeft": "8px",
            }) if badge else None,
        ], className="d-flex align-items-center"),
        html.Div(subtitle, style={
            "fontSize": "0.72rem", "color": "var(--cd-muted)", "marginTop": "2px",
        }) if subtitle else None,
    ], style={
        "marginBottom": "12px",
        "paddingBottom": "8px",
        "borderBottom": "1px solid var(--cd-border)",
    })


# ── Main layout ───────────────────────────────────────────────────────────────

def layout() -> html.Div:
    candidates, date_str = _load_candidates()
    edgar_events         = _load_edgar_events()

    top_candidates = sorted(candidates, key=lambda c: c.get("catalyst_score", 0), reverse=True)[:15]
    live_signals   = [c for c in top_candidates if c.get("options_anomaly_score", 0) > 0 or c.get("edgar_score", 0) > 0]
    watchlist_hits = [e for e in edgar_events if e.get("on_watchlist")]

    # Status badges
    status_badges = html.Div([
        dbc.Badge(
            f"{len(candidates)} candidates screened",
            style={"fontSize": "0.62rem", "backgroundColor": "#4dabf720", "color": "#4dabf7", "marginRight": "6px"},
        ),
        dbc.Badge(
            f"{len(live_signals)} live anomalies",
            style={
                "fontSize": "0.62rem",
                "backgroundColor": "#ff6b6b20" if live_signals else "#86869620",
                "color": "#ff6b6b" if live_signals else "#868e96",
                "marginRight": "6px",
            },
        ),
        dbc.Badge(
            f"{len(watchlist_hits)} EDGAR watchlist hits",
            style={
                "fontSize": "0.62rem",
                "backgroundColor": "#ffd43b20" if watchlist_hits else "#86869620",
                "color": "#ffd43b" if watchlist_hits else "#868e96",
                "marginRight": "6px",
            },
        ),
        html.Span(
            f"Screened: {date_str}" if date_str else "Not yet screened — run catalyst_screen.py",
            style={"fontSize": "0.62rem", "color": "var(--cd-muted)"},
        ),
    ], className="d-flex flex-wrap align-items-center mb-3")

    # Empty state
    if not candidates and not edgar_events:
        return html.Div([
            html.H4("Catalyst Alerts", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(
                "No catalyst data yet. Run the following to populate:",
                style={"fontSize": "0.82rem", "color": "var(--cd-muted)"},
            ),
            html.Pre(
                "python -m signals.catalyst_screen -v   # fundamental M&A screen\n"
                "python -m signals.edgar_monitor        # SEC 13D/13G/Form 4 monitor",
                style={
                    "fontSize": "0.78rem", "backgroundColor": "var(--cd-deep)",
                    "padding": "12px 16px", "borderRadius": "6px",
                    "border": "1px solid var(--cd-border)", "color": "#74c0fc",
                },
            ),
            _apls_retrospective(),
            dcc.Interval(id="catalyst-interval", interval=60_000, n_intervals=0),
        ])

    return html.Div([
        html.H4("Catalyst Alerts", className="text-light mb-1", style={"fontWeight": "600"}),
        html.P(
            "M&A target candidates ranked by composite catalyst score "
            "(fundamental + options flow + EDGAR + sentiment). "
            "F = fundamental screen (0–5), O = options anomaly (0–10), "
            "E = EDGAR filings (0–10), S = sentiment (0–10).",
            style={"fontSize": "0.78rem", "color": "var(--cd-muted)", "marginBottom": "16px"},
        ),

        status_badges,

        # ── APLS retrospective ───────────────────────────────────────────────
        _apls_retrospective(),

        dbc.Row([

            # ── Left: candidates list ──────────────────────────────────────
            dbc.Col([
                _section_header(
                    "Top M&A Candidates",
                    f"Ranked by composite catalyst score · screened {date_str}",
                    badge=f"{len(top_candidates)} shown",
                ),
                html.Div(
                    [_render_candidate_row(c, i + 1) for i, c in enumerate(top_candidates)]
                    if top_candidates else
                    html.P(
                        "No candidates match current thresholds. Adjust CATALYST_THRESHOLDS in config.py.",
                        style={"fontSize": "0.8rem", "color": "var(--cd-muted)"},
                    ),
                ),
            ], md=7, style={"paddingRight": "12px"}),

            # ── Right: EDGAR events ────────────────────────────────────────
            dbc.Col([
                _section_header(
                    "SEC EDGAR Events",
                    "13D / 13G / Form 4 — last 7 days",
                    badge=f"{len(edgar_events)} events",
                ),
                _render_edgar_table(edgar_events),

                html.Div(style={"height": "24px"}),

                # Score legend
                html.Div([
                    html.Div("Score key", style={
                        "fontSize": "0.68rem", "color": "var(--cd-muted)",
                        "fontWeight": "700", "textTransform": "uppercase",
                        "letterSpacing": "0.05em", "marginBottom": "8px",
                    }),
                    *[html.Div([
                        html.Div(style={
                            "width": "10px", "height": "10px", "borderRadius": "50%",
                            "backgroundColor": color, "marginRight": "7px", "flexShrink": "0",
                        }),
                        html.Span(label, style={"fontSize": "0.72rem", "color": "var(--cd-text2)"}),
                    ], className="d-flex align-items-center mb-1")
                    for color, label in [
                        ("#4dabf7", "F: fundamental screen (EV/Rev, cash, growth, sector, cap) · 35%"),
                        ("#ffd43b", "O: options anomaly (OTM spike, IV compression, P/C skew) · 35%"),
                        ("#ff6b6b", "E: EDGAR filing (13D = +7, 13G = +4, Form 4 cluster = +3) · 15%"),
                        ("#51cf66", "S: sentiment (Yahoo RSS + Finviz → Claude + FinBERT composite) · 15%"),
                    ]],
                ], style={
                    "padding": "12px 14px", "borderRadius": "8px",
                    "backgroundColor": "var(--cd-deep)",
                    "border": "1px solid var(--cd-border)",
                }),
            ], md=5),
        ]),

        dcc.Interval(id="catalyst-interval", interval=60_000, n_intervals=0),
    ])


# ── Callbacks ─────────────────────────────────────────────────────────────────

def register_callbacks(app) -> None:
    @app.callback(
        Output("catalyst-content", "children"),
        Input("catalyst-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()
