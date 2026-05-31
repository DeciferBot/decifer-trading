// One-time analysis email sender.
// Accepts a POST from scripts/watchlist_analysis.py and sends the formatted
// results via Resend. Uses the same RESEND_API_KEY / RESEND_FROM_EMAIL vars
// as the EOD email route — no new secrets required.

import { NextRequest, NextResponse } from "next/server";

const RESEND_KEY = process.env.RESEND_API_KEY;
const RESEND_FROM =
  process.env.RESEND_FROM_EMAIL ?? "Decifer <noreply@decifertrading.com>";

interface ScoredRow {
  symbol: string;
  score: number;
  direction: string;
  signal: string;
  apex_action: string | null;
  apex_conviction: string | null;
  apex_reasoning: string | null;
}

interface AnalysisPayload {
  recipients: string[];
  subject: string;
  regime: string;
  scored_rows: ScoredRow[];
  apex_summary: string;
  generated_at: string;
  note: string;
}

function directionColour(direction: string): string {
  if (direction === "LONG" || direction === "BUY") return "#16a34a";
  if (direction === "SHORT" || direction === "SELL") return "#dc2626";
  return "#6b7280";
}

function convictionColour(conviction: string | null): string {
  if (!conviction) return "#6b7280";
  const c = conviction.toUpperCase();
  if (c === "HIGH") return "#16a34a";
  if (c === "MEDIUM") return "#d97706";
  return "#6b7280";
}

function buildHtml(p: AnalysisPayload): string {
  const ts = new Date(p.generated_at).toUTCString();

  const rows = p.scored_rows
    .map((r) => {
      const apexCell = r.apex_action
        ? `<span style="color:${convictionColour(r.apex_conviction)};font-weight:600;">${r.apex_action}</span>`
            + (r.apex_conviction ? ` <span style="color:#9ca3af;font-size:11px;">(${r.apex_conviction})</span>` : "")
        : `<span style="color:#6b7280;">—</span>`;

      const reasoning = r.apex_reasoning
        ? `<div style="color:#9ca3af;font-size:11px;margin-top:3px;">${r.apex_reasoning}</div>`
        : "";

      return `
        <tr style="border-bottom:1px solid #1f2937;">
          <td style="padding:10px 8px;font-weight:700;color:#f9fafb;font-size:13px;">${r.symbol}</td>
          <td style="padding:10px 8px;color:#f9fafb;font-size:13px;">${r.score.toFixed(1)}</td>
          <td style="padding:10px 8px;color:${directionColour(r.direction)};font-size:13px;font-weight:600;">${r.direction}</td>
          <td style="padding:10px 8px;">${apexCell}${reasoning}</td>
        </tr>`;
    })
    .join("");

  const summaryBlock = p.apex_summary
    ? `<div style="background:#111827;border-left:3px solid #f97316;padding:14px 16px;margin:20px 0;border-radius:0 6px 6px 0;">
        <div style="color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Apex Summary</div>
        <div style="color:#e5e7eb;font-size:13px;line-height:1.6;">${p.apex_summary}</div>
       </div>`
    : "";

  return `<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#030712;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#030712;">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#0d1117;border:1px solid #1f2937;border-radius:8px;overflow:hidden;">

        <!-- Header -->
        <tr><td style="background:#111827;padding:20px 24px;border-bottom:1px solid #1f2937;">
          <div style="color:#f97316;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;font-weight:600;">Decifer</div>
          <div style="color:#f9fafb;font-size:18px;font-weight:700;margin-top:4px;">Watchlist Analysis</div>
          <div style="color:#6b7280;font-size:12px;margin-top:4px;">${ts} &nbsp;·&nbsp; Regime: ${p.regime}</div>
        </td></tr>

        <!-- Score table -->
        <tr><td style="padding:20px 24px 0;">
          <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
            <thead>
              <tr style="border-bottom:1px solid #374151;">
                <th style="text-align:left;padding:6px 8px;color:#6b7280;font-size:11px;text-transform:uppercase;font-weight:600;">Symbol</th>
                <th style="text-align:left;padding:6px 8px;color:#6b7280;font-size:11px;text-transform:uppercase;font-weight:600;">Score</th>
                <th style="text-align:left;padding:6px 8px;color:#6b7280;font-size:11px;text-transform:uppercase;font-weight:600;">Direction</th>
                <th style="text-align:left;padding:6px 8px;color:#6b7280;font-size:11px;text-transform:uppercase;font-weight:600;">Apex Read</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </td></tr>

        <!-- Apex summary -->
        <tr><td style="padding:0 24px;">${summaryBlock}</td></tr>

        <!-- Footer -->
        <tr><td style="padding:16px 24px;border-top:1px solid #1f2937;">
          <div style="color:#4b5563;font-size:11px;">${p.note}</div>
          <div style="color:#4b5563;font-size:11px;margin-top:2px;">Scores: Decifer signal engine · Synthesis: Claude Sonnet · Not financial advice.</div>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>`;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  if (!RESEND_KEY) {
    return NextResponse.json({ ok: false, error: "RESEND_API_KEY not configured" }, { status: 500 });
  }

  let payload: AnalysisPayload;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }

  if (!payload.recipients?.length || !payload.scored_rows?.length) {
    return NextResponse.json({ ok: false, error: "missing recipients or scored_rows" }, { status: 400 });
  }

  const html = buildHtml(payload);

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${RESEND_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: RESEND_FROM,
      to: payload.recipients,
      subject: payload.subject,
      html,
    }),
    cache: "no-store",
  });

  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = (body as { message?: string }).message ?? String(res.status);
    return NextResponse.json({ ok: false, error: msg }, { status: 502 });
  }

  return NextResponse.json({ ok: true, id: (body as { id?: string }).id });
}
