// EOD Market Summary — fetches 7 data sources, synthesizes with claude-sonnet-4-6.
// Exported generateEodSummary() is also called by the cron email route.

import { NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";

export const maxDuration = 60;

const FMP_KEY = process.env.FMP_API_KEY;
const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY;
const BASE = "https://financialmodelingprep.com/stable";
const MODEL = "claude-sonnet-4-6";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface EodItem {
  number: number;
  text: string;
  symbols: string[];
}

export interface EodTape {
  spy: number | null;
  qqq: number | null;
  iwm: number | null;
  vix: number | null;
  tlt: number | null;
  gld: number | null;
}

export interface EodSummaryPayload {
  marketDate: string;
  tape: EodTape;
  rawText: string;
  items: EodItem[];
  generatedAt: string;
  error?: string;
}

// ── Date helpers ──────────────────────────────────────────────────────────────

function nyToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export function nyDateLabel(): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date());
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

// ── Data fetchers ─────────────────────────────────────────────────────────────

async function fetchTape(): Promise<EodTape> {
  const tape: EodTape = { spy: null, qqq: null, iwm: null, vix: null, tlt: null, gld: null };
  if (!FMP_KEY) return tape;

  const [etfRes, vixRes] = await Promise.allSettled([
    fetch(`${BASE}/batch-quote-short?symbols=SPY,QQQ,IWM,TLT,GLD&apikey=${FMP_KEY}`, {
      cache: "no-store",
    }),
    // Try index symbol first, fall back to ETF proxy VIXY
    fetch(`${BASE}/quote/%5EVIX?apikey=${FMP_KEY}`, { cache: "no-store" }),
  ]);

  if (etfRes.status === "fulfilled" && etfRes.value.ok) {
    try {
      const raw: Array<{ symbol: string; price: number; change: number }> =
        await etfRes.value.json();
      for (const r of raw) {
        const prevClose = r.price - r.change;
        const pct =
          prevClose !== 0
            ? parseFloat(((r.change / prevClose) * 100).toFixed(2))
            : null;
        if (r.symbol === "SPY") tape.spy = pct;
        else if (r.symbol === "QQQ") tape.qqq = pct;
        else if (r.symbol === "IWM") tape.iwm = pct;
        else if (r.symbol === "TLT") tape.tlt = pct;
        else if (r.symbol === "GLD") tape.gld = pct;
      }
    } catch { /* graceful */ }
  }

  if (vixRes.status === "fulfilled" && vixRes.value.ok) {
    try {
      const raw = await vixRes.value.json();
      const q = Array.isArray(raw) ? raw[0] : raw;
      if (q && typeof q.price === "number") tape.vix = parseFloat(q.price.toFixed(2));
    } catch { /* graceful */ }
  }

  // Fallback: derive VIX level from UVXY if index call failed
  if (tape.vix === null && FMP_KEY) {
    try {
      const res = await fetch(`${BASE}/batch-quote-short?symbols=VIXY&apikey=${FMP_KEY}`, {
        cache: "no-store",
      });
      if (res.ok) {
        const raw: Array<{ symbol: string; price: number }> = await res.json();
        const v = raw.find((r) => r.symbol === "VIXY");
        // VIXY ≈ VIX/4 rough proxy — flag as approximate
        if (v) tape.vix = parseFloat((v.price * 4).toFixed(1));
      }
    } catch { /* graceful */ }
  }

  return tape;
}

async function fetchMovers(): Promise<string> {
  if (!FMP_KEY) return "";
  const [gainersRes, losersRes] = await Promise.allSettled([
    fetch(`${BASE}/biggest-gainers?apikey=${FMP_KEY}`, { cache: "no-store" }),
    fetch(`${BASE}/biggest-losers?apikey=${FMP_KEY}`, { cache: "no-store" }),
  ]);

  const lines: string[] = [];

  if (gainersRes.status === "fulfilled" && gainersRes.value.ok) {
    try {
      const raw: Array<{ symbol: string; name: string; changesPercentage: number | string; price?: number }> =
        await gainersRes.value.json();
      const top = raw.slice(0, 5).map((r) => {
        const pct =
          typeof r.changesPercentage === "string"
            ? r.changesPercentage
            : `+${Number(r.changesPercentage).toFixed(1)}%`;
        const price = r.price ? ` at $${Number(r.price).toFixed(2)}` : "";
        return `$${r.symbol} (${r.name}) ${pct}${price}`;
      });
      if (top.length) lines.push(`TOP GAINERS: ${top.join(", ")}`);
    } catch { /* graceful */ }
  }

  if (losersRes.status === "fulfilled" && losersRes.value.ok) {
    try {
      const raw: Array<{ symbol: string; name: string; changesPercentage: number | string; price?: number }> =
        await losersRes.value.json();
      const top = raw.slice(0, 5).map((r) => {
        const pct =
          typeof r.changesPercentage === "string"
            ? r.changesPercentage
            : `${Number(r.changesPercentage).toFixed(1)}%`;
        const price = r.price ? ` at $${Number(r.price).toFixed(2)}` : "";
        return `$${r.symbol} (${r.name}) ${pct}${price}`;
      });
      if (top.length) lines.push(`TOP LOSERS: ${top.join(", ")}`);
    } catch { /* graceful */ }
  }

  return lines.join("\n");
}

async function fetchMostActive(): Promise<string> {
  if (!FMP_KEY) return "";
  try {
    const res = await fetch(`${BASE}/actives?apikey=${FMP_KEY}`, { cache: "no-store" });
    if (!res.ok) return "";
    const raw: Array<{
      symbol: string;
      name: string;
      price?: number;
      changesPercentage?: number | string;
      volume?: number;
    }> = await res.json();
    if (!Array.isArray(raw) || !raw.length) return "";

    const lines = raw.slice(0, 10).map((r, i) => {
      const pct =
        r.changesPercentage !== undefined
          ? typeof r.changesPercentage === "number"
            ? ` ${r.changesPercentage >= 0 ? "+" : ""}${r.changesPercentage.toFixed(1)}%`
            : ` ${r.changesPercentage}`
          : "";
      const vol = r.volume
        ? r.volume >= 1_000_000
          ? ` vol ${(r.volume / 1_000_000).toFixed(1)}M shares`
          : ` vol ${(r.volume / 1_000).toFixed(0)}K shares`
        : "";
      return `${i + 1}. $${r.symbol} (${r.name})${pct}${vol}`;
    });

    return `MOST ACTIVE STOCKS TODAY (by share volume — these names typically dominate options flow too):\n${lines.join("\n")}`;
  } catch { return ""; }
}

async function fetchAnalystMoves(today: string): Promise<string> {
  if (!FMP_KEY) return "";
  try {
    // Fetch 500 records — analyst moves can arrive pre-market or late previous day
    const res = await fetch(
      `${BASE}/upgrades-downgrades?limit=500&apikey=${FMP_KEY}`,
      { cache: "no-store" }
    );
    if (!res.ok) return "";

    const raw: Array<{
      symbol: string;
      publishedDate: string;
      gradingCompany: string;
      action: string;
      fromGrade: string;
      toGrade: string;
      newPriceTarget?: number | null;
      previousPriceTarget?: number | null;
    }> = await res.json();

    const yesterday = addDays(today, -1);
    // Include today and yesterday (pre-market calls filed overnight)
    const relevant = raw.filter(
      (r) =>
        r.publishedDate?.startsWith(today) ||
        r.publishedDate?.startsWith(yesterday)
    );

    if (!relevant.length) return "";

    const lines = relevant.slice(0, 30).map((r) => {
      const dateLabel = r.publishedDate?.startsWith(today) ? "today" : "yesterday";
      let line = `$${r.symbol} [${dateLabel}]: ${r.gradingCompany} — ${r.action}`;
      if (r.fromGrade && r.toGrade && r.fromGrade !== r.toGrade) {
        line += ` (${r.fromGrade} → ${r.toGrade})`;
      } else if (r.toGrade) {
        line += ` (${r.toGrade})`;
      }
      if (r.previousPriceTarget && r.newPriceTarget) {
        line += ` | PT: $${r.previousPriceTarget} → $${r.newPriceTarget}`;
      } else if (r.newPriceTarget) {
        line += ` | PT: $${r.newPriceTarget}`;
      }
      return line;
    });

    return `ANALYST MOVES (today + yesterday, ${relevant.length} total):\n${lines.join("\n")}`;
  } catch { return ""; }
}

async function fetchInsiderBuys(): Promise<string> {
  if (!FMP_KEY) return "";
  try {
    const res = await fetch(
      `${BASE}/insider-trading?limit=200&apikey=${FMP_KEY}`,
      { cache: "no-store" }
    );
    if (!res.ok) return "";

    const raw: Array<{
      symbol: string;
      filingDate: string;
      reportingName: string;
      typeOfOwner: string;
      transactionType: string;
      securitiesTransacted: number;
      price: number | null;
    }> = await res.json();

    const cutoff = Date.now() - 48 * 60 * 60 * 1000;
    const buys = raw.filter((r) => {
      const isPurchase =
        r.transactionType === "P-Purchase" ||
        r.transactionType?.toLowerCase().includes("purchase");
      const isRecent = r.filingDate
        ? new Date(r.filingDate).getTime() > cutoff
        : false;
      return isPurchase && isRecent;
    });

    if (!buys.length) return "";

    const lines = buys.slice(0, 15).map((r) => {
      const shares = r.securitiesTransacted?.toLocaleString();
      const priceStr = r.price ? ` at $${r.price.toFixed(2)}/share` : "";
      const totalVal =
        r.price && r.securitiesTransacted
          ? ` = ~$${((r.price * r.securitiesTransacted) / 1_000_000).toFixed(2)}M`
          : "";
      return `$${r.symbol}: ${r.reportingName} (${r.typeOfOwner}) — ${shares} shares${priceStr}${totalVal} — filed ${r.filingDate?.slice(0, 10)}`;
    });

    return `INSIDER PURCHASES (last 48h — ${buys.length} buys):\n${lines.join("\n")}`;
  } catch { return ""; }
}

async function fetchTomorrowCalendar(today: string): Promise<string> {
  if (!FMP_KEY) return "";

  const tomorrow = addDays(today, 1);
  // Skip weekends
  const tomorrowDay = new Date(`${tomorrow}T12:00:00`).getDay();
  if (tomorrowDay === 0 || tomorrowDay === 6) return "";

  const [earningsRes, econRes] = await Promise.allSettled([
    fetch(
      `${BASE}/earnings-calendar?from=${tomorrow}&to=${tomorrow}&apikey=${FMP_KEY}`,
      { cache: "no-store" }
    ),
    fetch(
      `${BASE}/economic-calendar?from=${tomorrow}&to=${tomorrow}&apikey=${FMP_KEY}`,
      { cache: "no-store" }
    ),
  ]);

  const blocks: string[] = [];

  if (earningsRes.status === "fulfilled" && earningsRes.value.ok) {
    try {
      const raw: Array<{
        symbol: string;
        name: string;
        time: string;
        epsEstimated: number | null;
        revenueEstimated: number | null;
      }> = await earningsRes.value.json();

      if (Array.isArray(raw) && raw.length) {
        const items = raw.slice(0, 20).map((r) => {
          const timing =
            r.time === "bmo"
              ? "pre-market"
              : r.time === "amc"
              ? "after-close"
              : r.time === "dmh"
              ? "during market hours"
              : "";
          const eps =
            r.epsEstimated !== null
              ? `EPS est $${r.epsEstimated.toFixed(2)}`
              : "";
          const rev =
            r.revenueEstimated !== null
              ? `Rev est $${(r.revenueEstimated / 1e9).toFixed(2)}B`
              : "";
          const estimates = [eps, rev].filter(Boolean).join(", ");
          return `$${r.symbol} (${r.name || r.symbol})${timing ? " — " + timing : ""}${estimates ? " | " + estimates : ""}`;
        });
        blocks.push(`TOMORROW'S EARNINGS (${raw.length} companies reporting):\n${items.join("\n")}`);
      }
    } catch { /* graceful */ }
  }

  if (econRes.status === "fulfilled" && econRes.value.ok) {
    try {
      const raw: Array<{
        event: string;
        time: string;
        actual: number | null;
        previous: number | null;
        estimate: number | null;
        impact: string;
        country: string;
        unit?: string;
      }> = await econRes.value.json();

      if (Array.isArray(raw)) {
        const usEvents = raw
          .filter(
            (r) =>
              (r.country === "US" || !r.country) &&
              (r.impact === "High" || r.impact === "Medium")
          )
          .slice(0, 12);

        if (usEvents.length) {
          const items = usEvents.map((r) => {
            const prev = r.previous !== null ? `prev ${r.previous}${r.unit || ""}` : "";
            const est = r.estimate !== null ? `est ${r.estimate}${r.unit || ""}` : "";
            const context = [prev, est].filter(Boolean).join(", ");
            const time = r.time ? `${r.time} ET — ` : "";
            return `[${r.impact}] ${time}${r.event}${context ? ` (${context})` : ""}`;
          });
          blocks.push(`TOMORROW'S MACRO RELEASES (US, High/Medium impact):\n${items.join("\n")}`);
        }
      }
    } catch { /* graceful */ }
  }

  return blocks.join("\n\n");
}

async function fetchTopNews(): Promise<string> {
  if (!FMP_KEY) return "";
  const [stockRes, generalRes] = await Promise.allSettled([
    fetch(`${BASE}/news/stock-latest?limit=40&apikey=${FMP_KEY}`, { cache: "no-store" }),
    fetch(`${BASE}/news/general-latest?limit=20&apikey=${FMP_KEY}`, { cache: "no-store" }),
  ]);

  const now = Date.now();
  const items: Array<{ title: string; symbol: string | null; minutesAgo: number }> = [];

  const ingest = (raw: Array<{ title?: string; publishedDate?: string; symbol?: string }>) => {
    for (const n of raw) {
      if (!n.title) continue;
      const pub = n.publishedDate
        ? new Date(n.publishedDate.replace(" ", "T") + "Z").getTime()
        : 0;
      const minutesAgo = pub ? Math.max(0, Math.round((now - pub) / 60_000)) : 9999;
      if (minutesAgo > 720) continue;
      items.push({
        title: n.title.trim(),
        symbol: n.symbol?.toUpperCase() ?? null,
        minutesAgo,
      });
    }
  };

  try {
    if (stockRes.status === "fulfilled" && stockRes.value.ok)
      ingest(await stockRes.value.json());
    if (generalRes.status === "fulfilled" && generalRes.value.ok)
      ingest(await generalRes.value.json());
  } catch { /* graceful */ }

  const seen = new Set<string>();
  const deduped = items
    .sort((a, b) => a.minutesAgo - b.minutesAgo)
    .filter((n) => {
      const key = n.title.slice(0, 60).toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 25);

  if (!deduped.length) return "";

  const lines = deduped.map((n) => {
    const sym = n.symbol ? ` [$${n.symbol}]` : "";
    const age =
      n.minutesAgo < 60
        ? `${n.minutesAgo}m ago`
        : `${Math.round(n.minutesAgo / 60)}h ago`;
    return `${age}${sym} — ${n.title}`;
  });

  return `NOTABLE NEWS (last 12h):\n${lines.join("\n")}`;
}

// ── Claude synthesis ──────────────────────────────────────────────────────────

async function synthesize(
  tape: EodTape,
  contextBlocks: string[],
  tomorrowBlock: string,
  marketDate: string
): Promise<string> {
  if (!ANTHROPIC_KEY) return "";

  const fmtPct = (v: number | null) =>
    v === null ? "N/A" : `${v >= 0 ? "+" : ""}${v}%`;

  const tapeStr = [
    `SPY ${fmtPct(tape.spy)}`,
    `QQQ ${fmtPct(tape.qqq)}`,
    `IWM ${fmtPct(tape.iwm)}`,
    tape.vix !== null ? `VIX ${tape.vix}` : "VIX N/A",
    `TLT ${fmtPct(tape.tlt)}`,
    `GLD ${fmtPct(tape.gld)}`,
  ].join(" | ");

  const dataBlock = contextBlocks.filter(Boolean).join("\n\n");

  const userContent = `You are a senior market analyst writing the end-of-day market summary for ${marketDate}.

Market close tape: ${tapeStr}

===TODAY'S DATA===

${dataBlock || "Limited data available — synthesize from the tape and any available context."}

===TOMORROW'S SCHEDULE===

${tomorrowBlock || "No scheduled events data available."}

===INSTRUCTIONS===

Write a numbered market summary with two sections:

SECTION 1 — TODAY (numbered items 1 through 12-15):
- Order from most to least market-moving
- Use $TICKER format for every company (e.g., $NVDA, $MU, $MSFT)
- Each item: 2-4 sentences. State what happened, quantify it with exact numbers, explain market significance
- For analyst moves: always name the firm, old PT, new PT, and the thesis behind the call
- For insider purchases: GROUP ALL insider buys into ONE single item. List each purchase on its own line with: $TICKER — Name (Title) bought X,XXX shares at $XX.XX = ~$X.XM on [date]
- For most active stocks: note the top 3-5 by volume in one item as a proxy for where options activity concentrated
- Only include facts from the data — never invent details

SECTION 2 — WATCH TOMORROW:
Write this as a structured forward-looking section. Use the tomorrow's schedule data to produce:

Earnings: For each company reporting tomorrow, one sentence: who reports, when (pre-market/after-close), consensus EPS and revenue estimates, and what a beat or miss would mean for the sector.

Macro Data: For each High/Medium impact economic release, one sentence: what the release is, prior reading, consensus estimate, and what a surprise in either direction would mean for rates or equities.

Fed/Other: If any Fed speakers or FOMC events are in the data, note them and the key question markets are watching. If none, write "No Fed events scheduled."

Plain text only. No markdown headers, bold, italics, or --- dividers. Numbered items only for Section 1. Section 2 uses the Earnings/Macro Data/Fed labels as plain text headers.

Write the summary now.`;

  try {
    const anthropic = new Anthropic({ apiKey: ANTHROPIC_KEY });
    const msg = await anthropic.messages.create({
      model: MODEL,
      max_tokens: 3000,
      messages: [{ role: "user", content: userContent }],
    });
    const block = msg.content[0];
    return block.type === "text" ? block.text : "";
  } catch { return ""; }
}

// ── Parse numbered items from raw text ───────────────────────────────────────

export function parseEodItems(rawText: string): EodItem[] {
  const items: EodItem[] = [];
  let currentNum = 0;
  let currentText = "";

  const flush = () => {
    if (currentNum > 0 && currentText.trim()) {
      const symbols = [
        ...new Set(
          Array.from(currentText.matchAll(/\$([A-Z]{1,5})/g)).map((m) => m[1])
        ),
      ];
      items.push({ number: currentNum, text: currentText.trim(), symbols });
      currentNum = 0;
      currentText = "";
    }
  };

  const SECTION2_MARKERS = ["Watch Tomorrow", "Earnings:", "Macro Data:", "Fed/Other:"];

  for (const rawLine of rawText.split("\n")) {
    const line = rawLine
      .replace(/^\*{1,2}/, "")
      .replace(/\*{1,2}$/, "")
      .replace(/^#{1,3}\s*/, "")
      .replace(/\*\*/g, "")
      .trim();

    if (!line || line === "---") continue;

    // Stop parsing numbered items when Section 2 starts
    if (SECTION2_MARKERS.some((m) => line.startsWith(m))) {
      flush();
      break;
    }

    const match = line.match(/^(\d{1,2})\.\s+(.+)/);
    if (match) {
      flush();
      currentNum = parseInt(match[1]);
      currentText = match[2];
    } else if (currentNum > 0) {
      currentText += " " + line;
    }
  }
  flush();

  return items;
}

// ── Extract Watch Tomorrow section ────────────────────────────────────────────

export function extractWatchTomorrow(rawText: string): string {
  const markers = ["Watch Tomorrow", "WATCH TOMORROW", "Earnings:", "Macro Data:"];
  for (const marker of markers) {
    const idx = rawText.indexOf(marker);
    if (idx >= 0) return rawText.slice(idx).trim();
  }
  return "";
}

// ── Main generator (exported for cron route) ──────────────────────────────────

export async function generateEodSummary(): Promise<EodSummaryPayload> {
  const today = nyToday();
  const marketDate = nyDateLabel();
  const generatedAt = new Date().toISOString();

  const [tape, movers, mostActive, analystMoves, insiderBuys, topNews, tomorrowCalendar] =
    await Promise.all([
      fetchTape(),
      fetchMovers(),
      fetchMostActive(),
      fetchAnalystMoves(today),
      fetchInsiderBuys(),
      fetchTopNews(),
      fetchTomorrowCalendar(today),
    ]);

  const rawText = await synthesize(
    tape,
    [movers, mostActive, analystMoves, insiderBuys, topNews],
    tomorrowCalendar,
    marketDate
  );
  const items = parseEodItems(rawText);

  return { marketDate, tape, rawText, items, generatedAt };
}

// ── GET handler ───────────────────────────────────────────────────────────────

export async function GET(): Promise<NextResponse<EodSummaryPayload>> {
  const payload = await generateEodSummary();
  return NextResponse.json(payload);
}
