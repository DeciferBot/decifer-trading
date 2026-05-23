import { NextResponse } from "next/server";

const FMP_KEY = process.env.FMP_API_KEY;

// Sources that are video-only — skip them, we want readable articles
const SKIP_SITES = new Set([
  "youtube.com", "youtu.be", "rumble.com", "odysee.com",
  "tiktok.com", "vimeo.com",
]);

export interface Headline {
  title: string;
  summary: string;   // first 180 chars of text
  source: string;    // cleaned site name
  minutesAgo: number;
}

export async function GET() {
  if (!FMP_KEY) {
    return NextResponse.json({ error: "No FMP key configured" }, { status: 500 });
  }

  try {
    const res = await fetch(
      `https://financialmodelingprep.com/stable/news/general-latest?limit=25&apikey=${FMP_KEY}`,
      { next: { revalidate: 300 } },
    );
    if (!res.ok) throw new Error(`FMP ${res.status}`);

    const raw: Array<{
      title: string;
      text?: string;
      publishedDate: string;
      site: string;
    }> = await res.json();

    const now = Date.now();

    const headlines: Headline[] = raw
      .filter(n => n.title && !SKIP_SITES.has(n.site))
      .slice(0, 6)
      .map(n => {
        // FMP date format: "2026-05-22 18:00:14"
        const pub = new Date(n.publishedDate.replace(" ", "T") + "Z").getTime();
        const minutesAgo = Math.max(0, Math.round((now - pub) / 60_000));

        // Clean site name: strip www., strip .com/.co.uk etc.
        const source = n.site
          .replace(/^www\./, "")
          .replace(/\.(com|co\.\w+|org|net|io|us|uk)$/, "");

        return {
          title: n.title.trim(),
          summary: (n.text ?? "").trim().slice(0, 200),
          source,
          minutesAgo,
        };
      });

    return NextResponse.json({ headlines, ts: new Date().toISOString() });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
