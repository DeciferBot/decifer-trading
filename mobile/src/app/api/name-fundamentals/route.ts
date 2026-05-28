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

  // FMP stable API uses singular ?symbol= for single-symbol endpoints.
  // profile-symbol replaces the legacy v3/profile; grades-summary + price-target-consensus
  // replace the deprecated analyst-consensus endpoint.
  const [profileResult, metricsResult, gradeResult, priceTargetResult, growthResult] =
    await Promise.allSettled([
      fetch(`${base}/profile-symbol?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/key-metrics-ttm?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/grades-summary?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/price-target-consensus?symbol=${symbol}&${key}`, CACHE_OPTS),
      fetch(`${base}/financial-growth?symbol=${symbol}&period=annual&limit=1&${key}`, CACHE_OPTS),
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

  // ── Analyst: consensus from grades-summary, price target from price-target-consensus ──
  let analyst: {
    consensus?: string;
    priceTarget?: number;
    ratingCount?: number;
  } | undefined;

  if (gradeResult.status === "fulfilled" && gradeResult.value.ok) {
    try {
      const data = await gradeResult.value.json();
      const g = Array.isArray(data) ? data[0] : data;
      if (g) {
        const consensus = typeof g.consensus === "string" ? g.consensus : undefined;
        const buy =
          (typeof g.buy === "number" ? g.buy : 0) +
          (typeof g.strongBuy === "number" ? g.strongBuy : 0);
        const total =
          buy +
          (typeof g.hold === "number" ? g.hold : 0) +
          (typeof g.sell === "number" ? g.sell : 0) +
          (typeof g.strongSell === "number" ? g.strongSell : 0);
        if (consensus || total > 0) {
          analyst = { consensus, ratingCount: total > 0 ? total : undefined };
        }
      }
    } catch { /* graceful */ }
  }

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
