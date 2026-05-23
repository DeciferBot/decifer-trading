import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;

// ETF proxies for world indices — price + dollar change from FMP batch-quote-short
const WORLD: Array<{ sym: string; label: string; region: "US" | "Asia" | "Europe" }> = [
  { sym: "SPY",  label: "S&P 500",  region: "US"     },
  { sym: "QQQ",  label: "Nasdaq",   region: "US"     },
  { sym: "IWM",  label: "Sm-Cap",   region: "US"     },
  { sym: "EWJ",  label: "Japan",    region: "Asia"   },
  { sym: "FXI",  label: "China",    region: "Asia"   },
  { sym: "EWH",  label: "HK",       region: "Asia"   },
  { sym: "EWU",  label: "UK",       region: "Europe" },
  { sym: "EWG",  label: "Germany",  region: "Europe" },
  { sym: "VGK",  label: "Europe",   region: "Europe" },
];

export interface MarketEntry {
  sym: string;
  label: string;
  region: "US" | "Asia" | "Europe";
  changePct: number | null;
}

export async function GET() {
  if (!FMP_KEY) {
    return NextResponse.json({ error: "No FMP key configured" }, { status: 500 });
  }

  const symbols = WORLD.map(w => w.sym).join(",");

  try {
    const res = await fetch(
      `https://financialmodelingprep.com/stable/batch-quote-short?symbols=${symbols}&apikey=${FMP_KEY}`,
      { next: { revalidate: 120 } },
    );
    if (!res.ok) throw new Error(`FMP ${res.status}`);

    const raw: Array<{ symbol: string; price: number; change: number }> = await res.json();
    const lookup = Object.fromEntries(raw.map(r => [r.symbol, r]));

    const markets: MarketEntry[] = WORLD.map(w => {
      const q = lookup[w.sym];
      // % change = dollar_change / previous_close = change / (price - change)
      const prevClose = q ? q.price - q.change : null;
      const pct = q && prevClose && prevClose !== 0
        ? parseFloat(((q.change / prevClose) * 100).toFixed(2))
        : null;
      return { sym: w.sym, label: w.label, region: w.region, changePct: pct };
    });

    return NextResponse.json({ markets, ts: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
