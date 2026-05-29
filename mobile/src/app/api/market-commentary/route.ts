import { NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

// Human-readable labels for raw driver IDs (mirrors FORCE_LABELS in customerBriefingModel.ts)
const DRIVER_LABELS: Record<string, string> = {
  ai_capex_growth:          "AI infrastructure spending",
  ai_compute_demand:        "AI compute demand",
  yields_rising:            "rising bond yields",
  yields_falling:           "falling bond yields",
  oil_supply_shock:         "oil supply pressure",
  geopolitical_risk_rising: "geopolitical risk",
  risk_on_rotation:         "risk-on rotation",
  risk_off:                 "risk-off sentiment",
  gold_safe_haven_bid:      "gold safe-haven demand",
  credit_stress_easing:     "easing credit stress",
  small_cap_risk_on:        "small-cap risk-on",
  defence_rearmament:       "defence rearmament spending",
  futures_risk_on:          "positive futures positioning",
  futures_risk_off:         "negative futures positioning",
};

function driverLabel(raw: string): string {
  return DRIVER_LABELS[raw] ?? raw.toLowerCase().replace(/_/g, " ");
}

// 10-minute Vercel cache — commentary refreshes often enough but LLM calls are expensive
export const revalidate = 600;
export const maxDuration = 30; // LLM call needs >10s default

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

  const [tapeRes, intelRes, econRes, newsRes] = await Promise.allSettled([
    fetch(`${INTEL}/api/market-data/tape`,  { next: { revalidate: 300 } }),
    fetch(`${INTEL}/api/market-now`,        { next: { revalidate: 600 } }),
    FMP
      ? fetch(`${BASE}/economic-calendar?from=${today}&to=${today}&apikey=${FMP}`, { next: { revalidate: 600 } })
      : Promise.reject("no FMP key"),
    FMP
      ? fetch(`${BASE}/news/general-latest?limit=20&apikey=${FMP}`, { next: { revalidate: 300 } })
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

  // Parse recent news headlines (last 6 hours)
  type NewsRaw = { title?: string; text?: string; publishedDate?: string; site?: string };
  let recentNews: string[] = [];
  if (newsRes.status === "fulfilled" && newsRes.value.ok) {
    try {
      const arr: NewsRaw[] = await newsRes.value.json();
      const cutoff = Date.now() - 6 * 60 * 60 * 1000;
      const SKIP = new Set(["youtube.com", "youtu.be", "rumble.com", "tiktok.com", "vimeo.com"]);
      recentNews = (Array.isArray(arr) ? arr : [])
        .filter(n => {
          if (!n.title || SKIP.has(n.site ?? "")) return false;
          const pub = new Date((n.publishedDate ?? "").replace(" ", "T") + "Z").getTime();
          return pub >= cutoff;
        })
        .slice(0, 8)
        .map(n => `- ${n.title?.trim() ?? ""}${n.text ? ` — ${n.text.trim().slice(0, 120)}` : ""}`);
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
  const knownConflicts: string[] = Array.isArray(intel.known_conflicts) ? intel.known_conflicts as string[] : [];
  const keyEvents: Array<{ headline?: string; summary?: string }> = Array.isArray(intel.key_events)
    ? intel.key_events as Array<{ headline?: string; summary?: string }>
    : [];

  const intelBlock = [
    `Market mood: ${mood || "Unknown"}`,
    `Active drivers: ${drivers.slice(0, 5).join("; ") || "None"}`,
    ...(whatChanged.length ? whatChanged.slice(0, 3).map(w => `What changed: ${w}`) : []),
    ...(knownConflicts.length ? knownConflicts.slice(0, 3).map(c => `Conflict/tension: ${c}`) : []),
    ...(keyEvents.length ? keyEvents.slice(0, 3).map(e => `Key event: ${e.headline ?? ""}${e.summary ? ` — ${e.summary}` : ""}`) : []),
  ].join("\n");

  const newsBlock = recentNews.length > 0
    ? recentNews.join("\n")
    : "No recent headlines available.";

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
- Ground the summary in actual news events from the headlines — cite specific events (e.g. "Trump's call with Iranian officials", "Fed's Waller signalled caution on cuts") not vague generalities
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

RECENT NEWS HEADLINES (last 6 hours — use these to add specific, real context):
${newsBlock}

TODAY'S ECONOMIC CALENDAR (upcoming, not yet released):
${econBlock}

Write a JSON response with exactly this shape:
{
  "summary": "<3-5 sentences. For pre-market/after-hours: what yesterday's session did and why it matters, what's happening overnight, what the cross-asset picture is telling us. For live session: what's moving right now and why. Be specific with numbers. Explain the 'so what' for each data point.>",
  "watch": [
    {
      "event": "<plain English event name, no jargon>",
      "commentary": "<2-3 sentences: what this release measures in plain English, what the forecast implies vs last time, and specifically why this number matters *today* given the current market backdrop and recent news. Name the stakes: what does a beat or a miss mean for rates, stocks, or the themes currently in the headlines.>"
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

    // Enrich watch items with time/impact/est/prev from raw econ data.
    // Match by event name similarity (not array index) — LLM may reorder or rename events.
    const enriched: WatchItem[] = (parsed.watch ?? []).map(w => {
      const wLower = w.event.toLowerCase();
      const src = econRaw.find(e =>
        e.event && (
          e.event.toLowerCase().includes(wLower.slice(0, 8)) ||
          wLower.includes((e.event ?? "").toLowerCase().slice(0, 8))
        )
      ) ?? econRaw.find((_, i) => i === (parsed.watch ?? []).indexOf(w));
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
    // Build a real fallback from tape + intel data rather than a terse 1-liner
    const fallbackSentences: string[] = [];

    // 1. Equity lead
    if (tape.spy_pct != null) {
      const spyDir = tape.spy_pct >= 0.5 ? "rising" : tape.spy_pct <= -0.5 ? "falling" : "holding steady";
      const techNote = tape.qqq_pct != null && Math.abs(tape.qqq_pct - tape.spy_pct) > 0.4
        ? tape.qqq_pct > tape.spy_pct
          ? ` Tech is leading — Nasdaq at ${fmt(tape.qqq_pct, 1)}.`
          : ` Tech is lagging — Nasdaq at ${fmt(tape.qqq_pct, 1)}.`
        : "";
      fallbackSentences.push(`The S&P 500 is ${spyDir} at ${fmt(tape.spy_pct)}.${techNote}`);
    } else if (tape.es_pct != null) {
      const futDir = tape.es_pct >= 0.15 ? "pointing to a higher open" : tape.es_pct <= -0.15 ? "pointing lower" : "flat overnight";
      fallbackSentences.push(`S&P futures are ${fmt(tape.es_pct, 2)} — ${futDir}.`);
    }

    // 2. Cross-asset colour
    const crossAsset: string[] = [];
    if (tape.tlt_pct != null && Math.abs(tape.tlt_pct) > 0.3) {
      crossAsset.push(tape.tlt_pct > 0
        ? `bonds are rallying ${fmt(tape.tlt_pct, 1)}, reflecting rate-cut expectations`
        : `bonds are selling off ${fmt(Math.abs(tape.tlt_pct), 1)}, with yields pushing higher`);
    }
    if (tape.gld_pct != null && Math.abs(tape.gld_pct) > 0.5) {
      crossAsset.push(tape.gld_pct > 0
        ? `gold is up ${fmt(tape.gld_pct, 1)} on safe-haven demand`
        : `gold is down ${fmt(Math.abs(tape.gld_pct), 1)}, a sign of improving risk appetite`);
    }
    if (tape.uso_pct != null && Math.abs(tape.uso_pct) > 0.6) {
      crossAsset.push(tape.uso_pct < 0
        ? `oil is off ${fmt(Math.abs(tape.uso_pct), 1)}, easing energy-driven inflation pressure`
        : `oil is up ${fmt(tape.uso_pct, 1)}, keeping supply-side cost pressure alive`);
    }
    if (crossAsset.length > 0) {
      fallbackSentences.push(`Across asset classes, ${crossAsset.join(" and ")}.`);
    }

    // 3. Driver context
    if (drivers.length > 0) {
      const driverStr = drivers.slice(0, 2).map(driverLabel).join(" and ");
      fallbackSentences.push(`The primary forces in the market right now are ${driverStr}.`);
    } else if (mood) {
      fallbackSentences.push(`The overall tone is ${mood.toLowerCase()}.`);
    }

    // 4. Econ calendar note (conflict/tension is surfaced by the caution box in the UI, not here)
    if (econRaw.length > 0) {
      const highImpact = econRaw.filter(e => e.impact === "High");
      if (highImpact.length > 0) {
        fallbackSentences.push(`${highImpact.length} high-impact economic release${highImpact.length > 1 ? "s" : ""} scheduled today — ${highImpact.map(e => e.event).join(", ")}.`);
      }
    }

    const fallbackSummary = fallbackSentences.length > 0
      ? fallbackSentences.join(" ")
      : "Market data is currently unavailable. Check back shortly.";
    return NextResponse.json({ summary: fallbackSummary, watch: fallbackWatch, ts, source: "fallback" });
  }
}
