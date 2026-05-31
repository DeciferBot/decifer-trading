import { NextResponse } from "next/server";

export const revalidate = 120;

export async function GET(request: Request) {
  const apiKey = process.env.FMP_API_KEY;
  if (!apiKey) return NextResponse.json({ prices: {} });

  const url = new URL(request.url);
  const symbolsParam = url.searchParams.get("symbols");

  let symbols: string;
  if (symbolsParam) {
    const parsed = symbolsParam.split(",").map(s => s.trim()).filter(Boolean).slice(0, 100);
    symbols = parsed.join(",");
  } else {
    symbols = [
      "NVDA","AMD","INTC","MSFT","AMZN","GOOG","META","ORCL","CRM","NOW","PLTR",
      "TSM","ASML","AMAT","KLAC","LRCX","CDNS","SNPS","MU","AVGO","MRVL","ANET",
      "DELL","HPE","SMCI","NBIS","VRT","ETN","CEG","VST","NRG","EQIX","DLR","PWR",
      "EME","CRWD","PANW","CIEN","COHR","LITE","IREN",
      "RKLB","PL","SPIR","MNTS","LMT","NOC","RTX","LHX","VSAT","IRDM",
      "MOGA","CW","HEI","TDY","HXL","ATI","KTOS","AXON"
    ].join(",");
  }

  try {
    const res = await fetch(
      `https://financialmodelingprep.com/stable/batch-quote-short?symbols=${symbols}&apikey=${apiKey}`,
      { next: { revalidate: 120 } }
    );
    if (!res.ok) return NextResponse.json({ prices: {} });
    const data = await res.json();

    const prices: Record<string, { price: number; change_pct: number; volume: number }> = {};
    if (Array.isArray(data)) {
      for (const q of data) {
        prices[q.symbol] = {
          price: q.price ?? 0,
          change_pct: q.changesPercentage ?? 0,
          volume: q.volume ?? 0,
        };
      }
    }
    return NextResponse.json({ prices });
  } catch {
    return NextResponse.json({ prices: {} });
  }
}
