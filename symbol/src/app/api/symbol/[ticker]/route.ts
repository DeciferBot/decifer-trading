import { NextRequest, NextResponse } from "next/server";

export async function GET(
  _req: NextRequest,
  { params }: { params: { ticker: string } }
) {
  const { ticker } = params;
  const apiKey = process.env.INTELLIGENCE_API_KEY;

  if (!apiKey) {
    return NextResponse.json({ error: "API key not configured" }, { status: 401 });
  }

  const url = `https://intelligence.decifertrading.com/v1/symbol/${encodeURIComponent(ticker.toUpperCase())}`;

  try {
    const res = await fetch(url, {
      headers: { "X-API-Key": apiKey },
      cache: "no-store",
    });

    if (res.status === 404) {
      return NextResponse.json({ error: "not_found", symbol: ticker.toUpperCase() }, { status: 404 });
    }
    if (res.status === 401) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
    if (!res.ok) {
      return NextResponse.json({ error: "upstream_error" }, { status: 502 });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "fetch_failed" }, { status: 502 });
  }
}
