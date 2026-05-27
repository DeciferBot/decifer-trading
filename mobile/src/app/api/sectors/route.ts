import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;

export interface SectorEntry {
  sym: string;
  label: string;
  shortLabel: string;
  changePct: number | null;
}

// SPDR sector ETFs — the standard market sector map
const SECTORS: Array<{ sym: string; label: string; shortLabel: string }> = [
  { sym: "XLK",  label: "Technology",             shortLabel: "Tech"      },
  { sym: "XLC",  label: "Communication Services",  shortLabel: "Comm"      },
  { sym: "XLY",  label: "Consumer Discretionary",  shortLabel: "Cons Disc" },
  { sym: "XLF",  label: "Financials",              shortLabel: "Finance"   },
  { sym: "XLV",  label: "Health Care",             shortLabel: "Health"    },
  { sym: "XLI",  label: "Industrials",             shortLabel: "Industl"   },
  { sym: "XLE",  label: "Energy",                  shortLabel: "Energy"    },
  { sym: "XLB",  label: "Materials",               shortLabel: "Materials" },
  { sym: "XLP",  label: "Consumer Staples",        shortLabel: "Staples"   },
  { sym: "XLRE", label: "Real Estate",             shortLabel: "Real Est"  },
  { sym: "XLU",  label: "Utilities",               shortLabel: "Utilities" },
];

export async function GET() {
  const ts = new Date().toISOString();

  if (!FMP_KEY) {
    return NextResponse.json({
      sectors: SECTORS.map(s => ({ ...s, changePct: null })),
      ts,
    });
  }

  try {
    const syms = SECTORS.map(s => s.sym).join(",");
    const res = await fetch(
      `https://financialmodelingprep.com/stable/batch-quote-short?symbols=${syms}&apikey=${FMP_KEY}`,
      { next: { revalidate: 120 } },
    );

    if (!res.ok) throw new Error(`FMP ${res.status}`);

    const raw: Array<{ symbol: string; price: number; change: number }> = await res.json();
    const lookup = Object.fromEntries(raw.map(r => [r.symbol, r]));

    const sectors: SectorEntry[] = SECTORS.map(s => {
      const q = lookup[s.sym];
      if (!q) return { ...s, changePct: null };
      const prevClose = q.price - q.change;
      const changePct =
        prevClose !== 0
          ? parseFloat(((q.change / prevClose) * 100).toFixed(2))
          : null;
      return { ...s, changePct };
    });

    return NextResponse.json({ sectors, ts });
  } catch {
    return NextResponse.json({
      sectors: SECTORS.map(s => ({ ...s, changePct: null })),
      ts,
    });
  }
}
