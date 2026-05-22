"use client";

import { useEffect, useState, useCallback } from "react";
import { TrendingUp, TrendingDown, Minus, CircleDot, ChevronRight } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Regime } from "@/lib/api";
import { fmtMoney, fmtPct, pnlColor, translateSession, translateRegime, translateVix } from "@/lib/translate";
import type { Tab } from "@/components/BottomNav";

interface Props { onTabChange: (t: Tab) => void }

function pnlFromPositions(positions: BotState["positions"]) {
  return (positions ?? []).reduce((sum, p) => {
    const diff = (p.current ?? 0) - (p.entry ?? 0);
    const dir  = p.direction === "SHORT" ? -1 : 1;
    return sum + dir * diff * Math.abs(p.qty ?? 0);
  }, 0);
}

export default function TodayView({ onTabChange }: Props) {
  const [state, setState] = useState<BotState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.get<BotState>("/api/state");
      setState(d);
      setError(null);
    } catch (e) {
      setError("Can't reach the bot right now.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 12_000); return () => clearInterval(t); }, [load]);

  if (loading) return <TodaySkeleton />;

  if (error || !state) return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-3 p-6">
      <div className="h-12 w-12 rounded-full bg-rose-500/15 flex items-center justify-center">
        <Minus size={20} className="text-rose-400" />
      </div>
      <p className="text-slate-400 text-center text-sm">{error ?? "No data"}</p>
      <button
        onClick={() => { setLoading(true); load(); }}
        className="mt-1 px-4 py-2 rounded-full text-xs font-semibold bg-slate-800 text-slate-300 active:bg-slate-700"
      >
        Retry
      </button>
    </div>
  );

  const positions = state.positions ?? [];
  const openPnl = pnlFromPositions(positions);
  const dayPnl = state.daily_pnl ?? 0;
  const totalPnl = state.performance?.total_pnl ?? 0;
  const portfolioValue = state.portfolio_value ?? 0;
  const dayPct = portfolioValue > 0 ? (dayPnl / portfolioValue) * 100 : 0;
  const regime = state.regime as Regime | undefined;
  const vixValue = regime?.vix ?? 0;
  const regimeName = regime?.regime ?? "UNKNOWN";
  const vix = translateVix(vixValue);
  const sessionLabel = translateSession(state.session ?? "");
  const isRunning = !state.paused && state.session !== "CLOSED" && state.session !== "WEEKEND";

  // Best and worst open positions
  const withPnl = positions.map(p => {
    const diff = (p.current ?? 0) - (p.entry ?? 0);
    const pnlAmt = (p.direction === "SHORT" ? -diff : diff) * Math.abs(p.qty ?? 0);
    const pct = p.entry ? (diff / p.entry) * 100 * (p.direction === "SHORT" ? -1 : 1) : 0;
    return { ...p, pnlAmt, pct };
  });
  const best  = withPnl.sort((a, b) => b.pct - a.pct)[0];
  const worst = withPnl.sort((a, b) => a.pct - b.pct)[0];

  return (
    <div className="px-5 pt-6 pb-4 space-y-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-slate-500 tracking-widest uppercase">{sessionLabel}</p>
          <h1 className="text-lg font-bold text-white mt-0.5">Your Portfolio</h1>
        </div>
        <div className="flex items-center gap-2">
          <span className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-full ${isRunning ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-500/15 text-slate-400"}`}>
            <CircleDot size={9} className={isRunning ? "text-emerald-400" : "text-slate-500"} />
            {isRunning ? "Bot running" : state.paused ? "Paused" : "Market closed"}
          </span>
        </div>
      </div>

      {/* Hero: Portfolio Value */}
      <div className="rounded-2xl p-5" style={{ background: "linear-gradient(135deg, #101e38 0%, #0f1a2e 100%)", border: "1px solid #1e3050" }}>
        <p className="text-slate-400 text-sm mb-1">Total value</p>
        <p className="text-4xl font-bold text-white tracking-tight">{fmtMoney(portfolioValue)}</p>
        <div className="flex items-center gap-3 mt-3">
          <div className={`flex items-center gap-1.5 text-base font-semibold ${pnlColor(dayPnl)}`}>
            {dayPnl >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
            {dayPnl >= 0 ? "+" : ""}{fmtMoney(dayPnl)} ({fmtPct(dayPct)}) today
          </div>
        </div>
        <div className="mt-2 text-sm text-slate-500">
          All time: <span className={`font-semibold ${pnlColor(totalPnl)}`}>{totalPnl >= 0 ? "+" : ""}{fmtMoney(totalPnl, true)}</span>
        </div>
      </div>

      {/* Open P&L snapshot */}
      {positions.length > 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-semibold text-slate-300">Open positions</p>
            <button onClick={() => onTabChange("holdings")} className="flex items-center gap-1 text-xs font-semibold text-blue-400">
              {positions.length} open <ChevronRight size={13} />
            </button>
          </div>
          <div className={`text-2xl font-bold ${pnlColor(openPnl)}`}>
            {openPnl >= 0 ? "+" : ""}{fmtMoney(openPnl)}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">Unrealised gain / loss</p>

          {/* Best / Worst */}
          {best && worst && best.symbol !== worst.symbol && (
            <div className="grid grid-cols-2 gap-2 mt-3">
              <div className="rounded-xl bg-emerald-500/8 border border-emerald-500/15 p-2.5">
                <p className="text-[10px] text-emerald-500/70 font-semibold uppercase tracking-wider mb-1">Best today</p>
                <p className="text-sm font-bold text-white">{best.symbol}</p>
                <p className="text-sm font-semibold text-emerald-400">{fmtPct(best.pct)}</p>
              </div>
              <div className="rounded-xl bg-rose-500/8 border border-rose-500/15 p-2.5">
                <p className="text-[10px] text-rose-500/70 font-semibold uppercase tracking-wider mb-1">Under pressure</p>
                <p className="text-sm font-bold text-white">{worst.symbol}</p>
                <p className="text-sm font-semibold text-rose-400">{fmtPct(worst.pct)}</p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Market mood */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
        <p className="text-sm font-semibold text-slate-300 mb-3">Market mood</p>
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <p className="text-base font-bold text-white">{translateRegime(regimeName)}</p>
            <p className={`text-sm font-medium mt-0.5 ${vix.color}`}>{vix.label}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500">Fear index</p>
            <p className={`text-xl font-bold ${vix.color}`}>{vixValue.toFixed(1)}</p>
          </div>
        </div>
      </div>

      {/* Latest bot decision */}
      {state.last_decision && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-sm font-semibold text-slate-300 mb-2">Latest bot decision</p>
          <p className="text-white font-semibold">
            {state.last_decision.direction === "SHORT" ? "Shorting" : "Bought"} {state.last_decision.symbol}
            {state.last_decision.score != null && (
              <span className="ml-2 text-xs font-normal text-blue-400 bg-blue-400/10 px-1.5 py-0.5 rounded-full">{state.last_decision.score} pts</span>
            )}
          </p>
          {state.last_decision.thesis && (
            <p className="text-sm text-slate-400 mt-1.5 leading-relaxed line-clamp-3">{state.last_decision.thesis}</p>
          )}
        </div>
      )}

    </div>
  );
}

function TodaySkeleton() {
  return (
    <div className="px-5 pt-6 space-y-4">
      <div className="flex justify-between items-start">
        <Skeleton className="h-10 w-36 bg-[#161e2e]" />
        <Skeleton className="h-7 w-28 rounded-full bg-[#161e2e]" />
      </div>
      <Skeleton className="h-36 w-full rounded-2xl bg-[#161e2e]" />
      <Skeleton className="h-36 w-full rounded-2xl bg-[#161e2e]" />
      <Skeleton className="h-24 w-full rounded-2xl bg-[#161e2e]" />
    </div>
  );
}
