# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  portfolio.py                               ║
# ║   Multi-account position aggregation                         ║
# ║   Fetches positions across all configured IBKR accounts,     ║
# ║   merges them by symbol, and computes net exposure/P&L.      ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import logging

from config import CONFIG

log = logging.getLogger("decifer.portfolio")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _portfolio_item_to_dict(item) -> dict:
    """Convert an ib_async PortfolioItem to a plain serialisable dict."""
    contract = item.contract
    symbol = getattr(contract, "symbol", "UNKNOWN")
    sec_type = getattr(contract, "secType", "STK")
    # FX: IBKR stores symbol=base currency (e.g. "EUR") + currency="USD"
    # Reconstruct the full pair (e.g. "EURUSD") so positions show correctly.
    if sec_type == "CASH":
        _quote = getattr(contract, "currency", "")
        if _quote:
            symbol = symbol + _quote
    return {
        "symbol": symbol,
        "sec_type": sec_type,
        "position": item.position,
        "market_price": item.marketPrice,
        "market_value": item.marketValue,
        "avg_cost": item.averageCost,
        "unrealized_pnl": item.unrealizedPNL,
        "realized_pnl": item.realizedPNL,
        "currency": getattr(contract, "currency", "USD"),
        # Option-specific (None for equities)
        "strike": getattr(contract, "strike", None),
        "right": getattr(contract, "right", None),
        "expiry": getattr(contract, "lastTradeDateOrContractMonth", None),
    }


def _position_key(pos: dict) -> str:
    """Composite key that keeps stock and option positions separate."""
    if pos["sec_type"] == "OPT":
        return f"{pos['symbol']}_{pos['right']}_{pos['strike']}_{pos['expiry']}"
    return pos["symbol"]


# ── Account list resolution ──────────────────────────────────────────────────


def get_accounts_to_aggregate() -> list[str]:
    """Return the list of account IDs to include in aggregation.

    Priority:
      1. ``CONFIG["aggregate_accounts"]`` (explicit override list)
      2. All non-empty values from ``CONFIG["accounts"]`` registry

    Filters out empty strings so unconfigured slots are ignored.
    """
    explicit: list[str] = CONFIG.get("aggregate_accounts", [])
    if explicit:
        return [a for a in explicit if a]

    accounts_map: dict = CONFIG.get("accounts", {})
    return [v for v in accounts_map.values() if v]


# ── Per-account fetch ────────────────────────────────────────────────────────


def fetch_account_positions(ib, account_id: str) -> list[dict]:
    """Fetch all open positions for *account_id* via ib.portfolio().

    Returns an empty list on error so a single bad account never blocks
    the aggregation of the rest.
    """
    try:
        items = ib.portfolio(account_id)
        positions = [_portfolio_item_to_dict(i) for i in items if i.position != 0]
        log.debug("Account %s — %d open position(s)", account_id, len(positions))
        return positions
    except Exception as exc:
        log.warning("Could not fetch positions for account %s: %s", account_id, exc)
        return []


# ── Merge ────────────────────────────────────────────────────────────────────


def merge_positions(account_data: dict[str, list[dict]]) -> dict[str, dict]:
    """Merge per-account position lists into a single dict keyed by symbol.

    For each unique position key, sums:
      - net_position (qty)
      - market_value
      - unrealized_pnl
      - realized_pnl

    Also stores a per-account breakdown under ``merged[key]["accounts"]``.
    """
    merged: dict[str, dict] = {}

    for account_id, positions in account_data.items():
        for pos in positions:
            key = _position_key(pos)

            if key not in merged:
                merged[key] = {
                    "symbol": pos["symbol"],
                    "sec_type": pos["sec_type"],
                    "net_position": 0,
                    "market_value": 0.0,
                    "avg_cost": pos["avg_cost"],  # from first account seen
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "currency": pos["currency"],
                    # Option fields (None for equities)
                    "strike": pos.get("strike"),
                    "right": pos.get("right"),
                    "expiry": pos.get("expiry"),
                    # Per-account breakdown
                    "accounts": {},
                }

            merged[key]["net_position"] += pos["position"]
            merged[key]["market_value"] += pos["market_value"]
            merged[key]["unrealized_pnl"] += pos["unrealized_pnl"]
            merged[key]["realized_pnl"] += pos["realized_pnl"]
            merged[key]["accounts"][account_id] = {
                "position": pos["position"],
                "market_value": pos["market_value"],
                "unrealized_pnl": pos["unrealized_pnl"],
                "avg_cost": pos["avg_cost"],
            }

    return merged


# ── Exposure computation ─────────────────────────────────────────────────────


def compute_net_exposure(merged: dict[str, dict]) -> dict[str, dict]:
    """Add ``exposure_pct`` and ``direction`` to every position in *merged*.

    ``exposure_pct`` = |market_value| / total_abs_market_value × 100

    Mutates and returns the same dict so callers can chain calls.
    """
    total_abs = sum(abs(p["market_value"]) for p in merged.values())

    for pos in merged.values():
        if total_abs > 0:
            pos["exposure_pct"] = round(abs(pos["market_value"]) / total_abs * 100, 2)
        else:
            pos["exposure_pct"] = 0.0

        net = pos["net_position"]
        pos["direction"] = "LONG" if net > 0 else ("SHORT" if net < 0 else "FLAT")

    return merged


# ── Top-level aggregation ─────────────────────────────────────────────────────


def get_aggregate_summary(ib, accounts: list[str] | None = None) -> dict:
    """Fetch, merge, and summarise positions across *accounts*.

    Args:
        ib:       Connected ib_async IB instance.
        accounts: Account IDs to query. Defaults to ``get_accounts_to_aggregate()``.

    Returns a dict with keys:
        - ``accounts``  — list of account IDs queried
        - ``positions`` — merged position dict (keyed by composite symbol key)
        - ``totals``    — aggregate P&L and exposure metrics
    """
    if accounts is None:
        accounts = get_accounts_to_aggregate()

    if not accounts:
        log.warning("No accounts configured for aggregation — returning empty summary")
        return {
            "accounts": [],
            "positions": {},
            "totals": _empty_totals(),
        }

    # 1 — Fetch per-account
    account_data: dict[str, list[dict]] = {}
    for acct in accounts:
        account_data[acct] = fetch_account_positions(ib, acct)

    # 2 — Merge by symbol
    merged = merge_positions(account_data)

    # 3 — Compute exposure
    merged = compute_net_exposure(merged)

    # 4 — Roll-up totals
    totals = _compute_totals(merged)

    return {
        "accounts": accounts,
        "positions": merged,
        "totals": totals,
    }


# ── Internal helpers ─────────────────────────────────────────────────────────


def _empty_totals() -> dict:
    return {
        "market_value": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "total_pnl": 0.0,
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "position_count": 0,
        "long_count": 0,
        "short_count": 0,
        "long_exposure_pct": 0.0,
        "short_exposure_pct": 0.0,
    }


def _compute_totals(merged: dict[str, dict]) -> dict:
    """Roll up per-position figures into portfolio-level totals."""
    long_pos = [p for p in merged.values() if p["direction"] == "LONG"]
    short_pos = [p for p in merged.values() if p["direction"] == "SHORT"]

    total_mv = sum(p["market_value"] for p in merged.values())
    total_unreal = sum(p["unrealized_pnl"] for p in merged.values())
    total_real = sum(p["realized_pnl"] for p in merged.values())
    gross_exp = sum(abs(p["market_value"]) for p in merged.values())
    long_exp = sum(p["market_value"] for p in long_pos)
    short_exp = sum(abs(p["market_value"]) for p in short_pos)

    return {
        "market_value": round(total_mv, 2),
        "unrealized_pnl": round(total_unreal, 2),
        "realized_pnl": round(total_real, 2),
        "total_pnl": round(total_unreal + total_real, 2),
        "gross_exposure": round(gross_exp, 2),
        "net_exposure": round(total_mv, 2),
        "position_count": len(merged),
        "long_count": len(long_pos),
        "short_count": len(short_pos),
        "long_exposure_pct": round(long_exp / gross_exp * 100, 1) if gross_exp > 0 else 0.0,
        "short_exposure_pct": round(short_exp / gross_exp * 100, 1) if gross_exp > 0 else 0.0,
    }
