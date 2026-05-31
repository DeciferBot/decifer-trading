"use client";

import { useEffect, useState, useCallback } from "react";

interface Leader {
  hour: string;
  rank: number;
  symbol: string;
  volume: number;
  volumeM: string;
}

interface VolumeData {
  hours: string[];
  leaders: Leader[];
  ts: string;
  date: string;
  market_open: boolean;
  is_today: boolean;
}

const RANK_COLORS = [
  "bg-amber-400 text-black",
  "bg-slate-300 text-black",
  "bg-orange-600 text-white",
  "bg-slate-700 text-slate-200",
  "bg-slate-700 text-slate-200",
  "bg-slate-800 text-slate-300",
  "bg-slate-800 text-slate-300",
  "bg-slate-800 text-slate-300",
  "bg-slate-800 text-slate-300",
  "bg-slate-800 text-slate-300",
];

const BAR_OPACITY = [100, 80, 65, 50, 42, 35, 30, 25, 20, 15];

function formatHour(h: string): string {
  const [hh, mm] = h.split(":").map(Number);
  const suffix = hh >= 12 ? "PM" : "AM";
  const display = hh > 12 ? hh - 12 : hh === 0 ? 12 : hh;
  return `${display}:${String(mm).padStart(2, "0")} ${suffix}`;
}

function timeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export default function VolumePage() {
  const [data, setData] = useState<VolumeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/volume");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Clock tick for "Xs ago" display
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const byHour: Map<string, Leader[]> = new Map();
  if (data) {
    for (const l of data.leaders) {
      if (!byHour.has(l.hour)) byHour.set(l.hour, []);
      byHour.get(l.hour)!.push(l);
    }
  }

  const latestHour = data?.hours?.at(-1);
  const latestLeaders = latestHour ? (byHour.get(latestHour) ?? []) : [];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-slate-100 font-mono">
      {/* Header */}
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-slate-400 text-xs uppercase tracking-widest">Decifer</span>
          <span className="text-slate-500">|</span>
          <span className="text-white font-semibold tracking-wide">Volume Leaders</span>
          {data?.market_open && (
            <span className="flex items-center gap-1 text-emerald-400 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />
              Live
            </span>
          )}
          {data && !data.market_open && (
            <span className="text-slate-400 text-xs">
              {data.is_today ? "Market closed" : "Weekend · showing last session"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-400">
          {data && <span>Updated {timeAgo(data.ts)}</span>}
          {data && <span>{data.date}</span>}
          <button
            onClick={fetchData}
            className="text-slate-400 hover:text-white transition-colors px-2 py-1 border border-slate-700 rounded text-xs"
          >
            Refresh
          </button>
        </div>
      </header>

      <main className="px-6 py-6 max-w-screen-xl mx-auto">
        {loading && (
          <div className="flex items-center justify-center h-64 text-slate-500 text-sm">
            Loading volume data...
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center h-64 text-red-400 text-sm">
            {error}
          </div>
        )}

        {data && !loading && (
          <>
            {/* Current hour spotlight */}
            {latestLeaders.length > 0 && (
              <section className="mb-8">
                <div className="text-xs text-slate-400 uppercase tracking-widest mb-3">
                  {formatHour(latestHour!)} · top by volume
                </div>
                <div className="flex gap-2 flex-wrap">
                  {latestLeaders.slice(0, 10).map((l) => (
                    <div
                      key={l.symbol}
                      className={`rounded px-3 py-2 text-sm font-semibold ${RANK_COLORS[l.rank - 1]}`}
                    >
                      <span className="text-xs font-normal opacity-60 mr-1">#{l.rank}</span>
                      {l.symbol}
                      <span className="ml-2 text-xs font-normal opacity-75">{l.volumeM}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Hour-by-hour grid */}
            {data.hours.length === 0 ? (
              <div className="text-slate-400 text-sm py-12 text-center">
                No data for {data.date}. Market opens at 9:30 AM ET.
              </div>
            ) : (
              <section className="overflow-x-auto">
                <div className="text-xs text-slate-400 uppercase tracking-widest mb-3">
                  Hour by hour
                </div>

                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr>
                      <th className="text-left text-slate-400 pb-2 pr-4 font-normal w-8">#</th>
                      {data.hours.map((h) => (
                        <th
                          key={h}
                          className={`pb-2 px-1 font-normal text-center min-w-[80px] ${h === latestHour ? "text-white" : "text-slate-500"}`}
                        >
                          {formatHour(h)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from({ length: 10 }, (_, i) => i + 1).map((rank) => (
                      <tr key={rank}>
                        <td className="text-slate-400 pr-4 py-0.5">#{rank}</td>
                        {data.hours.map((h) => {
                          const leader = byHour.get(h)?.find((l) => l.rank === rank);
                          return (
                            <td key={h} className="px-0.5 py-0.5">
                              <div
                                className={`rounded text-center py-1.5 px-1 ${
                                  leader ? "text-white" : "bg-slate-900 text-slate-500"
                                } ${h === latestHour ? "ring-1 ring-slate-500" : ""}`}
                                style={
                                  leader
                                    ? { backgroundColor: `rgba(59,130,246,${BAR_OPACITY[rank - 1] / 100})` }
                                    : {}
                                }
                                title={leader ? `${leader.symbol} · ${leader.volumeM}` : ""}
                              >
                                {leader ? (
                                  <span className="font-semibold">{leader.symbol}</span>
                                ) : (
                                  <span>—</span>
                                )}
                              </div>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                    {/* Volume row */}
                    <tr>
                      <td className="text-slate-400 pr-4 pt-2 text-xs">Vol</td>
                      {data.hours.map((h) => {
                        const top = byHour.get(h)?.[0];
                        return (
                          <td key={h} className="px-0.5 pt-2 text-center text-slate-500">
                            {top ? top.volumeM : "—"}
                          </td>
                        );
                      })}
                    </tr>
                  </tbody>
                </table>
              </section>
            )}

            {/* Detail cards */}
            {data.hours.length > 0 && (
              <section className="mt-10">
                <div className="text-xs text-slate-400 uppercase tracking-widest mb-3">
                  All hours
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                  {[...data.hours].reverse().map((h) => {
                    const leaders = byHour.get(h) ?? [];
                    return (
                      <div key={h} className="bg-slate-900 rounded-lg p-3 border border-slate-800">
                        <div className={`text-xs font-semibold mb-2 ${h === latestHour ? "text-white" : "text-slate-400"}`}>
                          {formatHour(h)}
                          {h === latestHour && (
                            <span className="ml-2 text-emerald-400 text-xs">current</span>
                          )}
                        </div>
                        <div className="space-y-1">
                          {leaders.map((l) => (
                            <div key={l.symbol} className="flex items-center justify-between text-xs">
                              <div className="flex items-center gap-1.5">
                                <span className="text-slate-400 w-4 text-right">{l.rank}</span>
                                <span className="text-slate-100 font-semibold w-14">{l.symbol}</span>
                              </div>
                              <span className="text-slate-400">{l.volumeM}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
          </>
        )}
      </main>

      <footer className="border-t border-slate-800 px-6 py-4 text-xs text-slate-400 flex items-center justify-between">
        <span>volume.decifertrading.com</span>
        <span>Alpaca SIP feed · updates every 5 min · top 100 US stocks</span>
      </footer>
    </div>
  );
}
