import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;
const BASE = "https://financialmodelingprep.com/stable";
const CACHE = { next: { revalidate: 3600 } } as const; // 1hr — historical data is stable

export interface EarningsHistoryItem {
  date: string;
  epsActual: number | null;
  epsEstimate: number | null;
  revenueActual: number | null;
  revenueEstimate: number | null;
}

export interface CompanyProfile {
  name: string;
  description: string;
  sector: string;
  industry: string;
  image: string;
  website: string;
  mktCap: number | null;
  country: string;
}

export interface PriceSnapshot {
  price: number | null;
  change: number | null;
  changePct: number | null;
}

export interface EarningsHistoryPayload {
  symbol: string;
  profile: CompanyProfile | null;
  price: PriceSnapshot;
  history: EarningsHistoryItem[];
  ts: string;
}

function parseProfile(data: unknown): CompanyProfile | null {
  if (!Array.isArray(data) || !data[0]) return null;
  const r = data[0] as Record<string, unknown>;
  return {
    name: typeof r.companyName === "string" ? r.companyName : "",
    description: typeof r.description === "string" ? r.description : "",
    sector: typeof r.sector === "string" ? r.sector : "",
    industry: typeof r.industry === "string" ? r.industry : "",
    image: typeof r.image === "string" ? r.image : "",
    website: typeof r.website === "string" ? r.website : "",
    mktCap: typeof r.mktCap === "number" ? r.mktCap : null,
    country: typeof r.country === "string" ? r.country : "",
  };
}

function parsePrice(data: unknown): PriceSnapshot {
  if (!Array.isArray(data) || !data[0]) return { price: null, change: null, changePct: null };
  const r = data[0] as Record<string, unknown>;
  return {
    price: typeof r.price === "number" ? r.price : null,
    change: typeof r.change === "number" ? r.change : null,
    changePct: typeof r.changesPercentage === "number" ? r.changesPercentage : null,
  };
}

function parseHistory(data: unknown): EarningsHistoryItem[] {
  if (!Array.isArray(data)) return [];
  return data.slice(0, 6).map((item: unknown) => {
    const r = (item ?? {}) as Record<string, unknown>;
    return {
      date: typeof r.date === "string" ? r.date.slice(0, 10) : "",
      epsActual: typeof r.eps === "number" ? r.eps : null,
      epsEstimate: typeof r.epsEstimated === "number" ? r.epsEstimated : null,
      revenueActual: typeof r.revenue === "number" ? r.revenue : null,
      revenueEstimate: typeof r.revenueEstimated === "number" ? r.revenueEstimated : null,
    };
  }).filter(e => e.date);
}

export async function GET(request: Request): Promise<NextResponse<EarningsHistoryPayload>> {
  const { searchParams } = new URL(request.url);
  const symbol = (searchParams.get("symbol") ?? "").toUpperCase().trim();
  const ts = new Date().toISOString();

  if (!symbol || !FMP_KEY) {
    return NextResponse.json({ symbol, profile: null, price: { price: null, change: null, changePct: null }, history: [], ts });
  }

  const key = `apikey=${FMP_KEY}`;
  const [profileRes, priceRes, historyRes] = await Promise.allSettled([
    fetch(`${BASE}/profile?symbol=${symbol}&${key}`, CACHE),
    fetch(`${BASE}/quote-short?symbol=${symbol}&${key}`, CACHE),
    fetch(`${BASE}/historical/earnings?symbol=${symbol}&limit=6&${key}`, CACHE),
  ]);

  let profile: CompanyProfile | null = null;
  let price: PriceSnapshot = { price: null, change: null, changePct: null };
  let history: EarningsHistoryItem[] = [];

  if (profileRes.status === "fulfilled" && profileRes.value.ok) {
    try { profile = parseProfile(await profileRes.value.json()); } catch { /* graceful */ }
  }
  if (priceRes.status === "fulfilled" && priceRes.value.ok) {
    // quote-short only has price, need full quote for change
    try {
      const data = await priceRes.value.json();
      if (Array.isArray(data) && data[0]) {
        const r = data[0] as Record<string, unknown>;
        price.price = typeof r.price === "number" ? r.price : null;
      }
    } catch { /* graceful */ }
  }
  // Get full quote for change data
  try {
    const quoteRes = await fetch(`${BASE}/quote?symbol=${symbol}&${key}`, CACHE);
    if (quoteRes.ok) price = parsePrice(await quoteRes.json());
  } catch { /* graceful */ }

  if (historyRes.status === "fulfilled" && historyRes.value.ok) {
    try { history = parseHistory(await historyRes.value.json()); } catch { /* graceful */ }
  }

  return NextResponse.json({ symbol, profile, price, history, ts });
}
