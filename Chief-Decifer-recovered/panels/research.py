"""
Research panel — shows research documents as readable reports.
Each card shows the synthesis, source, and quick wins. Findings are
expandable: clicking a finding reveals its full summary, what changes,
why it matters, source evidence with credibility tags, and a computed
impact score (1–10). Findings are auto-synced to the Proposals column.
"""

import difflib
import hashlib
import json
import logging
import re
from pathlib import Path
from datetime import datetime
from dash import html, dcc, Input, Output, MATCH
import dash_bootstrap_components as dbc
from config import RESEARCH_DIR, SPECS_DIR

# ── Tier → roadmap phase mapping ──────────────────────────────────────────────

_PHASE_HINTS = {
    "consensus":       ("A", "P0"),
    "skew":            ("A", "P1"),
    "short":           ("A", "P1"),
    "scanner":         ("A", "P1"),
    "direction":       ("B", "P1"),
    "mean reversion":  ("B", "P1"),
    "reversion":       ("B", "P1"),
    "regime":          ("D", "P2"),
    "hmm":             ("D", "P2"),
    "calibrat":        ("D", "P2"),
    "ic":              ("C", "P2"),
    "validation":      ("C", "P2"),
    "signal":          ("B", "P1"),
    "alpha":           ("B", "P1"),
}

def _phase_for_feature(title: str):
    lower = title.lower()
    for keyword, (phase, priority) in _PHASE_HINTS.items():
        if keyword in lower:
            return phase, priority
    return "B", "P2"


def _spec_id_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
    suffix = hashlib.md5(title.encode()).hexdigest()[:6]
    return f"research-{slug}-{suffix}"


# ── Impact scoring ─────────────────────────────────────────────────────────────

def _compute_impact_score(finding: dict) -> dict:
    """
    Derive an impact score 1–10 for this finding.

    Methodology:
      - Parse expected_impact + why_it_matters for quantified improvements
        (win rate %, alpha %, Sharpe, x-return multiplier)
      - Tier 1 = +1 bonus
      - Difficulty: Easy = full score, Medium = ×0.9, Hard = ×0.75
        (captures ROI — Easy changes with big impact are highest priority)
      - Subsystem: Signal Generation scores highest for win rate

    Returns {"score": int, "label": str, "color": str, "reasoning": str}
    """
    impact_text = (
        finding.get("expected_impact", "") + " " +
        finding.get("why_it_matters", "")
    ).lower()

    tier       = finding.get("tier", 2)
    difficulty = finding.get("difficulty", "Medium")
    subsystem  = finding.get("subsystem", "")

    base = 3
    reasoning_parts = []

    # ── x-return multiplier (highest signal) ──
    x_match = re.search(r"(\d+(?:\.\d+)?)x\s+return", impact_text)
    if x_match:
        val = float(x_match.group(1))
        base = min(10, int(5 + val))
        reasoning_parts.append(f"Empirical {val}x return improvement cited")

    # ── Win rate % ──
    elif re.search(r"win\s+rate", impact_text):
        pct = re.search(r"\+(\d+)[–\-—](\d+)%", impact_text)
        if pct:
            avg = (int(pct.group(1)) + int(pct.group(2))) / 2
            base = min(10, int(4 + avg * 1.5))
            reasoning_parts.append(f"Directly targets win rate (+{pct.group(1)}–{pct.group(2)}%)")
        else:
            base = 7
            reasoning_parts.append("Win rate improvement referenced")

    # ── Sharpe ratio ──
    elif re.search(r"sharpe", impact_text):
        sh = re.search(r"(\d+\.\d+)", impact_text)
        if sh:
            val = float(sh.group(1))
            base = min(10, int(4 + val * 3))
            reasoning_parts.append(f"Sharpe ratio improvement of {val} cited")
        else:
            base = 6
            reasoning_parts.append("Sharpe ratio improvement referenced")

    # ── Annualised alpha % ──
    elif re.search(r"annuali|alpha", impact_text):
        pct = re.search(r"\+(\d+)[–\-—](\d+)%", impact_text)
        if pct:
            avg = (int(pct.group(1)) + int(pct.group(2))) / 2
            base = min(10, int(3 + avg * 0.8))
            reasoning_parts.append(f"Quantified alpha improvement (+{pct.group(1)}–{pct.group(2)}%)")
        else:
            base = 5
            reasoning_parts.append("Alpha improvement referenced")

    # ── Generic % improvement ──
    else:
        pct = re.search(r"\+(\d+)[–\-—](\d+)%", impact_text)
        if pct:
            avg = (int(pct.group(1)) + int(pct.group(2))) / 2
            base = min(10, int(3 + avg * 0.5))
            reasoning_parts.append(f"+{pct.group(1)}–{pct.group(2)}% improvement cited")
        elif re.search(r"\d+%", impact_text):
            base = 4
            reasoning_parts.append("Percentage improvement referenced")

    # ── Tier bonus ──
    if tier == 1:
        base = min(10, base + 1)
        reasoning_parts.append("Tier 1 priority")

    # ── Difficulty ROI adjustment ──
    if difficulty == "Easy":
        multiplier = 1.0
        reasoning_parts.append("Easy to implement (high ROI)")
    elif difficulty == "Hard":
        multiplier = 0.75
        reasoning_parts.append("Hard implementation reduces near-term ROI")
    else:
        multiplier = 0.90

    # ── Subsystem relevance ──
    if subsystem in ("Signal Generation",):
        reasoning_parts.append("Core signal engine — direct win-rate path")
    elif subsystem in ("Risk & Portfolio",):
        reasoning_parts.append("Risk management — drawdown / Sharpe path")

    score = max(1, min(10, round(base * multiplier)))

    if score >= 8:
        label, color = "High Impact", "#51cf66"
    elif score >= 5:
        label, color = "Moderate Impact", "#ffd43b"
    else:
        label, color = "Low Impact", "#868e96"

    return {
        "score":     score,
        "label":     label,
        "color":     color,
        "reasoning": "; ".join(reasoning_parts) if reasoning_parts else "Based on stated expected impact",
    }


# ── Source credibility ─────────────────────────────────────────────────────────

def _source_credibility_tags(source_evidence: str) -> list:
    """
    Parse source_evidence string → list of (display_text, tag, color).
    Tags: Peer-Reviewed / Preprint, Academic, Practitioner, Source
    """
    if not source_evidence:
        return []
    tags = []
    for src in re.split(r";|\n", source_evidence):
        src = src.strip()
        if not src:
            continue
        sl = src.lower()
        if any(x in sl for x in [
            "arxiv", "sciencedirect", "journal", "ssrn", "nature",
            "wiley", "springer", "elsevier", "pubmed", "ieee",
        ]):
            tag, color = "Peer-Reviewed / Preprint", "#51cf66"
        elif any(x in sl for x in [
            "cfa", "university", "study", "paper", "academic",
        ]):
            tag, color = "Academic", "#74c0fc"
        elif any(x in sl for x in [
            ".com", "blog", "practitioner", "backtest",
            "maven", "microalpha", "priceaction",
        ]):
            tag, color = "Practitioner", "#ffd43b"
        else:
            tag, color = "Source", "#868e96"
        display = src[:90] + ("…" if len(src) > 90 else "")
        tags.append((display, tag, color))
    return tags


# ── Finding card (accordion item) ─────────────────────────────────────────────

def _render_finding_row(finding: dict) -> dbc.AccordionItem:
    title           = finding.get("feature", finding.get("title", "Untitled"))
    impact          = _compute_impact_score(finding)
    difficulty      = finding.get("difficulty", "")
    dev_days        = finding.get("dev_days", "")
    expected_impact = finding.get("expected_impact", "")
    summary         = finding.get("summary", "")
    what_changes    = finding.get("what_changes", "")
    why_it_matters  = finding.get("why_it_matters", "")
    source_evidence = finding.get("source_evidence", "")
    module          = finding.get("module", "")
    subsystem       = finding.get("subsystem", "")

    # ── Accordion header ──
    header = html.Div([
        html.Div([
            # Impact score circle
            html.Span(str(impact["score"]), style={
                "display":         "inline-flex",
                "alignItems":      "center",
                "justifyContent":  "center",
                "width":           "26px",
                "height":          "26px",
                "borderRadius":    "50%",
                "backgroundColor": impact["color"] + "25",
                "border":          f"2px solid {impact['color']}",
                "color":           impact["color"],
                "fontSize":        "0.7rem",
                "fontWeight":      "800",
                "marginRight":     "10px",
                "flexShrink":      "0",
            }),
            html.Span(title, style={
                "fontWeight": "600", "fontSize": "0.85rem",
                "color": "var(--cd-text)",
            }),
        ], className="d-flex align-items-center"),
        html.Div([
            dbc.Badge(impact["label"], style={
                "fontSize": "0.58rem",
                "backgroundColor": impact["color"] + "20",
                "color": impact["color"], "marginRight": "5px",
            }),
            dbc.Badge(difficulty, style={
                "fontSize": "0.58rem",
                "backgroundColor": "#74c0fc20",
                "color": "#74c0fc", "marginRight": "5px",
            }) if difficulty else None,
            dbc.Badge(f"{dev_days}d", style={
                "fontSize": "0.58rem",
                "backgroundColor": "#868e9620",
                "color": "#868e96",
            }) if dev_days else None,
        ], className="d-flex align-items-center flex-wrap mt-1"),
    ])

    # ── Body sections ──
    sections = []

    # Impact score block
    sections.append(html.Div([
        html.Div("Expected Impact", style={
            "fontSize": "0.68rem", "color": "var(--cd-muted)",
            "fontWeight": "700", "textTransform": "uppercase",
            "letterSpacing": "0.05em", "marginBottom": "4px",
        }),
        html.Div(expected_impact or "—", style={
            "fontSize": "0.83rem", "color": "#51cf66", "fontWeight": "600",
        }),
        html.Div(
            f"Impact score {impact['score']}/10 — {impact['reasoning']}",
            style={"fontSize": "0.7rem", "color": "var(--cd-muted)", "marginTop": "3px"},
        ),
    ], style={
        "padding": "10px 14px", "borderRadius": "6px",
        "backgroundColor": "var(--cd-deep)",
        "border": f"1px solid {impact['color']}35",
        "marginBottom": "14px",
    }))

    # What is this?
    if summary:
        sections.append(html.Div([
            html.Div("What is this?", style={
                "fontSize": "0.68rem", "color": "var(--cd-muted)",
                "fontWeight": "700", "textTransform": "uppercase",
                "letterSpacing": "0.05em", "marginBottom": "5px",
            }),
            html.Div(summary, style={
                "fontSize": "0.82rem", "color": "var(--cd-text2)", "lineHeight": "1.75",
            }),
        ], style={"marginBottom": "14px"}))

    # What changes in the bot?
    if what_changes:
        sections.append(html.Div([
            html.Div("What changes in the bot?", style={
                "fontSize": "0.68rem", "color": "var(--cd-muted)",
                "fontWeight": "700", "textTransform": "uppercase",
                "letterSpacing": "0.05em", "marginBottom": "5px",
            }),
            html.Div(what_changes, style={
                "fontSize": "0.82rem", "color": "var(--cd-text2)", "lineHeight": "1.75",
            }),
        ], style={"marginBottom": "14px"}))

    # Why it matters
    if why_it_matters:
        sections.append(html.Div([
            html.Div("Why it matters", style={
                "fontSize": "0.68rem", "color": "var(--cd-muted)",
                "fontWeight": "700", "textTransform": "uppercase",
                "letterSpacing": "0.05em", "marginBottom": "5px",
            }),
            html.Div(why_it_matters, style={
                "fontSize": "0.82rem", "color": "var(--cd-text2)", "lineHeight": "1.75",
            }),
        ], style={"marginBottom": "14px"}))

    # Sources with credibility badges
    source_tags = _source_credibility_tags(source_evidence)
    if source_tags:
        source_rows = [
            html.Div([
                dbc.Badge(tag, style={
                    "fontSize": "0.58rem",
                    "backgroundColor": color + "20",
                    "color": color,
                    "marginRight": "7px",
                    "flexShrink": "0",
                    "alignSelf": "flex-start",
                    "marginTop": "2px",
                }),
                html.Span(display, style={
                    "fontSize": "0.75rem", "color": "var(--cd-text2)",
                    "lineHeight": "1.5",
                }),
            ], className="d-flex align-items-start mb-2")
            for display, tag, color in source_tags
        ]
        sections.append(html.Div([
            html.Div("Sources", style={
                "fontSize": "0.68rem", "color": "var(--cd-muted)",
                "fontWeight": "700", "textTransform": "uppercase",
                "letterSpacing": "0.05em", "marginBottom": "7px",
            }),
            *source_rows,
        ], style={
            "padding": "10px 14px", "borderRadius": "6px",
            "backgroundColor": "var(--cd-deep)",
            "border": "1px solid var(--cd-border)",
            "marginBottom": "6px",
        }))
    elif source_evidence:
        # raw string fallback if parsing found nothing
        sections.append(html.Div([
            html.Div("Sources", style={
                "fontSize": "0.68rem", "color": "var(--cd-muted)",
                "fontWeight": "700", "textTransform": "uppercase",
                "letterSpacing": "0.05em", "marginBottom": "5px",
            }),
            html.Div(source_evidence, style={
                "fontSize": "0.75rem", "color": "var(--cd-text2)", "lineHeight": "1.6",
            }),
        ], style={"marginBottom": "6px"}))
    else:
        sections.append(html.Div(
            dbc.Badge("No source evidence recorded", style={
                "fontSize": "0.58rem", "backgroundColor": "#fa525220",
                "color": "#fa5252",
            }),
            style={"marginBottom": "6px"},
        ))

    # Module / subsystem meta
    meta = []
    if module:
        meta.append(dbc.Badge(module, style={
            "fontSize": "0.58rem", "backgroundColor": "#ff6b0020",
            "color": "#ff6b00", "marginRight": "4px",
        }))
    if subsystem:
        meta.append(dbc.Badge(subsystem, style={
            "fontSize": "0.58rem", "backgroundColor": "#da77f220",
            "color": "#da77f2",
        }))
    if meta:
        sections.append(html.Div(meta, className="d-flex flex-wrap mt-1"))

    return dbc.AccordionItem(
        html.Div(sections, style={"padding": "6px 2px"}),
        title=header,
    )


# ── Auto-sync findings → Proposals ───────────────────────────────────────────

def _sync_findings_to_proposals(reports: list) -> dict[str, int]:
    """
    For each finding, create a spec file with status='spec_complete'
    if one doesn't already exist. Includes impact_score in spec.
    Returns {report_topic: count_created}.
    """
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {f.stem for f in SPECS_DIR.glob("*.json")}
    created: dict[str, int] = {}

    for report in reports:
        topic    = report.get("topic", "Research")
        findings = report.get("findings", [])
        count    = 0
        seen_titles_this_report: list[str] = []
        for finding in findings:
            title = finding.get("feature", finding.get("title", "")).strip()
            if not title:
                logging.warning(
                    "research._sync_findings_to_proposals: empty/whitespace title in "
                    "report '%s' — finding skipped", topic
                )
                continue

            # Fuzzy intra-report dedup — skip near-duplicates within same report
            if any(
                difflib.SequenceMatcher(None, title.lower(), s.lower()).ratio() >= 0.85
                for s in seen_titles_this_report
            ):
                continue
            seen_titles_this_report.append(title)

            spec_id = _spec_id_from_title(title)
            if spec_id in existing_ids:
                continue

            # Backward-compat guard: check old ID format (without hash suffix)
            old_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
            old_id = f"research-{old_slug}"
            if old_id in existing_ids:
                existing_ids.add(spec_id)   # prevent re-creation under new format
                continue
            phase, priority = _phase_for_feature(title)
            impact = _compute_impact_score(finding)
            spec = {
                "id":              spec_id,
                "title":           title,
                "status":          "spec_complete",
                "phase":           phase,
                "priority":        priority,
                "summary":         finding.get("summary", ""),
                "source":          "research",
                "research_topic":  topic,
                "impact_score":    impact["score"],
                "impact_label":    impact["label"],
                "expected_impact": finding.get("expected_impact", ""),
                "difficulty":      finding.get("difficulty", ""),
                "dev_days":        finding.get("dev_days", ""),
                "created_at":      datetime.utcnow().isoformat() + "Z",
            }
            spec_path = SPECS_DIR / f"{spec_id}.json"
            spec_path.write_text(json.dumps(spec, indent=2))
            existing_ids.add(spec_id)
            count += 1
        if count:
            created[topic] = count

    return created


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_research():
    if not RESEARCH_DIR.exists():
        return []
    files = sorted(RESEARCH_DIR.glob("*.json"), reverse=True)
    reports = []
    for f in files[:10]:
        try:
            reports.append(json.loads(f.read_text()))
        except Exception:
            pass
    return reports


# ── Card renderer ─────────────────────────────────────────────────────────────

def _render_research_card(report: dict, idx: int):
    topic      = report.get("topic", "Research")
    date       = report.get("date", "")
    source     = report.get("source", "")
    synthesis  = report.get("synthesis", "")
    quick_wins = report.get("top_3_quick_wins", [])
    findings   = report.get("findings", [])

    # Short preview of synthesis
    preview_cutoff = 280
    is_long        = len(synthesis) > preview_cutoff
    preview_text   = synthesis[:preview_cutoff] + ("…" if is_long else "")

    # Source badge
    sl = source.lower()
    if "web" in sl:
        source_badge = dbc.Badge("Web Research", style={
            "fontSize": "0.6rem", "backgroundColor": "#4dabf720",
            "color": "#4dabf7", "fontWeight": "600",
        })
    elif "autonomous" in sl:
        source_badge = dbc.Badge("Autonomous Research", style={
            "fontSize": "0.6rem", "backgroundColor": "#51cf6620",
            "color": "#51cf66", "fontWeight": "600",
        })
    else:
        source_badge = dbc.Badge(source[:40] if source else "Research", style={
            "fontSize": "0.6rem", "backgroundColor": "#74c0fc20",
            "color": "#74c0fc", "fontWeight": "600",
        }) if source else None

    # Quick wins
    quick_wins_section = None
    if quick_wins:
        quick_wins_section = html.Div([
            html.Div([
                html.Span("🎯 ", style={"marginRight": "4px"}),
                html.Span("Quick Wins", style={
                    "fontWeight": "700", "fontSize": "0.8rem", "color": "#51cf66",
                }),
            ], className="mb-2"),
            html.Ul([
                html.Li(w, style={
                    "fontSize": "0.78rem", "color": "var(--cd-text2)",
                    "lineHeight": "1.6", "marginBottom": "2px",
                }) for w in quick_wins
            ], style={"paddingLeft": "18px", "marginBottom": "0"}),
        ], style={
            "backgroundColor": "var(--cd-deep)", "borderRadius": "8px",
            "padding": "12px 16px", "border": "1px solid #51cf6630",
            "marginTop": "12px",
        })

    # Full synthesis toggle
    full_synthesis = html.Div(
        html.Div(synthesis, style={
            "fontSize": "0.82rem", "color": "var(--cd-text2)", "lineHeight": "1.7",
        }),
        id={"type": "research-full", "index": idx},
        style={"display": "none", "marginTop": "6px"},
    )
    read_more_btn = html.Span(
        "▶ Read more",
        id={"type": "research-toggle", "index": idx},
        n_clicks=0,
        style={
            "color": "#4dabf7", "fontSize": "0.72rem", "cursor": "pointer",
            "fontWeight": "600", "userSelect": "none",
        },
    ) if is_long else None

    synthesis_section = html.Div([
        html.Div(preview_text, style={
            "fontSize": "0.82rem", "color": "var(--cd-text2)", "lineHeight": "1.7",
        }),
        full_synthesis,
        html.Div(read_more_btn, className="mt-1") if read_more_btn else None,
    ])

    # Findings accordion
    findings_section = None
    if findings:
        findings_section = html.Div([
            html.Div([
                html.Span("📋 ", style={"marginRight": "4px"}),
                html.Span(
                    f"Findings ({len(findings)})",
                    style={"fontWeight": "700", "fontSize": "0.8rem", "color": "var(--cd-text)"},
                ),
                html.Small(
                    " — click any finding to read the full breakdown, sources & impact score",
                    style={"color": "var(--cd-muted)", "fontSize": "0.7rem", "marginLeft": "6px"},
                ),
            ], className="mb-3"),
            dbc.Accordion(
                [_render_finding_row(f) for f in findings],
                start_collapsed=True,
                flush=True,
                style={"borderRadius": "6px", "overflow": "hidden"},
            ),
        ], style={
            "marginTop": "18px",
            "borderTop": "1px solid var(--cd-border)",
            "paddingTop": "16px",
        })

    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.H5(topic, className="mb-0", style={
                    "fontWeight": "700", "color": "var(--cd-text)", "fontSize": "1rem",
                }),
                source_badge,
            ], className="d-flex align-items-center gap-2 flex-wrap"),
            html.Small(date, style={"color": "var(--cd-muted)", "fontSize": "0.72rem"}) if date else None,
        ], className="mb-3", style={
            "borderBottom": "1px solid var(--cd-border)", "paddingBottom": "12px",
        }),
        synthesis_section,
        quick_wins_section,
        findings_section,
    ], style={
        "backgroundColor": "var(--cd-card)", "borderRadius": "10px",
        "padding": "20px 22px", "border": "1px solid var(--cd-border)",
        "borderLeft": "3px solid #4dabf7", "marginBottom": "18px",
    })


# ── Main layout ───────────────────────────────────────────────────────────────

def layout():
    research = _load_research()

    if not research:
        return html.Div([
            html.H4("Research", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(
                "No research reports yet. When Chief runs a research task, "
                "the findings will appear here.",
                className="text-muted",
            ),
            dcc.Interval(id="research-interval", interval=30_000, n_intervals=0),
        ])

    _sync_findings_to_proposals(research)
    cards = [_render_research_card(r, i) for i, r in enumerate(research)]

    return html.Div([
        html.H4("Research", className="text-light mb-1", style={"fontWeight": "600"}),
        html.P(
            "Research reports from autonomous scans. Click any finding to read "
            "the full breakdown, source evidence, credibility assessment, and "
            "impact score. Findings are automatically added to Proposals.",
            style={"fontSize": "0.8rem", "color": "var(--cd-muted)", "marginBottom": "20px"},
        ),
        *cards,
        dcc.Interval(id="research-interval", interval=30_000, n_intervals=0),
    ])


# ── Callbacks ─────────────────────────────────────────────────────────────────

def register_callbacks(app):
    @app.callback(
        Output("research-content", "children"),
        Input("research-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()

    @app.callback(
        Output({"type": "research-full", "index": MATCH}, "style"),
        Output({"type": "research-toggle", "index": MATCH}, "children"),
        Input({"type": "research-toggle", "index": MATCH}, "n_clicks"),
        prevent_initial_call=True,
    )
    def toggle_full_synthesis(n_clicks):
        if not n_clicks:
            return {"display": "none", "marginTop": "6px"}, "▶ Read more"
        is_open = (n_clicks % 2) == 1
        if is_open:
            return {"display": "block", "marginTop": "6px"}, "▼ Show less"
        return {"display": "none", "marginTop": "6px"}, "▶ Read more"
