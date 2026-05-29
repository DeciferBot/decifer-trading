"use client";

import { useEffect, useState, useCallback } from "react";
import { ArrowRight, CircleDot, AlertTriangle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Regime, type Position } from "@/lib/api";
import {
  fmtMoney, fmtPct, pnlColor,
  translateRegime, translateVix, translateSession, cleanThesis, fmtNYTime, fmtLocalTime,
} from "@/lib/translate";
import type { Tab } from "@/components/BottomNav";

interface Props { onTabChange: (t: Tab) => void }

function calcPnl(pos: Position): number {
  if (pos.pnl != null) return pos.pnl;
  const diff = (pos.current ?? 0) - (pos.entry ?? 0);
  return diff * (pos.direction === "SHORT" ? -1 : 1) * Math.abs(pos.qty ?? 0);
}

// How far current price is from the stop loss, as % of current price.
// Returns null if stop is unknown. Positive = safe buffer.
function stopProximityPct(pos: Position): number | null {
  if (!pos.sl || !pos.current) return null;
  const dist = pos.direction === "SHORT"
    ? (pos.sl - pos.current) / pos.current * 100
    : (pos.current - pos.sl)  / pos.current * 100;
  return Math.max(0, dist);
}

function PositionRow({ pos, onTap }: { pos: Position; onTap: () => void }) {
  const isLong  = pos.direction !== "SHORT";
  const pnl     = calcPnl(pos);
  const pct     = pos.entry ? ((pos.current - pos.entry) / pos.entry) * 100 * (isLong ? 1 : -1) : 0;
  const prox    = stopProximityPct(pos);
  const isNear  = prox !== null && prox < 3;

  // Buffer bar: 0–20% proximity maps to 0–100% width
  const barW    = prox !== null ? Math.min(prox / 20 * 100, 100) : null;
  const barColor = prox === null ? "" : prox < 3 ? "bg-rose-500" : prox < 8 ? "bg-amber-400" : "bg-emerald-500";

  return (
    <button
      onClick={onTap}
      className="w-full text-left py-3 border-b border-[#1e2a3a] last:border-0 active:opacity-70"
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          {isNear && <AlertTriangle size={11} className="text-rose-400 shrink-0" />}
          <span className="text-sm font-bold text-white">{pos.symbol}</span>
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
            isLong ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"
          }`}>
            {isLong ? "↑ Long" : "↓ Short"}
          </span>
        </div>
        <div className="text-right">
          <span className={`text-sm font-bold ${pnlColor(pnl)}`}>
            {pnl >= 0 ? "+" : ""}{fmtMoney(pnl, true)}
          </span>
          <span className={`text-[10px] ml-1 ${pnlColor(pct)}`}>
            ({fmtPct(pct)})
          </span>
        </div>
      </div>

      {/* Stop proximity buffer bar — full = safe, empty = at stop */}
      {barW !== null && (
        <div className="h-[3px] w-full rounded-full bg-[#1e2a3a] overflow-hidden">
          <div className={`h-full rounded-full ${barColor}`} style={{ width: `${barW}%` }} />
        </div>
      )}
    </button>
  );
}

export default function TodayView({ onTabChange }: Props) {
  const [state,   setState]   = useState<BotState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api.get<BotState>("/api/state");
      setState(s);
      setError(null);
    } catch {
      setError("Can't reach the bot right now.");
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-compiler/react-compiler
  useEffect(() => {
    load();
    const t = setInterval(load, 12_000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return (
    <div className="px-5 pt-6 space-y-4">
      <Skeleton className="h-6 w-28 bg-[#161e2e]" />
      <Skeleton className="h-20 w-full bg-[#161e2e]" />
      <Skeleton className="h-10 w-full rounded-xl bg-[#161e2e]" />
      <Skeleton className="h-36 w-full rounded-2xl bg-[#161e2e]" />
    </div>
  );

  if (error || !state) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3 p-6">
      <p className="text-slate-400 text-center text-sm">{error ?? "No data"}</p>
      <button
        onClick={() => { setLoading(true); load(); }}
        className="px-4 py-2 rounded-full text-xs font-semibold bg-slate-800 text-slate-300 active:bg-slate-700"
      >
        Retry
      </button>
    </div>
  );

  const regime     = state.regime as Regime | undefined;
  const vixValue   = regime?.vix ?? 0;
  const vix        = translateVix(vixValue);
  const regimeName = regime?.regime ?? "UNKNOWN";
  const positions  = state.positions ?? [];
  const dayPnl     = state.daily_pnl ?? 0;
  const portValue  = state.portfolio_value ?? 0;
  const dayPct     = portValue > 0 ? (dayPnl / portValue) * 100 : 0;
  const isRunning  = !state.paused && state.session !== "CLOSED" && state.session !== "WEEKEND";

  const isPanic = regimeName === "PANIC" || vixValue >= 30;
  const isBull  = (regimeName.includes("BULL") || regimeName.includes("TRENDING_UP")) && vixValue < 22;

  const unrealized = positions.reduce((sum, p) => sum + calcPnl(p), 0);

  // Sort by urgency: positions nearest stop first
  const sorted = [...positions].sort((a, b) => (stopProximityPct(a) ?? 100) - (stopProximityPct(b) ?? 100));
  const alerts = sorted.filter(p => { const pr = stopProximityPct(p); return pr !== null && pr < 3; });

  const spyChg = regime?.spy_chg_1d;
  const qqqChg = regime?.qqq_chg_1d;

  return (
    <div className="px-5 pt-5 pb-4 space-y-4">

      {/* ── Status row ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <span className={`flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1.5 rounded-full ${
          isRunning ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-700/50 text-slate-500"
        }`}>
          <CircleDot size={9} />
          {isRunning ? "Running" : state.paused ? "Paused" : translateSession(state.session ?? "")}
        </span>
        <span className={`text-[11px] font-semibold px-2.5 py-1.5 rounded-full ${
          isPanic ? "bg-rose-500/15 text-rose-400"
          : isBull ? "bg-emerald-500/15 text-emerald-400"
          : "bg-amber-500/15 text-amber-300"
        }`}>
          {translateRegime(regimeName)}
        </span>
      </div>

      {/* ── Portfolio hero ──────────────────────────────────────────────── */}
      <div>
        <p className="text-[10px] text-slate-600 uppercase tracking-widest mb-1">Portfolio value</p>
        <p className="text-[2.6rem] font-bold text-white leading-none tracking-tight">
          {fmtMoney(portValue)}
        </p>
        <p className={`text-sm font-semibold mt-2 ${pnlColor(dayPnl)}`}>
          {dayPnl >= 0 ? "+" : ""}{fmtMoney(dayPnl)} today
          <span className="font-normal opacity-60 ml-1.5">({fmtPct(dayPct)})</span>
        </p>
      </div>

      {/* ── Attention banner (only when near stop) ──────────────────────── */}
      {alerts.length > 0 && (
        <button
          onClick={() => onTabChange("holdings")}
          className="w-full flex items-center gap-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 px-4 py-3 active:bg-rose-500/15"
        >
          <AlertTriangle size={14} className="text-rose-400 shrink-0" />
          <p className="text-sm text-rose-300 font-semibold text-left flex-1">
            {alerts.length === 1
              ? `${alerts[0].symbol} is near its stop`
              : `${alerts.length} positions near stop loss`}
          </p>
          <ArrowRight size={13} className="text-rose-400 shrink-0" />
        </button>
      )}

      {/* ── Quick stats strip ───────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-xl bg-[#101622] border border-[#1e2a3a] p-3 text-center">
          <p className="text-[9px] text-slate-600 uppercase tracking-wider mb-1">Open P&amp;L</p>
          <p className={`text-sm font-bold ${pnlColor(unrealized)}`}>
            {unrealized >= 0 ? "+" : ""}{fmtMoney(unrealized, true)}
          </p>
        </div>
        <div className="rounded-xl bg-[#101622] border border-[#1e2a3a] p-3 text-center">
          <p className="text-[9px] text-slate-600 uppercase tracking-wider mb-1">Positions</p>
          <p className="text-sm font-bold text-white">{positions.length}</p>
        </div>
        <div className="rounded-xl bg-[#101622] border border-[#1e2a3a] p-3 text-center">
          <p className="text-[9px] text-slate-600 uppercase tracking-wider mb-1">Scans</p>
          <p className="text-sm font-bold text-white">{state.scan_count ?? "—"}</p>
        </div>
      </div>
      {state.last_scan && (
        <p className="text-[10px] text-slate-700 text-center -mt-2">
          Last scanned at {fmtNYTime(state.last_scan)}
          <span className="text-slate-800"> · {fmtLocalTime(state.last_scan)}</span>
        </p>
      )}

      {/* ── Market context (tap → Apex) ──────────────────────────────────── */}
      <button
        onClick={() => onTabChange("apex")}
        className="w-full flex items-center gap-3 rounded-xl bg-[#101622] border border-[#1e2a3a] px-4 py-3 active:bg-[#131c2e]"
      >
        <div className={`w-2 h-2 rounded-full shrink-0 ${
          isPanic ? "bg-rose-400" : isBull ? "bg-emerald-400" : "bg-amber-400"
        }`} />
        <p className="text-sm text-slate-300 flex-1 text-left">
          <span className="font-semibold text-white">{translateRegime(regimeName)}</span>
          <span className="text-slate-600 mx-1.5">·</span>
          <span className={vix.color}>{vix.label}</span>
          {spyChg != null && (
            <>
              <span className="text-slate-600 mx-1.5">·</span>
              <span className={pnlColor(spyChg)}>SPY {spyChg >= 0 ? "+" : ""}{spyChg.toFixed(1)}%</span>
            </>
          )}
          {qqqChg != null && spyChg == null && (
            <>
              <span className="text-slate-600 mx-1.5">·</span>
              <span className={pnlColor(qqqChg)}>QQQ {qqqChg >= 0 ? "+" : ""}{qqqChg.toFixed(1)}%</span>
            </>
          )}
        </p>
        <ArrowRight size={13} className="text-slate-600 shrink-0" />
      </button>

      {/* ── Positions ───────────────────────────────────────────────────── */}
      {positions.length > 0 ? (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] px-4 pt-3 pb-1">
          <div className="flex items-center justify-between mb-0.5">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Positions</p>
            <span className="text-[10px] text-slate-600">{positions.length} open</span>
          </div>
          {sorted.slice(0, 4).map(p => (
            <PositionRow
              key={p.symbol + (p.open_time ?? "")}
              pos={p}
              onTap={() => onTabChange("holdings")}
            />
          ))}
          {positions.length > 4 && (
            <button
              onClick={() => onTabChange("holdings")}
              className="flex items-center gap-1 text-xs text-blue-400 font-semibold py-3 active:opacity-70"
            >
              +{positions.length - 4} more · entries, stops &amp; thesis <ArrowRight size={11} />
            </button>
          )}
        </div>
      ) : (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] px-4 py-5 text-center">
          <p className="text-sm text-slate-600">No open positions — scanning the market</p>
        </div>
      )}

      {/* ── Last bot action ──────────────────────────────────────────────── */}
      {state.last_decision && (
        <button
          onClick={() => onTabChange("activity")}
          className="w-full flex items-center gap-3 rounded-xl bg-[#101622] border border-[#1e2a3a] px-4 py-3 active:bg-[#131c2e]"
        >
          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 ${
            state.last_decision.direction === "SHORT"
              ? "bg-rose-500/15 text-rose-400"
              : "bg-emerald-500/15 text-emerald-400"
          }`}>
            {state.last_decision.direction === "SHORT" ? "↓" : "↑"}
          </div>
          <div className="flex-1 min-w-0 text-left">
            <p className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-0.5">Last action</p>
            <p className="text-sm font-semibold text-white">
              {state.last_decision.direction === "SHORT" ? "Shorted" : "Bought"}{" "}
              {state.last_decision.symbol}
              {state.last_decision.price != null && (
                <span className="text-slate-500 font-normal ml-1">@ {fmtMoney(state.last_decision.price)}</span>
              )}
            </p>
            {state.last_decision.thesis && (
              <p className="text-xs text-slate-500 mt-0.5 truncate">
                {cleanThesis(state.last_decision.thesis)}
              </p>
            )}
          </div>
          <ArrowRight size={13} className="text-slate-600 shrink-0" />
        </button>
      )}

    </div>
  );
}
