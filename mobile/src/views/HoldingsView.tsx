"use client";

import { useEffect, useState, useCallback } from "react";
import { Wallet, TrendingUp, TrendingDown } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState, type Position, type PMDecision } from "@/lib/api";
import { fmtMoney, fmtPct, pnlColor, translateDirection, translateTradeType, translateConviction, holdDuration, translateThesisStatus, fmtNYTime } from "@/lib/translate";
import PositionSheet from "@/components/PositionSheet";

interface PMResponse { decisions?: PMDecision[] }

function calcPnl(p: Position) {
  const diff = (p.current ?? 0) - (p.entry ?? 0);
  const dir = p.direction === "SHORT" ? -1 : 1;
  return { pnlAmt: dir * diff * Math.abs(p.qty ?? 0), pct: p.entry ? (diff / p.entry) * 100 * dir : 0 };
}

function PositionCard({ p, pmDecisions, onTap }: { p: Position; pmDecisions: PMDecision[]; onTap: () => void }) {
  const { pnlAmt, pct } = calcPnl(p);
  const isLong = p.direction !== "SHORT";

  // Latest PM status for this symbol
  const pmEntry = pmDecisions
    .filter(d => d.symbol === p.symbol)
    .sort((a, b) => new Date(b.ts ?? 0).getTime() - new Date(a.ts ?? 0).getTime())[0];
  const thesisStatus = pmEntry?.thesis_status ? translateThesisStatus(pmEntry.thesis_status) : null;

  return (
    <button
      onClick={onTap}
      className="w-full text-left rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4 transition-all active:scale-[0.99] active:bg-[#131c2e]"
    >
      {/* Symbol + direction */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold text-white">{p.symbol}</span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${isLong ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"}`}>
              {translateDirection(p.direction)}
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            {p.trade_type ? translateTradeType(p.trade_type) : "Position"}
            {p.open_time ? ` · ${holdDuration(p.open_time)} ago` : ""}
          </p>
        </div>
        <div className="text-right">
          {p.score != null && (
            <p className="text-xs text-slate-500">{translateConviction(p.score)}</p>
          )}
          {thesisStatus && (
            <p className={`text-xs font-semibold mt-0.5 ${thesisStatus.color}`}>{thesisStatus.label}</p>
          )}
        </div>
      </div>

      {/* P&L */}
      <div className="flex items-end justify-between">
        <div>
          <p className={`text-2xl font-bold ${pnlColor(pnlAmt)}`}>
            {pnlAmt >= 0 ? "+" : ""}{fmtMoney(pnlAmt)}
          </p>
          <p className={`text-sm font-semibold ${pnlColor(pnlAmt)}`}>{fmtPct(pct)}</p>
        </div>
        {pnlAmt >= 0
          ? <TrendingUp size={28} className="text-emerald-400/40" />
          : <TrendingDown size={28} className="text-rose-400/40" />
        }
      </div>

      {/* Entry / Current / Opened — labelled columns */}
      <div className="mt-3 pt-3 border-t border-[#1e2a3a]">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <p className="text-[9px] font-bold text-slate-600 uppercase tracking-wider mb-0.5">Entry</p>
            <p className="text-sm font-semibold text-slate-300">{fmtMoney(p.entry ?? 0)}</p>
          </div>
          <div>
            <p className="text-[9px] font-bold text-slate-600 uppercase tracking-wider mb-0.5">Current</p>
            <p className="text-sm font-semibold text-white">{fmtMoney(p.current ?? 0)}</p>
          </div>
          <div>
            <p className="text-[9px] font-bold text-slate-600 uppercase tracking-wider mb-0.5">Opened</p>
            <p className="text-[10px] text-slate-500 leading-tight">{p.open_time ? fmtNYTime(p.open_time) : "—"}</p>
          </div>
        </div>
        {p.entry_thesis && (
          <p className="text-[10px] text-blue-400/50 font-semibold mt-2 text-right">tap for why →</p>
        )}
      </div>
    </button>
  );
}

export default function HoldingsView() {
  const [positions, setPositions]     = useState<Position[]>([]);
  const [pmDecisions, setPmDecisions] = useState<PMDecision[]>([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<string | null>(null);
  const [selected, setSelected]       = useState<Position | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, pm] = await Promise.all([
        api.get<BotState>("/api/state"),
        api.get<PMResponse>("/api/pm").catch(() => ({ decisions: [] })),
      ]);
      const sorted = (d.positions ?? []).slice().sort((a, b) => calcPnl(b).pnlAmt - calcPnl(a).pnlAmt);
      setPositions(sorted);
      const pmData = pm as PMResponse;
      setPmDecisions(pmData.decisions ?? (Array.isArray(pm) ? (pm as PMDecision[]) : []));
      setError(null);
    } catch { setError("Can't reach the bot right now."); }
    finally { setLoading(false); }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); const t = setInterval(load, 15_000); return () => clearInterval(t); }, [load]);

  if (loading) return (
    <div className="px-5 pt-6 space-y-3">
      <Skeleton className="h-7 w-32 bg-[#161e2e]" />
      {[1,2,3].map(i => <Skeleton key={i} className="h-40 w-full rounded-2xl bg-[#161e2e]" />)}
    </div>
  );

  const totalOpenPnl = positions.reduce((s, p) => s + calcPnl(p).pnlAmt, 0);

  return (
    <>
      <div className="px-5 pt-6 pb-4 space-y-3">
        <div className="flex items-end justify-between mb-1">
          <div>
            <h2 className="text-lg font-bold text-white">What you own</h2>
            <p className="text-sm text-slate-500">{positions.length} open position{positions.length !== 1 ? "s" : ""} · tap to see why</p>
          </div>
          {positions.length > 0 && (
            <div className="text-right">
              <p className={`text-lg font-bold ${pnlColor(totalOpenPnl)}`}>
                {totalOpenPnl >= 0 ? "+" : ""}{fmtMoney(totalOpenPnl)}
              </p>
              <p className="text-xs text-slate-500">Unrealised gain/loss</p>
            </div>
          )}
        </div>

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        {positions.length === 0 && !error ? (
          <div className="flex flex-col items-center justify-center py-24 gap-4">
            <div className="h-16 w-16 rounded-full bg-slate-800 flex items-center justify-center">
              <Wallet size={28} className="text-slate-500" />
            </div>
            <p className="text-slate-400 text-base font-medium">Nothing open right now</p>
            <p className="text-slate-600 text-sm text-center px-8">The bot will buy when it sees a strong opportunity</p>
          </div>
        ) : (
          positions.map(p => (
            <PositionCard
              key={p.symbol + (p.open_time ?? "")}
              p={p}
              pmDecisions={pmDecisions}
              onTap={() => setSelected(p)}
            />
          ))
        )}
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
