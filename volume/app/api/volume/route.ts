import { NextResponse } from "next/server";

const ALPACA_KEY = process.env.ALPACA_API_KEY;
const ALPACA_SECRET = process.env.ALPACA_SECRET_KEY;
const ALPACA_BASE = "https://data.alpaca.markets";

// Universe: large-cap core + high-activity tech/crypto/growth names from watchlist
const SYMBOLS = [
  "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","JPM","UNH",
  "XOM","LLY","V","MA","HD","PG","COST","MRK","ABBV","CVX",
  "BAC","CRM","NFLX","AMD","PEP","ORCL","TMO","KO","ADBE","WMT",
  "ACN","MCD","CSCO","ABT","LIN","DHR","TXN","NKE","QCOM","PM",
  "NEE","RTX","HON","CAT","IBM","GE","AMGN","SBUX","ISRG","PLD",
  "INTC","INTU","AXP","NOW","BKNG","ELV","SPGI","GS","UNP","LOW",
  "MS","BLK","DE","MDT","SYK","REGN","GILD","ADI","PH","ETN",
  "VRTX","ZTS","MO","CI","MDLZ","CB","DUK","SO","WM","CME",
  "CL","MMC","ITW","TGT","APD","BMY","PYPL","C","USB","MU",
  "PANW","CRWD","SNOW","PLTR","ARM","MSTR","HOOD","COIN","AMC","GME",
  "SMCI","TSM","TER","VRT","VICR","VST","ZS","RIOT","RBRK","SOFI",
  "NOK","OKTA","OSCR","MRVL","MDB","MPWR","NBIS","NTAP","HPE","HIVE",
  "LUNR","IREN","LRCX","LUMN","MARA","GTLB","SNDK",
  "DDOG","DELL","DOCN","EQT","FIG","FLEX","GEV","CIFR","CLSK","CORZ",
  "CRWV","CRDO","AAOI","ANET","ASML","TEAM","BTDR","BMNR","CNTA","AEHR",
  "AMKR","APLD",
];

export interface HourPoint {
  hour: string;
  volume: number;
  volumeM: string;
  ratio: number; // hourly volume vs normal hourly pace
}

export interface Mover {
  symbol: string;
  peakRatio: number;      // highest hourly ratio across the day
  peakHour: string;
  dayVolume: number;
  dayVolumeM: string;
  priceChangePct: number | null;
  series: HourPoint[];    // aligned to top-level `hours`
}

export interface VolumeResponse {
  hours: string[];
  unusual: Mover[];
  traded: Mover[];
  ts: string;
  date: string;
  market_open: boolean;
  is_today: boolean;
}

function nyTime(): Date {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
}
function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function mostRecentTradingDay(): { date: string; isToday: boolean } {
  const d = nyTime();
  const day = d.getDay();
  if (day === 0) { const f = new Date(d); f.setDate(f.getDate() - 2); return { date: fmtDate(f), isToday: false }; }
  if (day === 6) { const f = new Date(d); f.setDate(f.getDate() - 1); return { date: fmtDate(f), isToday: false }; }
  return { date: fmtDate(d), isToday: true };
}
function isMarketOpen(): boolean {
  const d = nyTime();
  if (d.getDay() === 0 || d.getDay() === 6) return false;
  const mins = d.getHours() * 60 + d.getMinutes();
  return mins >= 570 && mins < 960;
}
function fmtVol(v: number): string {
  return v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : `${(v / 1_000).toFixed(0)}K`;
}
function alpacaFetch(url: URL, revalidate: number) {
  return fetch(url.toString(), {
    headers: { "APCA-API-KEY-ID": ALPACA_KEY!, "APCA-API-SECRET-KEY": ALPACA_SECRET! },
    next: { revalidate },
  });
}

export async function GET() {
  if (!ALPACA_KEY || !ALPACA_SECRET) {
    return NextResponse.json({ error: "Missing Alpaca credentials" }, { status: 500 });
  }

  const { date, isToday } = mostRecentTradingDay();

  // Today's hourly bars (with OHLC)
  const hourlyUrl = new URL(`${ALPACA_BASE}/v2/stocks/bars`);
  hourlyUrl.searchParams.set("symbols", SYMBOLS.join(","));
  hourlyUrl.searchParams.set("timeframe", "1Hour");
  hourlyUrl.searchParams.set("start", `${date}T09:30:00-04:00`);
  hourlyUrl.searchParams.set("end", `${date}T16:00:00-04:00`);
  hourlyUrl.searchParams.set("limit", "10000");
  hourlyUrl.searchParams.set("feed", "sip");
  hourlyUrl.searchParams.set("adjustment", "raw");

  // 30-day daily bars for the volume baseline
  const histEnd = new Date(date + "T16:00:00-04:00");
  histEnd.setDate(histEnd.getDate() - 1);
  const histStart = new Date(histEnd);
  histStart.setDate(histStart.getDate() - 44);
  const histUrl = new URL(`${ALPACA_BASE}/v2/stocks/bars`);
  histUrl.searchParams.set("symbols", SYMBOLS.join(","));
  histUrl.searchParams.set("timeframe", "1Day");
  histUrl.searchParams.set("start", fmtDate(histStart) + "T00:00:00Z");
  histUrl.searchParams.set("end", fmtDate(histEnd) + "T23:59:59Z");
  histUrl.searchParams.set("limit", "10000");
  histUrl.searchParams.set("adjustment", "split");

  const [hourlyRes, histRes] = await Promise.all([
    alpacaFetch(hourlyUrl, 300),
    alpacaFetch(histUrl, 3600),
  ]);

  if (!hourlyRes.ok) {
    return NextResponse.json({ error: `Alpaca hourly error: ${await hourlyRes.text()}` }, { status: 502 });
  }

  type Bar = { t: string; v: number; o: number; c: number };
  const hourlyData = await hourlyRes.json() as { bars: Record<string, Bar[]> };

  // Volume baseline
  const avgDailyVol: Map<string, number> = new Map();
  if (histRes.ok) {
    const histData = await histRes.json() as { bars: Record<string, { v: number }[]> };
    for (const [sym, bars] of Object.entries(histData.bars ?? {})) {
      if (bars.length >= 5) avgDailyVol.set(sym, bars.reduce((s, b) => s + b.v, 0) / bars.length);
    }
  }

  // Collect the set of trading hours present
  const hourSet = new Set<string>();
  const labelOf = (iso: string) => {
    const ny = new Date(new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York" }));
    return `${ny.getHours()}:${String(ny.getMinutes()).padStart(2, "0")}`;
  };
  for (const bars of Object.values(hourlyData.bars ?? {})) {
    for (const b of bars) hourSet.add(labelOf(b.t));
  }
  const hours = Array.from(hourSet).sort((a, b) => {
    const [ah, am] = a.split(":").map(Number);
    const [bh, bm] = b.split(":").map(Number);
    return ah * 60 + am - (bh * 60 + bm);
  });

  const HOURS_PER_SESSION = 6.5;
  const UNUSUAL_THRESHOLD = 1.5;

  // Build a Mover per symbol
  const movers: Mover[] = [];
  for (const [symbol, bars] of Object.entries(hourlyData.bars ?? {})) {
    if (!bars.length) continue;
    const sorted = [...bars].sort((a, b) => new Date(a.t).getTime() - new Date(b.t).getTime());
    const dayOpen = sorted[0].o;
    const dayClose = sorted[sorted.length - 1].c;
    const priceChangePct = dayOpen ? parseFloat((((dayClose - dayOpen) / dayOpen) * 100).toFixed(2)) : null;

    const avgDaily = avgDailyVol.get(symbol);
    const expectedPerHour = avgDaily ? avgDaily / HOURS_PER_SESSION : 0;

    const volByHour: Map<string, number> = new Map();
    for (const b of sorted) volByHour.set(labelOf(b.t), b.v);

    const series: HourPoint[] = hours.map((h) => {
      const vol = volByHour.get(h) ?? 0;
      const ratio = expectedPerHour ? vol / expectedPerHour : 0;
      return { hour: h, volume: vol, volumeM: fmtVol(vol), ratio: parseFloat(ratio.toFixed(2)) };
    });

    const peakRatio = series.reduce((mx, p) => Math.max(mx, p.ratio), 0);
    const peakHour = series.find((p) => p.ratio === peakRatio)?.hour ?? hours[0];
    const dayVolume = sorted.reduce((s, b) => s + b.v, 0);

    movers.push({
      symbol,
      peakRatio: parseFloat(peakRatio.toFixed(1)),
      peakHour,
      dayVolume,
      dayVolumeM: fmtVol(dayVolume),
      priceChangePct,
      series,
    });
  }

  const unusual = movers
    .filter((m) => m.peakRatio >= UNUSUAL_THRESHOLD)
    .sort((a, b) => b.peakRatio - a.peakRatio)
    .slice(0, 20);

  const traded = [...movers]
    .sort((a, b) => b.dayVolume - a.dayVolume)
    .slice(0, 20);

  return NextResponse.json({
    hours,
    unusual,
    traded,
    ts: new Date().toISOString(),
    date,
    market_open: isMarketOpen(),
    is_today: isToday,
  } satisfies VolumeResponse);
}
