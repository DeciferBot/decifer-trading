#!/usr/bin/env python3
"""
bot_account.py — Account data fetching for the Decifer trading bot.

Covers: P&L subscription, portfolio value snapshot, account details,
news headline aggregation, FX snapshot, equity history persistence,
IBKR Flex Query backfill, and trade-based reconstruction.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import bot_state
from bot_state import EQUITY_FILE, clog, dash
from config import CONFIG

log = logging.getLogger("decifer.bot")

_EQUITY_BAK = EQUITY_FILE + ".bak"
_EQUITY_TMP = EQUITY_FILE + ".tmp"
_BACKFILL_DONE = False  # only run once per process


# ── Equity history persistence ────────────────────────────────────────────────


def load_equity_history() -> list:
    """Load equity history; fall back to .bak if the main file is missing or corrupt."""
    for path in (EQUITY_FILE, _EQUITY_BAK):
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    if path == _EQUITY_BAK:
                        log.warning("equity_history: loaded from backup — main file was missing/corrupt")
                    return data
        except Exception as exc:
            log.warning(f"equity_history: could not read {path} — {exc}")
    return []


def save_equity_history(history: list):
    """
    Persist equity history atomically (write-to-temp → rename) so a crash
    mid-write never corrupts the file.  Rotates a .bak copy after every 50
    saves to give a second recovery point.
    """
    global _save_counter
    try:
        trimmed = history[-2000:]
        with open(_EQUITY_TMP, "w") as f:
            json.dump(trimmed, f)
        os.replace(_EQUITY_TMP, EQUITY_FILE)

        # Rotate backup every 50 saves (~50 scan cycles ≈ a few hours)
        _save_counter = getattr(save_equity_history, "_counter", 0) + 1
        save_equity_history._counter = _save_counter
        if _save_counter % 50 == 0:
            try:
                import shutil

                shutil.copy2(EQUITY_FILE, _EQUITY_BAK)
            except Exception:
                pass
    except Exception as e:
        log.error(f"Failed to save equity history: {e}")


# ── IBKR Flex Web Service ─────────────────────────────────────────────────────

_FLEX_SEND_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
_FLEX_RECEIVE_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"


def _fetch_flex_nav(token: str, query_id: str) -> list[dict] | None:
    """
    Fetch historical daily NAV from IBKR Flex Web Service.
    Returns list of {date, value} dicts sorted oldest→newest, or None on failure.

    Setup (one-time, 5 min):
      IBKR Client Portal → Reports & Statements → Flex Queries → Create Query
        • Section: Account Information → Net Asset Value
        • Date range: all available, Period: Daily, Format: XML
      Note the Query ID.  Under the same menu: generate a Flex Token.
      Store both in .env as IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID.
    """
    log.info(f"[Flex] Requesting statement for query {query_id}...")
    try:
        params = urllib.parse.urlencode({"t": token, "q": query_id, "v": "3"})
        with urllib.request.urlopen(f"{_FLEX_SEND_URL}?{params}", timeout=30) as r:
            root = ET.fromstring(r.read())
        status = root.findtext("Status")
        ref_code = root.findtext("ReferenceCode")
        if status != "Success" or not ref_code:
            log.warning(f"[Flex] Request rejected: status={status}")
            return None

        log.info(f"[Flex] Ref={ref_code}; polling for statement...")
        time.sleep(5)
        xml_data = None
        for attempt in range(8):
            params2 = urllib.parse.urlencode({"t": token, "q": ref_code, "v": "3"})
            with urllib.request.urlopen(f"{_FLEX_RECEIVE_URL}?{params2}", timeout=30) as r:
                raw = r.read()
            if b"<FlexQueryResponse" in raw:
                xml_data = raw
                break
            try:
                err = ET.fromstring(raw)
                if err.findtext("ErrorCode") in ("1019", "1100"):
                    log.info(f"[Flex] Not ready yet ({attempt + 1}/8)...")
                    time.sleep(5)
                    continue
            except Exception:
                pass
            log.warning(f"[Flex] Unexpected response: {raw[:200]}")
            return None
        if xml_data is None:
            log.warning("[Flex] Timed out waiting for statement.")
            return None

        xml_root = ET.fromstring(xml_data)
        points: list[dict] = []

        # Primary: EquitySummaryByReportDateInBase
        for node in xml_root.iter("EquitySummaryByReportDateInBase"):
            raw_date = node.get("reportDate") or node.get("date") or ""
            total = node.get("total") or node.get("nav") or ""
            if raw_date and total:
                try:
                    dt = datetime.strptime(raw_date, "%Y-%m-%d")
                    points.append({"date": dt.strftime("%Y-%m-%d 16:00 ET"), "value": round(float(total), 2)})
                except (ValueError, TypeError):
                    pass

        # Fallback: MTMPerformanceSummaryInBase
        if not points:
            for node in xml_root.iter("MTMPerformanceSummaryInBase"):
                raw_date = node.get("date") or ""
                total = node.get("endingValue") or node.get("nav") or ""
                if raw_date and total:
                    try:
                        dt = datetime.strptime(raw_date, "%Y-%m-%d")
                        points.append({"date": dt.strftime("%Y-%m-%d 16:00 ET"), "value": round(float(total), 2)})
                    except (ValueError, TypeError):
                        pass

        if not points:
            log.warning("[Flex] No NAV data found. Ensure query includes 'Net Asset Value'.")
            return None

        points.sort(key=lambda x: x["date"])
        log.info(f"[Flex] {len(points)} daily NAV points: {points[0]['date']} → {points[-1]['date']}")
        return points

    except Exception as exc:
        log.warning(f"[Flex] Error: {exc}")
        return None


# ── Trade-based reconstruction ────────────────────────────────────────────────


def _reconstruct_from_trades(starting_capital: float, existing: list) -> list[dict]:
    """
    Approximate daily portfolio values from closed trade P&L.
    The gap between the last reconstructed day and the first real equity_history
    point is linearly interpolated (accounts for unrealized P&L drift).
    """
    try:
        from learning import load_trades as _load_trades

        trades = _load_trades()
    except Exception:
        try:
            trades_file = os.path.join(os.path.dirname(EQUITY_FILE), "trades.json")
            with open(trades_file) as f:
                trades = json.load(f)
        except Exception:
            return []

    daily_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        ts = t.get("exit_time") or t.get("timestamp") or ""
        ts = str(ts).split("T")[0].split(" ")[0]
        if ts and len(ts) == 10:
            daily_pnl[ts] += float(t.get("pnl") or 0)

    if not daily_pnl:
        return []

    existing_dates = {e["date"].split(" ")[0] for e in existing}
    anchor_date = existing[0]["date"].split(" ")[0] if existing else None
    anchor_value = existing[0]["value"] if existing else None

    # Build cumulative curve
    first_dt = datetime.strptime(min(daily_pnl.keys()), "%Y-%m-%d")
    day_zero = (first_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    cumulative = starting_capital
    trade_days: list[tuple[str, float]] = [(day_zero, starting_capital)]
    for d in sorted(daily_pnl.keys()):
        cumulative += daily_pnl[d]
        trade_days.append((d, cumulative))

    # Linear interpolation over the unrealized-P&L gap
    if anchor_date and anchor_value is not None:
        points_before = [(d, v) for d, v in trade_days if d <= anchor_date]
        if points_before:
            last_d, last_v = points_before[-1]
            if last_d < anchor_date:
                gap_days = (datetime.strptime(anchor_date, "%Y-%m-%d") - datetime.strptime(last_d, "%Y-%m-%d")).days
                step = (anchor_value - last_v) / max(gap_days, 1)
                adjusted = []
                for d, v in trade_days:
                    if last_d < d < anchor_date:
                        elapsed = (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(last_d, "%Y-%m-%d")).days
                        v = round(last_v + step * elapsed, 2)
                    adjusted.append((d, v))
                trade_days = adjusted

    points: list[dict] = []
    seen: set[str] = set()
    for d, v in trade_days:
        if d in existing_dates:
            continue
        for hhmm in ("09:30", "16:00"):
            key = f"{d} {hhmm} ET"
            if key not in seen:
                seen.add(key)
                points.append({"date": key, "value": round(v, 2)})

    points.sort(key=lambda x: x["date"])
    log.info(
        f"[Reconstruct] {len(points)} points generated: "
        f"{points[0]['date'] if points else 'none'} → "
        f"{points[-1]['date'] if points else 'none'}"
    )
    return points


# ── Auto-backfill on startup ──────────────────────────────────────────────────


def backfill_equity_history_if_needed() -> bool:
    """
    Called once at bot startup.  If equity history is missing or covers fewer
    than 30 days, attempts to extend it:
      1. IBKR Flex Web Service (accurate daily NAV) — requires token in config/.env
      2. Trade-based reconstruction (approximate, no setup required)

    Returns True if the history was extended.
    """
    global _BACKFILL_DONE
    if _BACKFILL_DONE:
        return False
    _BACKFILL_DONE = True

    history = dash.get("equity_history", [])
    if history:
        earliest = history[0]["date"].split(" ")[0]
        earliest_dt = datetime.strptime(earliest, "%Y-%m-%d")
        days_back = (datetime.now() - earliest_dt).days
        if days_back >= 30:
            log.info(f"[Backfill] History covers {days_back} days — no backfill needed.")
            return False

    log.info("[Backfill] Equity history is shallow (<30 days). Attempting recovery...")

    capital_file = os.path.join(os.path.dirname(EQUITY_FILE), "capital_base.json")
    try:
        with open(capital_file) as f:
            starting = json.load(f).get("starting_capital", 1_000_000)
    except Exception:
        starting = 1_000_000

    new_points: list[dict] | None = None

    # ── Method 1: IBKR Flex ───────────────────────────────────────────────────
    token = CONFIG.get("ibkr_flex_token", "") or os.environ.get("IBKR_FLEX_TOKEN", "")
    query_id = CONFIG.get("ibkr_flex_query_id", "") or os.environ.get("IBKR_FLEX_QUERY_ID", "")
    if token and query_id:
        clog("INFO", "[Backfill] Fetching historical NAV from IBKR Flex Web Service...")
        new_points = _fetch_flex_nav(token, query_id)
        if new_points:
            clog("INFO", f"[Backfill] Flex: {len(new_points)} NAV points retrieved.")
    else:
        log.info(
            "[Backfill] Flex Query skipped (no IBKR_FLEX_TOKEN/IBKR_FLEX_QUERY_ID). "
            "Set them in .env for accurate IBKR historical NAV."
        )

    # ── Method 2: trade reconstruction ───────────────────────────────────────
    if not new_points:
        clog("INFO", "[Backfill] Reconstructing equity curve from trade history...")
        new_points = _reconstruct_from_trades(starting, history)

    if not new_points:
        log.warning("[Backfill] No historical data recovered.")
        return False

    # Merge: deduplicate, sort, cap at 2000
    existing_dates = {e["date"] for e in history}
    merged = sorted([p for p in new_points if p["date"] not in existing_dates] + history, key=lambda x: x["date"])
    if len(merged) > 2000:
        merged = merged[-2000:]

    added = len(merged) - len(history)
    if added <= 0:
        log.info("[Backfill] All recovered dates already present.")
        return False

    dash["equity_history"] = merged
    save_equity_history(merged)
    clog("INFO", f"[Backfill] +{added} historical points. History now: {merged[0]['date']} → {merged[-1]['date']}")
    return True


# ── Account data ──────────────────────────────────────────────────────────────


def _prev_day_close_value() -> float | None:
    """Return the last equity snapshot from the most recent previous day."""
    history = dash.get("equity_history", [])
    if not history:
        return None
    today_str = datetime.now().strftime("%Y-%m-%d")
    for entry in reversed(history):
        d = entry.get("date", "")
        if not d.startswith(today_str):
            return entry.get("value")
    return None


def get_account_data():
    """Fetch portfolio value and daily P&L from IBKR."""
    ib = bot_state.ib
    try:
        vals = ib.accountValues(CONFIG["active_account"])
        pv = 0.0
        for v in vals:
            if v.tag == "NetLiquidation" and v.currency == "USD":
                pv = float(v.value)
                break
        pnl = 0.0
        if pv > 0:
            # Prefer IBKR's native daily P&L from the reqPnL subscription.
            # dailyPnL == nan means IBKR hasn't pushed a value yet — fall back to equity history.
            sub = bot_state._pnl_subscription
            if sub is not None:
                raw = getattr(sub, "dailyPnL", math.nan)
                if not math.isnan(raw):
                    return pv, round(raw, 2)
            # Fallback: delta from yesterday's last equity snapshot
            prev_close = _prev_day_close_value()
            if prev_close is not None and prev_close > 0:
                pnl = round(pv - prev_close, 2)
        return pv, pnl
    except Exception as e:
        clog("ERROR", f"Account data error: {e}")
        return dash["portfolio_value"], dash["daily_pnl"]


def get_account_details():
    """Fetch extended account metrics from IBKR for dashboard KPI row."""
    ib = bot_state.ib
    details = {}
    try:
        vals = ib.accountValues(CONFIG["active_account"])
        tag_map = {
            "AvailableFunds": "available_cash",
            "BuyingPower": "buying_power",
            "GrossPositionValue": "gross_position_value",
            "MaintMarginReq": "margin_used",
            "ExcessLiquidity": "excess_liquidity",
            "TotalCashValue": "total_cash",
            "UnrealizedPnL": "unrealized_pnl",
            "RealizedPnL": "realized_pnl",
            "NetLiquidation": "net_liquidation",
        }
        for v in vals:
            if v.tag in tag_map and v.currency == "USD":
                try:
                    details[tag_map[v.tag]] = round(float(v.value), 2)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        clog("ERROR", f"Account details error: {e}")
    return details


# ── News & FX helpers ─────────────────────────────────────────────────────────


def get_news_headlines() -> list:
    """Return recent news headlines from cached scan data for agents."""
    try:
        all_headlines = []
        for sym, ndata in dash.get("news_data", {}).items():
            for h in ndata.get("headlines", [])[:3]:
                all_headlines.append(f"[{sym}] {h}")
        return all_headlines[:20]
    except Exception:
        return []


def get_fx_snapshot() -> dict:
    """Get snapshot of key FX pairs."""
    pairs = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X"}
    result = {}

    def fetch_pair(name, ticker):
        try:
            from signals import _safe_download

            data = _safe_download(ticker, period="1d", interval="1h", progress=False, auto_adjust=True)
            if data is not None and len(data) > 1:
                price = float(data["Close"].squeeze().iloc[-1])
                prev = float(data["Close"].squeeze().iloc[-2])
                return name, {"price": round(price, 5), "change_pct": round((price - prev) / prev * 100, 3)}
        except Exception:
            pass
        return name, None

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_pair, name, ticker) for name, ticker in pairs.items()]
        for future in futures:
            name, data = future.result()
            if data:
                result[name] = data
    return result
