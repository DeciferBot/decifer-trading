import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;
const MIN_MARKET_CAP = 1_000_000_000;

export interface Mover {
  symbol: string;
  name: string;
  price: number;
  changePct: number;
  logoUrl: string;
}

export interface MarketMoversPayload {
  gainers: Mover[];
  losers: Mover[];
  ts: string;
}

function toMover(raw: {
  symbol: string;
  name: string;
  price: number | string;
  changesPercentage: number | string;
}): Mover {
  const pct =
    typeof raw.changesPercentage === "string"
      ? parseFloat(raw.changesPercentage.replace("%", ""))
      : raw.changesPercentage;
  return {
    symbol: raw.symbol,
    name: raw.name ?? raw.symbol,
    price: typeof raw.price === "number" ? raw.price : parseFloat(String(raw.price)),
    changePct: parseFloat(pct.toFixed(2)),
    logoUrl: `https://images.financialmodelingprep.com/symbol/${raw.symbol}.png`,
  };
}

async function filterByMarketCap(movers: Mover[]): Promise<Mover[]> {
  if (!movers.length) return [];
  const symbols = movers.map(m => m.symbol).join(",");
  try {
    const res = await fetch(
      `https://financialmodelingprep.com/stable/batch-quote?symbols=${symbols}&apikey=${FMP_KEY}`,
      { next: { revalidate: 300 } }
    );
    if (!res.ok) return movers;
    const quotes: { symbol: string; marketCap?: number }[] = await res.json();
    const capMap = new Map(quotes.map(q => [q.symbol, q.marketCap ?? 0]));
    return movers.filter(m => (capMap.get(m.symbol) ?? 0) >= MIN_MARKET_CAP);
  } catch {
    return movers;
  }
}

export async function GET() {
  const ts = new Date().toISOString();

  if (!FMP_KEY) {
    return NextResponse.json({ gainers: [], losers: [], ts });
  }

  const [gainersRes, losersRes] = await Promise.allSettled([
    fetch(`https://financialmodelingprep.com/stable/biggest-gainers?apikey=${FMP_KEY}`, {
      next: { revalidate: 300 },
    }),
    fetch(`https://financialmodelingprep.com/stable/biggest-losers?apikey=${FMP_KEY}`, {
      next: { revalidate: 300 },
    }),
  ]);

  let rawGainers: Mover[] = [];
  let rawLosers: Mover[] = [];

  if (gainersRes.status === "fulfilled" && gainersRes.value.ok) {
    try {
      const raw = await gainersRes.value.json();
      rawGainers = (Array.isArray(raw) ? raw : [])
        .map(toMover)
        .filter(m => !isNaN(m.changePct) && !isNaN(m.price) && m.price >= 5);
    } catch { /* graceful */ }
  }

  if (losersRes.status === "fulfilled" && losersRes.value.ok) {
    try {
      const raw = await losersRes.value.json();
      rawLosers = (Array.isArray(raw) ? raw : [])
        .map(toMover)
        .filter(m => !isNaN(m.changePct) && !isNaN(m.price) && m.price >= 5);
    } catch { /* graceful */ }
  }

  // Fetch market caps for top candidates and filter to ≥$1B only
  const CANDIDATES = 30;
  const [gainers, losers] = await Promise.all([
    filterByMarketCap(rawGainers.slice(0, CANDIDATES)).then(r => r.slice(0, 5)),
    filterByMarketCap(rawLosers.slice(0, CANDIDATES)).then(r => r.slice(0, 5)),
  ]);

  return NextResponse.json({ gainers, losers, ts });
}
