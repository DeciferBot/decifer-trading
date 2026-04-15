"""
Chief's Brain panel — AI-generated product analysis.

Reads state/analysis/latest.json (written by analyse.py) and renders
a full product roadmap, immediate next action, untracked ideas, and risks.
Chief never calls the LLM directly — analyse.py does that on a schedule.
"""

import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from dash import html, dcc, Input, Output, State, callback_context, ALL
import dash_bootstrap_components as dbc
from config import STATE_DIR

ANALYSE_SCRIPT = Path(__file__).parent.parent / "analyse.py"

ANALYSIS_FILE = STATE_DIR / "analysis" / "latest.json"
VISION_FILE   = STATE_DIR / "vision.json"

SEVERITY_COLORS = {"high": "#ff6b6b", "medium": "#ffd43b", "low": "#4dabf7"}
SEVERITY_BG     = {"high": "#1a0a0a", "medium": "#1a1800", "low": "#0d1a2a"}
SCOPE_COLORS    = {"small": "#51cf66", "medium": "#ffd43b", "large": "#ff6b6b"}

# Phase ordering for DONE/CURRENT/UPCOMING logic
PHASE_ORDER = ["A", "B", "C", "D", "E", "F", "MA1", "MA2", "MA3"]

DONE_COLOR    = "#51cf66"
CURRENT_COLOR = "#4dabf7"
UPCOMING_COLOR = "#868e96"

# Shared state for non-blocking rerun
_rerun_state: dict = {"running": False, "ok": None, "msg": ""}

# TTL caches for expensive disk I/O
_BRAIN_TTL = 60  # seconds
_phase_status_cache: dict = {}
_phase_status_ts: float = 0.0
_fallback_hero_cache: dict = {"result": None, "valid": False}
_fallback_hero_ts: float = 0.0

# ── Chat constants & shared state ─────────────────────────────────────────────
CHAT_MODEL         = "claude-haiku-4-5-20251001"
CHAT_MAX_TOKENS    = 1024
CHAT_HISTORY_LIMIT = 10   # max conversation turns (user+assistant pairs) before trimming

_chat_state: dict = {"running": False, "reply": None, "error": None}


def _run_rerun_bg():
    """Run analyse.py in a background thread; update _rerun_state when done."""
    try:
        # Build env: inherit current env + override from .env file so the
        # API key is available even if the shell env has it set to ""
        from dotenv import dotenv_values
        import os
        env = os.environ.copy()
        dotenv_path = ANALYSE_SCRIPT.parent / ".env"
        for k, v in dotenv_values(dotenv_path).items():
            if v:  # only override if dotenv value is non-empty
                env[k] = v

        result = subprocess.run(
            [sys.executable, str(ANALYSE_SCRIPT)],
            cwd=str(ANALYSE_SCRIPT.parent),
            capture_output=True, text=True, timeout=180,
            env=env,
        )
        if result.returncode == 0:
            _rerun_state["ok"]  = True
            _rerun_state["msg"] = "✓ Analysis refreshed"
        else:
            err = (result.stderr or result.stdout or "unknown error")[-120:]
            _rerun_state["ok"]  = False
            _rerun_state["msg"] = f"✗ {err}"
    except subprocess.TimeoutExpired:
        _rerun_state["ok"]  = False
        _rerun_state["msg"] = "✗ Timed out (>3 min)"
    except Exception as e:
        _rerun_state["ok"]  = False
        _rerun_state["msg"] = f"✗ {e}"
    finally:
        _rerun_state["running"] = False


def _run_chat_bg(system: str, messages: list) -> None:
    """Run a Haiku chat completion in a background thread; update _chat_state when done."""
    import anthropic
    from dotenv import dotenv_values
    import os
    _chat_state["running"] = True
    _chat_state["reply"]   = None
    _chat_state["error"]   = None
    try:
        env = os.environ.copy()
        dotenv_path = ANALYSE_SCRIPT.parent / ".env"
        for k, v in dotenv_values(dotenv_path).items():
            if v:
                env[k] = v
        api_key = env.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=CHAT_MAX_TOKENS,
            system=system,
            messages=messages,
        )
        _chat_state["reply"] = resp.content[0].text
    except Exception as exc:
        _chat_state["error"] = str(exc)
    finally:
        _chat_state["running"] = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_analysis():
    if not ANALYSIS_FILE.exists():
        return None
    try:
        return json.loads(ANALYSIS_FILE.read_text())
    except Exception:
        return None


def _age_label(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        delta = now - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes}m ago"
        elif minutes < 1440:
            return f"{minutes // 60}h ago"
        else:
            return f"{delta.days}d ago"
    except Exception:
        return "unknown"


def _extract_phase_letter(phase_name: str) -> str:
    """
    Pull the phase identifier out of an AI-generated phase name.
    'Phase A — Signal Engine Hardening'  → 'A'
    'Phase MA1 — ...'                    → 'MA1'
    'Phase B: Paper Profitability'       → 'B'
    """
    m = re.search(r"Phase\s+(MA\d+|[A-F])\b", phase_name, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _load_phase_status() -> dict:
    """
    Returns {phase_letter: 'done' | 'current' | 'upcoming'} by reading
    state/specs/ and computing completion per phase letter.
    The first phase with any incomplete spec is CURRENT.
    Everything before it is DONE; everything after is UPCOMING.
    """
    global _phase_status_cache, _phase_status_ts
    if _phase_status_cache and time.time() - _phase_status_ts < _BRAIN_TTL:
        return _phase_status_cache
    specs_dir = STATE_DIR / "specs"
    if not specs_dir.exists():
        return {}

    counts: dict[str, dict] = {}  # letter → {total, complete}
    for f in specs_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            letter = d.get("phase", "").strip().upper()
            if not letter:
                continue
            if letter not in counts:
                counts[letter] = {"total": 0, "complete": 0}
            counts[letter]["total"] += 1
            if d.get("status") in ("complete", "shipped"):
                counts[letter]["complete"] += 1
        except Exception:
            pass

    result: dict[str, str] = {}
    found_current = False

    for letter in PHASE_ORDER:
        if letter not in counts:
            continue
        c = counts[letter]
        if c["complete"] == c["total"] and c["total"] > 0:
            result[letter] = "done"
        elif not found_current:
            result[letter] = "current"
            found_current = True
        else:
            result[letter] = "upcoming"

    # Any phase letter not in PHASE_ORDER (unexpected) → upcoming
    for letter in counts:
        if letter not in result:
            result[letter] = "upcoming"

    _phase_status_cache = result
    _phase_status_ts = time.time()
    return result


def _idea_cowork_prompt(idea: dict) -> str:
    title     = idea.get("title", "")
    rationale = idea.get("rationale", "")
    source    = idea.get("source", "")
    phase     = idea.get("suggested_phase", "")
    parts = [f'I want to spec and implement "{title}" for Decifer Trading.']
    if source:
        parts.append(f"This came from research: {source}")
    if rationale:
        parts.append(f"\nBackground:\n{rationale}")
    if phase:
        parts.append(f"\nThis belongs in {phase} of the roadmap.")
    parts.append(
        "\nPlease:\n"
        "1. Write a complete feature spec in the state/specs/ JSON format\n"
        "2. Identify which modules need to change\n"
        "3. Implement the feature"
    )
    return "\n".join(parts)


def _risk_fix_prompt(risk: dict) -> str:
    sev        = risk.get("severity", "medium")
    risk_text  = risk.get("risk", "")
    mitigation = risk.get("mitigation", "")
    parts = [
        f"There is a {sev}-severity risk in Decifer Trading that needs to be addressed.",
        f"\nRisk:\n{risk_text}",
    ]
    if mitigation:
        parts.append(f"\nMitigation strategy:\n{mitigation}")
    parts.append(
        "\nPlease:\n"
        "1. Investigate the root cause of this risk in the codebase\n"
        "2. Implement the mitigation\n"
        "3. Add tests to prevent regression"
    )
    return "\n".join(parts)


def _render_risk_prompt_area(risk: dict) -> html.Div:
    """Code prompt card shown when a risk 'Fix prompt' button is clicked."""
    prompt = _risk_fix_prompt(risk)
    sev    = risk.get("severity", "medium")
    color  = SEVERITY_COLORS.get(sev, "#868e96")
    return html.Div([
        html.Div([
            html.Div([
                html.Span("FIX PROMPT — ", style={
                    "fontSize": "0.55rem", "fontWeight": "800",
                    "color": color, "letterSpacing": "1px",
                    "textTransform": "uppercase",
                }),
                html.Span(risk.get("risk", "")[:80], style={
                    "fontSize": "0.7rem", "fontWeight": "700",
                    "color": "var(--cd-text)",
                }),
            ]),
            _copy_btn(prompt, f"risk-copy-{sev}"),
        ], style={
            "display": "flex", "justifyContent": "space-between",
            "alignItems": "center", "marginBottom": "10px",
        }),
        html.Pre(prompt, style={
            "backgroundColor": "#060e1a", "color": "#c5d8f0",
            "fontSize": "0.74rem", "lineHeight": "1.75",
            "padding": "14px 16px", "borderRadius": "6px",
            "whiteSpace": "pre-wrap", "wordBreak": "break-word", "margin": "0",
            "border": f"1px solid {color}30",
            "userSelect": "all", "cursor": "text",
        }),
        html.Div("Click 'Fix prompt →' again to close", style={
            "fontSize": "0.58rem", "color": "var(--cd-faint)",
            "marginTop": "8px", "textAlign": "right",
        }),
    ], style={
        "backgroundColor": "#0b1e34", "borderRadius": "10px",
        "padding": "18px 20px",
        "border": f"1px solid {color}30",
        "borderLeft": f"4px solid {color}",
        "marginTop": "16px",
    })


def _copy_btn(content: str, btn_id: str) -> html.Div:
    """Small copy-to-clipboard button using dcc.Clipboard."""
    try:
        return html.Div(
            dcc.Clipboard(
                content=content,
                title="Copy to clipboard",
                style={
                    "fontSize": "0.62rem", "color": "#4dabf7",
                    "cursor": "pointer", "padding": "3px 10px",
                    "borderRadius": "5px", "border": "1px solid rgba(77,171,247,0.4)",
                    "backgroundColor": "rgba(77,171,247,0.08)",
                    "userSelect": "none",
                },
            ),
        )
    except Exception:
        # dcc.Clipboard not available — silent fallback
        return html.Div()


# ── Section renderers ──────────────────────────────────────────────────────────

# ── Last trade card ────────────────────────────────────────────────────────────

def _load_last_decision():
    """Read last_decision.json from the Decifer Trading repo data directory."""
    from config import DECIFER_REPO_PATH
    if not DECIFER_REPO_PATH or not DECIFER_REPO_PATH.exists():
        return None
    f = DECIFER_REPO_PATH / "data" / "last_decision.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _render_last_trade_card():
    """
    Display the most recent trade taken by the Decifer bot as a rich card —
    same style as the LLY example: Ticker | Company | Alloc% | BUY/SELL,
    then Thesis / Edge / Risk / Expected Returns.
    """
    data = _load_last_decision()

    no_trade_style = {
        "backgroundColor": "var(--cd-card2)",
        "borderRadius": "14px", "padding": "20px 24px",
        "border": "1px solid var(--cd-border)",
        "borderLeft": "5px solid var(--cd-border)",
        "marginBottom": "28px",
    }

    if not data:
        return html.Div([
            html.Div("LAST TRADE", style={
                "fontSize": "0.58rem", "fontWeight": "800",
                "color": "var(--cd-muted)", "letterSpacing": "1.5px",
                "textTransform": "uppercase", "marginBottom": "10px",
            }),
            html.Div("No trades taken yet — the bot has not placed any positions.",
                     style={"fontSize": "0.8rem", "color": "var(--cd-muted)"}),
        ], style=no_trade_style)

    symbol     = data.get("symbol", "?")
    company    = data.get("company_name", symbol)
    direction  = data.get("direction", "BUY")
    alloc      = data.get("allocation_pct", 0)
    thesis     = data.get("thesis", "")
    edge       = data.get("edge_why_now", "")
    risk       = data.get("risk", "")
    exp        = data.get("expected_returns", {})
    agents     = data.get("agents_agreed", 0)
    ts         = data.get("timestamp", "")

    dir_color  = "#51cf66" if direction == "BUY" else "#ff6b6b"
    age        = _age_label(ts) if ts else ""

    # Allocation label
    alloc_label = f"{alloc:.0f}%"

    # Expected returns pill row
    returns_items = []
    for label, key in [("1M", "1m"), ("3M", "3m"), ("12M", "12m")]:
        val = exp.get(key)
        if val is not None:
            sign  = "+" if val >= 0 else ""
            color = "#51cf66" if val >= 0 else "#ff6b6b"
            returns_items.append(html.Span([
                html.Span(f"{label}: ", style={
                    "color": "var(--cd-muted)", "fontSize": "0.8rem",
                }),
                html.Span(f"{sign}{val:.1f}%", style={
                    "color": color, "fontWeight": "700", "fontSize": "0.8rem",
                }),
            ], style={"marginRight": "20px"}))

    def _row(label, text, label_color="var(--cd-text)"):
        return html.Div([
            html.Span(f"{label}: ", style={
                "fontWeight": "700", "color": label_color, "fontSize": "0.85rem",
            }),
            html.Span(text, style={
                "color": "var(--cd-text2)", "fontSize": "0.85rem", "lineHeight": "1.65",
            }),
        ], style={"marginBottom": "8px"})

    return html.Div([

        # Section label
        html.Div("LAST TRADE", style={
            "fontSize": "0.55rem", "fontWeight": "800", "letterSpacing": "1.5px",
            "color": "var(--cd-muted)", "textTransform": "uppercase", "marginBottom": "14px",
        }),

        # Headline: TICKER — Company | Alloc% | BUY
        html.Div([
            html.Span(symbol, style={
                "fontWeight": "900", "fontSize": "1.15rem", "color": "var(--cd-text)",
            }),
            html.Span(" \u2014 ", style={"color": "var(--cd-muted)", "fontSize": "1.0rem"}),
            html.Span(company, style={
                "fontWeight": "600", "fontSize": "1.0rem", "color": "var(--cd-text)",
            }),
            html.Span(f" | {alloc_label}", style={
                "color": "var(--cd-muted)", "fontSize": "0.9rem", "marginLeft": "6px",
            }),
            html.Span(f" | {direction}", style={
                "color": dir_color, "fontWeight": "800",
                "fontSize": "0.95rem", "marginLeft": "8px",
            }),
        ], style={"marginBottom": "16px", "lineHeight": "1.3"}),

        # Thesis
        _row("Thesis", thesis) if thesis else None,

        # Edge (why now)
        _row("Edge (why now)", edge) if edge else None,

        # Risk
        _row("Risk", risk) if risk else None,

        # Expected Returns
        html.Div([
            html.Span("Expected Returns: ", style={
                "fontWeight": "700", "color": "var(--cd-text)", "fontSize": "0.85rem",
            }),
            *returns_items,
        ], style={"marginBottom": "4px", "display": "flex", "flexWrap": "wrap",
                  "alignItems": "center"}) if returns_items else None,

        # Footer
        html.Div([
            html.Span(f"Trade taken {age}" if age else "",
                      style={"fontSize": "0.6rem", "color": "var(--cd-faint)"}),
            html.Span(f"  \u00b7  {agents}/4 agents agreed",
                      style={"fontSize": "0.6rem", "color": "var(--cd-faint)"}),
        ], style={"marginTop": "14px"}),

    ], style={
        "backgroundColor": "#0b1e34",
        "borderRadius": "14px",
        "padding": "24px 28px",
        "border": f"1px solid {dir_color}4d",
        "borderLeft": f"5px solid {dir_color}",
        "marginBottom": "28px",
        "boxShadow": f"0 4px 24px {dir_color}12",
    })


# ── Action type metadata ────────────────────────────────────────────────────────

_ACTION_TYPE_META = {
    "fix":          {"label": "FIX",               "color": "#ff6b6b", "icon": "⚠"},
    "build":        {"label": "BUILD",              "color": "#4dabf7", "icon": "▶"},
    "promote":      {"label": "PROMOTE TO PIPELINE","color": "#cc5de8", "icon": "↑"},
    "spec":         {"label": "WRITE SPEC",         "color": "#20c997", "icon": "✎"},
    "housekeeping": {"label": "HOUSEKEEPING",       "color": "#868e96", "icon": "⟳"},
    "validate":     {"label": "VALIDATE",           "color": "#51cf66", "icon": "✓"},
    "decision":     {"label": "DECISION NEEDED",    "color": "#ffd43b", "icon": "?"},
}

_DEFAULT_TYPE_META = {"label": "NEXT ACTION", "color": "#4dabf7", "icon": "▶"}


def _render_hero_next_action(analysis, skip=0):
    """Big recommendation hero box. Cycles through recommended_actions in order."""
    actions = analysis.get("recommended_actions", [])

    # Fallback: old schema (immediate_next_action + untracked_ideas)
    if not actions:
        ai_rec = analysis.get("immediate_next_action", {})
        ideas  = analysis.get("untracked_ideas", [])
        if ai_rec and ai_rec.get("action"):
            actions = [{
                "action":       ai_rec.get("action", ""),
                "type":         "build",
                "why_now":      ai_rec.get("rationale", ""),
                "source":       "AI analysis",
                "cowork_prompt": ai_rec.get("cowork_prompt", ""),
            }]
        for idea in ideas:
            actions.append({
                "action":       idea.get("title", ""),
                "type":         "promote",
                "why_now":      idea.get("rationale", ""),
                "source":       idea.get("source", "research"),
                "cowork_prompt": _idea_cowork_prompt(idea),
            })

    total = len(actions)

    if total == 0:
        return None

    idx      = skip % total
    rec      = actions[idx]
    action   = rec.get("action", "")
    why_now  = rec.get("why_now", rec.get("rationale", ""))
    source   = rec.get("source", "")
    prompt   = rec.get("cowork_prompt", "")
    atype    = rec.get("type", "build")
    meta     = _ACTION_TYPE_META.get(atype, _DEFAULT_TYPE_META)
    color    = meta["color"]
    label    = meta["label"]
    counter  = f"{idx + 1} of {total}" if total > 1 else None

    return html.Div([

        # Label row
        html.Div([
            html.Span(meta["icon"], style={
                "fontSize": "0.9rem", "color": color,
                "marginRight": "10px", "fontWeight": "900",
            }),
            html.Span(label, style={
                "fontSize": "0.65rem", "fontWeight": "900",
                "color": color, "letterSpacing": "2.5px",
                "textTransform": "uppercase",
            }),
            html.Span(source, style={
                "fontSize": "0.58rem", "color": "var(--cd-faint)", "marginLeft": "12px",
            }) if source else None,
            html.Div(style={"flex": "1"}),
            html.Span(counter, style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)",
            }) if counter else None,
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "14px"}),

        # Action — large and bold
        html.Div(action, style={
            "fontWeight": "800", "fontSize": "1.45rem",
            "color": "var(--cd-text)", "lineHeight": "1.3",
            "marginBottom": "12px",
        }),

        # Why now
        html.Div(why_now, style={
            "fontSize": "0.86rem", "color": "var(--cd-text2)",
            "lineHeight": "1.65", "marginBottom": "22px",
            "maxWidth": "760px",
        }),

        # Cowork prompt block
        html.Div([
            html.Div([
                html.Div([
                    html.Span("COWORK PROMPT", style={
                        "fontSize": "0.55rem", "fontWeight": "800",
                        "color": color, "letterSpacing": "1px",
                        "textTransform": "uppercase",
                    }),
                    html.Span(" — paste into a new Cowork session", style={
                        "fontSize": "0.6rem", "color": "var(--cd-faint)", "marginLeft": "8px",
                    }),
                ]),
                _copy_btn(prompt, "hero-copy"),
            ], style={
                "display": "flex", "justifyContent": "space-between",
                "alignItems": "center", "marginBottom": "8px",
            }),
            html.Pre(prompt, style={
                "backgroundColor": "#060e1a", "color": "#c5d8f0",
                "fontSize": "0.74rem", "lineHeight": "1.75",
                "padding": "16px 18px", "borderRadius": "6px",
                "whiteSpace": "pre-wrap", "wordBreak": "break-word", "margin": "0",
                "border": f"1px solid {color}33",
                "userSelect": "all", "cursor": "text",
            }),
        ]),

        # Previous / Next buttons
        html.Div([
            html.Button(
                "← Previous",
                id="brain-rec-prev-btn",
                n_clicks=0,
                style={
                    "fontSize": "0.65rem", "fontWeight": "700",
                    "color": "#868e96", "cursor": "pointer",
                    "padding": "5px 14px", "borderRadius": "5px",
                    "border": "1px solid rgba(134,142,150,0.35)",
                    "backgroundColor": "rgba(134,142,150,0.06)",
                    "marginRight": "8px",
                },
            ),
            html.Button(
                "Next →",
                id="brain-rec-next-btn",
                n_clicks=0,
                style={
                    "fontSize": "0.65rem", "fontWeight": "700",
                    "color": "#74c0fc", "cursor": "pointer",
                    "padding": "5px 14px", "borderRadius": "5px",
                    "border": "1px solid rgba(116,192,252,0.4)",
                    "backgroundColor": "rgba(116,192,252,0.08)",
                },
            ),
        ], style={"display": "flex", "alignItems": "center", "marginTop": "16px"}),

    ], style={
        "backgroundColor": "#0b1e34",
        "borderRadius": "14px",
        "padding": "28px 32px",
        "border": f"1px solid {color}4d",
        "borderLeft": f"5px solid {color}",
        "marginBottom": "28px",
        "boxShadow": f"0 4px 24px {color}12",
    })


def _render_meta_strip(analysis):
    """Compact meta info: vision, age, data sources."""
    age    = _age_label(analysis.get("generated_at", ""))
    model  = analysis.get("model", "claude")
    ds     = analysis.get("data_sources", {})
    sources_str = "  ·  ".join([
        f"{ds.get('specs', 0)} features",
        f"{ds.get('research', 0)} research docs",
        f"{ds.get('sessions', 0)} sessions",
        f"{ds.get('commits', 0)} commits",
    ])

    return html.Div([
        html.Div([
            html.Span("VISION  ", style={
                "fontSize": "0.52rem", "fontWeight": "800", "color": "#da77f2",
                "letterSpacing": "1px", "textTransform": "uppercase",
            }),
            html.Span(analysis.get("vision_statement", ""), style={
                "fontSize": "0.8rem", "color": "var(--cd-text2)",
                "lineHeight": "1.6", "fontStyle": "italic",
            }),
        ], style={
            "backgroundColor": "#180e20", "borderRadius": "8px",
            "padding": "12px 16px", "borderLeft": "3px solid #da77f2",
            "marginBottom": "8px",
        }),
        html.Div([
            html.Span(f"Analysis generated {age} · {model}", style={
                "fontSize": "0.6rem", "color": "var(--cd-faint)",
            }),
            html.Span("  ·  ", style={"color": "var(--cd-faint)", "fontSize": "0.6rem"}),
            html.Span(f"Based on: {sources_str}", style={
                "fontSize": "0.6rem", "color": "var(--cd-faint)",
            }),
        ], style={"paddingLeft": "4px"}),
    ], style={"marginBottom": "24px"})


def _render_stage_assessment(analysis):
    """Where we stand right now — plain English summary."""
    text    = analysis.get("current_stage_assessment", "")
    summary = analysis.get("executive_summary", "")
    if not text and not summary:
        return None

    children = [
        html.Div("WHERE WE STAND", style={
            "fontSize": "0.58rem", "fontWeight": "800", "letterSpacing": "1.2px",
            "color": "var(--cd-muted)", "textTransform": "uppercase", "marginBottom": "10px",
        }),
    ]
    if text:
        children.append(html.Div(text, style={
            "fontSize": "0.8rem", "color": "var(--cd-text2)",
            "lineHeight": "1.7", "marginBottom": "10px" if summary else "0",
        }))
    if summary:
        children.append(html.Div(summary, style={
            "fontSize": "0.78rem", "color": "var(--cd-text)",
            "lineHeight": "1.7", "borderTop": "1px solid var(--cd-border)",
            "paddingTop": "10px",
        }))

    return html.Div(children, style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "10px",
        "padding": "18px 22px", "border": "1px solid var(--cd-border)",
        "marginBottom": "24px",
    })


def _render_roadmap(analysis):
    phases      = analysis.get("product_roadmap", [])
    phase_status = _load_phase_status()

    if not phases:
        return None

    phase_cards = []
    for i, phase in enumerate(phases):
        scope        = phase.get("estimated_scope", "medium")
        scope_color  = SCOPE_COLORS.get(scope, "#868e96")
        features     = phase.get("features", [])
        phase_name   = phase.get("phase_name", f"Phase {i+1}")
        letter       = _extract_phase_letter(phase_name)
        status       = phase_status.get(letter, "upcoming")

        # Visual tokens per status
        if status == "done":
            badge_text  = "DONE"
            badge_color = DONE_COLOR
            num_bg      = "#0f2a18"
            num_color   = DONE_COLOR
            left_border = f"3px solid {DONE_COLOR}60"
            bg          = "var(--cd-card)"
            title_color = "var(--cd-muted)"
            num_symbol  = "✓"
        elif status == "current":
            badge_text  = "CURRENT PHASE"
            badge_color = CURRENT_COLOR
            num_bg      = "#4dabf7"
            num_color   = "#0a0f18"
            left_border = f"3px solid {CURRENT_COLOR}"
            bg          = "#0d1e30"
            title_color = "var(--cd-text)"
            num_symbol  = str(i + 1)
        else:
            badge_text  = "UPCOMING"
            badge_color = UPCOMING_COLOR
            num_bg      = "var(--cd-deep)"
            num_color   = UPCOMING_COLOR
            left_border = f"3px solid {UPCOMING_COLOR}40"
            bg          = "var(--cd-card)"
            title_color = "var(--cd-text2)"
            num_symbol  = str(i + 1)

        phase_cards.append(html.Div([

            # Header row
            html.Div([
                html.Div([
                    html.Span(num_symbol, style={
                        "width": "26px", "height": "26px", "borderRadius": "50%",
                        "backgroundColor": num_bg, "color": num_color,
                        "display": "inline-flex", "alignItems": "center",
                        "justifyContent": "center", "fontWeight": "800",
                        "fontSize": "0.72rem", "marginRight": "12px", "flexShrink": "0",
                        "border": f"1px solid {badge_color}40",
                    }),
                    html.Div([
                        html.Div(phase_name, style={
                            "fontWeight": "700", "fontSize": "0.92rem",
                            "color": title_color, "marginBottom": "2px",
                        }),
                        html.Div(phase.get("goal", ""), style={
                            "fontSize": "0.72rem", "color": "var(--cd-muted)",
                        }),
                    ]),
                ], style={"display": "flex", "alignItems": "flex-start", "flex": "1"}),

                # Badges
                html.Div([
                    html.Span(badge_text, style={
                        "fontSize": "0.54rem", "fontWeight": "800",
                        "padding": "2px 8px", "borderRadius": "4px",
                        "backgroundColor": f"{badge_color}18", "color": badge_color,
                        "border": f"1px solid {badge_color}40",
                        "textTransform": "uppercase", "letterSpacing": "0.5px",
                        "marginRight": "6px",
                    }),
                    html.Span(scope, style={
                        "fontSize": "0.54rem", "fontWeight": "700",
                        "padding": "2px 8px", "borderRadius": "4px",
                        "backgroundColor": f"{scope_color}18", "color": scope_color,
                        "border": f"1px solid {scope_color}30",
                        "textTransform": "uppercase", "letterSpacing": "0.4px",
                    }),
                ], style={"display": "flex", "alignItems": "center", "flexShrink": "0"}),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "flex-start", "marginBottom": "12px"}),

            # Why now — skip for done phases to keep them compact
            html.Div([
                html.Span("Why now: ", style={
                    "fontWeight": "700", "color": "var(--cd-muted)", "fontSize": "0.7rem",
                }),
                html.Span(phase.get("rationale", ""), style={
                    "fontSize": "0.7rem", "color": "var(--cd-text2)", "lineHeight": "1.5",
                }),
            ], style={"marginBottom": "12px"}) if status != "done" else None,

            # Features
            html.Div([
                html.Div([
                    html.Span("✓ " if status == "done" else "▸ ", style={
                        "color": DONE_COLOR if status == "done" else "#4dabf7",
                        "fontSize": "0.7rem",
                    }),
                    html.Span(f, style={
                        "fontSize": "0.72rem",
                        "color": "var(--cd-muted)" if status == "done" else "var(--cd-text2)",
                        "textDecoration": "line-through" if status == "done" else "none",
                    }),
                ], style={"marginBottom": "3px"})
                for f in features
            ], style={"marginBottom": "10px" if status != "done" else "0"}),

            # Done when — only show for non-done phases
            html.Div([
                html.Span("Done when: ", style={
                    "fontSize": "0.62rem", "fontWeight": "700",
                    "color": "#51cf66", "marginRight": "4px",
                }),
                html.Span(phase.get("exit_criteria", ""), style={
                    "fontSize": "0.65rem", "color": "var(--cd-muted)",
                    "fontStyle": "italic",
                }),
            ]) if status != "done" else None,

        ], style={
            "backgroundColor": bg,
            "borderRadius": "10px", "padding": "16px 20px",
            "border": "1px solid var(--cd-border-sub)",
            "borderLeft": left_border,
            "marginBottom": "8px",
            "opacity": "0.75" if status == "done" else "1",
        }))

    return html.Div([
        html.Div("ROADMAP", style={
            "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
            "color": "var(--cd-muted)", "textTransform": "uppercase",
            "marginBottom": "14px",
        }),
        html.Div(phase_cards),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "12px",
        "padding": "20px", "border": "1px solid var(--cd-border)",
        "marginBottom": "24px",
    })


def _render_opportunities(analysis, selected_idx=-1):
    """Ideas from research not yet turned into specs. Click 'Spec this' to get a cowork prompt."""
    ideas = analysis.get("untracked_ideas", [])
    if not ideas:
        return None

    cards = []
    for i, idea in enumerate(ideas):
        rationale = idea.get("rationale", "")

        # Try to pull a short impact line from the rationale
        # Matches patterns like "+2-3% win rate" or "2 dev days" or "Tier 1"
        impact_match = re.search(
            r"(\+[\d\.\-–]+ ?[\w%/]+(?: win rate| sharpe)?|Tier \d|[\d]+ dev days?)",
            rationale, re.IGNORECASE
        )
        impact_tag = impact_match.group(0) if impact_match else None

        cards.append(
            dbc.Col(html.Div([

                # Impact badge (if extractable)
                html.Div([
                    html.Span(impact_tag, style={
                        "fontSize": "0.65rem", "fontWeight": "800",
                        "padding": "2px 9px", "borderRadius": "4px",
                        "backgroundColor": "rgba(81,207,102,0.12)",
                        "color": "#51cf66",
                        "border": "1px solid rgba(81,207,102,0.3)",
                    }),
                ], style={"marginBottom": "8px"}) if impact_tag else None,

                # Title
                html.Div([
                    html.Span("💡 ", style={"marginRight": "4px"}),
                    html.Span(idea.get("title", ""), style={
                        "fontWeight": "700", "fontSize": "0.82rem",
                        "color": "#ffd43b",
                    }),
                ], style={"marginBottom": "8px"}),

                # Rationale
                html.Div(rationale, style={
                    "fontSize": "0.7rem", "color": "var(--cd-text2)",
                    "lineHeight": "1.5", "marginBottom": "10px",
                }),

                # Meta row
                html.Div([
                    html.Span(idea.get("source", ""), style={
                        "fontSize": "0.58rem", "color": "var(--cd-faint)",
                    }),
                    html.Span("  ·  Belongs in: ", style={
                        "fontSize": "0.58rem", "color": "var(--cd-faint)",
                    }),
                    html.Span(idea.get("suggested_phase", ""), style={
                        "fontSize": "0.58rem", "color": "#ffd43b",
                    }),
                ], style={"marginBottom": "12px"}),

                # Spec this button
                html.Button(
                    "Spec this →",
                    id={"type": "opp-spec-btn", "index": i},
                    n_clicks=0,
                    style={
                        "fontSize": "0.65rem", "fontWeight": "700",
                        "color": "#4dabf7", "cursor": "pointer",
                        "padding": "4px 12px", "borderRadius": "5px",
                        "border": "1px solid rgba(77,171,247,0.4)",
                        "backgroundColor": "rgba(77,171,247,0.08)",
                        "width": "100%",
                    },
                ),

            ], style={
                "backgroundColor": "var(--cd-deep)", "borderRadius": "8px",
                "padding": "14px 15px", "border": "1px solid rgba(255,212,59,0.15)",
                "borderLeft": "3px solid #ffd43b", "height": "100%",
                "display": "flex", "flexDirection": "column",
            }),
            md=4, className="mb-3"),
        )

    return html.Div([
        html.Div([
            html.Span("OPPORTUNITIES — NOT YET SPECCED", style={
                "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
                "color": "var(--cd-muted)", "textTransform": "uppercase",
            }),
            html.Span(
                f"  ·  {len(ideas)} idea{'s' if len(ideas) != 1 else ''} in research, "
                "not yet in the pipeline — click any card to get a Code prompt",
                style={"fontSize": "0.62rem", "color": "var(--cd-faint)"},
            ),
        ], style={"marginBottom": "14px"}),
        dbc.Row(cards),
        # Expanded prompt area — pre-populated if an idea is selected, updated by callback
        html.Div(
            _render_opp_prompt_area(ideas[selected_idx]) if 0 <= selected_idx < len(ideas) else None,
            id="brain-opp-prompt",
        ),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "12px",
        "padding": "20px", "border": "1px solid var(--cd-border)",
        "marginBottom": "24px",
    })


def _render_opp_prompt_area(idea: dict) -> html.Div:
    """Code prompt card shown when an opportunity is clicked."""
    prompt = _idea_cowork_prompt(idea)
    return html.Div([
        html.Div([
            html.Div([
                html.Span("CODE PROMPT — ", style={
                    "fontSize": "0.55rem", "fontWeight": "800",
                    "color": "#4dabf7", "letterSpacing": "1px",
                    "textTransform": "uppercase",
                }),
                html.Span(idea.get("title", ""), style={
                    "fontSize": "0.7rem", "fontWeight": "700",
                    "color": "#ffd43b",
                }),
            ]),
            _copy_btn(prompt, f"opp-copy-{idea.get('title','')}"),
        ], style={
            "display": "flex", "justifyContent": "space-between",
            "alignItems": "center", "marginBottom": "10px",
        }),
        html.Pre(prompt, style={
            "backgroundColor": "#060e1a", "color": "#c5d8f0",
            "fontSize": "0.74rem", "lineHeight": "1.75",
            "padding": "14px 16px", "borderRadius": "6px",
            "whiteSpace": "pre-wrap", "wordBreak": "break-word", "margin": "0",
            "border": "1px solid rgba(77,171,247,0.25)",
            "userSelect": "all", "cursor": "text",
        }),
        html.Div("Click 'Spec this' again to close", style={
            "fontSize": "0.58rem", "color": "var(--cd-faint)",
            "marginTop": "8px", "textAlign": "right",
        }),
    ], style={
        "backgroundColor": "#0b1e34", "borderRadius": "10px",
        "padding": "18px 20px",
        "border": "1px solid rgba(77,171,247,0.25)",
        "borderLeft": "4px solid #4dabf7",
        "marginTop": "16px",
    })


def _render_risks(analysis, selected_idx=-1):
    risks = analysis.get("risks", [])
    if not risks:
        return None

    cards = []
    for i, r in enumerate(risks):
        sev   = r.get("severity", "medium")
        color = SEVERITY_COLORS.get(sev, "#868e96")
        bg    = SEVERITY_BG.get(sev, "var(--cd-card2)")
        cards.append(html.Div([
            html.Div([
                html.Span(sev.upper(), style={
                    "fontSize": "0.55rem", "fontWeight": "800",
                    "padding": "2px 7px", "borderRadius": "4px",
                    "backgroundColor": f"{color}20", "color": color,
                    "border": f"1px solid {color}40",
                    "letterSpacing": "0.5px", "marginRight": "8px",
                }),
                html.Span(r.get("risk", ""), style={
                    "fontWeight": "600", "fontSize": "0.8rem",
                    "color": "var(--cd-text)",
                }),
            ], style={"marginBottom": "6px"}),
            html.Div([
                html.Div([
                    html.Span("→ ", style={"color": color, "fontWeight": "700"}),
                    html.Span(r.get("mitigation", ""), style={
                        "fontSize": "0.7rem", "color": "var(--cd-muted)",
                        "fontStyle": "italic",
                    }),
                ], style={"flex": "1"}),
                html.Button(
                    "Fix prompt →",
                    id={"type": "risk-fix-btn", "index": i},
                    n_clicks=0,
                    style={
                        "fontSize": "0.62rem", "fontWeight": "700",
                        "color": color, "cursor": "pointer",
                        "padding": "3px 10px", "borderRadius": "5px",
                        "border": f"1px solid {color}40",
                        "backgroundColor": f"{color}10",
                        "flexShrink": "0", "marginLeft": "12px",
                        "whiteSpace": "nowrap",
                    },
                ),
            ], style={"display": "flex", "alignItems": "flex-start"}),
        ], style={
            "backgroundColor": bg, "borderRadius": "8px", "padding": "12px 14px",
            "border": f"1px solid {color}30", "borderLeft": f"3px solid {color}",
            "marginBottom": "8px",
        }))

    return html.Div([
        html.Div("RISKS & BLOCKERS", style={
            "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
            "color": "var(--cd-muted)", "textTransform": "uppercase",
            "marginBottom": "14px",
        }),
        html.Div(cards),
        # Expanded fix prompt area — pre-populated if a risk is selected
        html.Div(
            _render_risk_prompt_area(risks[selected_idx]) if 0 <= selected_idx < len(risks) else None,
            id="brain-risk-prompt",
        ),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "12px",
        "padding": "20px", "border": "1px solid var(--cd-border)",
        "marginBottom": "24px",
    })


def _render_gaps(analysis):
    gaps = analysis.get("roadmap_gaps", "")
    if not gaps:
        return None
    return html.Div([
        html.Div("WHAT'S MISSING", style={
            "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
            "color": "var(--cd-muted)", "textTransform": "uppercase",
            "marginBottom": "10px",
        }),
        html.Div(gaps, style={
            "fontSize": "0.78rem", "color": "var(--cd-text2)", "lineHeight": "1.7",
        }),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "12px",
        "padding": "20px", "border": "1px solid var(--cd-border)",
        "marginBottom": "24px",
    })


def _render_live_fallback_hero():
    """DO THIS NEXT from raw state data — shown when analyse.py hasn't run yet."""
    global _fallback_hero_cache, _fallback_hero_ts
    if _fallback_hero_cache["valid"] and time.time() - _fallback_hero_ts < _BRAIN_TTL:
        return _fallback_hero_cache["result"]
    specs_dir    = STATE_DIR / "specs"
    backlog_file = STATE_DIR / "backlog.json"
    research_dir = STATE_DIR / "research"
    sessions_dir = STATE_DIR / "sessions"

    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    all_specs = []

    if specs_dir.exists():
        for f in sorted(specs_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                if d.get("status") not in ("complete", "shipped"):
                    all_specs.append(d)
            except Exception:
                pass

    if backlog_file.exists():
        try:
            items = json.loads(backlog_file.read_text())
            if isinstance(items, list):
                for d in items:
                    if d.get("status") not in ("complete", "shipped"):
                        all_specs.append(d)
        except Exception:
            pass

    all_specs.sort(key=lambda s: priority_order.get(s.get("priority", "P3"), 3))
    top_spec = all_specs[0] if all_specs else None

    quick_wins = []
    if research_dir.exists():
        files = sorted(research_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[:1]:
            try:
                d = json.loads(f.read_text())
                quick_wins = d.get("top_3_quick_wins", [])
                break
            except Exception:
                pass

    last_session_note = ""
    if sessions_dir.exists():
        files = sorted(sessions_dir.glob("*.json"), reverse=True)
        for f in files[:1]:
            try:
                d = json.loads(f.read_text())
                items = d.get("work_items", [])
                if items:
                    last_session_note = items[0].get("summary", "")
                break
            except Exception:
                pass

    if not top_spec and not quick_wins:
        _fallback_hero_cache = {"result": None, "valid": True}
        _fallback_hero_ts = time.time()
        return None

    if top_spec:
        action    = f"Build: {top_spec.get('title', 'Next feature')}"
        rationale = top_spec.get("summary", top_spec.get("description", ""))
        module    = top_spec.get("module", "")
        prompt    = f"I want to implement '{top_spec.get('title', '')}' in Decifer Trading."
        if module:
            prompt += f" It goes in {module}."
        if rationale:
            prompt += f"\n\n{rationale}"
        if last_session_note:
            prompt += f"\n\nLast session context: {last_session_note}"
    else:
        w         = quick_wins[0]
        action    = f"Quick win: {w}"
        rationale = "Identified as high-impact, low-effort by the autonomous researcher."
        prompt    = f"I want to implement '{w}' in Decifer Trading. Help me spec and build it."

    _fallback_result = html.Div([
        html.Div([
            html.Span("▶", style={
                "fontSize": "0.9rem", "color": "#4dabf7",
                "marginRight": "10px", "fontWeight": "900",
            }),
            html.Span("DO THIS NEXT", style={
                "fontSize": "0.65rem", "fontWeight": "900",
                "color": "#4dabf7", "letterSpacing": "2.5px",
                "textTransform": "uppercase",
            }),
            html.Span(" — from live pipeline data", style={
                "fontSize": "0.6rem", "color": "var(--cd-faint)", "marginLeft": "10px",
            }),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "14px"}),

        html.Div(action, style={
            "fontWeight": "800", "fontSize": "1.35rem",
            "color": "var(--cd-text)", "lineHeight": "1.3", "marginBottom": "12px",
        }),

        html.Div(rationale, style={
            "fontSize": "0.86rem", "color": "var(--cd-text2)",
            "lineHeight": "1.65", "marginBottom": "22px", "maxWidth": "760px",
        }) if rationale else None,

        html.Div([
            html.Div([
                html.Div([
                    html.Span("CODE PROMPT", style={
                        "fontSize": "0.55rem", "fontWeight": "800",
                        "color": "#4dabf7", "letterSpacing": "1px", "textTransform": "uppercase",
                    }),
                    html.Span(" — copy into a new Code session", style={
                        "fontSize": "0.6rem", "color": "var(--cd-faint)", "marginLeft": "8px",
                    }),
                ]),
                _copy_btn(prompt, "fallback-hero-copy"),
            ], style={
                "display": "flex", "justifyContent": "space-between",
                "alignItems": "center", "marginBottom": "8px",
            }),
            html.Pre(prompt, style={
                "backgroundColor": "#060e1a", "color": "#c5d8f0",
                "fontSize": "0.74rem", "lineHeight": "1.75",
                "padding": "16px 18px", "borderRadius": "6px",
                "whiteSpace": "pre-wrap", "wordBreak": "break-word", "margin": "0",
                "border": "1px solid rgba(77,171,247,0.25)",
                "userSelect": "all", "cursor": "text",
            }),
        ]),

    ], style={
        "backgroundColor": "#0b1e34", "borderRadius": "14px",
        "padding": "28px 32px", "border": "1px solid rgba(77,171,247,0.3)",
        "borderLeft": "5px solid #4dabf7", "marginBottom": "28px",
        "boxShadow": "0 4px 24px rgba(77,171,247,0.07)",
    })
    _fallback_hero_cache = {"result": _fallback_result, "valid": True}
    _fallback_hero_ts = time.time()
    return _fallback_result


def _render_empty():
    vision = {}
    if VISION_FILE.exists():
        try:
            vision = json.loads(VISION_FILE.read_text())
        except Exception:
            pass

    live_hero = _render_live_fallback_hero()

    return html.Div([
        live_hero,
        html.Div([
            html.Div("VISION", style={
                "fontSize": "0.58rem", "fontWeight": "800",
                "color": "#da77f2", "letterSpacing": "1px",
                "textTransform": "uppercase", "marginBottom": "8px",
            }),
            html.Div(vision.get("statement", "Vision not set."), style={
                "fontSize": "0.8rem", "color": "var(--cd-text2)",
                "lineHeight": "1.6", "fontStyle": "italic",
            }),
        ], style={
            "backgroundColor": "#180e20", "borderRadius": "8px",
            "padding": "14px 18px", "borderLeft": "3px solid #da77f2",
            "marginBottom": "24px",
        }) if vision.get("statement") else None,

        html.Div([
            html.Div([
                html.Span("🧠", style={"fontSize": "1.1rem", "marginRight": "8px"}),
                html.Span("No AI analysis yet", style={
                    "fontWeight": "700", "fontSize": "0.85rem", "color": "var(--cd-text)",
                }),
            ], style={"marginBottom": "8px", "display": "flex", "alignItems": "center"}),
            html.Div(
                "Run analyse.py to get a full AI-generated roadmap, deeper next-action reasoning, "
                "untracked opportunities, and risk assessment.",
                style={"fontSize": "0.74rem", "color": "var(--cd-muted)",
                       "lineHeight": "1.55", "marginBottom": "12px"},
            ),
            html.Pre(
                "cd 'Chief Designer/Chief-Decifer'\npython analyse.py",
                style={
                    "backgroundColor": "var(--cd-deep)", "color": "#c5d8f0",
                    "fontSize": "0.74rem", "padding": "10px 14px",
                    "borderRadius": "6px", "border": "1px solid var(--cd-border)",
                    "margin": "0",
                }
            ),
        ], style={
            "backgroundColor": "var(--cd-card2)", "borderRadius": "10px",
            "padding": "16px 20px", "border": "1px solid var(--cd-border)",
        }),
    ])


# ── Chat helpers ───────────────────────────────────────────────────────────────

def _load_recent_research(limit: int = 8) -> list:
    """Load synthesis + top wins from the N most recent research files."""
    research_dir = STATE_DIR / "research"
    if not research_dir.exists():
        return []
    files = sorted(research_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    results = []
    for f in files[:limit]:
        try:
            d = json.loads(f.read_text())
            results.append({
                "topic":             d.get("topic", ""),
                "date":              d.get("date", ""),
                "synthesis":         d.get("synthesis", ""),
                "top_3_quick_wins":  d.get("top_3_quick_wins", []),
                "findings":          d.get("findings", [])[:3],
            })
        except Exception:
            pass
    return results


def _load_vision() -> dict:
    if not VISION_FILE.exists():
        return {}
    try:
        return json.loads(VISION_FILE.read_text())
    except Exception:
        return {}


def _build_chat_system_prompt() -> str:
    """Build the system prompt injecting full analysis + raw research + vision."""
    analysis = _load_analysis() or {}
    research = _load_recent_research()
    vision   = _load_vision()
    return (
        "You are Chief Decifer — the product brain for the Decifer Trading system.\n"
        "You are in a read-only advisory role. Reason about the analysis below, answer "
        "questions, explore trade-offs, and help Amit understand your recommendations.\n"
        "Do NOT write code. Do NOT trigger actions. Do NOT modify any state.\n"
        "Be concise and direct. Ground every answer in the data provided — do not hallucinate.\n\n"
        "== CURRENT ANALYSIS ==\n"
        f"{json.dumps(analysis, indent=2)}\n\n"
        "== RAW RESEARCH SUMMARIES ==\n"
        f"{json.dumps(research, indent=2)}\n\n"
        "== VISION ==\n"
        f"{json.dumps(vision, indent=2)}\n"
    )


def _get_chip_texts(analysis: dict) -> list:
    """Return suggested question strings derived from the current analysis."""
    texts = []

    risks = analysis.get("risks", [])
    high_risks = [r for r in risks if r.get("severity") == "high"]
    if high_risks:
        title = high_risks[0].get("risk", "")[:50]
        texts.append(f"Explain the risk: {title}")

    roadmap = analysis.get("product_roadmap", [])
    if roadmap:
        texts.append("What do we need to exit the current phase?")

    ideas = analysis.get("untracked_ideas", [])
    if ideas:
        title = ideas[0].get("title", "")[:40]
        texts.append(f"Should we spec out '{title}'?")

    texts.append("What's the biggest gap in the roadmap?")
    return texts[:5]


def _build_suggested_chips(analysis: dict) -> list:
    """Render clickable question chips from the current analysis."""
    return [
        html.Button(
            text,
            id={"type": "chat-chip", "index": i},
            n_clicks=0,
            style={
                "fontSize": "0.65rem", "fontWeight": "600",
                "color": "#868e96", "cursor": "pointer",
                "padding": "4px 10px", "borderRadius": "12px",
                "border": "1px solid rgba(134,142,150,0.35)",
                "backgroundColor": "rgba(134,142,150,0.07)",
                "marginRight": "6px", "marginBottom": "6px",
                "whiteSpace": "nowrap",
            },
        )
        for i, text in enumerate(_get_chip_texts(analysis))
    ]


def _render_chat_message(role: str, text: str) -> html.Div:
    """Render a single message bubble; detects code blocks and adds copy buttons."""
    is_user = (role == "user")
    children = []
    parts = re.split(r"(```[\s\S]*?```)", text)
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            code = re.sub(r"^```[^\n]*\n?", "", part[3:-3]).strip()
            children.append(html.Div([
                html.Div([
                    html.Span("CODE PROMPT", style={
                        "fontSize": "0.55rem", "fontWeight": "800",
                        "color": "#4dabf7", "letterSpacing": "1px",
                    }),
                    _copy_btn(code, f"chat-code-{abs(hash(code)) % 100000}"),
                ], style={
                    "display": "flex", "justifyContent": "space-between",
                    "alignItems": "center", "marginBottom": "6px",
                }),
                html.Pre(code, style={
                    "backgroundColor": "#060e1a", "color": "#c5d8f0",
                    "fontSize": "0.72rem", "lineHeight": "1.7",
                    "padding": "12px 14px", "borderRadius": "6px",
                    "whiteSpace": "pre-wrap", "wordBreak": "break-word",
                    "margin": "0", "border": "1px solid rgba(77,171,247,0.25)",
                    "userSelect": "all",
                }),
            ], style={"marginTop": "8px", "marginBottom": "4px"}))
        elif part.strip():
            children.append(html.Div(part.strip(), style={
                "fontSize": "0.8rem",
                "color": "#e0e0e0" if is_user else "var(--cd-text2)",
                "lineHeight": "1.6",
            }))

    if is_user:
        return html.Div(
            html.Div(children, style={
                "backgroundColor": "#2a2a3e",
                "borderRadius": "10px 10px 2px 10px",
                "padding": "10px 14px", "maxWidth": "75%",
            }),
            style={"display": "flex", "justifyContent": "flex-end", "marginBottom": "10px"},
        )
    return html.Div(
        html.Div([
            html.Span("CHIEF", style={
                "fontSize": "0.55rem", "fontWeight": "800",
                "color": "#4dabf7", "letterSpacing": "1px",
                "display": "block", "marginBottom": "4px",
            }),
            *children,
        ], style={
            "backgroundColor": "#0d1a2a",
            "borderRadius": "10px 10px 10px 2px",
            "padding": "10px 14px", "maxWidth": "85%",
            "border": "1px solid rgba(77,171,247,0.15)",
        }),
        style={"display": "flex", "justifyContent": "flex-start", "marginBottom": "10px"},
    )


def _render_chat_messages(history: list, thinking: bool = False) -> list:
    """Render all history turns plus an optional 'thinking…' indicator."""
    items = [_render_chat_message(t["role"], t["content"]) for t in history]
    if thinking:
        items.append(html.Div(
            html.Div([
                html.Span("CHIEF", style={
                    "fontSize": "0.55rem", "fontWeight": "800",
                    "color": "#4dabf7", "letterSpacing": "1px",
                    "display": "block", "marginBottom": "4px",
                }),
                html.Span("thinking…", style={
                    "fontSize": "0.8rem", "color": "#868e96", "fontStyle": "italic",
                }),
            ], style={
                "backgroundColor": "#0d1a2a",
                "borderRadius": "10px 10px 10px 2px",
                "padding": "10px 14px", "maxWidth": "200px",
                "border": "1px solid rgba(77,171,247,0.15)",
            }),
            style={"display": "flex", "justifyContent": "flex-start", "marginBottom": "10px"},
        ))
    return items


def _render_chat_section(analysis: dict, chat_history: list = None) -> html.Div:
    """The 'Chat with Chief' block appended at the bottom of the Brain tab."""
    from datetime import timedelta
    chat_history = chat_history or []

    staleness = ""
    try:
        generated_at = analysis.get("generated_at", "")
        if generated_at:
            dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if (datetime.now(tz=timezone.utc) - dt) > timedelta(hours=24):
                staleness = f"⚠ Analysis is {_age_label(generated_at)} old — answers reflect that state"
    except Exception:
        pass

    trim_note = None
    if len(chat_history) >= CHAT_HISTORY_LIMIT * 2:
        trim_note = html.Div(
            "History trimmed to last 10 exchanges.",
            style={"fontSize": "0.62rem", "color": "#868e96",
                   "textAlign": "center", "marginBottom": "6px"},
        )

    return html.Div([
        # Header
        html.Div([
            html.Span("CHAT WITH CHIEF", style={
                "fontSize": "0.6rem", "fontWeight": "800",
                "letterSpacing": "1.5px", "color": CURRENT_COLOR,
                "textTransform": "uppercase",
            }),
            html.Span(staleness, style={
                "fontSize": "0.62rem", "color": "#ffd43b", "marginLeft": "12px",
            }) if staleness else None,
            html.Div(style={"flex": "1"}),
            html.Button(
                "Clear chat",
                id="brain-chat-clear",
                n_clicks=0,
                style={
                    "fontSize": "0.62rem", "fontWeight": "600",
                    "color": "#868e96", "cursor": "pointer",
                    "padding": "3px 10px", "borderRadius": "5px",
                    "border": "1px solid rgba(134,142,150,0.35)",
                    "backgroundColor": "transparent",
                },
            ),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "12px"}),

        # Suggested chips
        html.Div(
            _build_suggested_chips(analysis),
            style={"display": "flex", "flexWrap": "wrap", "marginBottom": "12px"},
        ),

        # Trim note (shown when history is at limit)
        trim_note,

        # Message thread
        html.Div(
            _render_chat_messages(chat_history),
            id="brain-chat-messages",
            style={
                "minHeight": "60px", "maxHeight": "420px",
                "overflowY": "auto", "marginBottom": "12px",
            },
        ),

        # Input row
        html.Div([
            dcc.Textarea(
                id="brain-chat-input",
                placeholder="Ask Chief anything about this analysis…",
                value="",
                style={
                    "flex": "1", "background": "#0d1a2a",
                    "color": "#e0e0e0",
                    "border": "1px solid rgba(77,171,247,0.3)",
                    "borderRadius": "6px", "padding": "8px 12px",
                    "fontSize": "0.78rem", "resize": "none",
                    "fontFamily": "inherit", "height": "60px",
                },
            ),
            html.Button(
                "Ask",
                id="brain-chat-submit",
                n_clicks=0,
                style={
                    "fontSize": "0.72rem", "fontWeight": "700",
                    "color": "#4dabf7", "cursor": "pointer",
                    "padding": "8px 18px", "borderRadius": "6px",
                    "border": "1px solid rgba(77,171,247,0.4)",
                    "backgroundColor": "rgba(77,171,247,0.10)",
                    "alignSelf": "flex-end",
                },
            ),
        ], style={"display": "flex", "gap": "8px", "alignItems": "flex-end"}),

    ], style={
        "backgroundColor": "var(--cd-card2)",
        "borderRadius": "12px", "padding": "20px",
        "border": f"1px solid {CURRENT_COLOR}33",
        "marginBottom": "24px",
    })


# ── Activity strip (compact feed for bottom of home page) ─────────────────────

def _render_activity_strip():
    """
    Show the last 8 activity events at the bottom of the Brain tab.
    Reads from state/activity.jsonl — same source as the Activity tab.
    """
    from config import ACTIVITY_FILE
    from datetime import timedelta

    entries = []
    if ACTIVITY_FILE.exists():
        try:
            lines = ACTIVITY_FILE.read_text().strip().splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts      = d.get("timestamp", "")
                summary = d.get("summary", "")
                etype   = d.get("type", "update")
                if not summary:
                    continue
                # Friendly age label
                age = ""
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    delta = datetime.now(tz=timezone.utc) - dt
                    if delta < timedelta(minutes=5):
                        age = "just now"
                    elif delta < timedelta(hours=1):
                        age = f"{int(delta.total_seconds()/60)}m ago"
                    elif delta < timedelta(hours=24):
                        age = f"{int(delta.total_seconds()/3600)}h ago"
                    elif delta.days < 7:
                        age = f"{delta.days}d ago"
                    else:
                        age = dt.strftime("%b %d")
                except Exception:
                    age = ts[:10] if len(ts) >= 10 else ts

                # Icon per type
                icon_map = {
                    "session_start": "▶", "session_end": "■",
                    "doc_created": "✎", "backlog_update": "⊞",
                    "feature_update": "⊞", "bugfix": "⚠", "deploy": "✓",
                }
                icon = icon_map.get(etype, "·")
                accent = "#51cf66" if etype == "deploy" else (
                    "#ff6b6b" if etype == "bugfix" else "#4dabf7"
                )

                entries.append((icon, accent, summary, age))
                if len(entries) >= 8:
                    break
        except Exception:
            pass

    if not entries:
        return None

    rows = []
    for icon, accent, summary, age in entries:
        rows.append(html.Div([
            html.Span(icon, style={
                "color": accent, "fontSize": "0.72rem",
                "width": "18px", "display": "inline-block",
                "flexShrink": "0",
            }),
            html.Span(summary, style={
                "fontSize": "0.78rem", "color": "var(--cd-text2)",
                "flex": "1", "lineHeight": "1.4",
            }),
            html.Span(age, style={
                "fontSize": "0.62rem", "color": "var(--cd-faint)",
                "marginLeft": "12px", "whiteSpace": "nowrap", "flexShrink": "0",
            }),
        ], style={
            "display": "flex", "alignItems": "flex-start", "gap": "8px",
            "padding": "7px 0",
            "borderBottom": "1px solid var(--cd-border)",
        }))

    return html.Div([
        html.Div("RECENT ACTIVITY", style={
            "fontSize": "0.55rem", "fontWeight": "800", "letterSpacing": "1.5px",
            "color": "var(--cd-muted)", "textTransform": "uppercase",
            "marginBottom": "10px",
        }),
        html.Div(rows),
    ], style={
        "backgroundColor": "var(--cd-card2)", "borderRadius": "12px",
        "padding": "16px 20px",
        "border": "1px solid var(--cd-border)",
        "marginTop": "24px",
    })


# ── Main layout ────────────────────────────────────────────────────────────────

def layout(opp_idx=-1, risk_idx=-1, rerun_status=None, chat_history=None, skip=0):
    analysis = _load_analysis()

    # Auto-populate status from background state if not explicitly passed
    if rerun_status is None:
        if _rerun_state["running"]:
            rerun_status = "⟳ Running analysis..."
        elif _rerun_state.get("msg"):
            rerun_status = _rerun_state["msg"]
        else:
            rerun_status = ""

    # Determine status span style based on content
    if rerun_status.startswith("✓"):
        status_style = {"fontSize": "0.7rem", "color": "#51cf66", "marginLeft": "10px"}
    elif rerun_status.startswith("✗"):
        status_style = {"fontSize": "0.7rem", "color": "#ff6b6b", "marginLeft": "10px"}
    elif rerun_status:
        status_style = {"fontSize": "0.7rem", "color": "#ffd43b", "marginLeft": "10px"}
    else:
        status_style = {"fontSize": "0.7rem", "color": "var(--cd-muted)", "marginLeft": "10px"}

    rerun_row = html.Div([
        dbc.Button(
            [html.Span("⟳ ", style={"fontWeight": "900"}), "Rerun Analysis"],
            id="brain-rerun-btn",
            n_clicks=0,
            size="sm",
            style={
                "fontSize": "0.72rem", "fontWeight": "600",
                "backgroundColor": "rgba(77,171,247,0.12)",
                "border": "1px solid rgba(77,171,247,0.35)",
                "color": "#4dabf7", "borderRadius": "6px",
                "padding": "5px 14px",
            },
        ),
        html.Span(
            rerun_status,
            id="brain-rerun-status",
            style=status_style,
        ),
    ], style={"display": "flex", "alignItems": "center", "marginBottom": "16px"})

    if not analysis:
        return html.Div([
            rerun_row,
            _render_empty(),
            dcc.Interval(id="brain-interval", interval=60_000, n_intervals=0),
        ])

    # Freshness warning — show if analysis is older than 24h
    age_warning = None
    try:
        from datetime import timedelta
        generated_at = analysis.get("generated_at", "")
        if generated_at:
            dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if (datetime.now(tz=timezone.utc) - dt) > timedelta(hours=24):
                age = _age_label(generated_at)
                age_warning = html.Div([
                    html.Span("⚠", style={"marginRight": "8px", "fontSize": "0.85rem"}),
                    html.Span(f"Analysis last updated {age} — run analyse.py to refresh",
                              style={"fontSize": "0.72rem", "fontWeight": "600"}),
                ], style={
                    "backgroundColor": "rgba(255,212,59,0.08)",
                    "border": "1px solid rgba(255,212,59,0.3)",
                    "borderRadius": "8px", "padding": "10px 16px",
                    "color": "#ffd43b", "marginBottom": "16px",
                    "display": "flex", "alignItems": "center",
                })
    except Exception:
        pass

    return html.Div([
        age_warning,

        # Rerun button row (always visible at top)
        rerun_row,

        # 1. DO THIS NEXT — hero, first thing you see
        _render_hero_next_action(analysis, skip=skip),

        # 2. Chat with Chief — immediately below the recommendation
        _render_chat_section(analysis, chat_history=chat_history or []),

        # 3. Where we stand right now
        _render_stage_assessment(analysis),

        # 4. Full roadmap with DONE / CURRENT / UPCOMING status
        _render_roadmap(analysis),

        # 5. Ideas found in research, not yet in the pipeline (click to spec)
        _render_opportunities(analysis, selected_idx=opp_idx),

        # 6. What's missing from the plan
        _render_gaps(analysis),

        # 7. Meta strip — vision, age, data sources
        html.Div(style={"borderBottom": "1px solid var(--cd-border)", "margin": "8px 0 20px"}),
        _render_meta_strip(analysis),

        dcc.Interval(id="brain-interval", interval=60_000, n_intervals=0),
    ])


def risks_layout(risk_idx=-1):
    """Standalone layout for the Risks & Blockers tab."""
    analysis = _load_analysis()
    if not analysis:
        return html.Div(
            "No analysis loaded — run Rerun Analysis on the Brain tab first.",
            style={"fontSize": "0.8rem", "color": "var(--cd-muted)", "padding": "24px"},
        )
    risks = analysis.get("risks", [])
    if not risks:
        return html.Div(
            "No risks in current analysis.",
            style={"fontSize": "0.8rem", "color": "var(--cd-muted)", "padding": "24px"},
        )
    generated_at = analysis.get("generated_at", "")
    age = _age_label(generated_at) if generated_at else "unknown"
    return html.Div([
        html.Div([
            html.Span("RISKS & BLOCKERS", style={
                "fontSize": "0.6rem", "fontWeight": "800", "letterSpacing": "1.5px",
                "color": "var(--cd-muted)", "textTransform": "uppercase",
            }),
            html.Span(
                f"  ·  {len(risks)} risk{'s' if len(risks) != 1 else ''}  ·  analysis {age}",
                style={"fontSize": "0.62rem", "color": "var(--cd-faint)"},
            ),
        ], style={"marginBottom": "16px"}),
        _render_risks(analysis, selected_idx=risk_idx),
        dcc.Interval(id="risks-interval", interval=60_000, n_intervals=0),
    ])


# ── Callbacks ──────────────────────────────────────────────────────────────────

def register_callbacks(app):
    from dash import no_update

    @app.callback(
        Output("brain-content", "children"),
        Input("brain-interval", "n_intervals"),
        Input("scan-complete", "data"),
        Input("brain-rec-skip", "data"),
        State("brain-opp-idx", "data"),
        State("brain-risk-idx", "data"),
        State("brain-chat-history", "data"),
    )
    def refresh(_n, _scan, rec_skip, opp_idx, risk_idx, chat_history):
        return layout(opp_idx=opp_idx or -1, risk_idx=risk_idx or -1,
                      chat_history=chat_history or [], skip=rec_skip or 0)

    @app.callback(
        Output("brain-rerun-status", "children"),
        Output("brain-rerun-status", "style"),
        Output("brain-rerun-poll", "disabled"),
        Input("brain-rerun-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def rerun_analysis(n_clicks):
        if not n_clicks:
            return no_update, no_update, no_update
        if _rerun_state["running"]:
            return (
                "⟳ Already running...",
                {"fontSize": "0.7rem", "color": "#ffd43b", "marginLeft": "10px"},
                False,
            )
        # Reset state and kick off background thread
        _rerun_state.update({"running": True, "ok": None, "msg": ""})
        threading.Thread(target=_run_rerun_bg, daemon=True).start()
        return (
            "⟳ Running analysis...",
            {"fontSize": "0.7rem", "color": "#ffd43b", "marginLeft": "10px"},
            False,  # enable poll
        )

    @app.callback(
        Output("brain-rerun-status", "children", allow_duplicate=True),
        Output("brain-rerun-status", "style", allow_duplicate=True),
        Output("brain-content", "children", allow_duplicate=True),
        Output("brain-rerun-poll", "disabled", allow_duplicate=True),
        Output("brain-rec-skip", "data", allow_duplicate=True),
        Input("brain-rerun-poll", "n_intervals"),
        State("brain-opp-idx", "data"),
        State("brain-risk-idx", "data"),
        State("brain-chat-history", "data"),
        State("brain-rec-skip", "data"),
        prevent_initial_call=True,
    )
    def poll_rerun_status(_n, opp_idx, risk_idx, chat_history, rec_skip):
        if _rerun_state["running"]:
            return (
                "⟳ Running analysis...",
                {"fontSize": "0.7rem", "color": "#ffd43b", "marginLeft": "10px"},
                no_update,
                False,  # keep polling
                no_update,
            )
        if _rerun_state["ok"] is None:
            # Nothing started — disable poll
            return no_update, no_update, no_update, True, no_update
        # Done (success or failure)
        msg = _rerun_state["msg"]
        ok  = _rerun_state["ok"]
        # Reset so next poll doesn't re-trigger
        _rerun_state["ok"] = None
        style = {
            "fontSize": "0.7rem",
            "color": "#51cf66" if ok else "#ff6b6b",
            "marginLeft": "10px",
        }
        # Reset recommendation skip counter on successful rerun so fresh
        # recommendations are visible immediately (not stuck at "all reviewed")
        new_skip = 0 if ok else (rec_skip or 0)
        return (
            msg,
            style,
            layout(opp_idx=opp_idx or -1, risk_idx=risk_idx or -1,
                   rerun_status=msg, chat_history=chat_history or [],
                   skip=new_skip),
            True,  # disable poll
            new_skip,
        )

    @app.callback(
        Output("brain-opp-idx", "data"),
        Input({"type": "opp-spec-btn", "index": ALL}, "n_clicks"),
        State("brain-opp-idx", "data"),
        prevent_initial_call=True,
    )
    def select_opportunity(n_clicks_list, current_idx):
        ctx = callback_context
        if not ctx.triggered:
            return -1
        try:
            triggered_id = ctx.triggered[0]["prop_id"]
            parsed       = json.loads(triggered_id.split(".")[0])
            clicked      = parsed["index"]
        except Exception:
            return -1
        return -1 if current_idx == clicked else clicked

    @app.callback(
        Output("brain-opp-prompt", "children"),
        Input("brain-opp-idx", "data"),
    )
    def render_opportunity_prompt(idx):
        if idx is None or idx < 0:
            return None
        analysis = _load_analysis()
        if not analysis:
            return None
        ideas = analysis.get("untracked_ideas", [])
        if idx >= len(ideas):
            return None
        return _render_opp_prompt_area(ideas[idx])

    @app.callback(
        Output("brain-risk-idx", "data"),
        Input({"type": "risk-fix-btn", "index": ALL}, "n_clicks"),
        State("brain-risk-idx", "data"),
        prevent_initial_call=True,
    )
    def select_risk(n_clicks_list, current_idx):
        ctx = callback_context
        if not ctx.triggered:
            return -1
        try:
            triggered_id = ctx.triggered[0]["prop_id"]
            parsed       = json.loads(triggered_id.split(".")[0])
            clicked      = parsed["index"]
        except Exception:
            return -1
        return -1 if current_idx == clicked else clicked

    @app.callback(
        Output("brain-risk-prompt", "children"),
        Input("brain-risk-idx", "data"),
    )
    def render_risk_prompt(idx):
        if idx is None or idx < 0:
            return None
        analysis = _load_analysis()
        if not analysis:
            return None
        risks = analysis.get("risks", [])
        if idx >= len(risks):
            return None
        return _render_risk_prompt_area(risks[idx])

    # ── Chat callbacks ──────────────────────────────────────────────────────────

    @app.callback(
        Output("brain-chat-history", "data"),
        Output("brain-chat-poll", "disabled"),
        Output("brain-chat-messages", "children"),
        Output("brain-chat-input", "value"),
        Input("brain-chat-submit", "n_clicks"),
        Input({"type": "chat-chip", "index": ALL}, "n_clicks"),
        State("brain-chat-input", "value"),
        State("brain-chat-history", "data"),
        prevent_initial_call=True,
    )
    def submit_chat(_btn, _chips, input_value, history):
        ctx = callback_context
        if not ctx.triggered:
            return no_update, no_update, no_update, no_update

        triggered_id = ctx.triggered[0]["prop_id"]

        if "chat-chip" in triggered_id:
            # Guard against spurious fires when chips are re-mounted during
            # layout refresh (Dash fires ALL-pattern callbacks on re-mount with n_clicks=0)
            if not ctx.triggered[0].get("value"):
                return no_update, no_update, no_update, no_update
            try:
                parsed    = json.loads(triggered_id.split(".")[0])
                chip_idx  = parsed["index"]
            except Exception:
                return no_update, no_update, no_update, no_update
            analysis   = _load_analysis() or {}
            chip_texts = _get_chip_texts(analysis)
            if chip_idx >= len(chip_texts):
                return no_update, no_update, no_update, no_update
            user_message = chip_texts[chip_idx]
        else:
            user_message = input_value

        if not user_message or not user_message.strip():
            return no_update, no_update, no_update, no_update
        if _chat_state["running"]:
            return no_update, no_update, no_update, no_update

        history = list(history or [])
        history.append({"role": "user", "content": user_message.strip()})
        # Trim to last CHAT_HISTORY_LIMIT turns (1 turn = user + assistant)
        if len(history) > CHAT_HISTORY_LIMIT * 2:
            history = history[-(CHAT_HISTORY_LIMIT * 2):]

        system_prompt = _build_chat_system_prompt()
        api_messages  = [{"role": m["role"], "content": m["content"]} for m in history]

        threading.Thread(
            target=_run_chat_bg,
            args=(system_prompt, api_messages),
            daemon=True,
        ).start()

        messages_ui = _render_chat_messages(history, thinking=True)
        return history, False, messages_ui, ""  # clear input, enable poll

    @app.callback(
        Output("brain-chat-history", "data", allow_duplicate=True),
        Output("brain-chat-poll", "disabled", allow_duplicate=True),
        Output("brain-chat-messages", "children", allow_duplicate=True),
        Input("brain-chat-poll", "n_intervals"),
        State("brain-chat-history", "data"),
        prevent_initial_call=True,
    )
    def poll_chat_reply(_n, history):
        if _chat_state["running"]:
            return no_update, False, _render_chat_messages(history or [], thinking=True)

        if _chat_state["reply"] is None and _chat_state["error"] is None:
            return no_update, True, no_update  # nothing in flight — disable poll

        history = list(history or [])
        if _chat_state["reply"]:
            history.append({"role": "assistant", "content": _chat_state["reply"]})

        error_msg = _chat_state["error"]
        _chat_state["reply"] = None
        _chat_state["error"] = None

        messages_ui = _render_chat_messages(history)
        if error_msg:
            messages_ui.append(html.Div(
                f"⚠ Error: {error_msg}",
                style={
                    "fontSize": "0.72rem", "color": "#ff6b6b",
                    "textAlign": "center", "marginTop": "6px",
                },
            ))
        return history, True, messages_ui  # disable poll

    @app.callback(
        Output("brain-chat-history", "data", allow_duplicate=True),
        Output("brain-chat-input", "value", allow_duplicate=True),
        Output("brain-chat-messages", "children", allow_duplicate=True),
        Input("brain-chat-clear", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_chat(_n):
        return [], "", []

    @app.callback(
        Output("brain-rec-skip", "data"),
        Input("brain-rec-prev-btn", "n_clicks"),
        Input("brain-rec-next-btn", "n_clicks"),
        State("brain-rec-skip", "data"),
        prevent_initial_call=True,
    )
    def navigate_recommendation(_prev, _next, skip):
        from dash import ctx
        current = skip or 0
        if ctx.triggered_id == "brain-rec-prev-btn":
            return current - 1
        return current + 1

    @app.callback(
        Output("risks-content", "children"),
        Input("risks-interval", "n_intervals"),
        Input("scan-complete", "data"),
        State("brain-risk-idx", "data"),
    )
    def refresh_risks(_n, _scan, risk_idx):
        return risks_layout(risk_idx=risk_idx or -1)
