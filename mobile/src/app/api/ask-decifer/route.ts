// Ask Decifer — streaming POST endpoint.
// Fetches full intelligence context, filters news to universe symbols,
// and streams a claude-sonnet-4-6 response.

import Anthropic from "@anthropic-ai/sdk";
import {
  buildSystemPrompt,
  extractUniverseSymbols,
  type AskNewsItem,
  type ChatMessage,
  type MacroEvent,
} from "@/lib/askDeciferModel";
import type { MarketNowPayload, TtgTheme } from "@/lib/customerApi";

const MODEL = "claude-sonnet-4-6";
const MAX_HISTORY_TURNS = 10; // last N messages passed to Claude
const MAX_TOKENS = 1024;

const INTELLIGENCE_BASE = (
  process.env.NEXT_PUBLIC_INTELLIGENCE_API_URL?.trim() ||
  "https://intelligence.decifertrading.com"
).replace(/\/$/, "");

const FMP_KEY = process.env.FMP_API_KEY;

async function fetchMarketNow(): Promise<MarketNowPayload | null> {
  try {
    const res = await fetch(`${INTELLIGENCE_BASE}/api/market-now`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function fetchMacroEvents(): Promise<MacroEvent[]> {
  try {
    const res = await fetch(`${INTELLIGENCE_BASE}/api/intelligence/macro-events?hours=48`, {
      cache: "no-store",
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.macro_events ?? [];
  } catch {
    return [];
  }
}

async function fetchTtgThemes(): Promise<TtgTheme[]> {
  try {
    const res = await fetch(`${INTELLIGENCE_BASE}/api/intelligence/themes`, {
      cache: "no-store",
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.theme_graph_themes ?? [];
  } catch {
    return [];
  }
}

async function fetchUniverseNews(universeSymbols: Set<string>): Promise<AskNewsItem[]> {
  if (!FMP_KEY || universeSymbols.size === 0) return [];
  try {
    const res = await fetch(
      `https://financialmodelingprep.com/stable/news/stock-latest?limit=60&apikey=${FMP_KEY}`,
      { next: { revalidate: 180 } },
    );
    if (!res.ok) return [];
    const raw: Array<{
      title?: string;
      text?: string;
      publishedDate?: string;
      site?: string;
      symbol?: string;
    }> = await res.json();

    const now = Date.now();
    const seen = new Set<string>();

    return raw
      .filter(n => {
        const sym = n.symbol?.toUpperCase();
        return n.title && sym && universeSymbols.has(sym);
      })
      .map(n => {
        const sym = n.symbol!.toUpperCase();
        const pub = n.publishedDate
          ? new Date(n.publishedDate.replace(" ", "T") + "Z").getTime()
          : 0;
        const minutesAgo = pub ? Math.max(0, Math.round((now - pub) / 60_000)) : 9999;
        const source = (n.site ?? "")
          .replace(/^www\./, "")
          .replace(/\.(com|co\.\w+|org|net|io|us|uk)$/, "");
        return { title: n.title!.trim(), symbol: sym, minutesAgo, summary: (n.text ?? "").trim().slice(0, 180), source };
      })
      .filter(n => {
        if (n.minutesAgo > 1440) return false;
        const key = n.title.slice(0, 60).toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => a.minutesAgo - b.minutesAgo)
      .slice(0, 15);
  } catch {
    return [];
  }
}

export async function POST(req: Request) {
  const anthropicKey = process.env.ANTHROPIC_API_KEY;
  if (!anthropicKey) {
    return new Response("ANTHROPIC_API_KEY not configured in mobile/.env.local", {
      status: 503,
    });
  }

  let body: { question?: string; history?: ChatMessage[] };
  try {
    body = await req.json();
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  const question = (body.question ?? "").trim();
  if (!question) return new Response("Question required", { status: 400 });

  const history: ChatMessage[] = (body.history ?? []).slice(-MAX_HISTORY_TURNS);

  // Fetch all intelligence context in parallel
  const [marketNow, ttgThemes, macroEvents] = await Promise.all([
    fetchMarketNow(),
    fetchTtgThemes(),
    fetchMacroEvents(),
  ]);

  const universeSymbols = extractUniverseSymbols(marketNow);
  const news = await fetchUniverseNews(universeSymbols);
  const systemPrompt = buildSystemPrompt(marketNow, ttgThemes, news, macroEvents);

  // Build message array — history + new user question
  const messages: Array<{ role: "user" | "assistant"; content: string }> = [
    ...history.map(m => ({ role: m.role, content: m.content })),
    { role: "user", content: question },
  ];

  const anthropic = new Anthropic({ apiKey: anthropicKey });

  const stream = anthropic.messages.stream({
    model: MODEL,
    max_tokens: MAX_TOKENS,
    system: systemPrompt,
    messages,
  });

  const readable = new ReadableStream({
    async start(controller) {
      try {
        for await (const event of stream) {
          if (
            event.type === "content_block_delta" &&
            event.delta.type === "text_delta"
          ) {
            controller.enqueue(new TextEncoder().encode(event.delta.text));
          }
        }
      } catch (err) {
        controller.error(err);
      } finally {
        controller.close();
      }
    },
  });

  return new Response(readable, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
