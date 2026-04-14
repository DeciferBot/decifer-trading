"""
Status strip — a compact health bar shown below the Chief Decifer header.
Answers four questions at a glance:
  · When was the last code commit?
  · Are the tests passing right now?
  · How many features have shipped?
  · When did we last have a Cowork session?

All reads are lightweight and cached so they don't slow down page load.
"""

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dash import html, dcc, Input, Output
import dash_bootstrap_components as dbc
from config import DECIFER_REPO_PATH, SESSIONS_DIR, SPECS_DIR, BACKLOG_FILE


# ── Data readers ──────────────────────────────────────────────────────────────

def _last_commit_age():
    """Return (age_string, is_stale) where stale = > 5 days."""
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return "no repo", True
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI"],
            cwd=DECIFER_REPO_PATH, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return "no commits", True
        dt = datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        delta = now - dt
        if delta < timedelta(hours=1):
            return f"{int(delta.total_seconds() / 60)}m ago", False
        elif delta < timedelta(days=1):
            return f"{int(delta.total_seconds() / 3600)}h ago", False
        elif delta < timedelta(days=5):
            return f"{delta.days}d ago", False
        else:
            return f"{delta.days}d ago", True
    except Exception:
        return "unknown", True


def _test_data():
    """Run pytest once and return a dict with all the numbers.
    Used for both the status pill display and the health score."""
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / "tests").exists():
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "pct": None,
                "label": "no tests", "color": "#868e96", "ok": False, "available": False}
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "--tb=no", "-q", "--no-header"],
            cwd=DECIFER_REPO_PATH, capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        passed = failed = errors = 0
        summary = re.search(r"=+\s*(.*?)\s*=+\s*$", output, re.MULTILINE)
        if summary:
            s = summary.group(1)
            for match in re.finditer(r"(\d+)\s+(passed|failed|error)", s):
                n, kind = int(match.group(1)), match.group(2)
                if kind == "passed":  passed = n
                elif kind == "failed": failed = n
                elif kind == "error":  errors = n
        collection_errors = len(re.findall(r"ERROR collecting", output))
        if collection_errors > 0 and errors == 0:
            errors = collection_errors
        total = passed + failed
        pct   = int(passed / total * 100) if total > 0 else None
        if errors > 0 and total == 0:
            label = f"{errors} import error{'s' if errors > 1 else ''}"
            color = "#ffd43b"
            ok    = False
        elif failed > 0 or errors > 0:
            label = f"{passed}/{total} passing ({pct}%)"
            color = "#ff6b6b" if (pct or 0) < 60 else "#ffd43b"
            ok    = False
        else:
            label = f"{passed} passing"
            color = "#51cf66"
            ok    = True
        return {"passed": passed, "failed": failed, "errors": errors, "total": total,
                "pct": pct, "label": label, "color": color, "ok": ok, "available": True}
    except Exception:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "pct": None,
                "label": "unknown", "color": "#868e96", "ok": False, "available": True}


def _test_status():
    """Backward-compatible wrapper — returns (label, color, is_ok)."""
    d = _test_data()
    return d["label"], d["color"], d["ok"]


def _last_commit_days():
    """Return the number of days since the last commit, or None."""
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI"],
            cwd=DECIFER_REPO_PATH, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        dt = datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
        return (datetime.now(tz=timezone.utc) - dt).days
    except Exception:
        return None


def _last_session_days():
    """Return the number of days since the last logged session, or None."""
    if not SESSIONS_DIR.exists():
        return None
    files = sorted(SESSIONS_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        data = json.loads(files[0].read_text())
        date_str = data.get("date", "")
        if not date_str:
            return None
        dt = datetime.fromisoformat(date_str.split("T")[0])
        return (datetime.now() - dt).days
    except Exception:
        return None


def _pipeline_state():
    """Return a dict describing current pipeline momentum."""
    seen = set()
    specs = []
    for src in [SPECS_DIR]:
        if src and src.exists():
            for f in src.glob("*.json"):
                try:
                    d = json.loads(f.read_text())
                    sid = d.get("id", f.stem)
                    if sid not in seen:
                        seen.add(sid)
                        specs.append(d)
                except Exception:
                    pass
    if BACKLOG_FILE.exists():
        try:
            items = json.loads(BACKLOG_FILE.read_text())
            if isinstance(items, list):
                for d in items:
                    sid = d.get("id", "")
                    if sid and sid not in seen:
                        seen.add(sid)
                        specs.append(d)
        except Exception:
            pass
    in_progress = any(s.get("status") == "in_progress" for s in specs)
    ready       = any(s.get("status") == "spec_complete" for s in specs)
    # Days since last shipped feature
    shipped_dates = []
    for s in specs:
        if s.get("status") == "complete" and s.get("completed_date"):
            try:
                shipped_dates.append(datetime.fromisoformat(s["completed_date"].split("T")[0]))
            except Exception:
                pass
    days_since_shipped = None
    if shipped_dates:
        latest = max(shipped_dates)
        days_since_shipped = (datetime.now() - latest).days
    return {
        "in_progress": in_progress,
        "ready": ready,
        "days_since_shipped": days_since_shipped,
        "total_shipped": sum(1 for s in specs if s.get("status") == "complete"),
    }


# ── Health Score ───────────────────────────────────────────────────────────────
#
# Scored entirely from live, measured data. No guesses.
# Each component is clearly defined and capped.
#
#  Tests (0–40)     — pass rate from running pytest
#  Activity (0–25)  — days since last git commit
#  Pipeline (0–20)  — shipping momentum from spec files
#  Sessions (0–15)  — days since last logged Cowork session
#  Total (0–100)    — grade: A≥90, B≥75, C≥60, D≥40, F<40

def _compute_health_score(tests, commit_days, pipeline, session_days):
    breakdown = {}

    # ── Test score (0-40) ────────────────────────────────────────────────
    if not tests["available"]:
        t_score = 0
        t_note  = "Repo not found"
    elif tests["errors"] > 0 and tests["total"] == 0:
        t_score = 0
        t_note  = "Tests can't load"
    elif tests["pct"] is None:
        t_score = 0
        t_note  = "No tests run"
    elif tests["ok"]:
        t_score = 40
        t_note  = f"All {tests['passed']} tests pass"
    elif tests["pct"] >= 95:
        t_score = 35
        t_note  = f"{tests['pct']}% passing"
    elif tests["pct"] >= 80:
        t_score = 25
        t_note  = f"{tests['pct']}% passing"
    elif tests["pct"] >= 60:
        t_score = 15
        t_note  = f"{tests['pct']}% passing"
    else:
        t_score = 5
        t_note  = f"{tests['pct']}% passing"
    breakdown["Tests"] = (t_score, 40, t_note)

    # ── Activity score (0-25) ────────────────────────────────────────────
    if commit_days is None:
        a_score = 0
        a_note  = "No repo connected"
    elif commit_days <= 2:
        a_score = 25
        a_note  = f"Commit {commit_days}d ago"
    elif commit_days <= 7:
        a_score = 20
        a_note  = f"Commit {commit_days}d ago"
    elif commit_days <= 14:
        a_score = 12
        a_note  = f"Commit {commit_days}d ago"
    elif commit_days <= 30:
        a_score = 5
        a_note  = f"Commit {commit_days}d ago"
    else:
        a_score = 0
        a_note  = f"No commits in {commit_days}d"
    breakdown["Activity"] = (a_score, 25, a_note)

    # ── Pipeline score (0-20) ────────────────────────────────────────────
    dsf = pipeline.get("days_since_shipped")
    if pipeline["in_progress"]:
        p_score = 20
        p_note  = "Feature in progress"
    elif pipeline["ready"]:
        p_score = 15
        p_note  = "Feature ready to build"
    elif dsf is not None and dsf <= 14:
        p_score = 18
        p_note  = f"Shipped {dsf}d ago"
    elif dsf is not None and dsf <= 30:
        p_score = 10
        p_note  = f"Shipped {dsf}d ago"
    elif pipeline["total_shipped"] > 0:
        p_score = 5
        p_note  = f"{pipeline['total_shipped']} features complete"
    else:
        p_score = 0
        p_note  = "Nothing shipped yet"
    breakdown["Pipeline"] = (p_score, 20, p_note)

    # ── Session score (0-15) ─────────────────────────────────────────────
    if session_days is None:
        s_score = 0
        s_note  = "No sessions logged"
    elif session_days == 0:
        s_score = 15
        s_note  = "Session today"
    elif session_days <= 3:
        s_score = 12
        s_note  = f"Session {session_days}d ago"
    elif session_days <= 7:
        s_score = 8
        s_note  = f"Session {session_days}d ago"
    elif session_days <= 14:
        s_score = 3
        s_note  = f"Session {session_days}d ago"
    else:
        s_score = 0
        s_note  = f"No session in {session_days}d"
    breakdown["Sessions"] = (s_score, 15, s_note)

    total = t_score + a_score + p_score + s_score
    if total >= 90:
        grade, grade_color = "A", "#51cf66"
    elif total >= 75:
        grade, grade_color = "B", "#a9e34b"
    elif total >= 60:
        grade, grade_color = "C", "#ffd43b"
    elif total >= 40:
        grade, grade_color = "D", "#ff922b"
    else:
        grade, grade_color = "F", "#ff6b6b"

    return {"score": total, "grade": grade, "grade_color": grade_color, "breakdown": breakdown}


def _render_score_pill(health):
    score      = health["score"]
    grade      = health["grade"]
    color      = health["grade_color"]
    breakdown  = health["breakdown"]

    tooltip_lines = []
    for name, (pts, cap, note) in breakdown.items():
        bar = "█" * int(pts / cap * 8) + "░" * (8 - int(pts / cap * 8))
        tooltip_lines.append(f"{name}: {pts}/{cap}  {bar}  {note}")

    return html.Div([
        html.Span("HEALTH", style={
            "fontSize": "0.55rem", "color": "var(--cd-faint)",
            "textTransform": "uppercase", "letterSpacing": "0.4px",
            "marginRight": "6px",
        }),
        html.Span(str(score), style={
            "fontSize": "0.8rem", "fontWeight": "800", "color": color,
        }),
        html.Span(f" / 100", style={
            "fontSize": "0.6rem", "color": "var(--cd-faint)", "marginRight": "4px",
        }),
        html.Span(grade, style={
            "fontSize": "0.68rem", "fontWeight": "800",
            "padding": "2px 7px", "borderRadius": "5px",
            "backgroundColor": f"{color}25", "color": color,
            "border": f"1px solid {color}50",
        }),
    ], title="\n".join(tooltip_lines), style={
        "display": "flex", "alignItems": "center",
        "padding": "4px 12px",
        "backgroundColor": "var(--cd-deep)",
        "borderRadius": "20px",
        "border": f"1px solid {color}40",
        "cursor": "default",
    })


def _features_shipped():
    """Count specs with status == 'complete'."""
    total = shipped = 0
    seen = set()
    for src in [SPECS_DIR, None]:
        if src and src.exists():
            for f in src.glob("*.json"):
                try:
                    d = json.loads(f.read_text())
                    sid = d.get("id", f.stem)
                    if sid not in seen:
                        seen.add(sid)
                        total += 1
                        if d.get("status") == "complete":
                            shipped += 1
                except Exception:
                    pass
    if BACKLOG_FILE.exists():
        try:
            items = json.loads(BACKLOG_FILE.read_text())
            if isinstance(items, list):
                for d in items:
                    sid = d.get("id", "")
                    if sid and sid not in seen:
                        seen.add(sid)
                        total += 1
                        if d.get("status") == "complete":
                            shipped += 1
        except Exception:
            pass
    return shipped, total


def _last_session_age():
    """Return (age_string, is_stale) where stale = > 7 days."""
    if not SESSIONS_DIR.exists():
        return "no sessions", True
    files = sorted(SESSIONS_DIR.glob("*.json"), reverse=True)
    if not files:
        return "no sessions", True
    try:
        data = json.loads(files[0].read_text())
        date_str = data.get("date", "")
        if not date_str:
            return "unknown", True
        # date_str might be "2026-03-28" or ISO format
        dt = datetime.fromisoformat(date_str.split("T")[0])
        now = datetime.now()
        delta = now - dt
        if delta.days == 0:
            return "today", False
        elif delta.days == 1:
            return "yesterday", False
        elif delta.days < 7:
            return f"{delta.days}d ago", False
        else:
            return f"{delta.days}d ago", True
    except Exception:
        return "unknown", True


# ── Pill renderer ─────────────────────────────────────────────────────────────

def _pill(icon, label, value, value_color, is_stale=False):
    stale_dot = html.Span("⚠", style={
        "fontSize": "0.55rem", "color": "#ffd43b", "marginLeft": "4px",
    }) if is_stale else None

    return html.Div([
        html.Span(icon, style={"fontSize": "0.7rem", "marginRight": "5px", "opacity": "0.6"}),
        html.Span(label, style={"fontSize": "0.6rem", "color": "var(--cd-faint)",
                                "textTransform": "uppercase", "letterSpacing": "0.4px",
                                "marginRight": "5px"}),
        html.Span(value, style={"fontSize": "0.65rem", "fontWeight": 700, "color": value_color}),
        stale_dot,
    ], style={
        "display": "flex", "alignItems": "center",
        "padding": "4px 12px",
        "backgroundColor": "var(--cd-deep)",
        "borderRadius": "20px",
        "border": "1px solid var(--cd-border-sub)",
    })


# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    commit_age, commit_stale   = _last_commit_age()
    tests                      = _test_data()
    shipped, total             = _features_shipped()
    session_age, session_stale = _last_session_age()

    # Gather raw numbers for health score
    commit_days  = _last_commit_days()
    session_days = _last_session_days()
    pipeline     = _pipeline_state()
    health       = _compute_health_score(tests, commit_days, pipeline, session_days)

    shipped_str   = f"{shipped}/{total}" if total > 0 else "—"
    shipped_color = "#51cf66" if shipped > 0 else "#868e96"

    stale_banner = None
    if commit_stale and session_stale:
        stale_banner = html.Div([
            html.Span("⚠ ", style={"color": "#ffd43b"}),
            html.Span(
                "No commits or sessions in over 5 days. "
                "The dashboard data may not reflect the current state of the bot.",
                style={"fontSize": "0.7rem", "color": "var(--cd-text2)"},
            ),
        ], style={
            "backgroundColor": "var(--cd-warn-bg)",
            "border": "1px solid rgba(255,212,59,0.3)",
            "borderRadius": "6px",
            "padding": "8px 14px",
            "marginTop": "8px",
        })

    return html.Div([
        html.Div([
            _render_score_pill(health),
            html.Div(style={
                "width": "1px", "height": "20px",
                "backgroundColor": "var(--cd-border)", "margin": "0 4px",
            }),
            _pill("⚡", "Last commit",  commit_age,          "#e9ecef" if not commit_stale else "#ffd43b", commit_stale),
            _pill("🧪", "Tests",        tests["label"],       tests["color"]),
            _pill("✅", "Shipped",      shipped_str,          shipped_color),
            _pill("💬", "Last session", session_age,          "#e9ecef" if not session_stale else "#ffd43b", session_stale),
        ], style={
            "display": "flex", "gap": "8px", "flexWrap": "wrap",
            "alignItems": "center", "paddingBottom": "10px",
        }),
        stale_banner,
    ], id="status-strip-content")


def register_callbacks(app):
    @app.callback(
        Output("status-strip-content", "children"),
        Input("global-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _scan):
        return layout().children
