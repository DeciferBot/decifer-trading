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

// "2026-05-29" -> "Friday, May 29" (no timezone shift)
function fullDate(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return dt.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

function timeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// Heat tint for a ratio (green scale, capped at ~12x)
function heatStyle(intensity: number): React.CSSProperties {
  const a = Math.min(Math.max(intensity, 0), 1);
  return {
    backgroundColor: `rgba(16,185,129,${0.05 + a * 0.20})`,
    borderColor: `rgba(16,185,129,${0.18 + a * 0.45})`,
  };
}

function ratioColor(ratio: number): string {
  if (ratio >= 5) return "text-emerald-300";
  if (ratio >= 3) return "text-emerald-400";
  if (ratio >= 2) return "text-emerald-500";
  return "text-slate-300";
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

  const isUnusual = tab === "unusual";
  const activeLeaders = isUnusual ? (data?.unusual_leaders ?? []) : (data?.leaders ?? []);
  const hasData = activeLeaders.length > 0;

  // Group by hour
  const byHour: Map<string, Leader[]> = new Map();
  for (const l of activeLeaders) {
    if (!byHour.has(l.hour)) byHour.set(l.hour, []);
    byHour.get(l.hour)!.push(l);
  }

  const latestHour = data?.hours?.at(-1);

  // Aggregate each symbol's peak across the day -> info cards
  const peakBySymbol: Map<string, { symbol: string; metric: number; ratio: number; volumeM: string; hour: string }> = new Map();
  for (const l of activeLeaders) {
    const metric = isUnusual ? (l.ratio ?? 0) : l.volume;
    const cur = peakBySymbol.get(l.symbol);
    if (!cur || metric > cur.metric) {
      peakBySymbol.set(l.symbol, {
        symbol: l.symbol,
        metric,
        ratio: l.ratio ?? 0,
        volumeM: l.volumeM,
        hour: l.hour,
      });
    }
  }
  const movers = Array.from(peakBySymbol.values()).sort((a, b) => b.metric - a.metric).slice(0, 12);
  const topMetric = movers[0]?.metric ?? 1;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-slate-100 font-mono">
      {/* Header */}
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <span className="text-slate-400 text-xs uppercase tracking-widest">Decifer</span>
          <span className="text-slate-500">|</span>
          <span className="text-white font-semibold tracking-wide">Volume</span>
          {data?.market_open && (
            <span className="flex items-center gap-1 text-emerald-400 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />
              Live now
            </span>
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-400">
          {data && <span>Updated {timeAgo(data.ts)}</span>}
          <button
            onClick={fetchData}
            className="text-slate-400 hover:text-white transition-colors px-2 py-1 border border-slate-700 rounded text-xs"
          >
            Refresh
          </button>
        </div>
      </header>

      <main className="px-6 py-8 max-w-screen-xl mx-auto">
        {loading && <div className="flex items-center justify-center h-64 text-slate-500 text-sm">Loading...</div>}
        {error && <div className="flex items-center justify-center h-64 text-red-400 text-sm">{error}</div>}

        {data && !loading && (
          <>
            {/* Date context — answers "which day is this?" */}
            <div className="mb-6">
              <h1 className="text-white text-xl font-semibold">{fullDate(data.date)}</h1>
              <p className="text-slate-400 text-sm mt-1">
                {data.is_today
                  ? "Today's trading so far. Each hour is a US market hour (Eastern time)."
                  : "Last completed trading session. Markets are closed for the weekend. 4:00 PM is the closing hour."}
              </p>
            </div>

            {/* Tab switcher */}
            <div className="flex gap-1 mb-2">
              <button
                onClick={() => setTab("unusual")}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  isUnusual ? "bg-slate-700 text-white" : "text-slate-500 hover:text-slate-300"
                }`}
              >
                Unusual volume
              </button>
              <button
                onClick={() => setTab("raw")}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  !isUnusual ? "bg-slate-700 text-white" : "text-slate-500 hover:text-slate-300"
                }`}
              >
                Most traded
              </button>
            </div>
            <p className="text-slate-400 text-xs mb-8 max-w-2xl">
              {isUnusual
                ? "Stocks trading well above their normal pace. A higher number means more activity than usual, which often points to news or a big move."
                : "Stocks with the most shares changing hands. The biggest names lead here almost every day."}
            </p>

            {!hasData && (
              <div className="text-slate-400 text-sm py-12">
                Nothing unusual yet. Stocks will appear here once they trade above their normal pace.
              </div>
            )}

            {hasData && (
              <>
                {/* Info cards — top movers of the day, heatmap tinted */}
                <section className="mb-12">
                  <div className="text-xs text-slate-400 uppercase tracking-widest mb-4">
                    Biggest moves today
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                    {movers.map((m) => (
                      <div
                        key={m.symbol}
                        className="rounded-lg border p-4 transition-colors"
                        style={isUnusual ? heatStyle(m.ratio / 12) : heatStyle(m.metric / topMetric)}
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-white font-bold text-lg">{m.symbol}</span>
                          {isUnusual ? (
                            <span className={`font-bold text-sm ${ratioColor(m.ratio)}`}>
                              {m.ratio.toFixed(1)}x
                            </span>
                          ) : (
                            <span className="text-emerald-400 font-bold text-sm">{m.volumeM}</span>
                          )}
                        </div>
                        <div className="text-slate-300 text-sm mt-0.5 truncate">
                          {NAMES[m.symbol] ?? m.symbol}
                        </div>
                        <div className="text-slate-500 text-xs mt-2">
                          {isUnusual ? "Peak at " : "Most at "}{formatHour(m.hour)}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>

                {/* Hour-by-hour heatmap matrix */}
                <section className="overflow-x-auto">
                  <div className="text-xs text-slate-400 uppercase tracking-widest mb-1">
                    Hour by hour
                  </div>
                  <p className="text-slate-500 text-xs mb-4">
                    Top names each hour. Brighter green means more unusual activity.
                  </p>
                  <table className="border-separate" style={{ borderSpacing: "4px", minWidth: `${data.hours.length * 104 + 40}px` }}>
                    <thead>
                      <tr>
                        <th className="text-left text-slate-500 font-normal text-xs w-6"></th>
                        {data.hours.map((h) => (
                          <th
                            key={h}
                            className={`font-normal text-xs pb-1 text-center ${h === latestHour ? "text-white" : "text-slate-500"}`}
                          >
                            {formatHour(h)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Array.from({ length: 10 }, (_, i) => i + 1).map((rank) => (
                        <tr key={rank}>
                          <td className="text-slate-600 text-xs text-right pr-1 align-middle">{rank}</td>
                          {data.hours.map((h) => {
                            const leader = byHour.get(h)?.find((l) => l.rank === rank);
                            if (!leader) {
                              return <td key={h} className="rounded bg-slate-900/40 h-9" />;
                            }
                            const intensity = isUnusual ? (leader.ratio ?? 0) / 12 : 0.3;
                            return (
                              <td
                                key={h}
                                className="rounded border h-9 px-2 text-center align-middle"
                                style={heatStyle(intensity)}
                                title={
                                  isUnusual && leader.ratioLabel
                                    ? `${NAMES[leader.symbol] ?? leader.symbol} · ${leader.ratioLabel}`
                                    : `${NAMES[leader.symbol] ?? leader.symbol} · ${leader.volumeM} shares`
                                }
                              >
                                <span className="text-white text-xs font-semibold">{leader.symbol}</span>
                                {isUnusual && leader.ratio != null && (
                                  <span className="text-emerald-300/80 text-[10px] ml-1">{leader.ratio.toFixed(1)}x</span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              </>
            )}
          </>
        )}
      </main>

      <footer className="border-t border-slate-800 px-6 py-4 mt-8 text-xs text-slate-500 flex items-center justify-between flex-wrap gap-2">
        <span>volume.decifertrading.com</span>
        <span>
          {isUnusual
            ? "Compared against each stock's 30-day average · Alpaca data · updates every 5 min"
            : "Alpaca data · updates every 5 min"}
        </span>
      </footer>
    </div>
  );
}
