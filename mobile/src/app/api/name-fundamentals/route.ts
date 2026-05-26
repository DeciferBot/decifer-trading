import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { parseSymbols } from "@/lib/namePriceUtils";

const FMP_KEY = process.env.FMP_API_KEY;

// 5-minute cache — fundamentals change slowly, but we want reasonable freshness.
const CACHE_OPTS = { next: { revalidate: 300 } } as const;

export async function GET(req: NextRequest) {
  const raw = req.nextUrl.searchParams.get("symbol");
  const symbols = parseSymbols(raw);

  if (symbols.length === 0) {
    return NextResponse.json({ error: "Valid symbol required" }, { status: 400 });
  }

  const symbol = symbols[0];

  if (!FMP_KEY) {
    return NextResponse.json({
      symbol,
      ts: new Date().toISOString(),
      available: false,
      source: "none",
    });
  }

  const base = "https://financialmodelingprep.com/stable";
  const key = `apikey=${FMP_KEY}`;

  const [profileResult, metricsResult, analystResult] = await Promise.allSettled([
    fetch(`${base}/profile?symbols=${symbol}&${key}`, CACHE_OPTS),
    fetch(`${base}/key-metrics-ttm?symbols=${symbol}&${key}`, CACHE_OPTS),
    fetch(`${base}/analyst-consensus?symbols=${symbol}&${key}`, CACHE_OPTS),
  ]);

  // ── Profile ──────────────────────────────────────────────────────────────────
  let profile: {
    companyName?: string;
    sector?: string;
    industry?: string;
    marketCap?: number;
  } | undefined;

  if (profileResult.status === "fulfilled" && profileResult.value.ok) {
    try {
      const data = await profileResult.value.json();
      const p = Array.isArray(data) ? data[0] : data;
      if (p) {
        profile = {
          companyName: typeof p.companyName === "string" ? p.companyName : undefined,
          sector: typeof p.sector === "string" ? p.sector : undefined,
          industry: typeof p.industry === "string" ? p.industry : undefined,
          marketCap: typeof p.mktCap === "number" && p.mktCap > 0 ? p.mktCap : undefined,
        };
        if (!profile.companyName && !profile.sector) profile = undefined;
      }
    } catch { /* graceful */ }
  }

  // ── Key metrics TTM ───────────────────────────────────────────────────────────
  let fundamentals: {
    revenue?: number;
    eps?: number;
    peRatio?: number;
    grossMargin?: number;
    revenueGrowth?: number;
  } | undefined;

  if (metricsResult.status === "fulfilled" && metricsResult.value.ok) {
    try {
      const data = await metricsResult.value.json();
      const m = Array.isArray(data) ? data[0] : data;
      if (m) {
        const pe =
          typeof m.peRatioTTM === "number" && m.peRatioTTM > 0
            ? parseFloat(m.peRatioTTM.toFixed(1))
            : undefined;
        const gm =
          typeof m.grossProfitMarginTTM === "number" && m.grossProfitMarginTTM > 0
            ? parseFloat(m.grossProfitMarginTTM.toFixed(4))
            : undefined;
        if (pe !== undefined || gm !== undefined) {
          fundamentals = { peRatio: pe, grossMargin: gm };
        }
      }
    } catch { /* graceful */ }
  }

  // ── Analyst consensus ─────────────────────────────────────────────────────────
  let analyst: {
    consensus?: string;
    priceTarget?: number;
    ratingCount?: number;
  } | undefined;

  if (analystResult.status === "fulfilled" && analystResult.value.ok) {
    try {
      const data = await analystResult.value.json();
      const a = Array.isArray(data) ? data[0] : data;
      if (a) {
        const consensus = typeof a.consensus === "string" ? a.consensus : undefined;
        const ratingCount =
          typeof a.numberOfAnalysts === "number" ? a.numberOfAnalysts : undefined;
        const priceTarget =
          typeof a.targetConsensus === "number" && a.targetConsensus > 0
            ? parseFloat(a.targetConsensus.toFixed(2))
            : undefined;
        if (consensus || ratingCount || priceTarget) {
          analyst = { consensus, ratingCount, priceTarget };
        }
      }
    } catch { /* graceful */ }
  }

  const available = !!(profile || fundamentals || analyst);

  return NextResponse.json({
    symbol,
    ts: new Date().toISOString(),
    ...(profile && { profile }),
    ...(fundamentals && { fundamentals }),
    ...(analyst && { analyst }),
    available,
    source: available ? "fmp" : "none",
  });
}
