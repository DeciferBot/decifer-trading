"""
Updates panel — unified feed of everything that's happened.
Pulls from sessions, specs, research, and git commits into one stream.
Filterable by category. Shipped items surface first. Each card shows a
plain-English bot-impact statement. Click any card for full details.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dash import html, dcc, Input, Output, State, ALL, MATCH, callback_context
import dash_bootstrap_components as dbc
from config import (
    DECIFER_REPO_PATH, SPECS_DIR, SESSIONS_DIR, RESEARCH_DIR,
    BACKLOG_FILE, DOCS_DIR, ACTIVITY_FILE,
    LIFECYCLE_LABELS,
)


# ── Bot-impact inference ──────────────────────────────────────────────────────

FILE_TO_SUBSYSTEM = {
    "bot.py":               "Trading Core",
    "orders.py":            "Trading Core",
    "smart_execution.py":   "Trading Core",
    "signals.py":           "Signal Generation",
    "scanner.py":           "Signal Generation",
    "options_scanner.py":   "Signal Generation",
    "agents.py":            "AI & Learning",
    "sentinel_agents.py":   "AI & Learning",
    "ml_engine.py":         "AI & Learning",
    "learning.py":          "AI & Learning",
    "risk.py":              "Risk & Portfolio",
    "portfolio_optimizer.py": "Risk & Portfolio",
    "news.py":              "News & Sentiment",
    "news_sentinel.py":     "News & Sentiment",
    "social_sentiment.py":  "News & Sentiment",
    # Trading Core
    "bot_trading.py":       "Trading Core",
    "bot_ibkr.py":          "Trading Core",
    "bot_account.py":       "Trading Core",
    "bot_state.py":         "Trading Core",
    "execution_agent.py":   "Trading Core",
    "fill_watcher.py":      "Trading Core",
    "ibkr_streaming.py":    "Trading Core",
    "phase_gate.py":        "Trading Core",
    # Signal Generation
    "signal_pipeline.py":   "Signal Generation",
    "signal_dispatcher.py": "Signal Generation",
    "signal_types.py":      "Signal Generation",
    "alpha_decay.py":       "Signal Generation",
    "ic_calculator.py":     "Signal Generation",
    "ic_validator.py":      "Signal Generation",
    "options.py":           "Signal Generation",
    "theme_tracker.py":     "Signal Generation",
    # Risk & Portfolio
    "portfolio.py":         "Risk & Portfolio",
    "backtester.py":        "Risk & Portfolio",
    "audit_candle_gate.py": "Risk & Portfolio",
    # News & Sentiment
    "catalyst_sentinel.py": "News & Sentiment",
    "telegram_bot.py":      "News & Sentiment",
    "bot_sentinel.py":      "News & Sentiment",
    # AI & Learning
    "data_collector.py":    "AI & Learning",
    # Analytics & UI
    "bot_dashboard.py":     "Analytics & UI",
    "wip_tracker.py":       "Analytics & UI",
}

SUBSYSTEM_IMPACT = {
    "Trading Core":      "changes how the bot routes and executes orders",
    "Signal Generation": "changes how the bot reads setups and entry signals",
    "Risk & Portfolio":  "changes position sizing or portfolio risk limits",
    "AI & Learning":     "changes how the bot adapts and learns from trades",
    "News & Sentiment":  "changes how market context feeds into decisions",
    "Analytics & UI":    "changes the monitoring dashboard or trade journal",
}


def _infer_bot_impact(files, category, title=""):
    """Return a short plain-English statement of what this update changes in the bot."""
    # Map touched files → subsystems
    subsystems_hit = set()
    for f in (files or []):
        fname = f.split("/")[-1]
        sub = FILE_TO_SUBSYSTEM.get(fname)
        if sub:
            subsystems_hit.add(sub)

    if subsystems_hit:
        parts = [SUBSYSTEM_IMPACT[s] for s in sorted(subsystems_hit) if s in SUBSYSTEM_IMPACT]
        return "This " + " and ".join(parts) + "."

    # Fall back to category heuristic
    cat_impacts = {
        "bugfix":   "fixes a defect that may have affected live trades.",
        "feature":  "adds new behaviour to the bot.",
        "refactor": "reorganises internal code — no behaviour change expected.",
        "test":     "adds or updates test coverage.",
        "research": "identifies improvements that could be built next.",
        "deployed": "ships a feature into the live bot.",
        "docs":     "updates documentation or design notes.",
        "session":  "records a development session.",
    }
    return cat_impacts.get(category, "updates the codebase.")


# ── Category definitions ─────────────────────────────────────────────────────

CATEGORIES = {
    "all":       {"label": "All",        "color": "light",     "accent": "#e9ecef"},
    "feature":   {"label": "Feature",    "color": "success",   "accent": "#51cf66"},
    "bugfix":    {"label": "Bug Fix",    "color": "danger",    "accent": "#ff6b6b"},
    "update":    {"label": "Update",     "color": "primary",   "accent": "#4dabf7"},
    "refactor":  {"label": "Refactor",   "color": "warning",   "accent": "#ffd43b"},
    "test":      {"label": "Test",       "color": "info",      "accent": "#74c0fc"},
    "research":  {"label": "Research",   "color": "info",      "accent": "#da77f2"},
    "deployed":  {"label": "Deployed",   "color": "success",   "accent": "#51cf66"},
    "spec":      {"label": "Spec",       "color": "secondary", "accent": "#868e96"},
    "docs":      {"label": "Docs",       "color": "secondary", "accent": "#868e96"},
    "session":   {"label": "Dev Session","color": "primary",   "accent": "#4dabf7"},
}


# ── Data loaders ─────────────────────────────────────────────────────────────

def _load_git_updates():
    if not DECIFER_REPO_PATH or not (DECIFER_REPO_PATH / ".git").exists():
        return []
    try:
        # Get commits with file stats
        result = subprocess.run(
            ["git", "log", "--format=%h|%an|%aI|%s", "-30"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        # Also get per-commit file stats
        stat_result = subprocess.run(
            ["git", "log", "--format=%h", "--stat", "-30"],
            cwd=DECIFER_REPO_PATH,
            capture_output=True, text=True, timeout=10,
        )
        # Build a hash -> stat map
        stat_map = {}
        if stat_result.returncode == 0:
            current_hash = None
            current_files = []
            for line in stat_result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if len(line) <= 8 and all(c in "0123456789abcdef" for c in line):
                    if current_hash:
                        stat_map[current_hash] = current_files
                    current_hash = line
                    current_files = []
                elif "|" in line and current_hash:
                    fname = line.split("|")[0].strip()
                    if fname and not fname.startswith(" "):
                        current_files.append(fname)
            if current_hash:
                stat_map[current_hash] = current_files

        updates = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue

            commit_hash = parts[0]
            msg = parts[3]
            msg_lower = msg.lower()

            if msg_lower.startswith("fix") or "bug" in msg_lower:
                cat = "bugfix"
            elif msg_lower.startswith("feat") or "add " in msg_lower:
                cat = "feature"
            elif msg_lower.startswith("refactor") or "clean" in msg_lower or "restructur" in msg_lower:
                cat = "refactor"
            elif msg_lower.startswith("test"):
                cat = "test"
            elif msg_lower.startswith("doc"):
                cat = "docs"
            elif "deploy" in msg_lower or "release" in msg_lower or "v3" in msg_lower:
                cat = "deployed"
            else:
                cat = "update"

            try:
                dt = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
                sort_key = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                time_str = _friendly_time(dt)
                date_str = dt.strftime("%B %d, %Y at %I:%M %p")
            except Exception:
                sort_key = "2000-01-01"
                time_str = "some time ago"
                date_str = "Unknown date"

            files_changed = stat_map.get(commit_hash, [])

            # Build a plain English expanded summary
            cat_label = CATEGORIES.get(cat, {}).get("label", cat)
            summary_lines = [
                f"This was a {cat_label.lower()} committed on {date_str} by {parts[1]}.",
            ]
            if files_changed:
                summary_lines.append(f"It changed {len(files_changed)} file{'s' if len(files_changed) != 1 else ''}: {', '.join(files_changed[:8])}")
                if len(files_changed) > 8:
                    summary_lines[-1] += f" and {len(files_changed) - 8} more."
                else:
                    summary_lines[-1] += "."

            updates.append({
                "category": cat,
                "title": msg,
                "author": parts[1],
                "time": time_str,
                "sort_key": sort_key,
                "source": "Git Commit",
                "expanded_summary": " ".join(summary_lines),
                "files_changed": files_changed,
                "date_full": date_str,
                "bot_impact": _infer_bot_impact(files_changed, cat, msg),
                "is_shipped": cat in ("deployed", "feature"),
            })

        return updates
    except Exception:
        return []


def _load_session_updates():
    if not SESSIONS_DIR.exists():
        return []
    updates = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        date_str = data.get("date", "")
        approved = data.get("approved_by", "")
        commits = data.get("git_commits", [])
        work_items = data.get("work_items", [])

        if not work_items:
            continue

        # Build one card per session (not per work item)
        types_in_session = set()
        file_set = set()
        item_summaries = []

        for item in work_items:
            item_type = item.get("type", "update")
            types_in_session.add(item_type)
            component = item.get("component", "")
            summary = item.get("summary", "")
            root_cause = item.get("root_cause", "")
            tests_ok = item.get("tests_passing")
            files = item.get("files_changed", [])
            file_set.update(files)

            desc = f"{component}: {summary}" if component else summary
            if root_cause:
                desc += f" (Reason: {root_cause})"
            if tests_ok is not None:
                desc += " — tests passing" if tests_ok else " — tests failing"
            item_summaries.append(desc)

        # Pick the most significant category
        if "bugfix" in types_in_session:
            cat = "bugfix"
        elif "feature" in types_in_session:
            cat = "feature"
        elif "refactor" in types_in_session:
            cat = "refactor"
        elif "test" in types_in_session:
            cat = "test"
        else:
            cat = "session"

        title = f"Dev session: {len(work_items)} item{'s' if len(work_items) != 1 else ''}"
        if len(work_items) == 1:
            title = item_summaries[0][:100]

        # Build expanded summary
        summary_lines = [f"Development session on {date_str}."]
        if approved:
            summary_lines.append(f"Approved by {approved}.")
        summary_lines.append(f"Worked on {len(work_items)} item{'s' if len(work_items) != 1 else ''}:")
        for i, s in enumerate(item_summaries[:5]):
            summary_lines.append(f"  {i+1}. {s}")
        if len(item_summaries) > 5:
            summary_lines.append(f"  ... and {len(item_summaries) - 5} more.")
        if file_set:
            summary_lines.append(f"Files touched: {', '.join(sorted(file_set)[:10])}")
        if commits:
            summary_lines.append(f"Commits: {', '.join(commits[:5])}")

        updates.append({
            "category": cat,
            "title": title,
            "author": approved or "Cowork",
            "time": date_str,
            "sort_key": date_str,
            "source": "Dev Session",
            "expanded_summary": "\n".join(summary_lines),
            "files_changed": sorted(file_set),
            "date_full": date_str,
            "work_item_count": len(work_items),
            "bot_impact": _infer_bot_impact(sorted(file_set), cat),
            "is_shipped": cat in ("deployed", "feature"),
        })

    return updates


def _load_research_updates():
    if not RESEARCH_DIR.exists():
        return []
    updates = []
    for f in sorted(RESEARCH_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        date_str = data.get("date", "")
        topic = data.get("topic", "Research")
        findings = data.get("findings", [])
        source = data.get("source", "")
        quick_wins = data.get("top_3_quick_wins", [])
        total_days = data.get("total_dev_days", 0)

        summary_lines = [f"Research report completed on {date_str}."]
        summary_lines.append(f"Topic: {topic}")
        if source:
            summary_lines.append(f"Source: {source}")
        summary_lines.append(f"Found {len(findings)} feature idea{'s' if len(findings) != 1 else ''}:")
        for finding in findings[:6]:
            name = finding.get("feature", finding.get("title", ""))
            impact = finding.get("expected_impact", "")
            difficulty = finding.get("difficulty", "")
            days = finding.get("dev_days", "")
            line = f"  \u2022 {name}"
            parts = []
            if impact:
                parts.append(impact)
            if difficulty:
                parts.append(difficulty.lower())
            if days:
                parts.append(f"{days} day{'s' if days != 1 else ''}")
            if parts:
                line += f" ({', '.join(parts)})"
            summary_lines.append(line)
        if len(findings) > 6:
            summary_lines.append(f"  ... and {len(findings) - 6} more.")
        if quick_wins:
            summary_lines.append("Quick wins: " + "; ".join(quick_wins))
        if total_days:
            summary_lines.append(f"Total estimated effort: {total_days} dev days.")

        # Short detail for card face
        detail_parts = []
        for finding in findings[:3]:
            name = finding.get("feature", finding.get("title", ""))
            impact = finding.get("expected_impact", "")
            if name:
                detail_parts.append(f"{name} ({impact})" if impact else name)
        short_detail = ", ".join(detail_parts)
        if len(findings) > 3:
            short_detail += f" and {len(findings) - 3} more"

        status = data.get("status", "active")
        is_complete = status == "complete"

        updates.append({
            "category": "deployed" if is_complete else "research",
            "title": f"{'[Shipped] ' if is_complete else ''}{topic}",
            "author": source or "Cowork",
            "time": data.get("completed_date", date_str),
            "sort_key": data.get("completed_date", date_str),
            "source": "Research — Shipped" if is_complete else "Research",
            "expanded_summary": "\n".join(summary_lines),
            "short_detail": short_detail,
            "date_full": date_str,
            "finding_count": len(findings),
            "bot_impact": "surfaces improvement ideas that feed the feature pipeline." if not is_complete else "shipped a researched improvement into the live bot.",
            "is_shipped": is_complete,
        })

    return updates


def _load_spec_updates():
    updates = []
    seen_ids = set()

    # Collect all spec data from both individual files and backlog.json
    all_spec_data = []

    if SPECS_DIR.exists():
        for f in sorted(SPECS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                if data.get("id") and data["id"] not in seen_ids:
                    all_spec_data.append(data)
                    seen_ids.add(data["id"])
            except Exception:
                continue

    if BACKLOG_FILE.exists():
        try:
            items = json.loads(BACKLOG_FILE.read_text())
            if isinstance(items, list):
                for data in items:
                    if data.get("id") and data["id"] not in seen_ids:
                        all_spec_data.append(data)
                        seen_ids.add(data["id"])
        except Exception:
            pass

    for data in all_spec_data:
        status = data.get("status", "spec_complete")
        title = data.get("title", data.get("id", "Unknown"))
        summary = data.get("summary", "")
        priority = data.get("priority", "")
        branch = data.get("branch", "")
        deps = data.get("dependencies", [])
        cat = "deployed" if status == "complete" else "spec"
        date_str = data.get("completed_date") or data.get("started_date") or data.get("designed_date", "")
        status_label = LIFECYCLE_LABELS.get(status, status)

        summary_lines = [f"Feature: {title}"]
        summary_lines.append(f"Status: {status_label}")
        if summary:
            summary_lines.append(f"Description: {summary}")
        if priority:
            summary_lines.append(f"Priority: {priority}")
        if branch:
            summary_lines.append(f"Branch: {branch}")
        if deps:
            summary_lines.append(f"Depends on: {', '.join(deps)}")
        dates = []
        if data.get("designed_date"):
            dates.append(f"Designed: {data['designed_date']}")
        if data.get("started_date"):
            dates.append(f"Started: {data['started_date']}")
        if data.get("completed_date"):
            dates.append(f"Completed: {data['completed_date']}")
        if dates:
            summary_lines.append(" | ".join(dates))

        spec_files = data.get("files_affected", []) or []
        updates.append({
            "category": cat,
            "title": title,
            "author": "",
            "time": date_str,
            "sort_key": date_str,
            "source": f"Spec — {status_label}",
            "expanded_summary": "\n".join(summary_lines),
            "short_detail": summary[:120] if summary else "",
            "date_full": date_str,
            "priority": priority,
            "bot_impact": _infer_bot_impact(spec_files, cat, title),
            "is_shipped": status == "complete",
        })

    return updates


def _load_docs_updates():
    """Load vision docs and other documents from state/docs/."""
    if not DOCS_DIR.exists():
        return []
    updates = []
    for f in sorted(DOCS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        title = data.get("title", "Untitled Document")
        doc_type = data.get("type", "doc")
        summary = data.get("summary", "")
        date_str = data.get("updated_date") or data.get("created_date", "")
        author = data.get("author", "")
        sections = data.get("sections", [])
        related = data.get("related_features", [])
        questions = data.get("open_questions", [])

        summary_lines = [f"Document: {title}"]
        summary_lines.append(f"Type: {doc_type.title()}")
        if summary:
            summary_lines.append(f"Summary: {summary}")
        if sections:
            summary_lines.append(f"Sections ({len(sections)}):")
            for sec in sections[:5]:
                heading = sec.get("heading", "")
                content = sec.get("content", "")[:120]
                summary_lines.append(f"  • {heading}: {content}")
        if related:
            summary_lines.append(f"Related features: {', '.join(related)}")
        if questions:
            summary_lines.append(f"Open questions: {len(questions)}")
            for q in questions[:3]:
                summary_lines.append(f"  ? {q}")

        updates.append({
            "category": "docs",
            "title": title,
            "author": author,
            "time": date_str,
            "sort_key": date_str,
            "source": f"Doc — {doc_type.title()}",
            "expanded_summary": "\n".join(summary_lines),
            "short_detail": summary[:120] if summary else "",
            "date_full": date_str,
            "bot_impact": "updates design documentation — no live code change.",
            "is_shipped": False,
        })

    return updates


def _load_activity_updates():
    """Load session activity from state/activity.jsonl (one JSON object per line)."""
    if not ACTIVITY_FILE.exists():
        return []
    updates = []
    try:
        lines = ACTIVITY_FILE.read_text().strip().splitlines()
    except Exception:
        return []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue

        event_type = data.get("type", "update")
        summary = data.get("summary", "")
        timestamp = data.get("timestamp", "")
        session_id = data.get("session_id", "")
        approved_by = data.get("approved_by", "")

        # Map activity types to categories
        cat_map = {
            "session_start": "session",
            "session_end": "session",
            "doc_created": "docs",
            "backlog_update": "feature",
            "feature_update": "feature",
            "bugfix": "bugfix",
            "deploy": "deployed",
        }
        cat = cat_map.get(event_type, "update")

        # Parse timestamp for sort key
        sort_key = timestamp
        try:
            dt = datetime.fromisoformat(timestamp)
            time_str = _friendly_time(dt)
            date_full = dt.strftime("%B %d, %Y at %I:%M %p")
        except Exception:
            time_str = timestamp
            date_full = timestamp

        # Build expanded summary
        summary_lines = [summary]
        if session_id:
            summary_lines.append(f"Session: {session_id}")
        if data.get("features_added"):
            summary_lines.append(f"Features added: {', '.join(data['features_added'])}")
        if data.get("doc_id"):
            summary_lines.append(f"Document: {data['doc_id']}")
        if approved_by:
            summary_lines.append(f"Approved by: {approved_by}")

        updates.append({
            "category": cat,
            "title": summary,
            "author": approved_by or "Cowork",
            "time": time_str,
            "sort_key": sort_key,
            "source": f"Activity — {event_type.replace('_', ' ').title()}",
            "expanded_summary": "\n".join(summary_lines),
            "date_full": date_full,
            "bot_impact": _infer_bot_impact([], cat, summary),
            "is_shipped": cat == "deployed",
        })

    return updates


def _friendly_time(dt):
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    if delta < timedelta(minutes=5):
        return "just now"
    elif delta < timedelta(hours=1):
        mins = int(delta.total_seconds() / 60)
        return f"{mins} min{'s' if mins != 1 else ''} ago"
    elif delta < timedelta(hours=24):
        hours = int(delta.total_seconds() / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif delta < timedelta(days=7):
        days = delta.days
        return f"{days} day{'s' if days != 1 else ''} ago"
    else:
        return dt.strftime("%b %d")


# ── Card renderer ────────────────────────────────────────────────────────────

def _traffic_light(update):
    """Return (colour_hex, label) for the card's traffic-light dot.
    Green  = shipped / deployed
    Yellow = not shipped, medium priority (default for non-critical)
    Red    = not shipped, critical / high priority or bug fixes
    """
    cat = update.get("category", "update")
    if cat == "deployed":
        return ("#51cf66", "Shipped")

    # Not shipped — check severity
    priority = update.get("priority", "").lower()
    if priority in ("critical", "high") or cat == "bugfix":
        return ("#ff6b6b", "Critical")

    return ("#ffd43b", "Medium")


def _render_update_card(update, index):
    """Render a clickable card. Clicking toggles the expanded detail."""
    cat = update.get("category", "update")
    cat_info = CATEGORIES.get(cat, CATEGORIES["update"])
    accent = cat_info["accent"]

    tl_color, tl_label = _traffic_light(update)

    title = update.get("title", "")
    if len(title) > 100:
        title = title[:97] + "..."

    short_detail = update.get("short_detail", "")
    if not short_detail and update.get("files_changed"):
        fcount = len(update["files_changed"])
        short_detail = f"{fcount} file{'s' if fcount != 1 else ''} changed"

    expanded = update.get("expanded_summary", "")

    # Footer items
    footer = []
    if update.get("author"):
        footer.append(html.Small(update["author"], className="text-muted me-2"))
    if update.get("time"):
        footer.append(html.Small(update["time"], className="text-muted me-2"))
    if update.get("source"):
        footer.append(html.Small(update["source"], style={"color": "var(--cd-faint)", "fontSize": "0.6rem"}))

    # Build the expanded detail section (hidden by default, shown on click)
    expanded_lines = expanded.split("\n") if expanded else []
    expanded_section = html.Div([
        html.Div(style={"borderTop": "1px solid var(--cd-border)", "margin": "10px 0"}),
        html.Div([
            html.Div(line, style={
                "fontSize": "0.75rem",
                "color": "var(--cd-text2)",
                "lineHeight": "1.6",
                "paddingLeft": "4px" if line.startswith("  ") else "0",
                "fontWeight": "500" if line.startswith("Topic:") or line.startswith("Status:") or line.startswith("Feature:") else "400",
            }) for line in expanded_lines
        ]),
        # Files list if present
        html.Div([
            html.Div(style={"borderTop": "1px solid #1a2030", "margin": "8px 0"}),
            html.Div([
                dbc.Badge(
                    fname.split("/")[-1],
                    color="secondary",
                    className="me-1 mb-1",
                    style={"fontSize": "0.6rem"},
                ) for fname in update.get("files_changed", [])[:12]
            ]),
            html.Small(
                f"+ {len(update['files_changed']) - 12} more",
                className="text-muted",
            ) if len(update.get("files_changed", [])) > 12 else None,
        ]) if update.get("files_changed") else None,
    ], id={"type": "card-detail", "index": index}, style={"display": "none"})

    return dbc.Col(
        html.Div([
            # Header: traffic light + badge
            html.Div([
                html.Div([
                    html.Span(style={
                        "display": "inline-block",
                        "width": "10px", "height": "10px",
                        "borderRadius": "50%",
                        "backgroundColor": tl_color,
                        "boxShadow": f"0 0 6px {tl_color}80",
                        "marginRight": "8px",
                        "verticalAlign": "middle",
                    }),
                    dbc.Badge(cat_info["label"], color=cat_info["color"], style={"fontSize": "0.6rem"}, className="me-2"),
                ], className="d-flex align-items-center"),
                html.Small(update.get("time", ""), className="text-muted"),
            ], className="d-flex align-items-center justify-content-between mb-2"),

            # Title
            html.Div(title, style={
                "fontWeight": "600", "fontSize": "0.85rem",
                "color": "var(--cd-text)", "lineHeight": "1.4", "marginBottom": "4px",
            }),

            # Bot impact — plain English effect on the bot
            html.Div([
                html.Span("Bot: ", style={"fontSize": "0.65rem", "color": "var(--cd-faint)",
                                          "fontWeight": 600, "marginRight": "3px"}),
                html.Span(update.get("bot_impact", ""),
                          style={"fontSize": "0.65rem", "color": "#6c7a8d", "lineHeight": "1.4"}),
            ], style={"marginBottom": "6px"}) if update.get("bot_impact") else None,

            # Short detail
            html.Div(short_detail, style={
                "fontSize": "0.7rem", "color": "var(--cd-muted)",
                "lineHeight": "1.4", "marginBottom": "8px",
            }) if short_detail else None,

            # Footer
            html.Div(footer, className="d-flex align-items-center"),

            # Click-to-expand hint
            html.Div([
                html.Small(
                    "\u25B6 Click for details",
                    id={"type": "card-hint", "index": index},
                    style={"color": "var(--cd-faint)", "fontSize": "0.6rem"},
                ),
            ], className="mt-2"),

            # Expanded detail (hidden)
            expanded_section,

        ], id={"type": "card-click", "index": index}, n_clicks=0, style={
            "backgroundColor": "var(--cd-card)",
            "borderRadius": "8px",
            "padding": "14px 16px",
            "borderLeft": f"3px solid {accent}",
            "border": "1px solid var(--cd-border)",
            "height": "100%",
            "cursor": "pointer",
            "transition": "border-color 0.2s ease",
        }),
        md=4, className="mb-3",
    )


# ── Main layout ──────────────────────────────────────────────────────────────

def layout():
    all_updates = (
        _load_git_updates()
        + _load_session_updates()
        + _load_research_updates()
        + _load_spec_updates()
        + _load_docs_updates()
        + _load_activity_updates()
    )

    # Sort: shipped/deployed items first (most recent), then everything else (most recent)
    shipped   = sorted([u for u in all_updates if u.get("is_shipped")],
                       key=lambda u: u.get("sort_key") or "", reverse=True)
    in_flight = sorted([u for u in all_updates if not u.get("is_shipped")],
                       key=lambda u: u.get("sort_key") or "", reverse=True)
    all_updates_sorted = shipped + in_flight

    # Count by category (for filter pills)
    cat_counts = {}
    for u in all_updates_sorted:
        c = u.get("category", "update")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    # Filter buttons
    filter_buttons = [
        dbc.Button(
            [f"All ", dbc.Badge(str(len(all_updates_sorted)), color="light", text_color="dark", className="ms-1")],
            id={"type": "filter-btn", "index": "all"},
            n_clicks=0, color="light", size="sm", className="me-2 mb-2",
            outline=True, style={"fontSize": "0.75rem"},
        ),
    ]
    sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)
    for cat_key, count in sorted_cats:
        cat_info = CATEGORIES.get(cat_key, CATEGORIES["update"])
        filter_buttons.append(
            dbc.Button(
                [f"{cat_info['label']} ", dbc.Badge(str(count), color="light", text_color="dark", className="ms-1")],
                id={"type": "filter-btn", "index": cat_key},
                n_clicks=0, color=cat_info["color"], size="sm", className="me-2 mb-2",
                outline=True, style={"fontSize": "0.75rem"},
            )
        )

    if not all_updates_sorted:
        return html.Div([
            html.H4("Updates", className="text-light mb-2", style={"fontWeight": "600"}),
            html.P(
                "No updates yet. Every commit, session, research report, and deployed feature "
                "will show up here as a card.",
                className="text-muted",
            ),
            dcc.Interval(id="pipeline-interval", interval=30_000, n_intervals=0),
        ])

    # Build card grid — shipped section first, then recent changes
    def _section_label(text, count):
        return html.Div([
            html.Span(text, style={"fontSize": "0.65rem", "fontWeight": 700,
                                   "color": "var(--cd-faint)", "textTransform": "uppercase",
                                   "letterSpacing": "0.8px"}),
            html.Span(f"  {count}", style={"fontSize": "0.6rem", "color": "var(--cd-faint)"}),
        ], style={"marginBottom": "10px", "marginTop": "4px",
                  "paddingBottom": "6px", "borderBottom": "1px solid var(--cd-border)"})

    shipped_cards   = [_render_update_card(u, i) for i, u in enumerate(shipped[:15])]
    in_flight_cards = [_render_update_card(u, i + len(shipped)) for i, u in enumerate(in_flight[:20])]

    card_grid_children = []
    if shipped_cards:
        card_grid_children.append(_section_label("Shipped", len(shipped)))
        card_grid_children.append(dbc.Row(shipped_cards))
    if in_flight_cards:
        card_grid_children.append(_section_label("Recent Changes", len(in_flight)))
        card_grid_children.append(dbc.Row(in_flight_cards))

    return html.Div([
        html.Div([
            html.H4("Updates", className="text-light mb-0", style={"fontWeight": "600"}),
            html.Small(f"{len(all_updates_sorted)} total", className="text-muted ms-2"),
        ], className="d-flex align-items-center mb-2"),
        html.P(
            "Shipped items first, then recent changes. Each card shows what changed in the bot. "
            "Click any card for full details.",
            className="text-muted small mb-3",
        ),

        # Filter bar
        html.Div(filter_buttons, className="mb-3"),

        # Cards — sectioned
        html.Div(id="updates-card-grid", children=card_grid_children),

        dcc.Store(id="updates-data", data=all_updates_sorted[:60]),
        dcc.Store(id="active-filter", data="all"),
        dcc.Interval(id="pipeline-interval", interval=30_000, n_intervals=0),
    ])


def register_callbacks(app):
    @app.callback(
        Output("pipeline-content", "children"),
        Input("pipeline-interval", "n_intervals"),
        Input("scan-complete", "data"),
    )
    def refresh(_n, _clicks):
        return layout()

    # Filter handler
    @app.callback(
        Output("updates-card-grid", "children"),
        Output("active-filter", "data"),
        Input({"type": "filter-btn", "index": ALL}, "n_clicks"),
        State("updates-data", "data"),
        State("active-filter", "data"),
        prevent_initial_call=True,
    )
    def handle_filter(n_clicks_list, all_data, current_filter):
        ctx = callback_context
        if not ctx.triggered:
            return dbc.Row(), current_filter
        triggered_id = ctx.triggered[0]["prop_id"]
        import json as _json
        try:
            parsed = _json.loads(triggered_id.split(".")[0])
            clicked_cat = parsed["index"]
        except Exception:
            clicked_cat = "all"
        if clicked_cat == "all":
            filtered = all_data
        else:
            filtered = [u for u in all_data if u.get("category") == clicked_cat]
        cards = [_render_update_card(u, i) for i, u in enumerate(filtered[:30])]
        if not cards:
            return html.Div(
                html.P(f"No {CATEGORIES.get(clicked_cat, {}).get('label', clicked_cat)} updates yet.", className="text-muted"),
                className="py-4",
            ), clicked_cat
        return dbc.Row(cards), clicked_cat

    # Card click → toggle detail
    @app.callback(
        Output({"type": "card-detail", "index": MATCH}, "style"),
        Output({"type": "card-hint", "index": MATCH}, "children"),
        Input({"type": "card-click", "index": MATCH}, "n_clicks"),
        prevent_initial_call=True,
    )
    def toggle_card_detail(n_clicks):
        if not n_clicks:
            return {"display": "none"}, "\u25B6 Click for details"
        is_open = (n_clicks % 2) == 1
        if is_open:
            return {"display": "block"}, "\u25BC Hide details"
        else:
            return {"display": "none"}, "\u25B6 Click for details"
