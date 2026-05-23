"use client";

import { useEffect, useState, useCallback } from "react";
import { TrendingUp, TrendingDown, Minus, Globe } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Regime } from "@/lib/api";
import { translateTheme, translateThemeState, translateVix } from "@/lib/translate";

interface Theme {
  theme_id: string;
  state: string;
  confidence: number;
  direction: string;
  reason?: string;
}
interface IntelligenceResponse {
  themes?: Theme[];
  market_map?: Record<string, unknown>;
}

function ChgBadge({ pct }: { pct?: number }) {
  const v = pct ?? 0;
  const pos = v >= 0;
  return (
    <span className={`flex items-center gap-0.5 text-sm font-semibold ${pos ? "text-emerald-400" : "text-rose-400"}`}>
      {pos ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
      {pos ? "+" : ""}{v.toFixed(2)}%
    </span>
  );
}

function IndexCard({ name, price, chg }: { name: string; price?: number; chg?: number }) {
  return (
    <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-3.5 flex items-center justify-between">
      <div>
        <p className="text-xs text-slate-500 font-medium mb-0.5">{name}</p>
        {price ? <p className="text-lg font-bold text-white">${price.toFixed(0)}</p> : <p className="text-lg font-bold text-slate-600">—</p>}
      </div>
      <ChgBadge pct={chg} />
    </div>
  );
}

export default function MarketView() {
  const [state, setState]   = useState<BotState | null>(null);
  const [intel, setIntel]   = useState<IntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [s, i] = await Promise.all([
        api.get<BotState>("/api/state"),
        api.get<IntelligenceResponse>("/api/intelligence").catch(() => ({})),
      ]);
      setState(s);
      setIntel(i);
    } catch { /* show whatever loaded */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, [load]);

  if (loading) return (
    <div className="px-5 pt-6 space-y-3">
      <Skeleton className="h-8 w-40 bg-[#161e2e]" />
      <Skeleton className="h-24 rounded-2xl bg-[#161e2e]" />
      <div className="grid grid-cols-2 gap-3">
        {[1,2,3].map(i => <Skeleton key={i} className="h-20 rounded-2xl bg-[#161e2e]" />)}
      </div>
      <Skeleton className="h-48 rounded-2xl bg-[#161e2e]" />
    </div>
  );

  const regime = state?.regime as Regime | undefined;
  const vixValue = regime?.vix ?? 0;
  const vix = translateVix(vixValue);
  const prose = regime?.tape_context?.prose ?? null;
  const hmm = regime?.hmm_regime;
  const sessionChar = regime?.session_character ?? "";

  // Show only active + crowded themes (skip dormant)
  const visibleThemes = (intel?.themes ?? [])
    .filter(t => t.state !== "dormant")
    .sort((a, b) => {
      const order = ["activated", "strengthening", "crowded", "headwind"];
      const ai = order.indexOf(a.state); const bi = order.indexOf(b.state);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    })
    .slice(0, 8);

  // Fear / greed signal from session_character
  const sessionMood: Record<string, string> = {
    FEAR_ELEVATED:    "Caution: elevated fear in the market",
    GREED_ELEVATED:   "Markets showing signs of greed",
    NEUTRAL:          "Balanced sentiment",
    RISK_OFF:         "Investors moving to safety",
    RISK_ON:          "Investors buying risk",
  };

  return (
    <div className="px-5 pt-6 pb-4 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-white">The market right now</h2>
        <p className="text-sm text-slate-500">Live conditions your bot is reading</p>
      </div>

      {/* Tape summary — plain English */}
      {prose && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-2">Today's tape</p>
          <p className="text-sm text-slate-200 leading-relaxed">{prose}</p>
          {sessionChar && sessionMood[sessionChar] && (
            <p className="text-xs text-amber-400 mt-2 font-medium">{sessionMood[sessionChar]}</p>
          )}
        </div>
      )}

      {/* VIX */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4 flex items-center gap-4">
        <div className="flex-1">
          <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-1">Fear index (VIX)</p>
          <p className={`text-2xl font-bold ${vix.color}`}>{vixValue.toFixed(1)}</p>
          <p className={`text-sm font-medium mt-0.5 ${vix.color}`}>{vix.label}</p>
        </div>
        {hmm && (
          <div className="text-right border-l border-[#1e2a3a] pl-4">
            <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-1">Trend model</p>
            <p className={`text-base font-bold capitalize ${hmm.regime === "bull" ? "text-emerald-400" : "text-rose-400"}`}>
              {hmm.regime === "bull" ? "Bull market" : "Bear market"}
            </p>
            <p className="text-xs text-slate-500">{(hmm.confidence * 100).toFixed(0)}% confident</p>
          </div>
        )}
      </div>

      {/* Index prices */}
      <div className="grid grid-cols-2 gap-3">
        <IndexCard name="S&P 500"   price={regime?.spy_price} chg={regime?.spy_chg_1d} />
        <IndexCard name="Nasdaq"    price={regime?.qqq_price} chg={regime?.qqq_chg_1d} />
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-3.5 flex items-center justify-between col-span-2">
          <div>
            <p className="text-xs text-slate-500 font-medium mb-0.5">Small caps (IWM)</p>
          </div>
          <ChgBadge pct={regime?.iwm_chg_1d} />
        </div>
      </div>

      {/* Active themes */}
      {visibleThemes.length > 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-sm font-semibold text-slate-300 mb-3">Active investment themes</p>
          <div className="space-y-2.5">
            {visibleThemes.map(t => {
              const status = translateThemeState(t.state);
              return (
                <div key={t.theme_id} className="flex items-center justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white truncate">{translateTheme(t.theme_id)}</p>
                    <p className="text-xs text-slate-500">{t.direction === "tailwind" ? "Opportunity" : "Headwind"}</p>
                  </div>
                  <span className={`text-xs font-semibold px-2 py-1 rounded-full shrink-0 ${status.color}`}>
                    {status.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {visibleThemes.length === 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-8 flex flex-col items-center gap-3">
          <Globe size={28} className="text-slate-600" />
          <p className="text-slate-500 text-sm text-center">No strong themes identified right now</p>
        </div>
      )}
    </div>
  );
}
