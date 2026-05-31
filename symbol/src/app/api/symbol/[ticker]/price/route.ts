import { NextRequest, NextResponse } from "next/server";

export async function GET(
  _req: NextRequest,
  { params }: { params: { ticker: string } }
) {
  const { ticker } = params;
  const fmpKey = process.env.FMP_API_KEY;
  if (!fmpKey) {
    return NextResponse.json({ error: "FMP key not configured" }, { status: 401 });
  }

  const sym = ticker.toUpperCase();
  const profileUrl = `https://financialmodelingprep.com/stable/profile?symbol=${sym}&apikey=${fmpKey}`;

  try {
    const res = await fetch(profileUrl, { next: { revalidate: 300 } });
    if (!res.ok) {
      return NextResponse.json({ error: "upstream_error" }, { status: 502 });
    }
    const json = await res.json();
    const p = Array.isArray(json) ? json[0] : json;
    if (!p || !p.symbol) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    return NextResponse.json({
      symbol: p.symbol,
      companyName: p.companyName ?? null,
      price: p.price ?? null,
      changesPercentage: p.changesPercentage ?? null,
      image: p.image ?? null,
      description: p.description ?? null,
      sector: p.sector ?? null,
      industry: p.industry ?? null,
      mktCap: p.mktCap ?? null,
    });
  } catch {
    return NextResponse.json({ error: "fetch_failed" }, { status: 502 });
  }
}
