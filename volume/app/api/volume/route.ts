import { NextResponse } from "next/server";

const ALPACA_KEY = process.env.ALPACA_API_KEY;
const ALPACA_SECRET = process.env.ALPACA_SECRET_KEY;
const ALPACA_BASE = "https://data.alpaca.markets";

// Top 100 most-traded US stocks by dollar volume
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
];

export interface HourlyBar {
  hour: string;  // "9:30", "10:30", etc.
  symbol: string;
  volume: number;
}

export interface VolumeResponse {
  hours: string[];
  leaders: { hour: string; rank: number; symbol: string; volume: number; volumeM: string }[];
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

// Returns the most recent trading day (today if weekday, Friday if weekend/Monday pre-market)
function mostRecentTradingDay(): { date: string; isToday: boolean } {
  const d = nyTime();
  const day = d.getDay(); // 0=Sun, 6=Sat
  if (day === 0) {
    // Sunday — go back to Friday
    const fri = new Date(d);
    fri.setDate(fri.getDate() - 2);
    return { date: fmtDate(fri), isToday: false };
  }
  if (day === 6) {
    // Saturday — go back to Friday
    const fri = new Date(d);
    fri.setDate(fri.getDate() - 1);
    return { date: fmtDate(fri), isToday: false };
  }
  return { date: fmtDate(d), isToday: true };
}

function isMarketOpen(): boolean {
  const d = nyTime();
  const day = d.getDay();
  if (day === 0 || day === 6) return false;
  const mins = d.getHours() * 60 + d.getMinutes();
  return mins >= 570 && mins < 960; // 9:30–16:00
}

export async function GET() {
  if (!ALPACA_KEY || !ALPACA_SECRET) {
    return NextResponse.json({ error: "Missing Alpaca credentials" }, { status: 500 });
  }

  const { date, isToday } = mostRecentTradingDay();
  const start = `${date}T09:30:00-04:00`;
  const end = `${date}T16:00:00-04:00`;

  // Fetch hourly bars for all symbols in one call
  const url = new URL(`${ALPACA_BASE}/v2/stocks/bars`);
  url.searchParams.set("symbols", SYMBOLS.join(","));
  url.searchParams.set("timeframe", "1Hour");
  url.searchParams.set("start", start);
  url.searchParams.set("end", end);
  url.searchParams.set("limit", "10000");
  url.searchParams.set("feed", "sip");
  url.searchParams.set("currency", "USD");
  url.searchParams.set("adjustment", "raw");

  const res = await fetch(url.toString(), {
    headers: {
      "APCA-API-KEY-ID": ALPACA_KEY,
      "APCA-API-SECRET-KEY": ALPACA_SECRET,
    },
    next: { revalidate: 300 },
  });

  if (!res.ok) {
    const text = await res.text();
    return NextResponse.json({ error: `Alpaca error: ${text}` }, { status: 502 });
  }

  const data = await res.json() as { bars: Record<string, Array<{ t: string; v: number; o: number; c: number; h: number; l: number }>> };

  // Build hour → symbol → volume map
  const hourMap: Map<string, Map<string, number>> = new Map();

  for (const [symbol, bars] of Object.entries(data.bars ?? {})) {
    for (const bar of bars) {
      const d = new Date(bar.t);
      const nyD = new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }));
      const h = nyD.getHours();
      const m = nyD.getMinutes();
      const label = `${h}:${String(m).padStart(2, "0")}`;
      if (!hourMap.has(label)) hourMap.set(label, new Map());
      hourMap.get(label)!.set(symbol, bar.v);
    }
  }

  // Sort hours chronologically
  const hours = Array.from(hourMap.keys()).sort((a, b) => {
    const [ah, am] = a.split(":").map(Number);
    const [bh, bm] = b.split(":").map(Number);
    return ah * 60 + am - (bh * 60 + bm);
  });

  // Top 10 per hour
  const leaders: VolumeResponse["leaders"] = [];
  for (const hour of hours) {
    const symMap = hourMap.get(hour)!;
    const sorted = Array.from(symMap.entries()).sort((a, b) => b[1] - a[1]).slice(0, 10);
    sorted.forEach(([symbol, volume], i) => {
      leaders.push({
        hour,
        rank: i + 1,
        symbol,
        volume,
        volumeM: volume >= 1_000_000
          ? `${(volume / 1_000_000).toFixed(1)}M`
          : `${(volume / 1_000).toFixed(0)}K`,
      });
    });
  }

  return NextResponse.json({
    hours,
    leaders,
    ts: new Date().toISOString(),
    date,
    market_open: isMarketOpen(),
    is_today: isToday,
  } satisfies VolumeResponse);
}
