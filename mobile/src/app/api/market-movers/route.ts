import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;

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

  let gainers: Mover[] = [];
  let losers: Mover[] = [];

  if (gainersRes.status === "fulfilled" && gainersRes.value.ok) {
    try {
      const raw = await gainersRes.value.json();
      gainers = (Array.isArray(raw) ? raw : [])
        .slice(0, 5)
        .map(toMover)
        .filter(m => !isNaN(m.changePct) && !isNaN(m.price));
    } catch { /* graceful */ }
  }

  if (losersRes.status === "fulfilled" && losersRes.value.ok) {
    try {
      const raw = await losersRes.value.json();
      losers = (Array.isArray(raw) ? raw : [])
        .slice(0, 5)
        .map(toMover)
        .filter(m => !isNaN(m.changePct) && !isNaN(m.price));
    } catch { /* graceful */ }
  }

  return NextResponse.json({ gainers, losers, ts });
}
