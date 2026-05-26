"use client";

import { X, TrendingUp, TrendingDown, Target, ShieldAlert, Clock, Zap } from "lucide-react";
import type { Position, PMDecision } from "@/lib/api";
import {
  fmtMoney, fmtPct, pnlColor, holdDuration,
  translateDirection, translateTradeType, translateSetupType,
  translateSignalDim, translateThesisStatus, translateRegime, translateConviction,
  cleanThesis, fmtNYTime, fmtLocalTime,
} from "@/lib/translate";

interface Props {
  position: Position | null;
  pmDecisions?: PMDecision[];
  onClose: () => void;
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">{children}</p>
  );
}

function Row({ label, value, sub, valueClass }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="flex items-start justify-between py-1.5 border-b border-[#1e2a3a] last:border-0">
      <p className="text-sm text-slate-400 shrink-0 mr-4">{label}</p>
      <div className="text-right">
        <p className={`text-sm font-semibold ${valueClass ?? "text-white"}`}>{value}</p>
        {sub && <p className="text-[10px] text-slate-600 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

export default function PositionSheet({ position, pmDecisions, onClose }: Props) {
  if (!position) return null;

  const isLong = position.direction === "LONG";
  const pnl = position.pnl ?? ((position.current - position.entry) * (isLong ? 1 : -1) * Math.abs(position.qty ?? 0));
  const pnlPct = position.entry ? ((position.current - position.entry) / position.entry) * 100 * (isLong ? 1 : -1) : 0;
  const duration = holdDuration(position.open_time ?? "");

  // Top 3 non-zero signal dimensions
  const topSignals = Object.entries(position.signal_scores ?? {})
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 4);

  // Latest PM decision for this symbol
  const pmEntry = (pmDecisions ?? [])
    .filter(d => d.symbol === position.symbol)
    .sort((a, b) => new Date(b.ts ?? 0).getTime() - new Date(a.ts ?? 0).getTime())[0];

  const thesisStatus = pmEntry?.thesis_status ? translateThesisStatus(pmEntry.thesis_status) : null;
  const conviction = translateConviction(position.score ?? 0);

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/70 z-40"
        style={{ backdropFilter: "blur(2px)" }}
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        className="fixed bottom-0 left-0 right-0 z-50 rounded-t-3xl overflow-y-auto"
        style={{
          background: "#0e1520",
          borderTop: "1px solid #1e2a3a",
          maxHeight: "88vh",
          paddingBottom: "calc(1.5rem + env(safe-area-inset-bottom))",
        }}
      >
        {/* Drag handle */}
        <div className="flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full bg-slate-700" />
        </div>

        {/* Header */}
        <div className="flex items-start justify-between px-5 pt-3 pb-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-2xl font-bold text-white">{position.symbol}</h2>
              <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${isLong ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"}`}>
                {isLong ? "Bought ↑" : "Shorting ↓"}
              </span>
            </div>
            {position.trade_type && (
              <p className="text-sm text-slate-400">{translateTradeType(position.trade_type)}{duration ? ` · ${duration}` : ""}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-full bg-[#161e2e] text-slate-400 hover:text-white transition-colors mt-0.5"
          >
            <X size={18} />
          </button>
        </div>

        {/* P&L Hero */}
        <div className="mx-5 mb-5 rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-xs text-slate-500 mb-1">Unrealised gain / loss</p>
          <p className={`text-3xl font-bold ${pnlColor(pnl)}`}>
            {pnl >= 0 ? "+" : ""}{fmtMoney(pnl)}
          </p>
          <p className={`text-sm font-medium mt-0.5 ${pnlColor(pnlPct)}`}>
            {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}% · entered at {fmtMoney(position.entry)}
          </p>
          {thesisStatus && (
            <div className="mt-3 pt-3 border-t border-[#1e2a3a] flex items-center gap-2">
              <p className="text-xs text-slate-500">Bot's view:</p>
              <p className={`text-xs font-semibold ${thesisStatus.color}`}>{thesisStatus.label}</p>
            </div>
          )}
        </div>

        <div className="px-5 space-y-5">

          {/* Why we entered this trade */}
          {position.entry_thesis && (
            <div>
              <SectionHeader>{isLong ? "Why we bought this" : "Why we shorted this"}</SectionHeader>
              <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
                <p className="text-sm text-slate-200 leading-relaxed">{cleanThesis(position.entry_thesis)}</p>
              </div>
            </div>
          )}

          {/* PM rationale (latest bot commentary on this position) */}
          {pmEntry?.rationale && pmEntry.rationale !== position.entry_thesis && (
            <div>
              <SectionHeader>Latest bot commentary</SectionHeader>
              <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4 flex items-start gap-3">
                <Zap size={14} className="text-blue-400 shrink-0 mt-0.5" />
                <p className="text-sm text-slate-300 leading-relaxed">{pmEntry.rationale}</p>
              </div>
            </div>
          )}

          {/* Trade details */}
          <div>
            <SectionHeader>Trade details</SectionHeader>
            <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
              {position.open_time && (
                <Row
                  label="Opened"
                  value={`${fmtNYTime(position.open_time)}${duration ? `  ·  ${duration} ago` : ""}`}
                  sub={fmtLocalTime(position.open_time)}
                />
              )}
              <Row label="Conviction" value={conviction} />
              {position.setup_type && <Row label="Setup type" value={translateSetupType(position.setup_type)} />}
              {position.entry_regime && <Row label="Market when bought" value={translateRegime(position.entry_regime)} />}
              <Row label="Quantity" value={`${Math.abs(position.qty ?? 0)} shares`} />
              {position.sl && (
                <Row
                  label="Stop loss (exit if wrong)"
                  value={fmtMoney(position.sl)}
                  valueClass="text-rose-400"
                />
              )}
              {position.tp && (
                <Row
                  label="Target price (take profit)"
                  value={fmtMoney(position.tp)}
                  valueClass="text-emerald-400"
                />
              )}
            </div>
          </div>

          {/* Signals that fired */}
          {topSignals.length > 0 && (
            <div>
              <SectionHeader>Signals that fired</SectionHeader>
              <div className="flex flex-wrap gap-2">
                {topSignals.map(([dim, val]) => (
                  <div key={dim} className="flex items-center gap-1.5 bg-blue-400/8 border border-blue-400/15 rounded-full px-3 py-1.5">
                    <div className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                    <p className="text-xs font-medium text-blue-300">{translateSignalDim(dim)}</p>
                    <p className="text-xs text-blue-400/70 font-semibold">+{val}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Risk targets */}
          {(position.sl || position.tp) && (
            <div>
              <SectionHeader>Risk / reward</SectionHeader>
              <div className="grid grid-cols-2 gap-3">
                {position.sl && (
                  <div className="rounded-2xl bg-rose-500/8 border border-rose-500/15 p-3.5">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <ShieldAlert size={12} className="text-rose-400" />
                      <p className="text-[10px] font-bold text-rose-400/70 uppercase tracking-wider">Stop loss</p>
                    </div>
                    <p className="text-base font-bold text-white">{fmtMoney(position.sl)}</p>
                    <p className="text-xs text-rose-400 mt-0.5">
                      {(((position.sl - position.entry) / position.entry) * 100 * (isLong ? 1 : -1)).toFixed(1)}% from entry
                    </p>
                  </div>
                )}
                {position.tp && (
                  <div className="rounded-2xl bg-emerald-500/8 border border-emerald-500/15 p-3.5">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <Target size={12} className="text-emerald-400" />
                      <p className="text-[10px] font-bold text-emerald-400/70 uppercase tracking-wider">Target</p>
                    </div>
                    <p className="text-base font-bold text-white">{fmtMoney(position.tp)}</p>
                    <p className="text-xs text-emerald-400 mt-0.5">
                      +{(((position.tp - position.entry) / position.entry) * 100 * (isLong ? 1 : -1)).toFixed(1)}% from entry
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

        </div>
      </div>
    </>
  );
}
