"use client";

import { useEffect, useState, useCallback } from "react";
import { ArrowUpRight, Minus } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Position, type PMDecision } from "@/lib/api";
import { fmtMoney, pnlColor, holdDuration, translateTradeType, translateConviction, cleanThesis } from "@/lib/translate";
import PositionSheet from "@/components/PositionSheet";

interface PMResponse { decisions?: PMDecision[] }

export default function ActivityView() {
  const [positions, setPositions]     = useState<Position[]>([]);
  const [pmDecisions, setPmDecisions] = useState<PMDecision[]>([]);
  const [loading, setLoading]         = useState(true);
  const [selected, setSelected]       = useState<Position | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, pm] = await Promise.all([
        api.get<BotState>("/api/state"),
        api.get<PMResponse>("/api/pm").catch(() => ({ decisions: [] })),
      ]);
      setPositions(s.positions ?? []);
      const pmData = pm as PMResponse;
      setPmDecisions(pmData.decisions ?? (Array.isArray(pm) ? (pm as PMDecision[]) : []));
    } catch { /* keep stale */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 15_000); return () => clearInterval(t); }, [load]);

  if (loading) return (
    <div className="px-5 pt-6 space-y-3">
      <Skeleton className="h-8 w-44 bg-[#161e2e]" />
      {[1,2,3,4,5].map(i => <Skeleton key={i} className="h-20 rounded-2xl bg-[#161e2e]" />)}
    </div>
  );

  // Sort: most recent first
  const sorted = [...positions].sort((a, b) =>
    new Date(b.open_time ?? 0).getTime() - new Date(a.open_time ?? 0).getTime()
  );

  if (!sorted.length) return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-3 p-6">
      <div className="h-12 w-12 rounded-full bg-slate-500/15 flex items-center justify-center">
        <Minus size={20} className="text-slate-400" />
      </div>
      <p className="text-slate-400 text-center text-sm">No activity yet — the bot will appear here when it starts trading</p>
    </div>
  );

  return (
    <>
      <div className="px-5 pt-6 pb-4 space-y-4">
        <div>
          <h2 className="text-lg font-bold text-white">What happened</h2>
          <p className="text-sm text-slate-500">Tap any trade to see why it was made</p>
        </div>

        <div className="space-y-2">
          {sorted.map((pos) => {
            const isLong = pos.direction === "LONG";
            const pnl = pos.pnl ?? ((pos.current - pos.entry) * (isLong ? 1 : -1) * Math.abs(pos.qty ?? 0));
            const pnlPct = pos.entry ? ((pos.current - pos.entry) / pos.entry) * 100 * (isLong ? 1 : -1) : 0;
            const duration = holdDuration(pos.open_time ?? "");
            const hasThesis = !!pos.entry_thesis;

            return (
              <button
                key={pos.symbol + (pos.open_time ?? "")}
                onClick={() => setSelected(pos)}
                className="w-full text-left rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4 transition-all active:scale-[0.99] active:bg-[#131c2e]"
              >
                <div className="flex items-start justify-between gap-3">
                  {/* Icon */}
                  <div className={`flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center mt-0.5 ${isLong ? "bg-emerald-500/15" : "bg-rose-500/15"}`}>
                    <ArrowUpRight
                      size={16}
                      className={isLong ? "text-emerald-400" : "text-rose-400 rotate-90"}
                    />
                  </div>

                  {/* Main content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <p className="text-sm font-bold text-white">{isLong ? "Bought" : "Shorted"} {pos.symbol}</p>
                      {duration && <p className="text-xs text-slate-500">{duration} ago</p>}
                    </div>
                    <p className="text-xs text-slate-500 mb-1.5">
                      {translateTradeType(pos.trade_type ?? "")}
                      {pos.score != null && ` · ${translateConviction(pos.score)}`}
                    </p>
                    {/* Thesis preview */}
                    {hasThesis ? (
                      <p className="text-xs text-slate-400 line-clamp-2 leading-relaxed">{cleanThesis(pos.entry_thesis!)}</p>
                    ) : (
                      <p className="text-xs text-slate-600 italic">entered at {fmtMoney(pos.entry)}</p>
                    )}
                  </div>

                  {/* P&L + tap hint */}
                  <div className="text-right shrink-0 flex flex-col items-end gap-1">
                    <p className={`text-sm font-bold ${pnlColor(pnl)}`}>
                      {pnl >= 0 ? "+" : ""}{fmtMoney(pnl)}
                    </p>
                    <p className={`text-xs ${pnlColor(pnlPct)}`}>
                      {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
                    </p>
                    {hasThesis && (
                      <p className="text-[9px] text-blue-400/60 font-semibold uppercase tracking-wider mt-1">tap for why →</p>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {selected && (
        <PositionSheet
          position={selected}
          pmDecisions={pmDecisions}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
