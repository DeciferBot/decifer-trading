#!/usr/bin/env python3
"""
Chief Decifer — Autonomous Researcher Agent
===========================================
Searches the internet autonomously for peer-reviewed papers, quantitative
research, historical trends, social sentiment analysis, and emerging trading
techniques. Proposes concrete, buildable features for Decifer Trading aligned
to the project vision.

Output: state/research/YYYY-MM-DD_<topic_slug>.json

Run manually:   python researcher.py [--topic TOPIC_ID]
Scheduled:      via Claude Code scheduled task

Requires:
  - ANTHROPIC_API_KEY in .env
"""

import json
import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# ── Paths ──────────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent
STATE_DIR    = BASE_DIR / "state"
VISION_FILE  = STATE_DIR / "vision.json"
SPECS_DIR    = STATE_DIR / "specs"
BACKLOG_FILE = STATE_DIR / "backlog.json"
RESEARCH_DIR = STATE_DIR / "research"

API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL   = "claude-sonnet-4-6"

MAX_TOKENS     = 8000   # enough for thorough research + structured output
SEARCH_BUDGET  = 10     # max web search calls Claude can make per run


# ── Research topic rotation ────────────────────────────────────────────────────
#
# Each topic targets a different layer of the trading system.
# Topics rotate round-robin based on which has been researched least recently.
# Each run picks ONE topic and goes deep on it.

TOPICS = [
    {
        "id":    "signal_alpha",
        "title": "Signal Quality & Alpha Generation",
        "slug":  "signal_alpha",
        "focus": (
            "Find peer-reviewed research, quant papers, and empirical studies on "
            "generating statistically significant alpha in algorithmic trading. "
            "Cover: multi-factor signal models, technical indicator effectiveness, "
            "momentum vs mean-reversion regimes, signal decay rates, and alternative "
            "data sources (sentiment, options flow, insider filings). Focus on "
            "practical techniques that work with free data (yfinance, Reddit, Yahoo RSS)."
        ),
    },
    {
        "id":    "risk_sizing",
        "title": "Risk Management & Position Sizing",
        "slug":  "risk_sizing",
        "focus": (
            "Research modern portfolio risk techniques: improvements over Kelly criterion, "
            "volatility-targeting approaches, CVaR vs VaR, drawdown-aware sizing, "
            "tail-risk hedging, and cross-asset correlation management. Look for "
            "academic papers and practitioner research from 2023-2025 that provide "
            "concrete formulas and implementation guidance."
        ),
    },
    {
        "id":    "regime_detection",
        "title": "Market Regime Detection & Adaptation",
        "slug":  "regime_detection",
        "focus": (
            "Find research on detecting market regimes (bull/bear/choppy/crisis) "
            "programmatically. Cover: Hidden Markov Models for regime classification, "
            "volatility regime switching, breadth-based regime indicators, "
            "VIX term structure signals, and adaptive strategy switching. "
            "Look for papers that show out-of-sample performance improvements."
        ),
    },
    {
        "id":    "sentiment_nlp",
        "title": "Sentiment Analysis & NLP for Trading",
        "slug":  "sentiment_nlp",
        "focus": (
            "Research how LLMs and NLP are being used as trading signals in 2024-2025. "
            "Cover: news sentiment scoring with Claude/GPT, Reddit/social signal extraction, "
            "earnings call NLP analysis, SEC filing sentiment, and multi-source sentiment "
            "aggregation. Find peer-reviewed papers and practitioner results showing "
            "alpha generation from text-based signals."
        ),
    },
    {
        "id":    "execution_quality",
        "title": "Order Execution & Market Microstructure",
        "slug":  "execution_quality",
        "focus": (
            "Research smart order execution for retail algorithmic traders: VWAP/TWAP "
            "algorithms, adverse selection avoidance, optimal limit order placement, "
            "spread capture, and slippage minimisation with IBKR. Also cover: "
            "market impact models, time-of-day execution patterns, and bracket order "
            "optimisation. Focus on techniques accessible via IBKR paper account."
        ),
    },
    {
        "id":    "ml_prediction",
        "title": "Machine Learning for Trade Prediction",
        "slug":  "ml_prediction",
        "focus": (
            "Find 2024-2025 research on ML models that predict short-term price movements "
            "for algorithmic trading. Cover: gradient boosting feature importance, "
            "LSTM/transformer for time-series, walk-forward model validation to prevent "
            "overfitting, ensemble methods, and reinforcement learning for strategy "
            "optimisation. Prioritise techniques that work with tabular/OHLCV data."
        ),
    },
    {
        "id":    "multi_account_saas",
        "title": "Multi-Account Trading Platform Architecture",
        "slug":  "multi_account_saas",
        "focus": (
            "Research architecture patterns for multi-tenant algorithmic trading platforms: "
            "signal broadcasting to multiple accounts, per-user isolation in containerised "
            "environments, WebSocket-based live signal delivery, broker API multiplexing "
            "across IBKR accounts, and risk isolation per user. Also find SaaS pricing "
            "models and onboarding patterns for copy-trading or signal subscription services."
        ),
    },
    {
        "id":    "backtesting_validation",
        "title": "Backtesting & Strategy Validation",
        "slug":  "backtesting_validation",
        "focus": (
            "Research rigorous backtesting methodologies: walk-forward optimisation, "
            "Monte Carlo permutation testing, deflated Sharpe ratio, survivorship bias "
            "correction, and realistic transaction cost modelling. Find papers that "
            "expose common backtesting pitfalls and provide concrete frameworks for "
            "validating that a strategy's edge is real and will persist out-of-sample."
        ),
    },
    {
        "id":    "catalyst_ma_signals",
        "title": "Catalyst & M&A Signal Detection",
        "slug":  "catalyst_ma_signals",
        "focus": (
            "Research how algorithmic traders detect asymmetric catalyst events — "
            "especially M&A acquisition targets — before announcement. Cover: "
            "(1) Pre-announcement options activity: academic evidence on abnormal "
            "OTM call volume, IV term structure compression, and put/call ratio shifts "
            "in the days before takeover announcements (cite specific studies). "
            "(2) SEC filing signals: how 13D/13G activist filings and Form 4 insider "
            "cluster buys predict M&A activity; latency from filing to announcement. "
            "(3) Fundamental screens for acquisition targets: EV/Revenue, net cash, "
            "revenue growth thresholds that historically select acquirees. "
            "(4) Real examples: any documented cases where these signals fired before "
            "major acquisitions (APLS/Biogen at 140% premium, similar deals in biotech "
            "or tech). Focus on techniques accessible with free data (yfinance, SEC "
            "EDGAR RSS, Yahoo RSS). Provide IC estimates, hit rates, and average "
            "lead time (days before announcement) for each signal type."
        ),
    },
]


# ── Context loaders ────────────────────────────────────────────────────────────

def _load_vision():
    if VISION_FILE.exists():
        try:
            return json.loads(VISION_FILE.read_text())
        except Exception:
            pass
    return {"statement": "Build a profitable multi-user trading platform.", "current_stage": "paper_trading_single_account"}


def _load_specs_summary():
    """Return a compact list of existing specs so Claude avoids duplicates."""
    specs = []
    seen = set()
    if SPECS_DIR.exists():
        for f in sorted(SPECS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                if d.get("id") and d["id"] not in seen:
                    specs.append({"id": d["id"], "title": d.get("title", ""), "status": d.get("status", "")})
                    seen.add(d["id"])
            except Exception:
                pass
    if BACKLOG_FILE.exists():
        try:
            items = json.loads(BACKLOG_FILE.read_text())
            if isinstance(items, list):
                for d in items:
                    if d.get("id") and d["id"] not in seen:
                        specs.append({"id": d["id"], "title": d.get("title", ""), "status": d.get("status", "")})
                        seen.add(d["id"])
        except Exception:
            pass
    return specs


def _load_recent_research_topics(n=5):
    """Return topic IDs of the n most recent research files."""
    if not RESEARCH_DIR.exists():
        return []
    files = sorted(RESEARCH_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    topics = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            slug = f.stem.split("_", 1)[-1] if "_" in f.stem else f.stem
            topics.append(slug)
        except Exception:
            pass
    return topics


def _pick_topic(forced_id=None):
    """Pick the topic that was researched least recently (or forced by CLI arg)."""
    if forced_id:
        match = next((t for t in TOPICS if t["id"] == forced_id), None)
        if match:
            return match
        print(f"WARNING: Unknown topic '{forced_id}'. Picking automatically.")

    recent = _load_recent_research_topics(n=len(TOPICS))
    # Find the first topic not in recent (or the one researched longest ago)
    for topic in TOPICS:
        if topic["slug"] not in recent:
            return topic
    # All topics covered at least once — pick the one researched longest ago
    for slug in reversed(recent):
        match = next((t for t in TOPICS if t["slug"] == slug), None)
        if match:
            return match
    return TOPICS[0]


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(topic, vision, specs):
    existing_titles = "\n".join(f"  - {s['title']} [{s['status']}]" for s in specs)

    return f"""You are Chief Decifer's autonomous researcher. Your job is to search the internet and find the best, most current research relevant to improving the Decifer Trading system.

## MISSION
Search for peer-reviewed papers, quantitative research, practitioner blogs, backtesting studies, and real-world evidence related to this topic:

**Topic: {topic['title']}**

{topic['focus']}

## PROJECT CONTEXT

**Vision:**
{vision.get('statement', '')}

**Current stage:** {vision.get('current_stage', 'paper_trading_single_account')}
The bot currently runs on a paper IBKR account with:
- 9-dimension signal scoring (trend, momentum, squeeze, flow, breakout, confluence, news, social, mean reversion)
- Kelly criterion position sizing
- 6 Claude agents for trade decisions
- Free data stack: yfinance, TradingView Screener, Yahoo RSS, Finviz

**STRICT DEDUPLICATION RULE — READ CAREFULLY:**
The following features already exist. You MUST NOT propose any feature that is
the same concept under a different name, or a minor variation of an existing feature.

EXAMPLES OF WHAT COUNTS AS A DUPLICATE:
  - "IC-Weighted Dynamic Signal Scorer" is a duplicate of "IC-Weighted Dynamic Signal Scoring"
  - "Regime-Gated Signal Router" is a duplicate of "Regime-Conditional Signal Weighting"
  - "Reddit Mention Velocity Signal" is a duplicate of "Reddit Mentions Surge Detector"

Before writing any finding, ask: "Is this the same underlying mechanism as something
already listed?" If yes, SKIP IT.

**Existing features (do not duplicate ANY of these):**
{existing_titles if existing_titles else '  (none yet)'}

## YOUR TASK

1. Use web_search to find real, current research — peer-reviewed papers, quant blogs, backtesting studies, empirical results. Target 2023-2025 research.
2. Read the actual content (not just abstracts) to extract concrete, implementable techniques.
3. Evaluate each finding for: feasibility with free data, expected impact, development complexity.
4. Identify 4-8 specific features we could build into Decifer Trading based on your findings.
5. Prioritise features that advance the vision (profitable paper → live → multi-user SaaS).

## OUTPUT FORMAT

Return a JSON object with EXACTLY this structure (no other text):

{{
  "topic": "{topic['title']}",
  "date": "{datetime.now().strftime('%Y-%m-%d')}",
  "source": "Autonomous web research by Chief Decifer",
  "synthesis": "<3-5 sentence synthesis of the most important patterns across all your research. What is the key insight that should shape what we build next?>",
  "top_3_quick_wins": [
    "<Feature title — highest impact, lowest effort>",
    "<Feature title>",
    "<Feature title>"
  ],
  "total_dev_days": <sum of dev_days across all findings>,
  "findings": [
    {{
      "feature": "<Specific, buildable feature name>",
      "tier": <1 for high impact, 2 for medium, 3 for specialised>,
      "expected_impact": "<quantified if possible, e.g. '+2-4% win rate' or 'reduces drawdown 15-25%'>",
      "dev_days": <realistic estimate 1-5>,
      "difficulty": "<Easy|Medium|Hard>",
      "summary": "<2-3 sentences. What it does, why it matters for Decifer specifically.>",
      "module": "<which Python file in decifer-trading this goes in, e.g. signals.py>",
      "upgrades": "<One sentence: what system component this upgrades>",
      "subsystem": "<Signal Generation|Risk & Portfolio|AI & Learning|Trading Core|News & Sentiment|Market Data|Analytics & UI>",
      "what_changes": "<Concrete description of what code/logic gets added or changed>",
      "why_it_matters": "<Why this matters for Decifer's specific situation — paper trading, bullish bias, multi-user vision>",
      "source_evidence": "<Paper title, URL, or practitioner source that supports this. Be specific.>"
    }}
  ]
}}

Be honest about uncertainty. If research is limited or mixed, say so in the summary. Every finding must be grounded in something you actually found — not general knowledge."""


# ── Claude API call with web search ──────────────────────────────────────────

def _run_research(topic, vision, specs):
    """Call Claude with web_search tool. Returns the raw response text."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=API_KEY)
    prompt = _build_prompt(topic, vision, specs)

    print(f"  Model:  {MODEL}")
    print(f"  Topic:  {topic['title']}")
    print(f"  Budget: up to {SEARCH_BUDGET} web searches")
    print()

    # Agentic loop — Claude may make multiple tool calls before returning text
    messages = [{"role": "user", "content": prompt}]
    search_count = 0
    final_text = ""

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": SEARCH_BUDGET,
            }],
            messages=messages,
        )

        # Collect any text blocks and tool calls
        tool_calls = []
        text_blocks = []

        for block in response.content:
            if block.type == "text":
                text_blocks.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)
            elif block.type == "server_tool_use":
                # Web search is handled server-side — just track it
                search_count += 1
                print(f"  [search {search_count}] {getattr(block, 'input', {}).get('query', '...')}")

        if text_blocks:
            final_text = "\n".join(text_blocks)

        # If stop reason is end_turn or no tool calls, we're done
        if response.stop_reason in ("end_turn", "max_tokens") or not tool_calls:
            break

        # Append assistant message and continue with tool results
        messages.append({"role": "assistant", "content": response.content})

        # Build tool results
        tool_results = []
        for tc in tool_calls:
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": "Search completed — results incorporated above.",
            })
        messages.append({"role": "user", "content": tool_results})

    print(f"\n  Searches used: {search_count}")
    return final_text


# ── Output writer ─────────────────────────────────────────────────────────────

def _parse_and_save(raw_text, topic):
    """Parse Claude's JSON output and save to state/research/."""
    # Strip markdown fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())

    # Extract JSON object — find first { to last }
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")

    data = json.loads(text[start:end])

    # Stamp metadata
    data["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    data["model"]        = MODEL
    data["status"]       = "active"
    data.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    data.setdefault("topic", topic["title"])

    # Write file
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y-%m-%d")
    filename  = f"{date_str}_{topic['slug']}.json"
    out_path  = RESEARCH_DIR / filename

    # If same topic was run today, add a counter suffix
    counter = 1
    while out_path.exists():
        out_path = RESEARCH_DIR / f"{date_str}_{topic['slug']}_{counter}.json"
        counter += 1

    out_path.write_text(json.dumps(data, indent=2, default=str))
    return out_path, data


# ── Main ──────────────────────────────────────────────────────────────────────

def run(forced_topic_id=None):
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    print("\nChief Decifer — Autonomous Researcher")
    print("=" * 42)

    # Load context
    vision = _load_vision()
    specs  = _load_specs_summary()
    topic  = _pick_topic(forced_id=forced_topic_id)

    print(f"Vision stage: {vision.get('current_stage', '?')}")
    print(f"Existing specs: {len(specs)}")
    print(f"Research topic: {topic['title']}")
    print()

    # Run research
    print("Searching the web...")
    raw = _run_research(topic, vision, specs)

    if not raw.strip():
        print("ERROR: Claude returned empty response.", file=sys.stderr)
        sys.exit(1)

    # Parse and save
    try:
        out_path, data = _parse_and_save(raw, topic)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: Failed to parse response as JSON: {e}", file=sys.stderr)
        err_path = RESEARCH_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_{topic['slug']}_raw_error.txt"
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        err_path.write_text(raw)
        print(f"Raw response saved to {err_path}")
        sys.exit(1)

    findings_count = len(data.get("findings", []))
    print(f"\n✓ Research complete — {findings_count} feature proposals")
    print(f"✓ Saved to {out_path.relative_to(BASE_DIR)}")
    print(f"\nSynthesis:\n{data.get('synthesis', '(none)')}")
    if data.get("top_3_quick_wins"):
        print("\nTop quick wins:")
        for w in data["top_3_quick_wins"]:
            print(f"  • {w}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chief Decifer Researcher Agent")
    parser.add_argument(
        "--topic", "-t",
        help=f"Force a specific topic ID. Options: {', '.join(t['id'] for t in TOPICS)}",
        default=None,
    )
    args = parser.parse_args()
    run(forced_topic_id=args.topic)
