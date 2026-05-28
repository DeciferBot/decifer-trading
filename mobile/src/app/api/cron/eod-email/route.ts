// Cron endpoint — generates EOD summary and emails it to the configured recipient.
// Schedule: vercel.json crons → 21:15 UTC Mon-Fri (5:15 PM EDT / 4:15 PM EST).
// Vercel automatically sends Authorization: Bearer $CRON_SECRET on cron invocations.
// Can also be triggered manually: GET /api/cron/eod-email (bypasses auth in dev).

import { NextRequest, NextResponse } from "next/server";
import {
  generateEodSummary,
  parseEodItems,
  type EodSummaryPayload,
  type EodTape,
} from "@/app/api/eod-summary/route";

export const maxDuration = 90;

const RESEND_KEY = process.env.RESEND_API_KEY;
const RESEND_FROM =
  process.env.RESEND_FROM_EMAIL ?? "Decifer <noreply@decifertrading.com>";
const EOD_EMAIL_TO = process.env.EOD_EMAIL_TO ?? "amit@decifer.io";

// ── Tape formatting ───────────────────────────────────────────────────────────

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v}%`;
}

function tapeColor(v: number | null, isVix = false): string {
  if (v === null) return "#6b7280";
  if (isVix) return v > 20 ? "#ef4444" : v > 15 ? "#f59e0b" : "#22c55e";
  return v >= 0 ? "#22c55e" : "#ef4444";
}

// ── Email HTML builder ────────────────────────────────────────────────────────

function buildTapeCell(label: string, value: string, color: string): string {
  return `<td style="padding:10px 14px;text-align:center;background:#111827;border-radius:8px;">
    <div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;">${label}</div>
    <div style="color:${color};font-size:18px;font-weight:700;margin-top:4px;font-feature-settings:'tnum';">${value}</div>
  </td>`;
}

function highlightTickers(text: string): string {
  return text.replace(
    /\$([A-Z]{1,5})\b/g,
    '<strong style="color:#f97316;font-weight:700;">$$$1</strong>'
  );
}

function buildItemRow(text: string, index: number): string {
  const highlighted = highlightTickers(text);
  return `
  <tr>
    <td style="padding:18px 0;border-bottom:1px solid #1f2937;vertical-align:top;">
      <table cellpadding="0" cellspacing="0" width="100%"><tr>
        <td style="width:32px;vertical-align:top;padding-top:2px;">
          <div style="width:26px;height:26px;background:#1f2937;border-radius:50%;text-align:center;line-height:26px;color:#f97316;font-weight:700;font-size:13px;">${index}</div>
        </td>
        <td style="color:#e5e7eb;font-size:15px;line-height:1.65;padding-left:12px;">${highlighted}</td>
      </tr></table>
    </td>
  </tr>`;
}

function buildHtml(payload: EodSummaryPayload): string {
  const { marketDate, tape, rawText, items, generatedAt } = payload;

  const tapeCells: Array<[string, string, string]> = [
    ["S&P 500", fmtPct(tape.spy), tapeColor(tape.spy)],
    ["Nasdaq", fmtPct(tape.qqq), tapeColor(tape.qqq)],
    ["Small Caps", fmtPct(tape.iwm), tapeColor(tape.iwm)],
    ["VIX", tape.vix !== null ? String(tape.vix) : "—", tapeColor(tape.vix, true)],
    ["Bonds", fmtPct(tape.tlt), tapeColor(tape.tlt)],
    ["Gold", fmtPct(tape.gld), tapeColor(tape.gld)],
  ];

  const tapeHtml = tapeCells
    .map(([label, value, color]) => buildTapeCell(label, value, color))
    .join('<td style="width:6px;"></td>');

  // Split items from watch-tomorrow section
  const watchIdx = rawText.indexOf("Watch Tomorrow:");
  const watchSection =
    watchIdx >= 0 ? rawText.slice(watchIdx + "Watch Tomorrow:".length).trim() : "";
  const watchLines = watchSection
    .split("\n")
    .map((l) => l.replace(/^[-•*]\s*/, "").trim())
    .filter(Boolean);

  const itemsHtml =
    items.length > 0
      ? items.map((item) => buildItemRow(item.text, item.number)).join("")
      : rawText
      ? `<tr><td style="padding:20px 0;color:#9ca3af;font-size:14px;line-height:1.7;white-space:pre-line;">${highlightTickers(rawText)}</td></tr>`
      : `<tr><td style="padding:20px 0;color:#6b7280;">No summary data available.</td></tr>`;

  const watchHtml =
    watchLines.length > 0
      ? `
  <tr>
    <td style="padding:28px 0 8px;">
      <div style="display:inline-block;background:#1c1007;border:1px solid #431407;border-radius:6px;padding:4px 10px;margin-bottom:14px;">
        <span style="color:#f97316;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">Watch Tomorrow</span>
      </div>
      ${watchLines.map((l) => `<div style="color:#9ca3af;font-size:14px;line-height:1.7;padding:4px 0 4px 16px;border-left:2px solid #374151;">${highlightTickers(l)}</div>`).join("")}
    </td>
  </tr>`
      : "";

  const generatedStr = new Date(generatedAt).toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Decifer EOD — ${marketDate}</title>
</head>
<body style="margin:0;padding:0;background:#030712;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#030712;padding:32px 16px;">
    <tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" role="presentation" style="max-width:640px;width:100%;">

      <!-- Header -->
      <tr>
        <td style="background:linear-gradient(135deg,#111827 0%,#0f172a 100%);border-radius:12px 12px 0 0;padding:28px 32px 24px;border-bottom:1px solid #1f2937;">
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td>
              <div style="color:#f97316;font-weight:800;font-size:20px;letter-spacing:-0.3px;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">DECIFER</div>
              <div style="color:#6b7280;font-size:12px;font-weight:500;margin-top:2px;text-transform:uppercase;letter-spacing:0.5px;">End of Day Market Summary</div>
            </td>
            <td align="right">
              <div style="color:#e5e7eb;font-size:17px;font-weight:600;">${marketDate}</div>
            </td>
          </tr></table>
        </td>
      </tr>

      <!-- Market Tape -->
      <tr>
        <td style="background:#0d1117;padding:20px 32px 20px;border-bottom:1px solid #1f2937;">
          <table cellpadding="0" cellspacing="0" width="100%">
            <tr>${tapeHtml}</tr>
          </table>
        </td>
      </tr>

      <!-- Summary Items -->
      <tr>
        <td style="background:#0d1117;padding:8px 32px 0;">
          <table width="100%" cellpadding="0" cellspacing="0">
            ${itemsHtml}
            ${watchHtml}
          </table>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#030712;border-radius:0 0 12px 12px;padding:20px 32px;border-top:1px solid #111827;">
          <div style="color:#374151;font-size:12px;line-height:1.6;">Generated ${generatedStr} &nbsp;·&nbsp; Decifer Trading &nbsp;·&nbsp; Paper account only</div>
          <div style="color:#1f2937;font-size:11px;margin-top:4px;">Data: FMP &amp; Alpaca &nbsp;·&nbsp; Synthesis: Claude Sonnet &nbsp;·&nbsp; Not financial advice.</div>
        </td>
      </tr>

    </table>
    </td></tr>
  </table>
</body>
</html>`;
}

// ── Email sender (Resend REST API — no package required) ──────────────────────

async function sendEmail(
  subject: string,
  html: string
): Promise<{ ok: boolean; id?: string; error?: string }> {
  if (!RESEND_KEY) return { ok: false, error: "RESEND_API_KEY not set" };

  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${RESEND_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: RESEND_FROM,
        to: [EOD_EMAIL_TO],
        subject,
        html,
      }),
      cache: "no-store",
    });

    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: (body as { message?: string }).message ?? String(res.status) };
    }
    return { ok: true, id: (body as { id?: string }).id };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ── Subject builder ───────────────────────────────────────────────────────────

function buildSubject(payload: EodSummaryPayload): string {
  const { marketDate, tape } = payload;
  const parts: string[] = [];
  if (tape.spy !== null) parts.push(`SPY ${fmtPct(tape.spy)}`);
  if (tape.vix !== null) parts.push(`VIX ${tape.vix}`);
  const suffix = parts.length ? ` | ${parts.join(" · ")}` : "";
  return `Decifer EOD — ${marketDate}${suffix}`;
}

// ── GET handler ───────────────────────────────────────────────────────────────

export async function GET(request: NextRequest) {
  // Validate Vercel cron secret (skipped if CRON_SECRET not set — allows manual triggers)
  const cronSecret = process.env.CRON_SECRET;
  if (cronSecret) {
    const auth = request.headers.get("authorization");
    if (auth !== `Bearer ${cronSecret}`) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  // Re-parse items in case we need them fresh (generateEodSummary also calls parseEodItems internally)
  const payload = await generateEodSummary();

  // Ensure items are populated even if generateEodSummary had a partial parse
  if (!payload.items.length && payload.rawText) {
    payload.items = parseEodItems(payload.rawText);
  }

  const subject = buildSubject(payload);
  const html = buildHtml(payload);
  const result = await sendEmail(subject, html);

  return NextResponse.json({
    ok: result.ok,
    emailId: result.id,
    sentTo: EOD_EMAIL_TO,
    subject,
    itemCount: payload.items.length,
    marketDate: payload.marketDate,
    generatedAt: payload.generatedAt,
    ...(result.error ? { error: result.error } : {}),
  });
}
