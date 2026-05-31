import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;
const BASE = "https://financialmodelingprep.com/stable";
const CALENDAR_CACHE = { next: { revalidate: 1800 } } as const;   // 30 min
const PROFILE_CACHE  = { next: { revalidate: 86400 } } as const;  // 24 hr — name/sector rarely change

export interface EarningsEntry {
  symbol: string;
  name: string;
  date: string;   // YYYY-MM-DD
  time: string;   // "bmo" | "amc" | ""
  epsEst: number | null;
  revEst: number | null;
  sector: string; // "" if unknown
}

export interface EarningsCalendarPayload {
  earnings: EarningsEntry[];
  ts: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function nyToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function addDays(from: string, days: number): string {
  const d = new Date(`${from}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function getWeekRange(today: string): { from: string; to: string } {
  const d = new Date(`${today}T12:00:00Z`);
  const dow = d.getUTCDay();
  let monday: string;
  if (dow === 0)      monday = addDays(today, 1);
  else if (dow === 6) monday = addDays(today, 2);
  else                monday = addDays(today, 1 - dow);
  return { from: monday, to: addDays(monday, 4) };
}

function isUsSymbol(s: string): boolean {
  if (!/^[A-Z]+$/.test(s) || s.length > 5) return false;
  if (s.length === 5 && (s.endsWith("F") || s.endsWith("Y") || s.endsWith("L"))) return false;
  return true;
}

// ── Parse earnings calendar ───────────────────────────────────────────────────

interface RawEarnings { symbol: string; date: string; time: string; epsEst: number | null; revEst: number | null; }

function parseCalendar(raw: unknown[]): RawEarnings[] {
  const seen = new Set<string>();
  const results: RawEarnings[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    const symbol = typeof r.symbol === "string" ? r.symbol.trim() : "";
    const date   = typeof r.date   === "string" ? r.date.trim()   : "";
    if (!symbol || !date || !isUsSymbol(symbol)) continue;
    if (seen.has(symbol + date)) continue;
    seen.add(symbol + date);
    const epsEst = typeof r.epsEstimated     === "number" ? r.epsEstimated     : null;
    const revEst = typeof r.revenueEstimated === "number" ? r.revenueEstimated : null;
    if (epsEst === null && revEst === null) continue;
    results.push({ symbol, date, time: typeof r.time === "string" ? r.time : "", epsEst, revEst });
  }
  return results;
}

// ── Fetch profile for a single symbol: returns name + sector ─────────────────
// stable/profile?symbol=X returns [{companyName, sector, ...}]

async function fetchProfile(symbol: string, key: string): Promise<{ name: string; sector: string }> {
  try {
    const res = await fetch(`${BASE}/profile?symbol=${symbol}&${key}`, PROFILE_CACHE);
    if (!res.ok) return { name: "", sector: "" };
    const data = await res.json();
    if (!Array.isArray(data) || !data[0]) return { name: "", sector: "" };
    const p = data[0] as Record<string, unknown>;
    return {
      name:   typeof p.companyName === "string" ? p.companyName.trim() : "",
      sector: typeof p.sector      === "string" ? p.sector.trim()      : "",
    };
  } catch {
    return { name: "", sector: "" };
  }
}

// ── GET ───────────────────────────────────────────────────────────────────────

export async function GET(): Promise<NextResponse<EarningsCalendarPayload>> {
  const ts = new Date().toISOString();
  if (!FMP_KEY) return NextResponse.json({ earnings: [], ts });

  const today = nyToday();
  const { from, to } = getWeekRange(today);
  const key = `apikey=${FMP_KEY}`;

  // Step 1: earnings calendar
  let raw: RawEarnings[] = [];
  try {
    const res = await fetch(
      `${BASE}/earnings-calendar?from=${from}&to=${to}&${key}`,
      CALENDAR_CACHE
    );
    if (res.ok) {
      const data = await res.json();
      raw = parseCalendar(Array.isArray(data) ? data : []);
    }
  } catch { /* graceful */ }

  // Step 2: enrich with name + sector via parallel profile calls.
  // All responses are cached for 24 hours — cold start is slow once, then instant.
  const profileResults = await Promise.allSettled(
    raw.map(e => fetchProfile(e.symbol, key))
  );

  // Step 3: assemble
  const earnings: EarningsEntry[] = raw.map((e, i) => {
    const p = profileResults[i].status === "fulfilled"
      ? profileResults[i].value
      : { name: "", sector: "" };
    return { symbol: e.symbol, name: p.name, sector: p.sector, date: e.date, time: e.time, epsEst: e.epsEst, revEst: e.revEst };
  });

  earnings.sort((a, b) => {
    const d = a.date.localeCompare(b.date);
    if (d !== 0) return d;
    const s = a.sector.localeCompare(b.sector);
    if (s !== 0) return s;
    return a.symbol.localeCompare(b.symbol);
  });

  return NextResponse.json({ earnings, ts });
}
