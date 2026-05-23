"use client";

import { useEffect, useState, useCallback } from "react";
import { ArrowRight, CircleDot } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Regime, type Position } from "@/lib/api";
import {
  fmtMoney, fmtPct, pnlColor,
  translateRegime, translateVix, translateSession,
} from "@/lib/translate";
import type { Tab } from "@/components/BottomNav";

interface Props { onTabChange: (t: Tab) => void }

function PositionMini({ pos }: { pos: Position }) {
  const isLong  = pos.direction === "LONG";
  const pnl     = pos.pnl ?? ((pos.current - pos.entry) * (isLong ? 1 : -1) * Math.abs(pos.qty ?? 0));
  const pnlPct  = pos.entry ? ((pos.current - pos.entry) / pos.entry) * 100 * (isLong ? 1 : -1) : 0;

  return (
    <div className="flex items-center justify-between py-2.5 border-b border-[#1e2a3a] last:border-0">
      <div className="flex items-center gap-2.5">
        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold ${
          isLong ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"
        }`}>
          {isLong ? "↑" : "↓"}
        </div>
        <div>
          <p className="text-sm font-bold text-white">{pos.symbol}</p>
          <p className="text-[10px] text-slate-500">{isLong ? "Long" : "Short"} · {Math.abs(pos.qty ?? 0)} shares</p>
        </div>
      </div>
      <div className="text-right">
        <p className={`text-sm font-bold ${pnlColor(pnl)}`}>
          {pnl >= 0 ? "+" : ""}{fmtMoney(pnl)}
        </p>
        <p className={`text-[10px] font-semibold ${pnlColor(pnlPct)}`}>
          {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
        </p>
      </div>
    </div>
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

  useEffect(() => {
    load();
    const t = setInterval(load, 12_000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return (
    <div className="px-5 pt-6 space-y-4">
      <Skeleton className="h-10 w-36 bg-[#161e2e]" />
      <Skeleton className="h-32 w-full rounded-2xl bg-[#161e2e]" />
      <Skeleton className="h-40 w-full rounded-2xl bg-[#161e2e]" />
      <Skeleton className="h-24 w-full rounded-2xl bg-[#161e2e]" />
    </div>
  );

  if (error || !state) return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-3 p-6">
      <p className="text-slate-400 text-center text-sm">{error ?? "No data"}</p>
      <button
        onClick={() => { setLoading(true); load(); }}
        className="mt-1 px-4 py-2 rounded-full text-xs font-semibold bg-slate-800 text-slate-300 active:bg-slate-700"
      >
        Retry
      </button>
    </div>
  );

  const regime      = state.regime as Regime | undefined;
  const vixValue    = regime?.vix ?? 0;
  const vix         = translateVix(vixValue);
  const prose       = regime?.tape_context?.prose ?? null;
  const regimeName  = regime?.regime ?? "UNKNOWN";
  const positions   = state.positions ?? [];
  const dayPnl      = state.daily_pnl ?? 0;
  const portValue   = state.portfolio_value ?? 0;
  const dayPct      = portValue > 0 ? (dayPnl / portValue) * 100 : 0;
  const isRunning   = !state.paused && state.session !== "CLOSED" && state.session !== "WEEKEND";

  const isPanic = regimeName === "PANIC" || vixValue >= 30;
  const isBull  = (regimeName.includes("BULL") || regimeName.includes("TRENDING_UP")) && vixValue < 22;
  const regimeBg   = isPanic ? "bg-rose-500/10 border-rose-500/20"
                   : isBull  ? "bg-emerald-500/8 border-emerald-500/20"
                   :           "bg-amber-500/8  border-amber-500/20";
  const regimeText = isPanic ? "text-rose-400" : isBull ? "text-emerald-400" : "text-amber-300";

  return (
    <div className="px-5 pt-6 pb-4 space-y-4">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] font-bold tracking-[0.22em] text-slate-600 uppercase mb-0.5">
            Amit Chopra
          </p>
          <h1 className="text-xl font-bold text-white">Portfolio</h1>
        </div>
        <span className={`flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1.5 rounded-full ${
          isRunning ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-700/50 text-slate-500"
        }`}>
          <CircleDot size={9} />
          {isRunning ? "Bot running" : state.paused ? "Paused" : translateSession(state.session ?? "")}
        </span>
      </div>

      {/* ── Portfolio value ─────────────────────────────────────────────────── */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-5">
        <p className="text-xs text-slate-500 mb-1">Total portfolio value</p>
        <p className="text-3xl font-bold text-white mb-1">{fmtMoney(portValue)}</p>
        <div className="flex items-baseline gap-2">
          <p className={`text-lg font-bold ${pnlColor(dayPnl)}`}>{fmtPct(dayPct)}</p>
          <p className={`text-sm ${pnlColor(dayPnl)}`}>
            {dayPnl >= 0 ? "+" : ""}{fmtMoney(dayPnl)} today
          </p>
        </div>
      </div>

      {/* ── Open positions ──────────────────────────────────────────────────── */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
        <div className="flex items-center justify-between mb-1">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            Open Positions
          </p>
          <span className="text-[11px] font-semibold text-slate-500">
            {positions.length} {positions.length === 1 ? "position" : "positions"}
          </span>
        </div>

        {positions.length > 0 ? (
          <>
            <div className="mt-2">
              {positions.slice(0, 5).map(p => (
                <PositionMini key={p.symbol + (p.open_time ?? "")} pos={p} />
              ))}
            </div>
            <button
              onClick={() => onTabChange("holdings")}
              className="flex items-center gap-1 text-xs text-blue-400 font-semibold mt-3 active:opacity-70"
            >
              Full detail, entry thesis &amp; stops <ArrowRight size={11} />
            </button>
          </>
        ) : (
          <p className="text-sm text-slate-600 mt-3 italic">No open positions — bot is scanning</p>
        )}
      </div>

      {/* ── Market today ────────────────────────────────────────────────────── */}
      <div className={`rounded-2xl border p-4 ${regimeBg}`}>
        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1.5">
          Market Today
        </p>
        <div className="flex items-start justify-between gap-3 mb-2">
          <p className={`text-base font-bold ${regimeText}`}>{translateRegime(regimeName)}</p>
          <div className="text-right shrink-0">
            <p className="text-[9px] text-slate-500 uppercase tracking-wider">Fear index</p>
            <p className={`text-xl font-bold ${vix.color}`}>{vixValue.toFixed(0)}</p>
            <p className={`text-[9px] font-semibold ${vix.color}`}>{vix.label}</p>
          </div>
        </div>
        {prose && (
          <p className="text-sm text-slate-300 leading-relaxed line-clamp-4">{prose}</p>
        )}
        <button
          onClick={() => onTabChange("apex")}
          className="flex items-center gap-1 text-xs text-blue-400 font-semibold mt-3 active:opacity-70"
        >
          Full market intelligence <ArrowRight size={11} />
        </button>
      </div>

      {/* ── Last decision ───────────────────────────────────────────────────── */}
      {state.last_decision && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">
            Bot&apos;s last trade
          </p>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-base font-bold text-white">{state.last_decision.symbol}</span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
              state.last_decision.direction === "SHORT"
                ? "text-rose-400 bg-rose-400/10"
                : "text-emerald-400 bg-emerald-400/10"
            }`}>
              {state.last_decision.direction === "SHORT" ? "Short ↓" : "Bought ↑"}
            </span>
          </div>
          {state.last_decision.thesis && (
            <p className="text-xs text-slate-400 leading-relaxed line-clamp-3">
              {state.last_decision.thesis}
            </p>
          )}
          <button
            onClick={() => onTabChange("activity")}
            className="flex items-center gap-1 text-xs text-blue-400 font-semibold mt-3 active:opacity-70"
          >
            See all trades &amp; reasons <ArrowRight size={11} />
          </button>
        </div>
      )}

    </div>
  );
}
