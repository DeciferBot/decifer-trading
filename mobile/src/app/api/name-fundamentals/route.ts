import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { parseSymbols } from "@/lib/namePriceUtils";

const FMP_KEY = process.env.FMP_API_KEY;
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

  // FMP stable API — verified working endpoints for this plan:
  // stable/profile (not profile-symbol), key-metrics-ttm, price-target-consensus, financial-growth.
  // grades-summary returns [] with this key — omitted.
  const [profileResult, metricsResult, priceTargetResult, growthResult, shortFloatResult] =
    await Promise.allSettled([
      fetch(`${base}/profile?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/key-metrics-ttm?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/price-target-consensus?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/financial-growth?symbol=${symbol}&period=annual&limit=1&${key}`, CACHE_OPTS),
      fetch(`${base}/shares-float?symbol=${symbol}&${key}`, { next: { revalidate: 86400 } }),
    ]);

  // ── Profile ──────────────────────────────────────────────────────────────────
  let profile: {
    companyName?: string;
    description?: string;
    sector?: string;
    industry?: string;
    mktCap?: number;
  } | undefined;

  if (profileResult.status === "fulfilled" && profileResult.value.ok) {
    try {
      const data = await profileResult.value.json();
      const p = Array.isArray(data) ? data[0] : data;
      if (p) {
        profile = {
          companyName: typeof p.companyName === "string" ? p.companyName : undefined,
          description:
            typeof p.description === "string" && p.description.length > 20
              ? p.description.slice(0, 600)
              : undefined,
          sector: typeof p.sector === "string" ? p.sector : undefined,
          industry: typeof p.industry === "string" ? p.industry : undefined,
          // FMP stable/profile-symbol returns "marketCap" (not "mktCap")
          mktCap:
            typeof p.marketCap === "number" && p.marketCap > 0 ? p.marketCap : undefined,
        };
        if (!profile.companyName && !profile.sector && !profile.description) profile = undefined;
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
        const eps =
          typeof m.epsTTM === "number" ? parseFloat(m.epsTTM.toFixed(2)) : undefined;
        if (pe !== undefined || gm !== undefined || eps !== undefined) {
          fundamentals = { peRatio: pe, grossMargin: gm, eps };
        }
      }
    } catch { /* graceful */ }
  }

  // ── Analyst: price target from price-target-consensus ────────────────────────
  let analyst: {
    consensus?: string;
    priceTarget?: number;
    ratingCount?: number;
  } | undefined;

  if (priceTargetResult.status === "fulfilled" && priceTargetResult.value.ok) {
    try {
      const data = await priceTargetResult.value.json();
      const p = Array.isArray(data) ? data[0] : data;
      if (p && typeof p.targetConsensus === "number" && p.targetConsensus > 0) {
        analyst = {
          ...(analyst ?? {}),
          priceTarget: parseFloat(p.targetConsensus.toFixed(2)),
        };
      }
    } catch { /* graceful */ }
  }

  // ── Revenue growth (annual) ──────────────────────────────────────────────────
  if (growthResult.status === "fulfilled" && growthResult.value.ok) {
    try {
      const data = await growthResult.value.json();
      const g = Array.isArray(data) ? data[0] : data;
      if (g && typeof g.revenueGrowth === "number") {
        fundamentals = {
          ...(fundamentals ?? {}),
          revenueGrowth: parseFloat(g.revenueGrowth.toFixed(4)),
        };
      }
    } catch { /* graceful */ }
  }

  // ── Float context (shares-float — free float %) ───────────────────────────────
  let floatContext: { freeFloatPct: number; floatShares: number | null } | undefined;

  if (shortFloatResult.status === "fulfilled" && shortFloatResult.value.ok) {
    try {
      const data = await shortFloatResult.value.json();
      const s = Array.isArray(data) ? data[0] : data;
      if (s && typeof s.freeFloat === "number" && s.freeFloat > 0) {
        floatContext = {
          freeFloatPct: parseFloat(s.freeFloat.toFixed(1)),
          floatShares: typeof s.floatShares === "number" ? s.floatShares : null,
        };
      }
    } catch { /* graceful */ }
  }

  const available = !!(profile || fundamentals || analyst || floatContext);

  return NextResponse.json({
    symbol,
    ts: new Date().toISOString(),
    ...(profile && { profile }),
    ...(fundamentals && { fundamentals }),
    ...(analyst && { analyst }),
    ...(floatContext && { floatContext }),
    available,
    source: available ? "fmp" : "none",
  });
}
