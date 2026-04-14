"""
analytics.py — Quantstats-powered analytics for the dashboard.

Computes portfolio metrics from closed trades in data/trades.json
and returns them as a JSON-serializable dict for the /api/analytics endpoint.
"""

import json
import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd
import quantstats as qs

log = logging.getLogger(__name__)

TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "trades.json")

# Cache for the LLM interpretation (keyed by trade_count → only regenerate on new closes)
_explain_cache = {"trade_count": -1, "data": None}


def _load_trades() -> list:
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    try:
        with open(TRADE_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return []

# Cache to avoid recomputing on every poll
_cache = {"ts": 0, "data": None}
_CACHE_TTL = 300  # 5 minutes


def _closed_trades() -> list[dict]:
    """Filter to trades with exit data and P&L."""
    trades = _load_trades()
    return [
        t for t in trades
        if t.get("exit_price") is not None
        and t.get("pnl") is not None
    ]


def _build_returns_series(closed: list[dict]) -> pd.Series:
    """
    Build a daily returns series from closed trades.
    Groups trades by exit date, sums daily P&L, divides by running capital.
    """
    if not closed:
        return pd.Series(dtype=float)

    rows = []
    for t in closed:
        exit_ts = t.get("exit_time") or t.get("timestamp")
        if not exit_ts:
            continue
        try:
            dt = pd.Timestamp(exit_ts)
            if dt.tzinfo is not None:
                dt = dt.tz_localize(None)
            dt = dt.normalize()
        except Exception:
            continue
        rows.append({"date": dt, "pnl": float(t["pnl"]),
                      "notional": abs(float(t.get("entry_price", 1)) * float(t.get("qty") or t.get("shares") or 1))})

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(rows)
    daily = df.groupby("date").agg({"pnl": "sum", "notional": "sum"}).sort_index()
    # Return = daily P&L / average notional exposure (avoids divide-by-zero)
    avg_notional = daily["notional"].mean() or 1.0
    daily["ret"] = daily["pnl"] / avg_notional
    return daily["ret"]


def _streak(pnls: list[float], positive: bool) -> int:
    """Longest consecutive win/loss streak."""
    best = current = 0
    for p in pnls:
        if (positive and p > 0) or (not positive and p <= 0):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _monthly_returns(returns: pd.Series) -> list[dict]:
    """Monthly returns table for heatmap."""
    if returns.empty:
        return []
    monthly = returns.resample("ME").sum()
    result = []
    for dt, ret in monthly.items():
        result.append({
            "year": dt.year,
            "month": dt.month,
            "return": round(float(ret) * 100, 2),
        })
    return result


def _drawdown_series(returns: pd.Series) -> list[dict]:
    """Compute drawdown curve for charting."""
    if returns.empty:
        return []
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    result = []
    for dt, val in dd.items():
        result.append({
            "date": dt.strftime("%Y-%m-%d"),
            "drawdown": round(float(val) * 100, 2),
        })
    return result


def _cumulative_returns(returns: pd.Series) -> list[dict]:
    """Cumulative returns curve for charting."""
    if returns.empty:
        return []
    cum = (1 + returns).cumprod() - 1
    result = []
    for dt, val in cum.items():
        result.append({
            "date": dt.strftime("%Y-%m-%d"),
            "cumulative": round(float(val) * 100, 2),
        })
    return result


def _by_regime(closed: list[dict]) -> dict:
    """Win rate and avg P&L grouped by regime."""
    regimes = {}
    for t in closed:
        r = t.get("regime") or "UNKNOWN"
        regimes.setdefault(r, []).append(t)
    result = {}
    for r, trades in regimes.items():
        wins = [t for t in trades if t["pnl"] > 0]
        result[r] = {
            "count": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "total_pnl": round(sum(t["pnl"] for t in trades), 2),
            "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2),
        }
    return result


def _by_direction(closed: list[dict]) -> dict:
    """Performance split by LONG vs SHORT."""
    result = {}
    for d in ("LONG", "SHORT"):
        trades = [t for t in closed if (t.get("direction") or "").upper() == d]
        if not trades:
            result[d] = {"count": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0}
            continue
        wins = [t for t in trades if t["pnl"] > 0]
        result[d] = {
            "count": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "total_pnl": round(sum(t["pnl"] for t in trades), 2),
            "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2),
        }
    return result


def _by_instrument(closed: list[dict]) -> dict:
    """Performance split by instrument type."""
    result = {}
    for t in closed:
        inst = (t.get("instrument") or "stock").lower()
        result.setdefault(inst, []).append(t)
    out = {}
    for inst, trades in result.items():
        wins = [t for t in trades if t["pnl"] > 0]
        out[inst] = {
            "count": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "total_pnl": round(sum(t["pnl"] for t in trades), 2),
        }
    return out


def _top_symbols(closed: list[dict], n: int = 5) -> dict:
    """Top winners and losers by symbol."""
    by_sym = {}
    for t in closed:
        s = t.get("symbol", "?")
        by_sym.setdefault(s, []).append(t)
    sym_pnl = {s: round(sum(t["pnl"] for t in ts), 2) for s, ts in by_sym.items()}
    sorted_syms = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    return {
        "best": [{"symbol": s, "pnl": p, "trades": len(by_sym[s])} for s, p in sorted_syms[:n]],
        "worst": [{"symbol": s, "pnl": p, "trades": len(by_sym[s])} for s, p in sorted_syms[-n:]],
    }


def _hold_time_stats(closed: list[dict]) -> dict:
    """Hold time distribution."""
    mins = [t["hold_minutes"] for t in closed if t.get("hold_minutes")]
    if not mins:
        return {"avg_minutes": 0, "median_minutes": 0, "min_minutes": 0, "max_minutes": 0}
    return {
        "avg_minutes": round(np.mean(mins), 1),
        "median_minutes": round(float(np.median(mins)), 1),
        "min_minutes": round(min(mins), 1),
        "max_minutes": round(max(mins), 1),
    }


def get_analytics() -> dict:
    """Main entry point — returns full analytics payload."""
    import time
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    closed = _closed_trades()
    if not closed:
        return {"error": "No closed trades yet", "trade_count": 0}

    returns = _build_returns_series(closed)
    pnls = [t["pnl"] for t in closed]

    # Core quantstats metrics (computed from returns series)
    metrics = {}
    if not returns.empty and len(returns) >= 2:
        try:
            metrics["sharpe"] = round(float(qs.stats.sharpe(returns)), 3)
        except Exception:
            metrics["sharpe"] = None
        try:
            metrics["sortino"] = round(float(qs.stats.sortino(returns)), 3)
        except Exception:
            metrics["sortino"] = None
        try:
            metrics["max_drawdown"] = round(float(qs.stats.max_drawdown(returns)) * 100, 2)
        except Exception:
            metrics["max_drawdown"] = None
        try:
            metrics["calmar"] = round(float(qs.stats.calmar(returns)), 3)
        except Exception:
            metrics["calmar"] = None
        try:
            metrics["volatility"] = round(float(qs.stats.volatility(returns)) * 100, 2)
        except Exception:
            metrics["volatility"] = None
        try:
            val = qs.stats.value_at_risk(returns)
            metrics["var_95"] = round(float(val) * 100, 2)
        except Exception:
            metrics["var_95"] = None
    else:
        metrics = {"sharpe": None, "sortino": None, "max_drawdown": None,
                   "calmar": None, "volatility": None, "var_95": None}

    # Trade-level stats
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1

    payload = {
        "trade_count": len(closed),
        "metrics": metrics,
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "total_pnl": round(sum(pnls), 2),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "expectancy": round((len(wins)/len(closed) * np.mean(wins) if wins else 0) +
                            ((len(losses)/len(closed)) * np.mean(losses) if losses else 0), 2),
        "win_streak": _streak(pnls, True),
        "loss_streak": _streak(pnls, False),
        "monthly_returns": _monthly_returns(returns),
        "drawdown_curve": _drawdown_series(returns),
        "cumulative_curve": _cumulative_returns(returns),
        "by_regime": _by_regime(closed),
        "by_direction": _by_direction(closed),
        "by_instrument": _by_instrument(closed),
        "top_symbols": _top_symbols(closed),
        "hold_time": _hold_time_stats(closed),
    }

    _cache["ts"] = now
    _cache["data"] = payload
    return payload


# ══════════════════════════════════════════════════════════════
# LLM INTERPRETATION — plain-English read of the metrics
# ══════════════════════════════════════════════════════════════

_EXPLAIN_SYSTEM_PROMPT = """You are a performance analyst reviewing a paper-trading account's track record.

Context:
- This is a PAPER account deliberately run with aggressive thresholds to generate training data across market regimes.
- The trade count is still small (100–200 range), so the goal is directional reading, not statistical certainty.
- The user wants plain English — no jargon, no hedging, no preamble.

You will receive a JSON payload of performance metrics. Interpret it and return ONLY a JSON object with this exact shape:

{
  "wins":       ["<short bullet>", "<short bullet>", ...],   // 2–4 items, what's genuinely good
  "concerns":   ["<short bullet>", "<short bullet>", ...],   // 2–4 items, what needs to get better
  "bottom_line": "<one sentence — the honest overall take>"
}

Rules:
- Each bullet ≤ 14 words. Lead with the metric or fact, then the implication.
- Call out trade-offs, not just numbers (e.g. "15-trade loss streak means the system takes pain before recovering").
- If the win rate is low but profit factor > 1, explain that tension in plain terms.
- If sample size is small (< 200 trades), say what's premature vs. what's already clear.
- Do NOT suggest parameter changes or new features. Just interpret.
- Do NOT use markdown, code fences, or any text outside the JSON object."""


def _call_claude_for_explanation(payload: dict) -> dict | None:
    """Call Claude Sonnet to interpret the analytics payload. Returns None on failure."""
    try:
        import anthropic

        from config import CONFIG

        client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])

        # Strip bulky chart arrays — the LLM only needs the headline numbers
        slim = {
            "trade_count": payload.get("trade_count"),
            "metrics": payload.get("metrics"),
            "win_rate": payload.get("win_rate"),
            "profit_factor": payload.get("profit_factor"),
            "avg_win": payload.get("avg_win"),
            "avg_loss": payload.get("avg_loss"),
            "total_pnl": payload.get("total_pnl"),
            "best_trade": payload.get("best_trade"),
            "worst_trade": payload.get("worst_trade"),
            "expectancy": payload.get("expectancy"),
            "win_streak": payload.get("win_streak"),
            "loss_streak": payload.get("loss_streak"),
            "by_direction": payload.get("by_direction"),
            "by_regime": payload.get("by_regime"),
            "by_instrument": payload.get("by_instrument"),
            "hold_time": payload.get("hold_time"),
        }

        resp = client.messages.create(
            model=CONFIG.get("claude_model", "claude-sonnet-4-6"),
            max_tokens=800,
            system=[{"type": "text", "text": _EXPLAIN_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": json.dumps(slim, indent=2)}],
        )
        text = resp.content[0].text.strip()

        # Strip any accidental fences and locate the JSON object
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            log.warning("explain_analytics: no JSON object in response")
            return None
        parsed = json.loads(text[start : end + 1])
        # Minimal shape check
        if not all(k in parsed for k in ("wins", "concerns", "bottom_line")):
            return None
        return parsed
    except Exception as exc:
        log.warning("explain_analytics LLM error: %s", exc)
        return None


def explain_analytics(force: bool = False) -> dict:
    """Return a plain-English interpretation of the current analytics payload.

    Cached by trade_count — only regenerates when new trades close.
    Pass force=True to bypass the cache and always call the LLM.
    """
    analytics = get_analytics()
    if analytics.get("error") and not analytics.get("trade_count"):
        return {"error": analytics["error"], "trade_count": 0}

    tc = analytics.get("trade_count", 0)
    if not force and _explain_cache["trade_count"] == tc and _explain_cache["data"]:
        return _explain_cache["data"]

    result = _call_claude_for_explanation(analytics)
    if result is None:
        return {"error": "Could not generate interpretation — see server log"}

    _explain_cache["trade_count"] = tc
    _explain_cache["data"] = result
    return result
