# Tests for universe_committed.py — name prefilter + dollar-volume ranking.
# No Alpaca calls — all I/O is mocked.

from __future__ import annotations

import json
from unittest.mock import patch

from universe_committed import (
    _is_common_stock,
    load_committed_universe,
    refresh_committed_universe,
)


# ── _is_common_stock ──────────────────────────────────────────────────────────


def test_is_common_stock_accepts_regular_tickers():
    for sym in ("AAPL", "MSFT", "NVDA", "SPY", "QQQ", "AMD", "F", "T"):
        assert _is_common_stock(sym), f"{sym!r} should pass"


def test_is_common_stock_rejects_warrants_units_rights():
    for sym in ("FOO.WS", "BAR.U", "BAZ.R", "QUX.WT"):
        assert not _is_common_stock(sym), f"{sym!r} should be rejected"


def test_is_common_stock_rejects_symbols_with_digits():
    # Often preferreds / when-issued tranches
    assert not _is_common_stock("BRK2")
    assert not _is_common_stock("A1B")


def test_is_common_stock_rejects_overlong_symbols():
    assert not _is_common_stock("ABCDEF")  # 6 chars


def test_is_common_stock_rejects_empty_or_non_ascii():
    assert not _is_common_stock("")
    assert not _is_common_stock("ÉÑ")


# ── refresh_committed_universe ────────────────────────────────────────────────


def test_refresh_ranks_by_dollar_volume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assets = [{"symbol": s, "exchange": "NASDAQ"} for s in ["A", "B", "C"]]
    snaps = {
        "A": {"prior_close": 100.0, "prev_volume": 10_000_000, "price": 100.0},   # 1B
        "B": {"prior_close": 50.0, "prev_volume": 100_000_000, "price": 50.0},    # 5B
        "C": {"prior_close": 200.0, "prev_volume": 20_000_000, "price": 200.0},   # 4B
    }
    with patch("universe_committed.get_all_tradable_equities", return_value=assets), \
         patch("universe_committed.fetch_snapshots_batched", return_value=snaps):
        result = refresh_committed_universe(top_n=10)
    assert [r["symbol"] for r in result] == ["B", "C", "A"]


def test_refresh_filters_low_price_low_volume_low_dv(tmp_path, monkeypatch):
    """Filter thresholds: price ≥ $1, prev_vol ≥ 50k, dv ≥ $1M."""
    monkeypatch.chdir(tmp_path)
    assets = [{"symbol": s, "exchange": "NASDAQ"} for s in ["OK", "CHEAP", "THIN", "TINY"]]
    snaps = {
        "OK":    {"prior_close": 10.0, "prev_volume": 1_000_000, "price": 10.0},  # dv = 10M ✓
        "CHEAP": {"prior_close": 0.50, "prev_volume": 10_000_000, "price": 0.50}, # price < 1
        "THIN":  {"prior_close": 100.0, "prev_volume": 10_000, "price": 100.0},   # vol < 50k
        "TINY":  {"prior_close": 5.0, "prev_volume": 100_000, "price": 5.0},      # dv = 500k < 1M
    }
    with patch("universe_committed.get_all_tradable_equities", return_value=assets), \
         patch("universe_committed.fetch_snapshots_batched", return_value=snaps):
        result = refresh_committed_universe(top_n=10)
    assert [r["symbol"] for r in result] == ["OK"]


def test_refresh_writes_json_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assets = [{"symbol": "AAPL", "exchange": "NASDAQ"}]
    snaps = {"AAPL": {"prior_close": 200.0, "prev_volume": 10_000_000, "price": 201.0}}

    with patch("universe_committed.get_all_tradable_equities", return_value=assets), \
         patch("universe_committed.fetch_snapshots_batched", return_value=snaps):
        refresh_committed_universe(top_n=1)

    path = tmp_path / "data" / "committed_universe.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["count"] == 1
    assert payload["symbols"][0]["symbol"] == "AAPL"
    assert "refreshed_at" in payload


def test_load_committed_universe_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_committed_universe() == []


def test_load_committed_universe_reads_written_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "committed_universe.json").write_text(
        json.dumps({"symbols": [{"symbol": "AAPL"}, {"symbol": "NVDA"}]})
    )
    assert load_committed_universe() == ["AAPL", "NVDA"]
