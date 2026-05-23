"use client";

import { useEffect, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { fmtMoney, pnlColor } from "@/lib/translate";

interface Analytics {
  trade_count?: number;
  win_rate?: number;
  total_pnl?: number;
  metrics?: { sharpe?: number; max_drawdown?: number; sortino?: number };
  monthly_returns?: Array<{ year: number; month: number; return: number }>;
  cumulative_curve?: Array<{ date: string; cumulative: number }>;
}

function sharpeLabel(s: number): string {
  if (s >= 2)   return "Excellent";
  if (s >= 1)   return "Good";
  if (s >= 0.5) return "Average";
  return "Below average";
}

const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

const ChartTip = ({ active, payload }: { active?: boolean; payload?: Array<{ value: number }> }) => {
  if (!active || !payload?.length) return null;
  const v = payload[0].value;
  return (
    <div className="rounded-lg bg-[#1e2a3a] border border-[#2a3a4a] px-2.5 py-1.5">
      <p className={`text-xs font-semibold ${v >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
        {v >= 0 ? "+" : ""}{v.toFixed(2)}%
      </p>
    </div>
  );
};

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4 flex flex-col gap-1">
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      <p className="text-2xl font-bold text-white">{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export default function ResultsView() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [error, setError]         = useState<string | null>(null);

  useEffect(() => {
    api.get<Analytics>("/api/analytics")
      .then(a => setAnalytics(a))
      .catch(() => setError("Can't load performance data."));
  }, []);

  if (error) return <div className="p-5 pt-6"><p className="text-rose-400 text-sm">{error}</p></div>;

  if (!analytics) return (
    <div className="px-5 pt-6 space-y-3">
      <Skeleton className="h-8 w-36 bg-[#161e2e]" />
      <div className="grid grid-cols-2 gap-3">
        {[1,2,3,4].map(i => <Skeleton key={i} className="h-24 rounded-2xl bg-[#161e2e]" />)}
      </div>
      <Skeleton className="h-52 rounded-2xl bg-[#161e2e]" />
    </div>
  );

  const sharpe   = analytics.metrics?.sharpe;
  const maxDd    = analytics.metrics?.max_drawdown;
  const winRate  = analytics.win_rate ?? 0;
  const trades   = analytics.trade_count ?? 0;
  const wins     = Math.round(trades * winRate / 100);
  const monthly  = (analytics.monthly_returns ?? []);
  const cumCurve = (analytics.cumulative_curve ?? []);

  return (
    <div className="px-5 pt-6 pb-4 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-white">How you're doing</h2>
        <p className="text-sm text-slate-500">{trades} trades total</p>
      </div>

      {/* 4 key stats */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          label="Total profit / loss"
          value={fmtMoney(analytics.total_pnl ?? 0, true)}
          sub="since you started"
        />
        <StatCard
          label={`Wins out of ${trades}`}
          value={`${wins} of ${trades}`}
          sub={`${winRate.toFixed(0)}% win rate`}
        />
        <StatCard
          label="Risk-adjusted return"
          value={sharpe != null ? `${sharpe.toFixed(2)}` : "—"}
          sub={sharpe != null ? sharpeLabel(sharpe) : "Not enough data"}
        />
        <StatCard
          label="Biggest drawdown"
          value={maxDd != null ? `${Math.abs(maxDd).toFixed(1)}%` : "—"}
          sub="max portfolio drop"
        />
      </div>

      {/* Cumulative return curve */}
      {cumCurve.length > 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-sm font-semibold text-slate-300 mb-1">Portfolio over time</p>
          <p className="text-xs text-slate-500 mb-3">Cumulative % return since start</p>
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart data={cumCurve} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}    />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#475569" }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis hide domain={["auto", "auto"]} />
              <Tooltip content={<ChartTip />} />
              <Area type="monotone" dataKey="cumulative" stroke="#3b82f6" strokeWidth={2} fill="url(#eq)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Monthly returns */}
      {monthly.length > 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-sm font-semibold text-slate-300 mb-3">Month by month</p>
          <div className="space-y-2">
            {monthly.map((m, i) => {
              const pct = m.return ?? 0;
              const barW = Math.min(100, Math.abs(pct) * 8);
              return (
                <div key={i} className="flex items-center gap-3">
                  <p className="text-xs text-slate-500 w-8 shrink-0">{MONTH_NAMES[(m.month ?? 1) - 1]}</p>
                  <div className="flex-1 h-6 rounded-lg bg-[#161e2e] overflow-hidden">
                    <div
                      className={`h-full rounded-lg transition-all ${pct >= 0 ? "bg-emerald-500/40" : "bg-rose-500/40"}`}
                      style={{ width: `${Math.max(4, barW)}%` }}
                    />
                  </div>
                  <p className={`text-xs font-semibold w-14 text-right ${pnlColor(pct)}`}>
                    {pct >= 0 ? "+" : ""}{pct.toFixed(1)}%
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
