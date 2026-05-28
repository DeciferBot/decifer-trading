// Dev-only preview route — renders the EOD email HTML directly in the browser.
// Not linked from any UI, not deployed as a cron target.

import { generateEodSummary, parseEodItems } from "@/app/api/eod-summary/route";
import { buildHtml } from "@/app/api/cron/eod-email/route";

export const maxDuration = 60;

export async function GET() {
  const payload = await generateEodSummary();
  if (!payload.items.length && payload.rawText) {
    payload.items = parseEodItems(payload.rawText);
  }
  const html = buildHtml(payload);
  return new Response(html, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}
