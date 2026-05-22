"use client";

import { useEffect, useState, useCallback } from "react";
import { TrendingUp, TrendingDown, CircleDot, ArrowRight } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Regime } from "@/lib/api";
import {
  fmtMoney, fmtPct, pnlColor,
  translateSession, translateRegime, translateVix,
  translateTheme, translateThemeState,
} from "@/lib/translate";
import type { Tab } from "@/components/BottomNav";

interface Theme {
  theme_id: string;
  state: string;
  confidence: number;
  direction: string;
}
interface IntelligenceResponse { themes?: Theme[] }
interface Props { onTabChange: (t: Tab) => void }

function IndexPill({ name, chg }: { name: string; chg?: number }) {
  const v = chg ?? 0;
  const pos = v >= 0;
  return (
    <div className={`flex flex-col items-center justify-center px-2 py-2.5 rounded-xl border ${
      pos ? "bg-emerald-500/8 border-emerald-500/15" : "bg-rose-500/8 border-rose-500/15"
    }`}>
      <span className="text-[9px] font-semibold text-slate-500 uppercase tracking-wider">{name}</span>
      <span className={`text-sm font-bold mt-0.5 ${pos ? "text-emerald-400" : "text-rose-400"}`}>
        {pos ? "+" : ""}{v.toFixed(2)}%
      </span>
    </div>
  );
}

export default function TodayView({ onTabChange }: Props) {
  const [state, setState]   = useState<BotState | null>(null);
  const [intel, setIntel]   = useState<IntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, i] = await Promise.all([
        api.get<BotState>("/api/state"),
        api.get<IntelligenceResponse>("/api/intelligence").catch(() => ({})),
      ]);
      setState(s);
      setIntel(i as IntelligenceResponse);
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

  if (loading) return <HomeSkeleton />;

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

  const regime    = state.regime as Regime | undefined;
  const vixValue  = regime?.vix ?? 0;
  const vix       = translateVix(vixValue);
  const prose     = regime?.tape_context?.prose ?? null;
  const regimeName = regime?.regime ?? "UNKNOWN";
  const sessionChar = regime?.session_character ?? "";
  const isRunning = !state.paused && state.session !== "CLOSED" && state.session !== "WEEKEND";

  // Sentiment signal: what should the reader feel?
  const sessionMood: Record<string, { label: string; color: string }> = {
    FEAR_ELEVATED:  { label: "Caution — elevated fear in the market", color: "text-amber-400"   },
    GREED_ELEVATED: { label: "Markets showing signs of greed",        color: "text-emerald-400" },
    RISK_OFF:       { label: "Investors are moving to safety",        color: "text-rose-400"    },
    RISK_ON:        { label: "Risk appetite is on — growth in favour", color: "text-emerald-400" },
  };
  const mood = sessionMood[sessionChar] ?? null;

  // Big card colour from regime + VIX
  const isPanic = regimeName === "PANIC" || vixValue >= 30;
  const isBull  = (regimeName.includes("BULL") || regimeName.includes("TRENDING_UP")) && vixValue < 22;
  const sentimentBg   = isPanic ? "bg-rose-500/10 border-rose-500/25"
    : isBull             ? "bg-emerald-500/10 border-emerald-500/25"
    :                      "bg-amber-500/10 border-amber-500/25";
  const sentimentText = isPanic ? "text-rose-400" : isBull ? "text-emerald-400" : "text-amber-300";

  // Active themes only — cap at 3 on home
  const activeThemes = (intel?.themes ?? [])
    .filter(t => t.state === "activated" || t.state === "strengthening")
    .slice(0, 3);

  const positions = state.positions ?? [];
  const dayPnl    = state.daily_pnl ?? 0;
  const portValue = state.portfolio_value ?? 0;
  const dayPct    = portValue > 0 ? (dayPnl / portValue) * 100 : 0;

  return (
    <div className="px-5 pt-6 pb-4 space-y-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-slate-500 tracking-widest uppercase">
            {translateSession(state.session ?? "")}
          </p>
          <h1 className="text-xl font-bold text-white mt-0.5">Market Snapshot</h1>
        </div>
        <span className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-full ${
          isRunning ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-700/60 text-slate-400"
        }`}>
          <CircleDot size={9} />
          {isRunning ? "Bot running" : state.paused ? "Paused" : "Closed"}
        </span>
      </div>

      {/* Market mood — big, plain English */}
      <div className={`rounded-2xl border p-4 ${sentimentBg}`}>
        <div className="flex items-start justify-between gap-3 mb-2">
          <p className={`text-2xl font-bold leading-tight ${sentimentText}`}>
            {translateRegime(regimeName)}
          </p>
          <div className="text-right shrink-0">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">Fear index</p>
            <p className={`text-2xl font-bold ${vix.color}`}>{vixValue.toFixed(0)}</p>
            <p className={`text-[10px] font-semibold ${vix.color}`}>{vix.label}</p>
          </div>
        </div>
        {mood && (
          <p className={`text-sm font-semibold mb-2 ${mood.color}`}>{mood.label}</p>
        )}
        {prose && (
          <p className="text-sm text-slate-300 leading-relaxed line-clamp-3">{prose}</p>
        )}
      </div>

      {/* Index moves — just % change, no price */}
      <div className="grid grid-cols-3 gap-2">
        <IndexPill name="S&P 500" chg={regime?.spy_chg_1d} />
        <IndexPill name="Nasdaq"  chg={regime?.qqq_chg_1d} />
        <IndexPill name="Sm-Cap"  chg={regime?.iwm_chg_1d} />
      </div>

      {/* What's working */}
      {activeThemes.length > 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
            What&apos;s working today
          </p>
          <div className="space-y-2.5">
            {activeThemes.map(t => {
              const status = translateThemeState(t.state);
              return (
                <div key={t.theme_id} className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-white">{translateTheme(t.theme_id)}</p>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full shrink-0 ${status.color}`}>
                    {status.label}
                  </span>
                </div>
              );
            })}
          </div>
          <button
            onClick={() => onTabChange("apex")}
            className="flex items-center gap-1 text-xs text-blue-400 font-semibold mt-3 active:opacity-70"
          >
            See Apex intelligence <ArrowRight size={11} />
          </button>
        </div>
      )}

      {/* Portfolio strip */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-slate-500 mb-0.5">Your portfolio</p>
            <p className="text-2xl font-bold text-white">{fmtMoney(portValue)}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500 mb-0.5">Today</p>
            <p className={`text-2xl font-bold ${pnlColor(dayPnl)}`}>
              {fmtPct(dayPct)}
            </p>
            <p className={`text-xs mt-0.5 ${pnlColor(dayPnl)}`}>
              {dayPnl >= 0 ? "+" : ""}{fmtMoney(dayPnl)}
            </p>
          </div>
        </div>
        {positions.length > 0 && (
          <button
            onClick={() => onTabChange("holdings")}
            className="flex items-center gap-1 text-xs text-blue-400 font-semibold mt-3 active:opacity-70"
          >
            {positions.length} open position{positions.length !== 1 ? "s" : ""} <ArrowRight size={11} />
          </button>
        )}
      </div>

      {/* Bot's last move — 1 line */}
      {state.last_decision && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-xs text-slate-500 uppercase tracking-wider font-semibold mb-2">
            Bot&apos;s last move
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
            {state.last_decision.score != null && (
              <span className="text-xs text-slate-500">{state.last_decision.score} pts</span>
            )}
          </div>
          {state.last_decision.thesis && (
            <p className="text-xs text-slate-400 leading-relaxed line-clamp-2">
              {state.last_decision.thesis}
            </p>
          )}
        </div>
      )}

    </div>
  );
}

function HomeSkeleton() {
  return (
    <div className="px-5 pt-6 space-y-4">
      <div className="flex justify-between items-start">
        <Skeleton className="h-10 w-36 bg-[#161e2e]" />
        <Skeleton className="h-7 w-24 rounded-full bg-[#161e2e]" />
      </div>
      <Skeleton className="h-40 w-full rounded-2xl bg-[#161e2e]" />
      <div className="grid grid-cols-3 gap-2">
        {[1, 2, 3].map(i => <Skeleton key={i} className="h-16 rounded-xl bg-[#161e2e]" />)}
      </div>
      <Skeleton className="h-28 w-full rounded-2xl bg-[#161e2e]" />
      <Skeleton className="h-24 w-full rounded-2xl bg-[#161e2e]" />
    </div>
  );
}
