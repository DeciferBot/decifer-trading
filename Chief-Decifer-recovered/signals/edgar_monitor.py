"""
SEC EDGAR RSS Monitor
======================
Polls the SEC EDGAR RSS feeds for activist / insider filing activity that can
precede acquisition announcements:

  - SC 13D  — activist investor crossing 5% stake with intent to influence
  - SC 13G  — passive investor crossing 5% stake
  - Form 4  — insider buy/sell transactions

Uses only Python stdlib (xml.etree.ElementTree + urllib) — no extra deps.

A CIK → ticker map is downloaded from SEC's public JSON once per session and
cached in state/catalyst/sec_tickers.json.

Run standalone:  python -m signals.edgar_monitor
Called from app: from signals.edgar_monitor import run_edgar_poll

Output: state/catalyst/edgar_events.json  (rolling last-7-days window)
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from config import CATALYST_DIR  # noqa: E402  chief-decifer/state/internal/catalyst/
EDGAR_FILE     = CATALYST_DIR / "edgar_events.json"
SEC_TICKERS_CACHE = CATALYST_DIR / "sec_tickers.json"

# SEC EDGAR RSS endpoints (public, no auth required)
_FEEDS = {
    "SC 13D": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13D&dateb=&owner=include&count=40&search_text=&output=atom",
    "SC 13G": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13G&dateb=&owner=include&count=40&search_text=&output=atom",
    "4":      "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=40&search_text=&output=atom",
}

_ATOM_NS = "http://www.w3.org/2005/Atom"


# ── SEC company tickers cache ─────────────────────────────────────────────────

def _load_sec_tickers() -> dict[str, str]:
    """
    Returns {cik_str → ticker} mapping.
    Downloads from SEC once and caches locally.  CIK is zero-padded to 10 digits.
    """
    # Use cache if less than 24 hours old
    if SEC_TICKERS_CACHE.exists():
        age_hours = (time.time() - SEC_TICKERS_CACHE.stat().st_mtime) / 3600
        if age_hours < 24:
            try:
                return json.loads(SEC_TICKERS_CACHE.read_text())
            except Exception:
                pass

    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChiefDecifer research@decifer.ai"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        # raw is {index: {cik_str, ticker, title}}
        mapping: dict[str, str] = {}
        for entry in raw.values():
            cik = str(entry.get("cik_str", "")).zfill(10)
            ticker = entry.get("ticker", "")
            if cik and ticker:
                mapping[cik] = ticker.upper()

        CATALYST_DIR.mkdir(parents=True, exist_ok=True)
        SEC_TICKERS_CACHE.write_text(json.dumps(mapping))
        return mapping
    except Exception as exc:
        print(f"  [edgar_monitor] WARNING: Could not fetch SEC tickers: {exc}", file=sys.stderr)
        return {}


def _cik_from_url(url: str) -> str | None:
    """Extract zero-padded 10-digit CIK from an EDGAR filing URL."""
    import re
    m = re.search(r"/cgi-bin/browse-edgar\?action=getcompany&CIK=(\d+)", url)
    if m:
        return m.group(1).zfill(10)
    m = re.search(r"/(\d{10})/", url)
    if m:
        return m.group(1)
    return None


# ── RSS fetcher ───────────────────────────────────────────────────────────────

def _fetch_feed(form_type: str, url: str) -> list[dict]:
    """
    Fetch and parse a single EDGAR RSS Atom feed.
    Returns list of event dicts.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChiefDecifer research@decifer.ai"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
    except urllib.error.URLError as exc:
        print(f"  [edgar_monitor] WARNING: Feed fetch failed ({form_type}): {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        print(f"  [edgar_monitor] WARNING: XML parse error ({form_type}): {exc}", file=sys.stderr)
        return []

    events = []
    ns = {"atom": _ATOM_NS}

    for entry in root.findall("atom:entry", ns):
        title_el    = entry.find("atom:title", ns)
        updated_el  = entry.find("atom:updated", ns)
        category_el = entry.find("atom:category", ns)
        link_el     = entry.find("atom:link", ns)
        summary_el  = entry.find("atom:summary", ns)

        title      = title_el.text.strip()   if title_el   is not None else ""
        updated    = updated_el.text.strip()  if updated_el is not None else ""
        category   = category_el.attrib.get("term", "") if category_el is not None else form_type
        link       = link_el.attrib.get("href", "")     if link_el     is not None else ""
        summary    = summary_el.text or ""               if summary_el  is not None else ""

        # Extract company name from title: "CompanyName (Form TYPE) ..."
        company_name = title.split("(")[0].strip() if "(" in title else title

        cik = _cik_from_url(link)

        events.append({
            "form_type":    form_type,
            "company_name": company_name,
            "cik":          cik,
            "ticker":       None,           # resolved below
            "title":        title,
            "updated":      updated,
            "link":         link,
            "summary":      summary[:300],
            "fetched_at":   datetime.now(tz=timezone.utc).isoformat(),
        })

    return events


# ── Ticker resolution ─────────────────────────────────────────────────────────

def _resolve_tickers(events: list[dict], cik_to_ticker: dict[str, str]) -> list[dict]:
    for ev in events:
        cik = ev.get("cik")
        if cik and cik in cik_to_ticker:
            ev["ticker"] = cik_to_ticker[cik]
    return events


# ── Watchlist cross-reference ─────────────────────────────────────────────────

def _flag_watchlist_hits(events: list[dict], watchlist_tickers: set[str]) -> list[dict]:
    """Mark events whose resolved ticker is in the M&A candidate watchlist."""
    for ev in events:
        ev["on_watchlist"] = (ev.get("ticker") or "").upper() in watchlist_tickers
    return events


# ── Score computation ─────────────────────────────────────────────────────────

def _edgar_score_for_ticker(ticker: str, events: list[dict]) -> tuple[int, list[str]]:
    """
    Compute an EDGAR signal score (0–10) for a ticker based on recent events.
    """
    ticker_events = [e for e in events if (e.get("ticker") or "").upper() == ticker.upper()]
    if not ticker_events:
        return 0, []

    score = 0
    notes = []

    activist_13d = [e for e in ticker_events if e["form_type"] == "SC 13D"]
    passive_13g  = [e for e in ticker_events if e["form_type"] == "SC 13G"]
    form4        = [e for e in ticker_events if e["form_type"] == "4"]

    if activist_13d:
        score += 7   # strongest signal — activist taking stake
        notes.append(f"SC 13D activist filing ({len(activist_13d)})")
    if passive_13g:
        score += 4
        notes.append(f"SC 13G passive >5% stake ({len(passive_13g)})")
    if len(form4) >= 2:
        score += 3   # cluster buying (2+ insiders in same period)
        notes.append(f"Insider cluster buys ({len(form4)} Form 4s)")
    elif len(form4) == 1:
        score += 1
        notes.append("Single insider buy (Form 4)")

    return min(10, score), notes


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_existing_events() -> list[dict]:
    if EDGAR_FILE.exists():
        try:
            return json.loads(EDGAR_FILE.read_text())
        except Exception:
            pass
    return []


def _save_events(events: list[dict]) -> None:
    """Keep only events from the last 7 days; deduplicate by (form_type, cik, updated)."""
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=7)).isoformat()

    existing = _load_existing_events()

    # Deduplicate: key = (form_type, cik, updated)
    seen = {(e["form_type"], e.get("cik"), e.get("updated")): e for e in existing}
    for ev in events:
        key = (ev["form_type"], ev.get("cik"), ev.get("updated"))
        seen[key] = ev

    # Prune old events
    fresh = [e for e in seen.values() if e.get("updated", "") >= cutoff]
    fresh.sort(key=lambda e: e.get("updated", ""), reverse=True)

    CATALYST_DIR.mkdir(parents=True, exist_ok=True)
    EDGAR_FILE.write_text(json.dumps(fresh, indent=2, default=str))


# ── Candidates update ─────────────────────────────────────────────────────────

def merge_into_candidates(events: list[dict]) -> int:
    """Update today's candidates file with EDGAR scores. Returns updated count."""
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    cand_file = CATALYST_DIR / f"candidates_{today}.json"
    if not cand_file.exists():
        return 0

    payload = json.loads(cand_file.read_text())
    updated = 0
    for candidate in payload.get("candidates", []):
        ticker = candidate["ticker"]
        e_score, e_notes = _edgar_score_for_ticker(ticker, events)
        if e_score > 0:
            candidate["edgar_score"]  = e_score
            candidate["edgar_events"] = e_notes
            # Recompute composite (F:35% + O:35% + E:15% + S:15%)
            f_score = candidate.get("fundamental_score", 0)
            o_score = candidate.get("options_anomaly_score", 0)
            s_score = candidate.get("sentiment_score", 0.0)
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


# ── Main ─────────────────────────────────────────────────────────────────────

def run_edgar_poll(
    watchlist_tickers: list[str] | None = None,
    verbose: bool = False,
) -> list[dict]:
    """
    Poll all monitored EDGAR feeds, resolve tickers, flag watchlist hits,
    save to edgar_events.json.

    Parameters
    ----------
    watchlist_tickers : additional tickers to watch for (e.g. from catalyst screen)
    verbose           : print progress

    Returns
    -------
    List of all fresh events collected.
    """
    CATALYST_DIR.mkdir(parents=True, exist_ok=True)

    if verbose:
        print("  [edgar_monitor] Loading SEC ticker map …")
    cik_to_ticker = _load_sec_tickers()

    watchlist = set((watchlist_tickers or []))
    # Also load today's candidate tickers
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    cand_file = CATALYST_DIR / f"candidates_{today}.json"
    if cand_file.exists():
        try:
            cands = json.loads(cand_file.read_text()).get("candidates", [])
            watchlist |= {c["ticker"] for c in cands}
        except Exception:
            pass

    all_events: list[dict] = []

    for form_type, url in _FEEDS.items():
        if verbose:
            print(f"  [edgar_monitor] Polling {form_type} feed …")
        events = _fetch_feed(form_type, url)
        events = _resolve_tickers(events, cik_to_ticker)
        events = _flag_watchlist_hits(events, watchlist)
        all_events.extend(events)
        time.sleep(1.0)  # be polite to SEC servers

    _save_events(all_events)

    if verbose:
        hits = [e for e in all_events if e.get("on_watchlist")]
        print(f"  [edgar_monitor] Fetched {len(all_events)} events, {len(hits)} watchlist hits")

    return all_events


if __name__ == "__main__":
    events = run_edgar_poll(verbose=True)
    print("\nWatchlist hits:")
    for e in events:
        if e.get("on_watchlist"):
            print(f"  {e['ticker']:6s}  {e['form_type']:6s}  {e['company_name']}")
    print("\nAll SC 13D filings (last 40):")
    for e in events:
        if e["form_type"] == "SC 13D":
            ticker = e.get("ticker") or "?"
            print(f"  {ticker:6s}  {e['company_name'][:50]}  {e['updated'][:10]}")
