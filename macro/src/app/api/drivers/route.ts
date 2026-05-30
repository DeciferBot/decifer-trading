import { NextResponse } from "next/server";

export const runtime = "edge";
export const revalidate = 0;

export async function GET() {
  const apiKey = process.env.INTELLIGENCE_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: "INTELLIGENCE_API_KEY not configured" }, { status: 500 });
  }

  try {
    const res = await fetch("https://intelligence.decifertrading.com/v1/drivers", {
      headers: { "X-API-Key": apiKey, "Accept": "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: `Upstream error: ${res.status} ${res.statusText}` },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json(data, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown fetch error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
