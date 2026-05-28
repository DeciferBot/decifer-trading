// EOD Market Summary — fetches 5 data sources, synthesizes with claude-sonnet-4-6.
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

// ── Data fetchers ─────────────────────────────────────────────────────────────

async function fetchTape(): Promise<EodTape> {
  const tape: EodTape = { spy: null, qqq: null, iwm: null, vix: null, tlt: null, gld: null };
  if (!FMP_KEY) return tape;

  const [etfRes, vixRes] = await Promise.allSettled([
    fetch(`${BASE}/batch-quote-short?symbols=SPY,QQQ,IWM,TLT,GLD&apikey=${FMP_KEY}`, {
      cache: "no-store",
    }),
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
      const raw: Array<{ symbol: string; name: string; changesPercentage: number | string }> =
        await gainersRes.value.json();
      const top = raw.slice(0, 5).map((r) => {
        const pct =
          typeof r.changesPercentage === "string"
            ? r.changesPercentage
            : `+${Number(r.changesPercentage).toFixed(1)}%`;
        return `$${r.symbol} (${r.name}) ${pct}`;
      });
      if (top.length) lines.push(`TOP GAINERS: ${top.join(", ")}`);
    } catch { /* graceful */ }
  }

  if (losersRes.status === "fulfilled" && losersRes.value.ok) {
    try {
      const raw: Array<{ symbol: string; name: string; changesPercentage: number | string }> =
        await losersRes.value.json();
      const top = raw.slice(0, 5).map((r) => {
        const pct =
          typeof r.changesPercentage === "string"
            ? r.changesPercentage
            : `${Number(r.changesPercentage).toFixed(1)}%`;
        return `$${r.symbol} (${r.name}) ${pct}`;
      });
      if (top.length) lines.push(`TOP LOSERS: ${top.join(", ")}`);
    } catch { /* graceful */ }
  }

  return lines.join("\n");
}

async function fetchAnalystMoves(today: string): Promise<string> {
  if (!FMP_KEY) return "";
  try {
    const res = await fetch(
      `${BASE}/upgrades-downgrades?limit=200&apikey=${FMP_KEY}`,
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

    const todayItems = raw.filter((r) => r.publishedDate?.startsWith(today));
    if (!todayItems.length) return "";

    const lines = todayItems.slice(0, 20).map((r) => {
      let line = `$${r.symbol}: ${r.gradingCompany} ${r.action}`;
      if (r.fromGrade && r.toGrade) line += ` (${r.fromGrade} → ${r.toGrade})`;
      if (r.newPriceTarget && r.previousPriceTarget) {
        line += ` — PT $${r.previousPriceTarget} → $${r.newPriceTarget}`;
      } else if (r.newPriceTarget) {
        line += ` — PT $${r.newPriceTarget}`;
      }
      return line;
    });

    return `ANALYST MOVES TODAY (${todayItems.length} total, showing top 20):\n${lines.join("\n")}`;
  } catch { return ""; }
}

async function fetchInsiderBuys(): Promise<string> {
  if (!FMP_KEY) return "";
  try {
    const res = await fetch(
      `${BASE}/insider-trading?limit=100&apikey=${FMP_KEY}`,
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

    const lines = buys.slice(0, 12).map((r) => {
      const qty = r.securitiesTransacted?.toLocaleString();
      const value =
        r.price && r.securitiesTransacted
          ? ` ~$${((r.price * r.securitiesTransacted) / 1_000_000).toFixed(2)}M`
          : ` ${qty} shares`;
      return `$${r.symbol}: ${r.reportingName} (${r.typeOfOwner}) bought${value} on ${r.filingDate?.slice(0, 10)}`;
    });

    return `INSIDER PURCHASES (last 48h):\n${lines.join("\n")}`;
  } catch { return ""; }
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
      if (minutesAgo > 720) continue; // last 12h only
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

  return `NOTABLE NEWS (last 12h, ${deduped.length} items):\n${lines.join("\n")}`;
}

// ── Claude synthesis ──────────────────────────────────────────────────────────

async function synthesize(
  tape: EodTape,
  contextBlocks: string[],
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

Today's data:

${dataBlock || "Limited data available — synthesize from the tape and any available context."}

Write a numbered market summary. Requirements:
- 10–15 numbered items, ordered from most to least market-moving
- Use $TICKER format for every company mentioned (e.g., $NVDA, $MU, $MSFT)
- Each item: 2–4 sentences. State what happened, quantify it, explain why it matters to a sophisticated investor
- Include analyst moves (name the firm and the exact PT change), insider purchases (role + dollar size), notable corporate deals, macro observations
- Only include facts from the data provided — never invent details
- End with a line break then "Watch Tomorrow:" followed by 3 forward-looking bullets derived strictly from today's stories
- IMPORTANT: Plain text only. No markdown headers, no bold (**), no italics, no --- dividers. Just numbered items.

Write the summary now.`;

  try {
    const anthropic = new Anthropic({ apiKey: ANTHROPIC_KEY });
    const msg = await anthropic.messages.create({
      model: MODEL,
      max_tokens: 2500,
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

  for (const rawLine of rawText.split("\n")) {
    // Strip markdown: bold markers, header hashes, horizontal rules
    const line = rawLine
      .replace(/^\*{1,2}/, "")   // leading **
      .replace(/\*{1,2}$/, "")   // trailing **
      .replace(/^#{1,3}\s*/, "")  // ## headers
      .replace(/\*\*/g, "")       // inline bold
      .trim();

    if (!line || line === "---") continue;

    const match = line.match(/^(\d{1,2})\.\s+(.+)/);
    if (match) {
      flush();
      currentNum = parseInt(match[1]);
      currentText = match[2];
    } else if (currentNum > 0 && !line.startsWith("Watch Tomorrow")) {
      currentText += " " + line;
    } else if (line.startsWith("Watch Tomorrow")) {
      flush();
      break;
    }
  }
  flush();

  return items;
}

// ── Main generator (exported for cron route) ──────────────────────────────────

export async function generateEodSummary(): Promise<EodSummaryPayload> {
  const today = nyToday();
  const marketDate = nyDateLabel();
  const generatedAt = new Date().toISOString();

  const [tape, movers, analystMoves, insiderBuys, topNews] = await Promise.all([
    fetchTape(),
    fetchMovers(),
    fetchAnalystMoves(today),
    fetchInsiderBuys(),
    fetchTopNews(),
  ]);

  const rawText = await synthesize(
    tape,
    [movers, analystMoves, insiderBuys, topNews],
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
