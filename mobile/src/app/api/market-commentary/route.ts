import { NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

// 10-minute Vercel cache — commentary refreshes often enough but LLM calls are expensive
export const revalidate = 600;

export interface MarketCommentaryPayload {
  summary: string;           // 3-4 sentence briefing: yesterday + cross-asset + overnight
  watch: WatchItem[];        // top events with plain-English commentary
  ts: string;
  source: "llm" | "fallback";
}

export interface WatchItem {
  event: string;             // plain label
  impact: string;            // "High" | "Medium"
  time: string;              // "8:30 AM ET" or ""
  est: string;               // "0.3%" or ""
  prev: string;              // "0.3%" or ""
  commentary: string;        // 2–3 sentences: what it is, what to watch, why it matters now
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, dec = 2): string {
  if (n == null) return "N/A";
  return `${n >= 0 ? "+" : ""}${n.toFixed(dec)}%`;
}

function fmtTime(t: string): string {
  if (!t || t === "All Day") return "";
  const [h, m] = t.split(":").map(Number);
  if (isNaN(h)) return t;
  const p = h >= 12 ? "PM" : "AM";
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${h12}:${String(m).padStart(2, "0")} ${p} ET`;
}

// ── GET ───────────────────────────────────────────────────────────────────────

export async function GET(): Promise<NextResponse<MarketCommentaryPayload>> {
  const ts = new Date().toISOString();
  const INTEL = process.env.INTELLIGENCE_API_URL ?? "https://intelligence.decifertrading.com";
  const FMP   = process.env.FMP_API_KEY;
  const BASE  = "https://financialmodelingprep.com/stable";

  // ── 1. Fetch all data in parallel ─────────────────────────────────────────
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());

  const [tapeRes, intelRes, econRes] = await Promise.allSettled([
    fetch(`${INTEL}/api/market-data/tape`,  { next: { revalidate: 300 } }),
    fetch(`${INTEL}/api/market-now`,        { next: { revalidate: 600 } }),
    FMP
      ? fetch(`${BASE}/economic-calendar?from=${today}&to=${today}&apikey=${FMP}`, { next: { revalidate: 600 } })
      : Promise.reject("no FMP key"),
  ]);

  // Parse tape — prefer intelligence server, fall back to FMP direct
  type Tape = Record<string, number | null>;
  let tape: Tape = {};
  if (tapeRes.status === "fulfilled" && tapeRes.value.ok) {
    try { tape = await tapeRes.value.json(); } catch { /* ignore */ }
  }
  // If intelligence tape is empty, fetch core symbols directly from FMP
  const tapeIsEmpty = Object.values(tape).every(v => v == null);
  if (tapeIsEmpty && FMP) {
    try {
      const fmpSymbols = "SPY,QQQ,DIA,IWM,TLT,GLD,USO,UUP";
      const fmpRes = await fetch(
        `${BASE}/batch-quote-short?symbols=${fmpSymbols}&apikey=${FMP}`,
        { next: { revalidate: 120 } }
      );
      if (fmpRes.ok) {
        const quotes: Array<{ symbol: string; price: number; change: number }> = await fmpRes.json();
        for (const q of quotes) {
          const prev = q.price - q.change;
          const pct = prev !== 0 ? parseFloat(((q.change / prev) * 100).toFixed(2)) : null;
          const key = ({ SPY: "spy_pct", QQQ: "qqq_pct", DIA: "dia_pct", IWM: "iwm_pct",
                         TLT: "tlt_pct", GLD: "gld_pct", USO: "uso_pct", UUP: "dxy_pct" } as Record<string, string>)[q.symbol];
          if (key) tape[key] = pct;
        }
      }
    } catch { /* ignore */ }
  }

  // Parse intelligence
  type Intel = Record<string, unknown>;
  let intel: Intel = {};
  if (intelRes.status === "fulfilled" && intelRes.value.ok) {
    try { intel = await intelRes.value.json(); } catch { /* ignore */ }
  }

  // Parse econ calendar
  type EconRaw = { event?: string; time?: string; impact?: string; actual?: number | null; estimate?: number | null; previous?: number | null; unit?: string | null };
  let econRaw: EconRaw[] = [];
  if (econRes.status === "fulfilled" && econRes.value.ok) {
    try {
      const arr = await econRes.value.json();
      econRaw = Array.isArray(arr) ? arr.filter((e: EconRaw) =>
        (e.impact === "High" || e.impact === "Medium") && e.actual == null
      ).slice(0, 6) : [];
    } catch { /* ignore */ }
  }

  // ── 2. Build context block for Claude ─────────────────────────────────────
  const isWeekend = [0, 6].includes(new Date().getDay());
  const nyHour = parseInt(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "numeric", hour12: false,
  }).format(new Date()));
  const sessionState = isWeekend ? "weekend"
    : nyHour >= 9 && nyHour < 16 ? "market open"
    : nyHour < 9 ? "pre-market"
    : "after-hours";

  const tapeBlock = [
    `S&P 500 (SPY): ${fmt(tape.spy_pct)}`,
    `Nasdaq (QQQ): ${fmt(tape.qqq_pct)}`,
    `Dow (DIA): ${fmt(tape.dia_pct ?? null)}`,
    `Small caps (IWM): ${fmt(tape.iwm_pct ?? null)}`,
    `Bonds (TLT): ${fmt(tape.tlt_pct)}`,
    `Gold (GLD): ${fmt(tape.gld_pct)}`,
    `Oil (USO): ${fmt(tape.uso_pct)}`,
    `Dollar (DXY/UUP): ${fmt(tape.dxy_pct ?? null)}`,
    `VIX: ${tape.vix != null ? tape.vix.toFixed(1) : "N/A"}`,
    ...(tape.es_pct != null ? [`S&P Futures (ES): ${fmt(tape.es_pct)}`] : []),
    ...(tape.nq_pct != null ? [`Nasdaq Futures (NQ): ${fmt(tape.nq_pct)}`] : []),
  ].join("\n");

  const drivers: string[] = Array.isArray(intel.key_drivers) ? intel.key_drivers as string[] : [];
  const mood: string = typeof intel.market_mood === "string" ? intel.market_mood : "";
  const whatChanged: string[] = Array.isArray(intel.what_changed) ? intel.what_changed as string[] : [];

  const intelBlock = [
    `Market mood: ${mood || "Unknown"}`,
    `Active drivers: ${drivers.slice(0, 5).join("; ") || "None"}`,
    ...(whatChanged.length ? [`Recent context: ${whatChanged[0]}`] : []),
  ].join("\n");

  const econBlock = econRaw.length === 0 ? "No significant economic releases scheduled today."
    : econRaw.map(e => {
        const parts = [`Event: ${e.event ?? "Unknown"} (${e.impact ?? "?"} impact)`];
        if (e.time) parts.push(`Time: ${fmtTime(e.time)}`);
        if (e.estimate != null) parts.push(`Forecast: ${e.estimate}${e.unit ?? ""}`);
        if (e.previous != null) parts.push(`Previous: ${e.previous}${e.unit ?? ""}`);
        return parts.join(" | ");
      }).join("\n");

  // ── 3. Call Claude ────────────────────────────────────────────────────────
  const SYSTEM = `You are Decifer's market intelligence engine writing for a mobile app.
Your audience is an intelligent non-professional investor who wants to understand what markets are doing and why — in plain English, no jargon.

Rules:
- Never use words like: trade, buy, sell, long, short, position, portfolio, alpha, thesis, execute, broker, order
- Write like a sharp financial journalist, not a chatbot
- Be specific about numbers but explain what they mean
- If equities AND bonds AND gold all moved the same direction, call that out — it's unusual
- Do not pad or repeat yourself
- Output must be valid JSON with exactly two keys: "summary" and "watch"`;

  const USER = `Session state: ${sessionState}
Time (New York): ${new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: true }).format(new Date())}

MARKET DATA (daily % change unless noted):
${tapeBlock}

INTELLIGENCE CONTEXT:
${intelBlock}

TODAY'S ECONOMIC CALENDAR (upcoming, not yet released):
${econBlock}

Write a JSON response with exactly this shape:
{
  "summary": "<3-5 sentences. For pre-market/after-hours: what yesterday's session did and why it matters, what's happening overnight, what the cross-asset picture is telling us. For live session: what's moving right now and why. Be specific with numbers. Explain the 'so what' for each data point.>",
  "watch": [
    {
      "event": "<plain English event name, no jargon>",
      "commentary": "<2-3 sentences: what this release measures in plain English, what the forecast implies vs last time, and specifically why this number matters *today* given the current market backdrop. Name the stakes: what does a beat or a miss mean for rates, stocks, or the themes currently driving markets.>"
    }
  ]
}

Include up to 3 items in "watch" — only events from today's calendar. If no events, use an empty array.
Return only the JSON object. No markdown, no explanation outside the JSON.`;

  try {
    const msg = await client.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 1024,
      system: SYSTEM,
      messages: [{ role: "user", content: USER }],
    });

    const raw = msg.content[0].type === "text" ? msg.content[0].text.trim() : "";
    // Strip any accidental markdown fences
    const json = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
    const parsed = JSON.parse(json) as { summary: string; watch: Array<{ event: string; commentary: string }> };

    // Enrich watch items with time/impact/est/prev from raw econ data
    const enriched: WatchItem[] = (parsed.watch ?? []).map((w, i) => {
      const src = econRaw[i];
      return {
        event: w.event,
        impact: src?.impact ?? "Medium",
        time: src?.time ? fmtTime(src.time) : "",
        est: src?.estimate != null ? `${src.estimate}${src.unit ?? ""}` : "",
        prev: src?.previous != null ? `${src.previous}${src.unit ?? ""}` : "",
        commentary: w.commentary,
      };
    });

    return NextResponse.json({ summary: parsed.summary, watch: enriched, ts, source: "llm" });

  } catch (err) {
    // Graceful fallback — return a minimal useful response
    console.error("market-commentary LLM call failed:", err);
    const fallbackWatch: WatchItem[] = econRaw.slice(0, 3).map(e => ({
      event: e.event ?? "Economic Release",
      impact: e.impact ?? "Medium",
      time: e.time ? fmtTime(e.time) : "",
      est: e.estimate != null ? `${e.estimate}${e.unit ?? ""}` : "",
      prev: e.previous != null ? `${e.previous}${e.unit ?? ""}` : "",
      commentary: `${e.impact === "High" ? "High-impact release" : "Medium-impact release"}.${e.estimate != null ? ` Forecast: ${e.estimate}${e.unit ?? ""}.` : ""}${e.previous != null ? ` Previous: ${e.previous}${e.unit ?? ""}.` : ""}`,
    }));
    // Build a real fallback from tape data rather than showing a loading message
    let fallbackSummary: string;
    if (mood) {
      fallbackSummary = `${mood}. Active drivers: ${drivers.slice(0, 2).join(", ") || "none"}.`;
    } else {
      const tapeParts: string[] = [];
      if (tape.spy_pct != null) tapeParts.push(`S&P 500 ${fmt(tape.spy_pct)}`);
      if (tape.qqq_pct != null) tapeParts.push(`Nasdaq ${fmt(tape.qqq_pct)}`);
      if (tape.tlt_pct != null) tapeParts.push(`bonds ${fmt(tape.tlt_pct)}`);
      if (tape.gld_pct != null) tapeParts.push(`gold ${fmt(tape.gld_pct)}`);
      if (tape.uso_pct != null) tapeParts.push(`oil ${fmt(tape.uso_pct)}`);
      fallbackSummary = tapeParts.length > 0
        ? `Markets today: ${tapeParts.join(", ")}.${econRaw.length > 0 ? ` ${econRaw.length} economic release${econRaw.length > 1 ? "s" : ""} scheduled.` : ""}`
        : "Market data is currently unavailable. Check back shortly.";
    }
    return NextResponse.json({ summary: fallbackSummary, watch: fallbackWatch, ts, source: "fallback" });
  }
}
