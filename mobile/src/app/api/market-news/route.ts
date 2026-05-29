import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;

// Map common symbols to readable theme labels for news chips
const SYMBOL_THEME: Record<string, string> = {
  NVDA: "AI Infrastructure", AMD: "AI Infrastructure", AVGO: "AI Infrastructure",
  TSM: "AI Infrastructure", SMCI: "AI Infrastructure", DELL: "AI Infrastructure",
  INTC: "AI Infrastructure", QCOM: "AI Infrastructure", MU: "AI Infrastructure",
  MSFT: "AI Infrastructure", GOOGL: "AI Infrastructure", META: "AI Infrastructure",
  AMZN: "AI Infrastructure", AAPL: "Tech",
  LMT: "Defence", RTX: "Defence", NOC: "Defence", BA: "Defence", GD: "Defence",
  PLTR: "Defence", CACI: "Defence",
  XOM: "Energy", CVX: "Energy", COP: "Energy", OXY: "Energy",
  GLD: "Gold", GDX: "Gold", NEM: "Gold", AEM: "Gold",
  LLY: "Healthcare", NVO: "Healthcare", HIMS: "Healthcare",
  UNH: "Healthcare", PFE: "Healthcare", ABBV: "Healthcare",
  TSLA: "EV & Autos", F: "Autos", GM: "Autos",
  JPM: "Financials", GS: "Financials", MS: "Financials", BAC: "Financials",
  BTC: "Digital Assets", ETH: "Digital Assets", MSTR: "Digital Assets", COIN: "Digital Assets",
};

const SKIP_SITES = new Set(["youtube.com", "youtu.be", "rumble.com", "tiktok.com"]);

export interface NewsItem {
  title: string;
  summary: string;
  source: string;
  minutesAgo: number;
  symbol: string | null;
  themeLabel: string | null;
  logoUrl: string | null;
}

export async function GET(request: Request) {
  const ts = new Date().toISOString();

  if (!FMP_KEY) return NextResponse.json({ news: [], ts });

  // When caller provides a symbol list, use the filtered endpoint (same as bot dashboard).
  // Fall back to stock-latest only when no symbols are available.
  const { searchParams } = new URL(request.url);
  const symbolsParam = searchParams.get("symbols") ?? "";
  const symbolList = symbolsParam
    .split(",")
    .map(s => s.trim().toUpperCase())
    .filter(Boolean)
    .slice(0, 50);

  const stockUrl =
    symbolList.length > 0
      ? `https://financialmodelingprep.com/stable/news/stock?symbols=${symbolList.join(",")}&limit=30&apikey=${FMP_KEY}`
      : `https://financialmodelingprep.com/stable/news/stock-latest?limit=20&apikey=${FMP_KEY}`;

  // Always fetch macro/general news — this covers Trump, Fed, Iran, oil, war, macro drivers.
  const generalUrl = `https://financialmodelingprep.com/stable/news/general-latest?limit=15&apikey=${FMP_KEY}`;

  const [stockRes, generalRes] = await Promise.allSettled([
    fetch(stockUrl, { next: { revalidate: 180 } }),
    fetch(generalUrl, { next: { revalidate: 300 } }),
  ]);

  const now = Date.now();
  const MAX_AGE_MINUTES = 1440; // 24h

  const parseItem = (n: {
    title?: string;
    text?: string;
    publishedDate?: string;
    site?: string;
    symbol?: string;
    publisher?: string;
  }): NewsItem | null => {
    if (!n.title) return null;
    const site = n.site ?? n.publisher ?? "";
    if (SKIP_SITES.has(site)) return null;

    const pub = n.publishedDate
      ? new Date(n.publishedDate.replace(" ", "T") + (n.publishedDate.includes("Z") ? "" : "Z")).getTime()
      : 0;
    const minutesAgo = pub ? Math.max(0, Math.round((now - pub) / 60_000)) : 9999;
    if (minutesAgo > MAX_AGE_MINUTES) return null;

    const source = site.replace(/^www\./, "").replace(/\.(com|co\.\w+|org|net|io|us|uk)$/, "");
    const sym = n.symbol?.toUpperCase() ?? null;
    const themeLabel = sym ? (SYMBOL_THEME[sym] ?? null) : null;
    const logoUrl = sym ? `https://images.financialmodelingprep.com/symbol/${sym}.png` : null;

    return {
      title: n.title.trim(),
      summary: (n.text ?? "").trim().slice(0, 180),
      source,
      minutesAgo,
      symbol: sym,
      themeLabel,
      logoUrl,
    };
  };

  const items: NewsItem[] = [];

  if (stockRes.status === "fulfilled" && stockRes.value.ok) {
    try {
      const raw: object[] = await stockRes.value.json();
      for (const n of raw) {
        const item = parseItem(n as Parameters<typeof parseItem>[0]);
        if (item) items.push(item);
      }
    } catch { /* graceful */ }
  }

  if (generalRes.status === "fulfilled" && generalRes.value.ok) {
    try {
      const raw: object[] = await generalRes.value.json();
      for (const n of raw) {
        const item = parseItem(n as Parameters<typeof parseItem>[0]);
        if (item) items.push(item);
      }
    } catch { /* graceful */ }
  }

  // Sort by recency, deduplicate by title prefix
  const seen = new Set<string>();
  const deduped = items
    .sort((a, b) => a.minutesAgo - b.minutesAgo)
    .filter(item => {
      const key = item.title.slice(0, 60).toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 15);

  return NextResponse.json({ news: deduped, ts });
}
