import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;
const BASE = "https://financialmodelingprep.com/stable";

// 10-minute Vercel cache — morning brief data is slow-moving
const CACHE_OPTS = { next: { revalidate: 600 } } as const;

// ── Exported types (consumed by TodayTab and downstream) ─────────────────────

export interface EconEvent {
  event: string;
  date: string;
  time: string;
  country: string;
  actual: number | null;
  previous: number | null;
  estimate: number | null;
  impact: string;
  unit: string | null;
}

export interface EarningsItem {
  symbol: string;
  name: string;
  date: string;
  time: string; // "bmo" | "amc" | "dmh" | ""
  epsEst: number | null;
  revEst: number | null;
}

export interface AnalystItem {
  symbol: string;
  publishedDate: string;
  gradingCompany: string;
  action: string;
  fromGrade: string;
  toGrade: string;
  priceWhenPosted: number | null;
}

export interface MorningBriefPayload {
  econ: EconEvent[];
  earnings: EarningsItem[];
  analyst: AnalystItem[];
  ts: string;
}

// ── Date helpers ──────────────────────────────────────────────────────────────

/** Returns today's date in YYYY-MM-DD format using America/New_York timezone. */
function nyToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/** Returns a date string N days after `from` in YYYY-MM-DD format. */
function addDays(from: string, days: number): string {
  const d = new Date(`${from}T00:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

// ── Parsers ───────────────────────────────────────────────────────────────────

function parseEcon(raw: unknown[]): EconEvent[] {
  const results: EconEvent[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;

    // Country filter: US only (or missing/null country)
    const country =
      r.country === null || r.country === undefined
        ? ""
        : typeof r.country === "string"
        ? r.country
        : "";
    if (country !== "" && country !== "US") continue;

    // Impact filter: High or Medium only
    const impact = typeof r.impact === "string" ? r.impact : "";
    if (impact !== "High" && impact !== "Medium") continue;

    const event = typeof r.event === "string" ? r.event : "";
    if (!event) continue;

    results.push({
      event,
      date: typeof r.date === "string" ? r.date : "",
      time: typeof r.time === "string" ? r.time : "",
      country,
      actual: typeof r.actual === "number" ? r.actual : null,
      previous: typeof r.previous === "number" ? r.previous : null,
      estimate: typeof r.estimate === "number" ? r.estimate : null,
      impact,
      unit: typeof r.unit === "string" ? r.unit : null,
    });

    if (results.length >= 20) break;
  }
  return results;
}

function parseEarnings(raw: unknown[]): EarningsItem[] {
  const results: EarningsItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;

    const symbol = typeof r.symbol === "string" ? r.symbol.trim() : "";
    const date = typeof r.date === "string" ? r.date.trim() : "";
    if (!symbol || !date) continue;

    results.push({
      symbol,
      name:
        typeof r.name === "string" && r.name.trim()
          ? r.name.trim()
          : symbol,
      date,
      time: typeof r.time === "string" ? r.time : "",
      epsEst: typeof r.epsEstimated === "number" ? r.epsEstimated : null,
      revEst: typeof r.revenueEstimated === "number" ? r.revenueEstimated : null,
    });

    if (results.length >= 200) break;
  }
  return results;
}

function parseAnalyst(raw: unknown[]): AnalystItem[] {
  const results: AnalystItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;

    const symbol = typeof r.symbol === "string" ? r.symbol.trim() : "";
    const gradingCompany =
      typeof r.gradingCompany === "string" ? r.gradingCompany.trim() : "";
    if (!symbol || !gradingCompany) continue;

    results.push({
      symbol,
      publishedDate:
        typeof r.publishedDate === "string" ? r.publishedDate : "",
      gradingCompany,
      action: typeof r.action === "string" ? r.action : "",
      fromGrade: typeof r.fromGrade === "string" ? r.fromGrade : "",
      toGrade: typeof r.toGrade === "string" ? r.toGrade : "",
      priceWhenPosted:
        typeof r.priceWhenPosted === "number" ? r.priceWhenPosted : null,
    });

    if (results.length >= 100) break;
  }
  return results;
}

// ── GET handler ───────────────────────────────────────────────────────────────

export async function GET(): Promise<NextResponse<MorningBriefPayload>> {
  const ts = new Date().toISOString();

  if (!FMP_KEY) {
    return NextResponse.json({ econ: [], earnings: [], analyst: [], ts });
  }

  const today = nyToday();
  const in7d = addDays(today, 7);
  const key = `apikey=${FMP_KEY}`;

  const [econResult, earningsResult, analystResult] = await Promise.allSettled([
    fetch(
      `${BASE}/economic-calendar?from=${today}&to=${today}&${key}`,
      CACHE_OPTS
    ),
    fetch(
      `${BASE}/earnings-calendar?from=${today}&to=${in7d}&${key}`,
      CACHE_OPTS
    ),
    fetch(`${BASE}/upgrades-downgrades?limit=100&${key}`, CACHE_OPTS),
  ]);

  let econ: EconEvent[] = [];
  let earnings: EarningsItem[] = [];
  let analyst: AnalystItem[] = [];

  if (econResult.status === "fulfilled" && econResult.value.ok) {
    try {
      const data = await econResult.value.json();
      econ = parseEcon(Array.isArray(data) ? data : []);
    } catch { /* graceful — returns empty */ }
  }

  if (earningsResult.status === "fulfilled" && earningsResult.value.ok) {
    try {
      const data = await earningsResult.value.json();
      earnings = parseEarnings(Array.isArray(data) ? data : []);
    } catch { /* graceful — returns empty */ }
  }

  if (analystResult.status === "fulfilled" && analystResult.value.ok) {
    try {
      const data = await analystResult.value.json();
      analyst = parseAnalyst(Array.isArray(data) ? data : []);
    } catch { /* graceful — returns empty */ }
  }

  return NextResponse.json({ econ, earnings, analyst, ts });
}
