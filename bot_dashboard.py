#!/usr/bin/env python3
"""
bot_dashboard.py — HTTP dashboard handler for the Decifer trading bot.

Covers: DashHandler(BaseHTTPRequestHandler) and start_dashboard().
"""

import json
import logging
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from config import CONFIG
import bot_state
from bot_state import dash, clog
from orders import flatten_all, get_open_positions
from bot_account import get_account_details
from bot_ibkr import sync_orders_from_ibkr
from bot_trading import run_scan

log = logging.getLogger("decifer.bot")


class DashHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            _bot = sys.modules.get("bot")
            html = (_bot.DASHBOARD_HTML if _bot else "") or ""
            self.wfile.write(html.encode())
        elif self.path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # Include current settings so dashboard form can show live values
            state = dict(dash)
            # Total P&L = NetLiquidation - effective capital (starting + deposits - withdrawals)
            from learning import get_effective_capital
            eff_cap = get_effective_capital()
            state["effective_capital"] = eff_cap
            # Extended account metrics for KPI row
            state["account_details"] = get_account_details()
            if state.get("performance"):
                state["performance"] = dict(state["performance"])
                state["performance"]["total_pnl"] = round(state.get("portfolio_value", 0) - eff_cap, 2)
            # Directional skew (roadmap #07)
            try:
                regime_name = (state.get("regime") or {}).get("regime", "UNKNOWN")
                from learning import get_directional_skew
                state["skew"] = {
                    "48h": get_directional_skew(window_hours=48, regime=regime_name),
                    "7d":  get_directional_skew(window_hours=168, regime=regime_name),
                }
            except Exception:
                state["skew"] = None
            # Last decision — for trade card on home page
            try:
                import os as _os
                _ld_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                         "data", "last_decision.json")
                if _os.path.exists(_ld_path):
                    with open(_ld_path) as _ldf:
                        state["last_decision"] = json.load(_ldf)
                else:
                    state["last_decision"] = None
            except Exception:
                state["last_decision"] = None
            state["settings"] = {
                "risk_pct_per_trade":       CONFIG.get("risk_pct_per_trade", 0.04),
                "daily_loss_limit":         CONFIG.get("daily_loss_limit", 0.06),
                "max_positions":            CONFIG.get("max_positions", 12),
                "min_cash_reserve":         CONFIG.get("min_cash_reserve", 0.10),
                "max_single_position":      CONFIG.get("max_single_position", 0.15),
                "min_score_to_trade":       CONFIG.get("min_score_to_trade", 28),
                "high_conviction_score":    CONFIG.get("high_conviction_score", 38),
                "agents_required_to_agree": CONFIG.get("agents_required_to_agree", 3),
                "options_min_score":        CONFIG.get("options_min_score", 35),
                "options_max_risk_pct":     CONFIG.get("options_max_risk_pct", 0.025),
                "options_max_ivr":          CONFIG.get("options_max_ivr", 65),
                "options_target_delta":     CONFIG.get("options_target_delta", 0.50),
                "options_delta_range":      CONFIG.get("options_delta_range", 0.35),
                # Sentinel settings
                "sentinel_enabled":             CONFIG.get("sentinel_enabled", True),
                "sentinel_poll_seconds":        CONFIG.get("sentinel_poll_seconds", 45),
                "sentinel_cooldown_minutes":    CONFIG.get("sentinel_cooldown_minutes", 10),
                "sentinel_max_trades_per_hour": CONFIG.get("sentinel_max_trades_per_hour", 3),
                "sentinel_risk_multiplier":     CONFIG.get("sentinel_risk_multiplier", 0.75),
                "sentinel_keyword_threshold":   CONFIG.get("sentinel_keyword_threshold", 3),
                "sentinel_min_confidence":      CONFIG.get("sentinel_min_confidence", 5),
                "sentinel_use_ibkr":            CONFIG.get("sentinel_use_ibkr", True),
                "sentinel_use_finviz":          CONFIG.get("sentinel_use_finviz", True),
            }
            self.wfile.write(json.dumps(state).encode())
        elif self.path == "/api/favourites":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"favourites": dash.get("favourites", [])}).encode())
        elif self.path == "/api/ic_weights":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                from ic_calculator import (
                    get_current_weights, get_ic_weight_history, EQUAL_WEIGHTS,
                )
                import json as _json, os as _os
                weights = get_current_weights()
                history = get_ic_weight_history(last_n=4)
                # Read raw_ic and metadata from cache file if available
                _wf = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                    "data", "ic_weights.json")
                raw_ic = {}
                updated = None
                n_records = 0
                using_equal = True
                if _os.path.exists(_wf):
                    try:
                        with open(_wf) as _f:
                            _d = _json.load(_f)
                        raw_ic      = _d.get("raw_ic", {})
                        updated     = _d.get("updated")
                        n_records   = _d.get("n_records", 0)
                        using_equal = _d.get("using_equal_weights", True)
                    except Exception:
                        pass
                payload = {
                    "weights":             weights,
                    "raw_ic":              raw_ic,
                    "updated":             updated,
                    "n_records":           n_records,
                    "using_equal_weights": using_equal,
                    "history":             history,
                }
            except Exception as exc:
                log.warning("ic_weights error: %s", exc)
                payload = {"error": str(exc), "weights": {}, "history": []}
            self.wfile.write(json.dumps(payload).encode())
        elif self.path == "/api/alpha_decay":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                from alpha_decay import get_alpha_decay_stats
                stats = get_alpha_decay_stats()
            except Exception as exc:
                log.warning("alpha_decay error: %s", exc)
                stats = {"error": str(exc), "trade_count": 0,
                         "horizons": [], "groups": {}, "optimal_horizon": None}
            self.wfile.write(json.dumps(stats).encode())
        elif self.path == "/api/portfolio":
            # Multi-account aggregated position view
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                from portfolio import get_aggregate_summary
                summary = get_aggregate_summary(bot_state.ib)
            except Exception as exc:
                log.warning("Portfolio aggregation error: %s", exc)
                summary = {"accounts": [], "positions": {}, "totals": {}, "error": str(exc)}
            self.wfile.write(json.dumps(summary).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        ib = bot_state.ib
        if self.path == "/api/kill":
            dash["killed"] = True
            clog("RISK", "🚨 KILL SWITCH — executing FLATTEN ALL immediately...")
            # Execute immediately via emergency IB connection (separate clientId)
            try:
                flatten_all(ib)  # Uses emergency connection internally; ib is fallback
                clog("RISK", "🚨 FLATTEN ALL complete")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "detail": "All positions flattened"}).encode())
            except Exception as e:
                clog("ERROR", f"🚨 FLATTEN ALL failed: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/close":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            symbol = body.get("symbol", "").upper().strip()
            if not symbol:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No symbol provided"}).encode())
            else:
                # Execute immediately via emergency IB connection (no queuing!)
                clog("TRADE", f"📤 Closing {symbol} immediately...")
                try:
                    from orders import close_position
                    result = close_position(ib, symbol)
                    if result:
                        clog("TRADE", f"✅ {result}")
                        dash["positions"] = get_open_positions()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "detail": result}).encode())
                    else:
                        clog("ERROR", f"❌ {symbol} not found in portfolio")
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": False, "error": f"{symbol} not found in portfolio"}).encode())
                except Exception as e:
                    clog("ERROR", f"❌ Close {symbol} failed: {e}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/cancel-order":
            length   = int(self.headers.get("Content-Length", 0))
            body     = json.loads(self.rfile.read(length)) if length else {}
            order_id = body.get("order_id")
            if not order_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "No order_id provided"}).encode())
            else:
                try:
                    cancelled = False
                    for t in ib.openTrades():
                        if t.order.orderId == order_id:
                            ib.cancelOrder(t.order)
                            ib.sleep(1)
                            cancelled = True
                            clog("TRADE", f"❌ Cancelled order #{order_id} ({t.contract.symbol}) via dashboard")
                            break
                    if cancelled:
                        # Update orders.json
                        from learning import update_order_status
                        update_order_status(order_id, "CANCELLED")
                        sync_orders_from_ibkr()
                        # Remove pending entry from open_trades tracker
                        from orders import open_trades
                        cancelled_keys = [k for k, v in open_trades.items()
                                          if v.get("order_id") == order_id and v.get("status") == "PENDING"]
                        for k in cancelled_keys:
                            clog("TRADE", f"Removed cancelled pending order {k} from tracker")
                            del open_trades[k]
                        dash["positions"] = get_open_positions()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "detail": f"Order #{order_id} cancelled"}).encode())
                    else:
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": False, "error": f"Order #{order_id} not found in open orders"}).encode())
                except Exception as e:
                    clog("ERROR", f"Cancel order #{order_id} failed: {e}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/api/pause":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            dash["paused"] = body.get("paused", not dash["paused"])
            self.send_response(200)
            self.end_headers()
            clog("INFO", f"Bot {'paused' if dash['paused'] else 'resumed'} via dashboard")
        elif self.path == "/api/favourites":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            favs   = [s.upper().strip() for s in body.get("favourites", []) if s.strip()]
            dash["favourites"] = favs
            _bot = sys.modules.get("bot")
            if _bot:
                _bot.save_favourites(favs)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "favourites": favs}).encode())
            clog("INFO", f"Favourites updated: {favs}")
        elif self.path == "/api/scan":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            clog("INFO", "⚡ Force scan triggered via dashboard")
            threading.Thread(target=run_scan, daemon=True).start()
        elif self.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            # Apply settings directly to CONFIG (live update, no restart needed)
            applied = []
            for key, val in body.items():
                if key in CONFIG:
                    CONFIG[key] = val
                    applied.append(key)
            # Persist to disk so settings survive restarts
            _bot = sys.modules.get("bot")
            if _bot:
                _bot.save_settings_overrides(body)
                _bot._sync_dash_from_config()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "applied": applied}).encode())
            clog("INFO", f"⚙️ Settings applied & saved via dashboard: {', '.join(applied)}")
        elif self.path == "/api/capital-adjustment":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            amount = float(body.get("amount", 0))
            note   = body.get("note", "")
            if amount != 0:
                from learning import record_capital_adjustment
                record_capital_adjustment(amount, note)
            from learning import get_effective_capital
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "effective_capital": get_effective_capital()}).encode())
        elif self.path == "/api/restart":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            clog("INFO", "🔄 Restart requested via dashboard")
            # Restart in background — replace current process with fresh one
            import subprocess, sys as _sys, os as _os

            def do_restart():
                import time
                time.sleep(1)
                ib.disconnect()
                _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

            threading.Thread(target=do_restart, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # Suppress default HTTP logs


def start_dashboard():
    server = HTTPServer(("", CONFIG["dashboard_port"]), DashHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    clog("INFO", f"Dashboard live → http://localhost:{CONFIG['dashboard_port']}")
