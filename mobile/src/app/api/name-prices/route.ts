import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { parseSymbols, type NamePriceEntry } from "@/lib/namePriceUtils";

const FMP_KEY = process.env.FMP_API_KEY;

function emptyPrices(symbols: string[]): NamePriceEntry[] {
  return symbols.map(s => ({ symbol: s, price: null, changePct: null }));
}

export async function GET(req: NextRequest) {
  const raw = req.nextUrl.searchParams.get("symbols");
  const symbols = parseSymbols(raw);

  if (symbols.length === 0) {
    return NextResponse.json({ prices: [], ts: new Date().toISOString() });
  }

  if (!FMP_KEY) {
    return NextResponse.json({ prices: emptyPrices(symbols), ts: new Date().toISOString() });
  }

  try {
    const url = `https://financialmodelingprep.com/stable/batch-quote-short?symbols=${symbols.join(",")}&apikey=${FMP_KEY}`;
    const res = await fetch(url, { next: { revalidate: 120 } });

    if (!res.ok) {
      return NextResponse.json({ prices: emptyPrices(symbols), ts: new Date().toISOString() });
    }

    const data: Array<{ symbol: string; price: number; change: number }> = await res.json();
    const lookup = Object.fromEntries(data.map(r => [r.symbol, r]));

    const prices: NamePriceEntry[] = symbols.map(sym => {
      const q = lookup[sym];
      if (!q) return { symbol: sym, price: null, changePct: null };
      const prevClose = q.price - q.change;
      const changePct =
        prevClose !== 0 ? parseFloat(((q.change / prevClose) * 100).toFixed(2)) : null;
      return { symbol: sym, price: q.price, changePct };
    });

    return NextResponse.json({ prices, ts: new Date().toISOString() });
  } catch {
    return NextResponse.json({ prices: emptyPrices(symbols), ts: new Date().toISOString() });
  }
}
