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

// FMP stable/grades response fields: symbol, date, gradingCompany, previousGrade, newGrade, action.
// Note: FMP ignores limit= on this endpoint and returns full history — cutoffDate guards memory.
function parseAnalyst(raw: unknown[], cutoffDate?: string): AnalystItem[] {
  const results: AnalystItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;

    const symbol = typeof r.symbol === "string" ? r.symbol.trim() : "";
    const gradingCompany =
      typeof r.gradingCompany === "string" ? r.gradingCompany.trim() : "";
    if (!symbol || !gradingCompany) continue;

    // date field in grades endpoint (YYYY-MM-DD), map to publishedDate for downstream consumers
    const publishedDate = typeof r.date === "string" ? r.date.slice(0, 10) : "";
    if (!publishedDate) continue;
    // FMP returns full history sorted newest-first; stop once past our cutoff window
    if (cutoffDate && publishedDate < cutoffDate) break;

    results.push({
      symbol,
      publishedDate,
      gradingCompany,
      action: typeof r.action === "string" ? r.action : "",
      fromGrade: typeof r.previousGrade === "string" ? r.previousGrade : "",
      toGrade: typeof r.newGrade === "string" ? r.newGrade : "",
      priceWhenPosted: null,
    });
  }
  return results;
}

// Key symbols from the intelligence roster — covers all major TTG and operational themes.
// Fetch recent grades for these in parallel (stable/grades requires a symbol; no bulk endpoint).
const ANALYST_SYMBOLS = [
  // AI semiconductors & infrastructure
  "NVDA", "TSM", "AVGO", "AMD", "ASML", "QCOM", "MRVL", "ARM",
  // AI compute / cloud platforms
  "MSFT", "GOOGL", "AMZN", "META", "CRM", "PLTR", "ORCL", "SNOW",
  // AI servers & power
  "SMCI", "DELL", "VRT", "ETN", "CEG", "ANET",
  // Defence
  "RTX", "LMT", "NOC", "GD",
  // Healthcare / GLP-1
  "LLY", "MRK", "ABBV", "NVO",
  // Financials
  "JPM", "GS", "BAC",
  // Copper / critical minerals
  "FCX", "SCCO",
];

// ── GET handler ───────────────────────────────────────────────────────────────

export async function GET(): Promise<NextResponse<MorningBriefPayload>> {
  const ts = new Date().toISOString();

  if (!FMP_KEY) {
    return NextResponse.json({ econ: [], earnings: [], analyst: [], ts });
  }

  const today = nyToday();
  const in7d = addDays(today, 7);
  const key = `apikey=${FMP_KEY}`;

  // Fetch econ, earnings, and analyst grades in parallel.
  // Analyst grades require per-symbol calls (no bulk endpoint in FMP stable API).
  const [econResult, earningsResult, ...analystResults] = await Promise.allSettled([
    fetch(
      `${BASE}/economic-calendar?from=${today}&to=${today}&${key}`,
      CACHE_OPTS
    ),
    fetch(
      `${BASE}/earnings-calendar?from=${today}&to=${in7d}&${key}`,
      CACHE_OPTS
    ),
    ...ANALYST_SYMBOLS.map(sym =>
      fetch(`${BASE}/grades?symbol=${sym}&limit=30&${key}`, CACHE_OPTS)
    ),
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

  // Merge per-symbol analyst grade results, deduplicate by symbol+date+firm, sort newest first.
  // cutoff=14 days ago: parseAnalyst stops early on each symbol's full-history response (FMP ignores limit=).
  const cutoff14d = addDays(today, -14);
  const analystRaw: AnalystItem[] = [];
  for (const result of analystResults) {
    if (result.status !== "fulfilled" || !result.value.ok) continue;
    try {
      const data = await result.value.json();
      analystRaw.push(...parseAnalyst(Array.isArray(data) ? data : [], cutoff14d));
    } catch { /* graceful */ }
  }
  const seen = new Set<string>();
  analyst = analystRaw
    .filter(a => {
      const key = `${a.symbol}|${a.publishedDate}|${a.gradingCompany}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => b.publishedDate.localeCompare(a.publishedDate))
    .slice(0, 100);

  return NextResponse.json({ econ, earnings, analyst, ts });
}
