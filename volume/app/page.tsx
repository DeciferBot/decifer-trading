"use client";

import { useEffect, useState, useCallback } from "react";

interface HourPoint {
  hour: string;
  volume: number;
  volumeM: string;
  ratio: number;
}
interface Mover {
  symbol: string;
  peakRatio: number;
  peakHour: string;
  dayVolume: number;
  dayVolumeM: string;
  priceChangePct: number | null;
  series: HourPoint[];
}
interface VolumeData {
  hours: string[];
  unusual: Mover[];
  traded: Mover[];
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

function name(sym: string): string {
  return NAMES[sym] ?? sym;
}

function formatHour(h: string): string {
  const [hh, mm] = h.split(":").map(Number);
  const suffix = hh >= 12 ? "PM" : "AM";
  const display = hh > 12 ? hh - 12 : hh === 0 ? 12 : hh;
  return mm === 0 ? `${display} ${suffix}` : `${display}:${String(mm).padStart(2, "0")} ${suffix}`;
}
function fullDate(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}
function timeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}
function pricePct(p: number | null): { text: string; cls: string } {
  if (p == null) return { text: "", cls: "text-slate-500" };
  const sign = p > 0 ? "+" : "";
  if (p > 0.05) return { text: `${sign}${p.toFixed(2)}%`, cls: "text-emerald-400" };
  if (p < -0.05) return { text: `${p.toFixed(2)}%`, cls: "text-rose-400" };
  return { text: `${p.toFixed(2)}%`, cls: "text-slate-400" };
}

// Intraday bar strip — height = ratio (unusual) or volume (traded), peak bar bright
function IntradayBars({ mover, mode }: { mover: Mover; mode: Tab }) {
  const vals = mover.series.map((p) => (mode === "unusual" ? p.ratio : p.volume));
  const max = Math.max(...vals, 0.0001);
  const peakIdx = vals.indexOf(Math.max(...vals));
  return (
    <div className="flex items-end gap-[3px] h-10">
      {mover.series.map((p, i) => {
        const h = Math.max((vals[i] / max) * 100, 6);
        const isPeak = i === peakIdx;
        return (
          <div
            key={p.hour}
            className="flex-1 rounded-sm transition-colors"
            style={{
              height: `${h}%`,
              backgroundColor: isPeak ? "rgb(52,211,153)" : "rgba(52,211,153,0.22)",
            }}
            title={
              mode === "unusual"
                ? `${formatHour(p.hour)} · ${p.ratio.toFixed(1)}x normal`
                : `${formatHour(p.hour)} · ${p.volumeM} shares`
            }
          />
        );
      })}
    </div>
  );
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
  const movers = (isUnusual ? data?.unusual : data?.traded) ?? [];
  const lead = movers[0];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-slate-100 font-mono">
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
            {/* Date context */}
            <div className="mb-6">
              <h1 className="text-white text-xl font-semibold">{fullDate(data.date)}</h1>
              <p className="text-slate-400 text-sm mt-1">
                {data.is_today
                  ? "Today's trading so far, hour by hour (US Eastern time)."
                  : "The last completed trading session. Markets are closed for the weekend. The bars run from the 10 AM hour to the 4 PM close."}
              </p>
            </div>

            {/* Tabs */}
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

            {/* Plain-English lead */}
            {lead && (
              <p className="text-slate-300 text-sm mb-8 max-w-3xl leading-relaxed">
                {isUnusual ? (
                  <>
                    <span className="text-white font-semibold">{name(lead.symbol)}</span> saw the most unusual
                    activity, trading <span className="text-emerald-400 font-semibold">{lead.peakRatio.toFixed(1)}x</span> its
                    normal pace around {formatHour(lead.peakHour)}.
                    {lead.priceChangePct != null && Math.abs(lead.priceChangePct) > 0.05 && (
                      <> The stock finished {lead.priceChangePct > 0 ? "up" : "down"}{" "}
                        <span className={pricePct(lead.priceChangePct).cls + " font-semibold"}>
                          {pricePct(lead.priceChangePct).text}
                        </span> on the day.</>
                    )}{" "}
                    Heavy volume with a price move is usually where the real story is.
                  </>
                ) : (
                  <>
                    <span className="text-white font-semibold">{name(lead.symbol)}</span> had the most shares
                    change hands today ({lead.dayVolumeM}). The biggest companies lead this list almost every day,
                    so the <span className="text-slate-200">Unusual volume</span> tab is usually more telling.
                  </>
                )}
              </p>
            )}

            {/* Mover cards with intraday pattern */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {movers.map((m) => {
                const price = pricePct(m.priceChangePct);
                return (
                  <div key={m.symbol} className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 hover:border-slate-700 transition-colors">
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <div className="min-w-0">
                        <div className="flex items-baseline gap-2">
                          <span className="text-white font-bold text-base">{m.symbol}</span>
                          {price.text && <span className={`text-xs font-semibold ${price.cls}`}>{price.text}</span>}
                        </div>
                        <div className="text-slate-400 text-xs truncate">{name(m.symbol)}</div>
                      </div>
                      <div className="text-right flex-shrink-0">
                        {isUnusual ? (
                          <>
                            <div className="text-emerald-400 font-bold text-base leading-none">{m.peakRatio.toFixed(1)}x</div>
                            <div className="text-slate-500 text-[10px] mt-1">normal pace</div>
                          </>
                        ) : (
                          <>
                            <div className="text-emerald-400 font-bold text-base leading-none">{m.dayVolumeM}</div>
                            <div className="text-slate-500 text-[10px] mt-1">shares</div>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="mt-3">
                      <IntradayBars mover={m} mode={tab} />
                      <div className="flex justify-between mt-1.5 text-[10px] text-slate-500">
                        <span>{formatHour(m.series[0]?.hour ?? "")}</span>
                        <span className="text-emerald-400/80">peak {formatHour(m.peakHour)}</span>
                        <span>{formatHour(m.series[m.series.length - 1]?.hour ?? "")}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {movers.length === 0 && (
              <div className="text-slate-400 text-sm py-12">
                Nothing unusual yet. Stocks will appear here once they trade above their normal pace.
              </div>
            )}
          </>
        )}
      </main>

      <footer className="border-t border-slate-800 px-6 py-4 mt-8 text-xs text-slate-500 flex items-center justify-between flex-wrap gap-2">
        <span>volume.decifertrading.com</span>
        <span>
          {isUnusual
            ? "Each stock compared to its own 30-day average · Alpaca data · updates every 5 min"
            : "Total shares traded · Alpaca data · updates every 5 min"}
        </span>
      </footer>
    </div>
  );
}
