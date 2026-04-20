"""
Multi-Source Sentiment Scoring Pipeline
=========================================
Fetches recent headlines for M&A candidate tickers from:
  1. Yahoo Finance RSS  — finance.yahoo.com/rss/headline?s={ticker}
  2. Finviz             — finviz.com/quote.ashx?t={ticker} (news table, best-effort)

Scores each headline set via three methods:
  1. Claude zero-shot direct prompt (no chain-of-thought — latency < 1s, Haiku model)
  2. FinBERT local scorer — ProsusAI/finbert (free, 72.2% directional accuracy)
  3. Regression-calibrated composite (weighted combination → 0–10)

Output: sentiment_score (0–10) added to candidates_{today}.json as the
10th signal dimension.  Composite catalyst_score is recomputed with
F:35% + O:35% + E:15% + S:15% weighting.

Calibration rationale:
  FinBERT 72.2% directional accuracy (Kirtac & Germano 2024)
  Claude zero-shot ~65% (ACM ICAIF 2025 — no CoT is best for financial sentiment)
  FinBERT weight 0.60, Claude weight 0.40

Run standalone:  python -m signals.sentiment_scorer --tickers AAPL MSFT
Called from app: from signals.sentiment_scorer import run_sentiment_scan
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger("sentiment_scorer")

from config import CATALYST_DIR  # noqa: E402  chief-decifer/state/internal/catalyst/
SENTIMENT_SNAPSHOTS_FILE = CATALYST_DIR / "sentiment_snapshots.jsonl"

# ── Regression calibration weights ────────────────────────────────────────────
# Derived from accuracy literature: FinBERT 72.2% > Claude zero-shot ~65%.
_CLAUDE_WEIGHT  = 0.40
_FINBERT_WEIGHT = 0.60

# ── FinBERT pipeline (lazy-loaded singleton) ──────────────────────────────────
_finbert_pipeline = None
_finbert_available: bool | None = None  # None = untried, False = unavailable


def _get_finbert_pipeline():
    """Return the FinBERT pipeline, loading it on first call. Returns None if unavailable."""
    global _finbert_pipeline, _finbert_available
    if _finbert_available is False:
        return None
    if _finbert_pipeline is not None:
        return _finbert_pipeline
    try:
        from transformers import pipeline as hf_pipeline
        _finbert_pipeline = hf_pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            top_k=None,
        )
        _finbert_available = True
        logger.info("[sentiment_scorer] FinBERT pipeline loaded (ProsusAI/finbert)")
        return _finbert_pipeline
    except Exception as exc:
        _finbert_available = False
        logger.warning(
            "[sentiment_scorer] FinBERT unavailable (%s) — sentiment will use Claude only", exc
        )
        return None


# ── News fetchers ─────────────────────────────────────────────────────────────

def _fetch_fmp_headlines(ticker: str) -> list[str]:
    """FMP stock news — 15min TTL, sourced from major financial wire services."""
    try:
        import fmp_client as fmp
        if not fmp.is_available():
            return []
        articles = fmp.get_stock_news(ticker, limit=10)
        return [a["title"] for a in articles if a.get("title")]
    except Exception:
        return []


def _fetch_yahoo_rss(ticker: str) -> list[str]:
    """Return up to 15 headline titles from Yahoo Finance RSS for ticker."""
    url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ChiefDecifer research@decifer.ai"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
        root = ET.fromstring(body)
        titles: list[str] = []
        for item in root.findall(".//item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
        return titles[:15]
    except Exception:
        return []


def _fetch_finviz_headlines(ticker: str) -> list[str]:
    """
    Best-effort scrape of Finviz news table for ticker.
    Returns [] gracefully if Cloudflare blocks or parse fails.
    Uses lxml (already a project dependency) with XPath — no cssselect needed.
    """
    url = f"https://finviz.com/quote.ashx?t={ticker}&p=d"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finviz.com/",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html_bytes = resp.read()
        html_text = html_bytes.decode("utf-8", errors="ignore")

        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html_text)
        # Finviz news table: <table id="news-table">
        rows = tree.xpath('//table[@id="news-table"]//tr')
        headlines: list[str] = []
        for row in rows:
            links = row.xpath('.//a')
            if links:
                text = links[0].text_content().strip()
                if text and len(text) > 10:
                    headlines.append(text)
        return headlines[:15]
    except Exception:
        return []


def _collect_headlines(ticker: str) -> list[str]:
    """
    Fetch and merge headlines. FMP primary (15min TTL), Yahoo RSS + Finviz fallback.
    Deduplicates by first-60-chars of lowercased text.
    """
    fmp    = _fetch_fmp_headlines(ticker)
    yahoo  = _fetch_yahoo_rss(ticker) if len(fmp) < 5 else []
    finviz = _fetch_finviz_headlines(ticker) if len(fmp) < 5 else []
    seen: set[str] = set()
    merged: list[str] = []
    for h in fmp + yahoo + finviz:
        key = h.lower()[:60]
        if key not in seen:
            seen.add(key)
            merged.append(h)
    return merged[:20]


# ── Claude scorer (zero-shot, no chain-of-thought) ────────────────────────────

def _score_with_claude(headlines: list[str], ticker: str) -> float | None:
    """
    Direct zero-shot Claude prompt — no chain-of-thought.
    Model: claude-haiku-4-5-20251001 (fast, low-cost).
    Returns float in [-1.0, 1.0] or None on failure.

    Prompt design follows ACM ICAIF 2025 finding: direct prompts outperform
    CoT for financial sentiment; single-token numeric output minimises latency.
    """
    if not headlines:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        bullet_list = "\n".join(f"- {h}" for h in headlines[:10])
        prompt = (
            f"Ticker: {ticker}\n"
            "Rate how strongly these headlines suggest this company is an M&A target "
            "or subject to significant corporate action (activist stake, strategic review, "
            "buyout rumour, takeover bid, merger talks).\n"
            "0.0 = no M&A signal (routine earnings, product launches, analyst ratings, "
            "company acquiring small assets). "
            "1.0 = strong M&A target signal (activist SC 13D, takeover bid, "
            "exploring strategic alternatives, acquisition offer received).\n"
            "Reply with a single decimal number only.\n\n"
            f"{bullet_list}"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        score = float(text)
        return max(-1.0, min(1.0, score))
    except Exception as exc:
        logger.warning("[sentiment_scorer] Claude scoring failed for %s: %s", ticker, exc)
        return None


# ── FinBERT scorer ────────────────────────────────────────────────────────────

def _score_with_finbert(headlines: list[str]) -> float | None:
    """
    Score headlines with ProsusAI/finbert.
    Aggregates per-label probabilities across all headlines.
    Returns weighted average sentiment in [-1.0, 1.0] or None if unavailable.
    """
    if not headlines:
        return None
    pipe = _get_finbert_pipeline()
    if pipe is None:
        return None

    label_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    weighted_sum = 0.0
    total_weight = 0.0

    for headline in headlines[:15]:
        try:
            results = pipe(headline[:512], top_k=None)
            if not results:
                continue
            # results: [{"label": "positive", "score": 0.92}, ...]
            for item in results:
                lbl = item["label"].lower()
                if lbl in label_map:
                    weighted_sum += label_map[lbl] * item["score"]
                    total_weight += item["score"]
        except Exception:
            continue

    if total_weight == 0:
        return None
    return max(-1.0, min(1.0, weighted_sum / total_weight))


# ── Regression-calibrated composite ──────────────────────────────────────────

def _composite_sentiment_score(
    claude_raw: float | None,
    finbert_raw: float | None,
) -> tuple[float, list[str]]:
    """
    Combine Claude and FinBERT scores with regression-calibrated weights.
    Maps combined score [-1, 1] → 0–10 for the signal dimension.

    Returns (score_0_to_10, display_flags).
    """
    flags: list[str] = []

    if claude_raw is not None:
        flags.append(f"Claude: {claude_raw:+.2f}")
    if finbert_raw is not None:
        flags.append(f"FinBERT: {finbert_raw:+.2f}")

    if claude_raw is None and finbert_raw is None:
        return 0.0, ["No sentiment data"]

    if claude_raw is not None and finbert_raw is not None:
        raw = _CLAUDE_WEIGHT * claude_raw + _FINBERT_WEIGHT * finbert_raw
        flags.append(
            f"Composite({int(_CLAUDE_WEIGHT*100)}%C+{int(_FINBERT_WEIGHT*100)}%F): {raw:+.2f}"
        )
    elif finbert_raw is not None:
        raw = finbert_raw
        flags.append("(FinBERT only)")
    else:
        raw = claude_raw  # type: ignore[assignment]
        flags.append("(Claude only)")

    # Map [-1, 1] → [0, 10]
    score = round((raw + 1.0) / 2.0 * 10.0, 1)

    if raw >= 0.3:
        flags.append("Bullish")
    elif raw <= -0.3:
        flags.append("Bearish")
    else:
        flags.append("Neutral")

    return score, flags


# ── Single-ticker scorer ──────────────────────────────────────────────────────

def _score_ticker_sentiment(ticker: str) -> dict:
    """
    Collect headlines and compute all three sentiment scores for one ticker.

    Returns dict with keys:
      ticker, sentiment_score, sentiment_claude, sentiment_finbert,
      sentiment_flags, headlines_count, scored_at
    """
    headlines = _collect_headlines(ticker)

    claude_raw  = _score_with_claude(headlines, ticker)
    finbert_raw = _score_with_finbert(headlines)
    score, flags = _composite_sentiment_score(claude_raw, finbert_raw)

    return {
        "ticker":            ticker,
        "sentiment_score":   score,
        "sentiment_claude":  claude_raw,
        "sentiment_finbert": finbert_raw,
        "sentiment_flags":   flags,
        "headlines_count":   len(headlines),
        "scored_at":         datetime.utcnow().isoformat() + "Z",
    }


# ── Main scan ─────────────────────────────────────────────────────────────────

def run_sentiment_scan(
    tickers: list[str],
    verbose: bool = False,
) -> dict[str, dict]:
    """
    Score sentiment for each ticker.

    Returns
    -------
    Dict mapping ticker → sentiment result dict.
    Snapshots are appended to state/catalyst/sentiment_snapshots.jsonl.
    """
    CATALYST_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    today = datetime.utcnow().strftime("%Y-%m-%d")

    for i, ticker in enumerate(tickers):
        if verbose:
            print(
                f"  [sentiment_scorer] ({i+1}/{len(tickers)}) {ticker} …",
                end=" ",
                flush=True,
            )

        result = _score_ticker_sentiment(ticker)
        results[ticker] = result

        # Append snapshot for IC health tracking over time
        snapshot = {"date": today, **result}
        with SENTIMENT_SNAPSHOTS_FILE.open("a") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")

        if verbose:
            score = result["sentiment_score"]
            flags = result.get("sentiment_flags", [])
            print(f"score={score}/10  {' | '.join(flags)}")

        # Polite rate limit: Yahoo RSS + Finviz fetch + Claude API call per ticker
        time.sleep(0.5)

    return results


# ── Merge results into candidates file ───────────────────────────────────────

def merge_into_candidates(scan_results: dict[str, dict]) -> int:
    """
    Update today's candidates file with sentiment scores.
    Recomputes composite catalyst_score with 4-signal weighting:
      F:35% + O:35% + E:15% + S:15%

    Returns number of candidates updated.
    """
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    cand_file = CATALYST_DIR / f"candidates_{today}.json"
    if not cand_file.exists():
        return 0

    payload = json.loads(cand_file.read_text())
    updated = 0

    for candidate in payload.get("candidates", []):
        ticker = candidate["ticker"]
        if ticker not in scan_results:
            continue

        res = scan_results[ticker]
        candidate["sentiment_score"]   = res.get("sentiment_score", 0.0)
        candidate["sentiment_claude"]  = res.get("sentiment_claude")
        candidate["sentiment_finbert"] = res.get("sentiment_finbert")
        candidate["sentiment_flags"]   = res.get("sentiment_flags", [])

        # Recompute composite with all four signal dimensions
        f_score = candidate.get("fundamental_score", 0)
        o_score = candidate.get("options_anomaly_score", 0)
        e_score = candidate.get("edgar_score", 0)
        s_score = candidate["sentiment_score"]

        candidate["catalyst_score"] = round(
            0.35 * (f_score / 5 * 10) +
            0.35 * o_score +
            0.15 * e_score +
            0.15 * s_score,
            1,
        )
        updated += 1

    cand_file.write_text(json.dumps(payload, indent=2, default=str))
    return updated


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    parser = argparse.ArgumentParser(description="Multi-Source Sentiment Scorer")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    results = run_sentiment_scan(args.tickers, verbose=True)
    print(f"\nSummary: scored {len(results)} tickers")
    for t, r in sorted(results.items(), key=lambda x: -x[1]["sentiment_score"]):
        print(f"  {t:6s}  {r['sentiment_score']:4.1f}/10  {' | '.join(r['sentiment_flags'])}")
