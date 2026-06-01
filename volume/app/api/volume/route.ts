import { NextResponse } from "next/server";

const ALPACA_KEY = process.env.ALPACA_API_KEY;
const ALPACA_SECRET = process.env.ALPACA_SECRET_KEY;
const ALPACA_BASE = "https://data.alpaca.markets";

// Universe: large-cap core + high-activity tech/crypto/growth names from watchlist
const SYMBOLS = [
  // Mega-cap core
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
  // Watchlist batch 1
  "SMCI","TSM","TER","VRT","VICR","VST","ZS","RIOT","RBRK","SOFI",
  "NOK","OKTA","OSCR","MRVL","MDB","MPWR","NBIS","NTAP","HPE","HIVE",
  "LUNR","IREN","LRCX","LUMN","MARA","GTLB","SNDK",
  // Watchlist batch 2
  "DDOG","DELL","DOCN","EQT","FIG","FLEX","GEV","CIFR","CLSK","CORZ",
  "CRWV","CRDO","AAOI","ANET","ASML","TEAM","BTDR","BMNR","CNTA","AEHR",
  "AMKR","APLD",
];

export interface LeaderEntry {
  hour: string;
  rank: number;
  symbol: string;
  volume: number;
  volumeM: string;
  // unusual view only
  ratio?: number;
  ratioLabel?: string;
}

export interface VolumeResponse {
  hours: string[];
  leaders: LeaderEntry[];
  unusual_leaders: LeaderEntry[];
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
  if (day === 0) { const fri = new Date(d); fri.setDate(fri.getDate() - 2); return { date: fmtDate(fri), isToday: false }; }
  if (day === 6) { const fri = new Date(d); fri.setDate(fri.getDate() - 1); return { date: fmtDate(fri), isToday: false }; }
  return { date: fmtDate(d), isToday: true };
}

function isMarketOpen(): boolean {
  const d = nyTime();
  const day = d.getDay();
  if (day === 0 || day === 6) return false;
  const mins = d.getHours() * 60 + d.getMinutes();
  return mins >= 570 && mins < 960;
}

function fmtVol(v: number): string {
  return v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : `${(v / 1_000).toFixed(0)}K`;
}

function alpacaFetch(url: URL, revalidate: number) {
  return fetch(url.toString(), {
    headers: {
      "APCA-API-KEY-ID": ALPACA_KEY!,
      "APCA-API-SECRET-KEY": ALPACA_SECRET!,
    },
    next: { revalidate },
  });
}

export async function GET() {
  if (!ALPACA_KEY || !ALPACA_SECRET) {
    return NextResponse.json({ error: "Missing Alpaca credentials" }, { status: 500 });
  }

  const { date, isToday } = mostRecentTradingDay();

  // --- Fetch 1: today's hourly bars ---
  const hourlyUrl = new URL(`${ALPACA_BASE}/v2/stocks/bars`);
  hourlyUrl.searchParams.set("symbols", SYMBOLS.join(","));
  hourlyUrl.searchParams.set("timeframe", "1Hour");
  hourlyUrl.searchParams.set("start", `${date}T09:30:00-04:00`);
  hourlyUrl.searchParams.set("end", `${date}T16:00:00-04:00`);
  hourlyUrl.searchParams.set("limit", "10000");
  hourlyUrl.searchParams.set("feed", "sip");
  hourlyUrl.searchParams.set("adjustment", "raw");

  // --- Fetch 2: 30-day daily bars for baseline ---
  const histEnd = new Date(date + "T16:00:00-04:00");
  histEnd.setDate(histEnd.getDate() - 1); // exclude today
  const histStart = new Date(histEnd);
  histStart.setDate(histStart.getDate() - 44); // ~30 trading days in ~44 calendar days

  const histUrl = new URL(`${ALPACA_BASE}/v2/stocks/bars`);
  histUrl.searchParams.set("symbols", SYMBOLS.join(","));
  histUrl.searchParams.set("timeframe", "1Day");
  histUrl.searchParams.set("start", fmtDate(histStart) + "T00:00:00Z");
  histUrl.searchParams.set("end", fmtDate(histEnd) + "T23:59:59Z");
  histUrl.searchParams.set("limit", "10000");
  histUrl.searchParams.set("adjustment", "split");

  const [hourlyRes, histRes] = await Promise.all([
    alpacaFetch(hourlyUrl, 300),
    alpacaFetch(histUrl, 3600), // history changes slowly, cache 1h
  ]);

  if (!hourlyRes.ok) {
    return NextResponse.json({ error: `Alpaca hourly error: ${await hourlyRes.text()}` }, { status: 502 });
  }

  type BarRecord = { t: string; v: number };
  const hourlyData = await hourlyRes.json() as { bars: Record<string, BarRecord[]> };

  // Average daily volume per symbol from history
  const avgDailyVol: Map<string, number> = new Map();
  if (histRes.ok) {
    const histData = await histRes.json() as { bars: Record<string, BarRecord[]> };
    for (const [sym, bars] of Object.entries(histData.bars ?? {})) {
      if (bars.length >= 5) {
        const avg = bars.reduce((s, b) => s + b.v, 0) / bars.length;
        avgDailyVol.set(sym, avg);
      }
    }
  }

  // Build hour → symbol → volume map
  const hourMap: Map<string, Map<string, number>> = new Map();
  for (const [symbol, bars] of Object.entries(hourlyData.bars ?? {})) {
    for (const bar of bars) {
      const nyD = new Date(new Date(bar.t).toLocaleString("en-US", { timeZone: "America/New_York" }));
      const label = `${nyD.getHours()}:${String(nyD.getMinutes()).padStart(2, "0")}`;
      if (!hourMap.has(label)) hourMap.set(label, new Map());
      hourMap.get(label)!.set(symbol, bar.v);
    }
  }

  const hours = Array.from(hourMap.keys()).sort((a, b) => {
    const [ah, am] = a.split(":").map(Number);
    const [bh, bm] = b.split(":").map(Number);
    return ah * 60 + am - (bh * 60 + bm);
  });

  const leaders: LeaderEntry[] = [];
  const unusual_leaders: LeaderEntry[] = [];

  // Trading session is ~6.5 hours; expected volume per hour = avg_daily / 6.5
  const HOURS_PER_SESSION = 6.5;
  // Only surface names trading at least 1.5x their normal hourly pace
  const UNUSUAL_THRESHOLD = 1.5;

  for (const hour of hours) {
    const symMap = hourMap.get(hour)!;

    // Raw top 10
    const rawSorted = Array.from(symMap.entries()).sort((a, b) => b[1] - a[1]).slice(0, 10);
    rawSorted.forEach(([symbol, volume], i) => {
      leaders.push({ hour, rank: i + 1, symbol, volume, volumeM: fmtVol(volume) });
    });

    // Unusual: ratio = hourly_vol / (avg_daily_vol / 6.5). Only flag genuine spikes (>= 1.5x).
    const withRatio: Array<{ symbol: string; volume: number; ratio: number }> = [];
    for (const [symbol, volume] of symMap.entries()) {
      const avgDaily = avgDailyVol.get(symbol);
      if (!avgDaily) continue; // no baseline yet (new IPO etc.)
      const expectedPerHour = avgDaily / HOURS_PER_SESSION;
      const ratio = volume / expectedPerHour;
      if (ratio < UNUSUAL_THRESHOLD) continue; // skip normal-activity names
      withRatio.push({ symbol, volume, ratio });
    }
    withRatio.sort((a, b) => b.ratio - a.ratio);
    withRatio.slice(0, 10).forEach(({ symbol, volume, ratio }, i) => {
      unusual_leaders.push({
        hour,
        rank: i + 1,
        symbol,
        volume,
        volumeM: fmtVol(volume),
        ratio: parseFloat(ratio.toFixed(1)),
        ratioLabel: `${ratio.toFixed(1)}x normal`,
      });
    });
  }

  return NextResponse.json({
    hours,
    leaders,
    unusual_leaders,
    ts: new Date().toISOString(),
    date,
    market_open: isMarketOpen(),
    is_today: isToday,
  } satisfies VolumeResponse);
}
