"use client";

import { useEffect, useState, useCallback } from "react";

interface Leader {
  hour: string;
  rank: number;
  symbol: string;
  volume: number;
  volumeM: string;
  ratio?: number;
  ratioLabel?: string;
}

interface VolumeData {
  hours: string[];
  leaders: Leader[];
  unusual_leaders: Leader[];
  ts: string;
  date: string;
  market_open: boolean;
  is_today: boolean;
}

type Tab = "unusual" | "raw";

const NAMES: Record<string, string> = {
  AAPL:"Apple", MSFT:"Microsoft", NVDA:"Nvidia", AMZN:"Amazon", GOOGL:"Alphabet",
  META:"Meta", TSLA:"Tesla", AVGO:"Broadcom", JPM:"JPMorgan", UNH:"UnitedHealth",
  XOM:"ExxonMobil", LLY:"Eli Lilly", V:"Visa", MA:"Mastercard", HD:"Home Depot",
  PG:"Procter & Gamble", COST:"Costco", MRK:"Merck", ABBV:"AbbVie", CVX:"Chevron",
  BAC:"Bank of America", CRM:"Salesforce", NFLX:"Netflix", AMD:"AMD", PEP:"PepsiCo",
  ORCL:"Oracle", TMO:"Thermo Fisher", KO:"Coca-Cola", ADBE:"Adobe", WMT:"Walmart",
  ACN:"Accenture", MCD:"McDonald's", CSCO:"Cisco", ABT:"Abbott", LIN:"Linde",
  DHR:"Danaher", TXN:"Texas Instruments", NKE:"Nike", QCOM:"Qualcomm", PM:"Philip Morris",
  NEE:"NextEra Energy", RTX:"RTX Corp", HON:"Honeywell", CAT:"Caterpillar", IBM:"IBM",
  GE:"GE Aerospace", AMGN:"Amgen", SBUX:"Starbucks", ISRG:"Intuitive Surgical", PLD:"Prologis",
  INTC:"Intel", INTU:"Intuit", AXP:"American Express", NOW:"ServiceNow", BKNG:"Booking",
  ELV:"Elevance", SPGI:"S&P Global", GS:"Goldman Sachs", UNP:"Union Pacific", LOW:"Lowe's",
  MS:"Morgan Stanley", BLK:"BlackRock", DE:"John Deere", MDT:"Medtronic", SYK:"Stryker",
  REGN:"Regeneron", GILD:"Gilead", ADI:"Analog Devices", PH:"Parker Hannifin", ETN:"Eaton",
  VRTX:"Vertex", ZTS:"Zoetis", MO:"Altria", CI:"Cigna", MDLZ:"Mondelez",
  CB:"Chubb", DUK:"Duke Energy", SO:"Southern Co", WM:"Waste Management", CME:"CME Group",
  CL:"Colgate", MMC:"Marsh McLennan", ITW:"Illinois Tool Works", TGT:"Target", APD:"Air Products",
  BMY:"Bristol-Myers", PYPL:"PayPal", C:"Citigroup", USB:"US Bancorp", MU:"Micron",
  PANW:"Palo Alto", CRWD:"CrowdStrike", SNOW:"Snowflake", PLTR:"Palantir", ARM:"Arm Holdings",
  MSTR:"Strategy", HOOD:"Robinhood", COIN:"Coinbase", AMC:"AMC", GME:"GameStop",
  SMCI:"Super Micro", TSM:"Taiwan Semi", TER:"Teradyne", VRT:"Vertiv", VICR:"Vicor",
  VST:"Vistra Energy", ZS:"Zscaler", RIOT:"Riot Platforms", RBRK:"Rubrik", SOFI:"SoFi",
  NOK:"Nokia", OKTA:"Okta", OSCR:"Oscar Health", MRVL:"Marvell", MDB:"MongoDB",
  MPWR:"Monolithic Power", NBIS:"Nebius", NTAP:"NetApp", HPE:"HP Enterprise", HIVE:"HIVE Digital",
  LUNR:"Intuitive Machines", IREN:"IREN", LRCX:"Lam Research", LUMN:"Lumen", MARA:"Marathon Digital",
  GTLB:"GitLab", SNDK:"SanDisk",
  DDOG:"Datadog", DELL:"Dell", DOCN:"DigitalOcean", EQT:"EQT", FIG:"Figma",
  FLEX:"Flex", GEV:"GE Vernova", CIFR:"Cipher Mining", CLSK:"CleanSpark", CORZ:"Core Scientific",
  CRWV:"CoreWeave", CRDO:"Credo Tech", AAOI:"Applied Opto", ANET:"Arista", ASML:"ASML",
  TEAM:"Atlassian", BTDR:"Bitdeer", BMNR:"BitMine", CNTA:"Centessa", AEHR:"Aehr Test",
  AMKR:"Amkor", APLD:"Applied Digital",
};

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

function volBarWidth(volume: number, maxVolume: number): number {
  if (!maxVolume) return 0;
  return Math.round((volume / maxVolume) * 100);
}

// Colour the ratio badge: green for big spikes, amber for moderate
function ratioColor(ratio: number): string {
  if (ratio >= 5) return "text-emerald-300";
  if (ratio >= 3) return "text-emerald-400";
  if (ratio >= 2) return "text-amber-400";
  return "text-slate-400";
}

export default function VolumePage() {
  const [data, setData] = useState<VolumeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("unusual");
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

  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const activeLeaders = tab === "unusual" ? (data?.unusual_leaders ?? []) : (data?.leaders ?? []);

  const byHour: Map<string, Leader[]> = new Map();
  for (const l of activeLeaders) {
    if (!byHour.has(l.hour)) byHour.set(l.hour, []);
    byHour.get(l.hour)!.push(l);
  }

  const latestHour = data?.hours?.at(-1);
  const latestLeaders = latestHour ? (byHour.get(latestHour) ?? []) : [];
  const latestTopVol = latestLeaders[0]?.volume ?? 0;
  const latestTopRatio = latestLeaders[0]?.ratio ?? 0;

  const hasUnusual = (data?.unusual_leaders?.length ?? 0) > 0;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-slate-100 font-mono">
      {/* Header */}
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-slate-400 text-xs uppercase tracking-widest">Decifer</span>
          <span className="text-slate-500">|</span>
          <span className="text-white font-semibold tracking-wide">Volume</span>
          {data?.market_open && (
            <span className="flex items-center gap-1 text-emerald-400 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />
              Live
            </span>
          )}
          {data && !data.market_open && (
            <span className="text-slate-400 text-xs">
              {data.is_today ? "Market closed" : "Weekend · last session"}
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

      <main className="px-6 py-8 max-w-screen-xl mx-auto">
        {loading && (
          <div className="flex items-center justify-center h-64 text-slate-500 text-sm">
            Loading...
          </div>
        )}
        {error && (
          <div className="flex items-center justify-center h-64 text-red-400 text-sm">{error}</div>
        )}
        {data && !loading && data.hours.length === 0 && (
          <div className="text-slate-400 text-sm py-12 text-center">
            No data for {data.date}. Market opens at 9:30 AM ET.
          </div>
        )}

        {data && !loading && data.hours.length > 0 && (
          <>
            {/* Tab switcher */}
            <div className="flex gap-1 mb-8">
              <button
                onClick={() => setTab("unusual")}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  tab === "unusual"
                    ? "bg-slate-700 text-white"
                    : "text-slate-500 hover:text-slate-300"
                }`}
              >
                Unusual volume
              </button>
              <button
                onClick={() => setTab("raw")}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  tab === "raw"
                    ? "bg-slate-700 text-white"
                    : "text-slate-500 hover:text-slate-300"
                }`}
              >
                Most traded
              </button>
            </div>

            {/* Unusual tab: no baseline yet */}
            {tab === "unusual" && !hasUnusual && (
              <div className="text-slate-400 text-sm py-8">
                Building baselines — unusual volume data will appear once 30-day averages are ready.
              </div>
            )}

            {/* Latest hour spotlight */}
            {latestLeaders.length > 0 && (tab === "raw" || hasUnusual) && (
              <section className="mb-10">
                <div className="text-xs text-slate-400 uppercase tracking-widest mb-4">
                  {formatHour(latestHour!)}
                  {tab === "unusual" && (
                    <span className="ml-2 normal-case text-slate-500">trading above normal</span>
                  )}
                </div>
                <div className="space-y-1.5 max-w-xl">
                  {latestLeaders.slice(0, 10).map((l) => (
                    <div key={l.symbol} className="flex items-center gap-3">
                      <span className="text-slate-500 text-xs w-5 text-right">{l.rank}</span>
                      <div className="w-36 flex-shrink-0">
                        <span className="text-white font-semibold text-sm">{l.symbol}</span>
                        {NAMES[l.symbol] && (
                          <span className="text-slate-400 text-xs ml-2">{NAMES[l.symbol]}</span>
                        )}
                      </div>
                      <div className="flex-1 h-px bg-slate-800 relative">
                        <div
                          className="absolute left-0 top-0 h-px bg-slate-500"
                          style={{
                            width: tab === "unusual"
                              ? `${volBarWidth(l.ratio ?? 0, latestTopRatio)}%`
                              : `${volBarWidth(l.volume, latestTopVol)}%`,
                          }}
                        />
                      </div>
                      {tab === "unusual" && l.ratioLabel ? (
                        <span className={`text-xs w-24 text-right font-semibold ${ratioColor(l.ratio ?? 0)}`}>
                          {l.ratioLabel}
                        </span>
                      ) : (
                        <span className="text-slate-400 text-xs w-20 text-right">{l.volumeM} shares</span>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Hour-by-hour grid */}
            {(tab === "raw" || hasUnusual) && (
              <section className="overflow-x-auto mb-10">
                <div className="text-xs text-slate-400 uppercase tracking-widest mb-4">
                  Hour by hour
                </div>
                <table className="text-xs border-collapse" style={{ minWidth: `${data.hours.length * 90 + 48}px` }}>
                  <thead>
                    <tr>
                      <th className="text-left text-slate-500 pb-2 pr-6 font-normal w-8">#</th>
                      {data.hours.map((h) => (
                        <th
                          key={h}
                          className={`pb-2 pr-3 font-normal text-left w-20 ${h === latestHour ? "text-white" : "text-slate-500"}`}
                        >
                          {formatHour(h)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from({ length: 10 }, (_, i) => i + 1).map((rank) => (
                      <tr key={rank} className="border-t border-slate-900">
                        <td className="text-slate-600 pr-6 py-1 text-right">{rank}</td>
                        {data.hours.map((h) => {
                          const leader = byHour.get(h)?.find((l) => l.rank === rank);
                          return (
                            <td
                              key={h}
                              className={`pr-3 py-1 ${h === latestHour ? "text-white" : "text-slate-300"}`}
                              title={
                                leader
                                  ? tab === "unusual" && leader.ratioLabel
                                    ? `${NAMES[leader.symbol] ?? leader.symbol} · ${leader.ratioLabel}`
                                    : `${NAMES[leader.symbol] ?? leader.symbol} · ${leader.volumeM} shares`
                                  : ""
                              }
                            >
                              {leader ? (
                                <span className={rank === 1 ? "font-bold" : "font-medium"}>
                                  {leader.symbol}
                                </span>
                              ) : (
                                <span className="text-slate-700">·</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                    <tr className="border-t border-slate-800">
                      <td className="text-slate-600 pr-6 pt-2">
                        {tab === "unusual" ? "peak" : "vol"}
                      </td>
                      {data.hours.map((h) => {
                        const top = byHour.get(h)?.[0];
                        return (
                          <td key={h} className={`pr-3 pt-2 ${h === latestHour ? "text-slate-300" : "text-slate-500"}`}>
                            {top
                              ? tab === "unusual" && top.ratioLabel
                                ? top.ratioLabel
                                : top.volumeM
                              : ""}
                          </td>
                        );
                      })}
                    </tr>
                  </tbody>
                </table>
              </section>
            )}

            {/* All hours detail */}
            {(tab === "raw" || hasUnusual) && (
              <section>
                <div className="text-xs text-slate-400 uppercase tracking-widest mb-4">
                  All hours
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 gap-px bg-slate-800">
                  {[...data.hours].reverse().map((h) => {
                    const hourLeaders = byHour.get(h) ?? [];
                    return (
                      <div key={h} className="bg-[#0a0a0a] p-3">
                        <div className={`text-xs mb-3 ${h === latestHour ? "text-white font-semibold" : "text-slate-400"}`}>
                          {formatHour(h)}
                        </div>
                        <div className="space-y-1.5">
                          {hourLeaders.map((l) => (
                            <div key={l.symbol} className="flex items-center gap-1.5">
                              <span className="text-slate-600 text-xs w-3">{l.rank}</span>
                              <div className="flex-1 min-w-0">
                                <span className={`text-xs font-semibold ${l.rank === 1 ? "text-white" : "text-slate-300"}`}>
                                  {l.symbol}
                                </span>
                                {NAMES[l.symbol] && (
                                  <span className="text-slate-500 text-xs ml-1">{NAMES[l.symbol]}</span>
                                )}
                              </div>
                              {tab === "unusual" && l.ratioLabel ? (
                                <span className={`text-xs flex-shrink-0 font-semibold ${ratioColor(l.ratio ?? 0)}`}>
                                  {l.ratioLabel}
                                </span>
                              ) : (
                                <span className="text-slate-500 text-xs flex-shrink-0">{l.volumeM}</span>
                              )}
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

      <footer className="border-t border-slate-800 px-6 py-4 mt-8 text-xs text-slate-500 flex items-center justify-between">
        <span>volume.decifertrading.com</span>
        <span>
          {tab === "unusual"
            ? "Ratio vs 30-day average · Alpaca SIP · updates every 5 min"
            : "Alpaca SIP · updates every 5 min"}
        </span>
      </footer>
    </div>
  );
}
