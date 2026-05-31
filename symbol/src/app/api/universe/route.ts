import { NextResponse } from "next/server";

export async function GET() {
  const apiKey = process.env.INTELLIGENCE_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: "API key not configured" }, { status: 401 });
  }

  try {
    const res = await fetch("https://intelligence.decifertrading.com/v1/universe", {
      headers: { "X-API-Key": apiKey },
      next: { revalidate: 300 },
    });
    if (!res.ok) {
      return NextResponse.json({ error: "upstream_error" }, { status: 502 });
    }
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ error: "fetch_failed" }, { status: 502 });
  }
}
