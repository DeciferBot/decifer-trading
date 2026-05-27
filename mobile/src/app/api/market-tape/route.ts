import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;

export type TapeType = "equity" | "rates" | "commodity" | "safe_haven" | "vol" | "dollar";

export interface TapeEntry {
  sym: string;
  label: string;
  type: TapeType;
  changePct: number | null;
  level: number | null;
}

const ETF_TAPE: Array<{ sym: string; label: string; type: TapeType }> = [
  { sym: "SPY", label: "S&P 500",    type: "equity"    },
  { sym: "QQQ", label: "Nasdaq",     type: "equity"    },
  { sym: "IWM", label: "Small Caps", type: "equity"    },
  { sym: "TLT", label: "Bonds",      type: "rates"     },
  { sym: "GLD", label: "Gold",       type: "safe_haven"},
  { sym: "USO", label: "Oil",        type: "commodity" },
  { sym: "UUP", label: "US Dollar",  type: "dollar"    },
];

export async function GET() {
  if (!FMP_KEY) {
    const empty = ETF_TAPE.map(e => ({ sym: e.sym, label: e.label, type: e.type, changePct: null, level: null }));
    empty.push({ sym: "VIX", label: "VIX", type: "vol" as TapeType, changePct: null, level: null });
    return NextResponse.json({ tape: empty, ts: new Date().toISOString() });
  }

  const symbols = ETF_TAPE.map(e => e.sym).join(",");

  const [etfResult, vixResult] = await Promise.allSettled([
    fetch(
      `https://financialmodelingprep.com/stable/batch-quote-short?symbols=${symbols}&apikey=${FMP_KEY}`,
      { next: { revalidate: 120 } },
    ),
    fetch(
      `https://financialmodelingprep.com/stable/quote/%5EVIX?apikey=${FMP_KEY}`,
      { next: { revalidate: 120 } },
    ),
  ]);

  const tape: TapeEntry[] = [];

  if (etfResult.status === "fulfilled" && etfResult.value.ok) {
    const raw: Array<{ symbol: string; price: number; change: number }> =
      await etfResult.value.json();
    const lookup = Object.fromEntries(raw.map(r => [r.symbol, r]));
    for (const e of ETF_TAPE) {
      const q = lookup[e.sym];
      const prevClose = q ? q.price - q.change : null;
      const pct =
        q && prevClose && prevClose !== 0
          ? parseFloat(((q.change / prevClose) * 100).toFixed(2))
          : null;
      tape.push({
        sym: e.sym,
        label: e.label,
        type: e.type,
        changePct: pct,
        level: q?.price ?? null,
      });
    }
  } else {
    for (const e of ETF_TAPE) {
      tape.push({ sym: e.sym, label: e.label, type: e.type, changePct: null, level: null });
    }
  }

  let vixLevel: number | null = null;
  let vixChangePct: number | null = null;
  if (vixResult.status === "fulfilled" && vixResult.value.ok) {
    const vixData = await vixResult.value.json();
    const vixQ = Array.isArray(vixData) ? vixData[0] : vixData;
    if (vixQ?.price != null) {
      vixLevel = parseFloat((vixQ.price as number).toFixed(2));
      const prev = (vixQ.price as number) - (vixQ.change ?? 0);
      if (prev !== 0 && vixQ.change != null) {
        vixChangePct = parseFloat(((vixQ.change / prev) * 100).toFixed(2));
      }
    }
  }

  tape.push({
    sym: "VIX",
    label: "VIX",
    type: "vol",
    changePct: vixChangePct,
    level: vixLevel,
  });

  return NextResponse.json({ tape, ts: new Date().toISOString() });
}
