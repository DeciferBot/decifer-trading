// Cron endpoint — generates EOD summary and emails it to the configured recipient.
// Schedule: vercel.json crons → 21:15 UTC Mon-Fri (5:15 PM EDT / 4:15 PM EST).
// Vercel automatically sends Authorization: Bearer $CRON_SECRET on cron invocations.
// Can also be triggered manually: GET /api/cron/eod-email (bypasses auth in dev).

import { NextRequest, NextResponse } from "next/server";
import {
  generateEodSummary,
  parseEodItems,
  extractWatchTomorrow,
  type EodSummaryPayload,
  type EodMover,
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

// ── Category icons ────────────────────────────────────────────────────────────

const CATEGORY_ICONS: Record<string, string> = {
  ANALYST: "&#128202;", // 📊
  INSIDER: "&#128100;", // 👤
  EARNINGS: "&#128176;", // 💰
  MACRO: "&#127968;",   // 🏦
  MOVERS: "&#128200;",  // 📈
  DEAL: "&#129309;",    // 🤝
  NEWS: "&#128240;",    // 📰
  SECTOR: "&#127981;",  // 🏭
  RATES: "&#128201;",   // 📉
  FED: "&#127968;",     // 🏦
};

function extractCategory(text: string): { icon: string; cleanText: string } {
  const match = text.match(/^\[([A-Z]+)\]\s*/);
  if (match) {
    const cat = match[1].toUpperCase();
    return {
      icon: CATEGORY_ICONS[cat] ?? "&bull;",
      cleanText: text.slice(match[0].length),
    };
  }
  return { icon: "&bull;", cleanText: text };
}

// ── Ticker highlighter ────────────────────────────────────────────────────────

function highlightTickers(text: string): string {
  return text.replace(
    /\$([A-Z]{1,5})\b/g,
    '<strong style="color:#f97316;font-weight:700;">$$$1</strong>'
  );
}

// ── Tape cell builder ─────────────────────────────────────────────────────────

function buildTapeCell(label: string, value: string, color: string): string {
  return `<td style="padding:10px 14px;text-align:center;background:#111827;border-radius:8px;">
    <div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;">${label}</div>
    <div style="color:${color};font-size:18px;font-weight:700;margin-top:4px;font-feature-settings:'tnum';">${value}</div>
  </td>`;
}

// ── Movers section ────────────────────────────────────────────────────────────

function buildMoverRows(movers: EodMover[], color: string, sign: string): string {
  return movers.slice(0, 5).map(m => {
    const absPct = Math.abs(m.pct).toFixed(1);
    const pctStr = `${sign}${absPct}%`;
    const priceStr = m.price !== undefined ? `$${m.price.toFixed(2)}` : "";
    return `
    <tr>
      <td style="padding:5px 0;border-bottom:1px solid #0d1117;vertical-align:middle;">
        <table cellpadding="0" cellspacing="0" width="100%"><tr>
          <td style="vertical-align:middle;">
            <div style="color:#f97316;font-weight:700;font-size:12px;font-family:'Courier New',Courier,monospace;letter-spacing:0.5px;">${m.symbol}</div>
            <div style="color:#374151;font-size:10px;overflow:hidden;white-space:nowrap;max-width:90px;text-overflow:ellipsis;">${m.name.slice(0, 20)}</div>
          </td>
          <td align="right" style="vertical-align:middle;white-space:nowrap;padding-left:8px;">
            <div style="color:${color};font-size:15px;font-weight:700;line-height:1.2;">${pctStr}</div>
            <div style="color:#374151;font-size:10px;">${priceStr}</div>
          </td>
        </tr></table>
      </td>
    </tr>`;
  }).join("");
}

function buildMoversSection(gainers: EodMover[], losers: EodMover[]): string {
  if (!gainers.length && !losers.length) return "";

  return `
  <tr>
    <td style="background:#0d1117;padding:14px 32px 18px;border-bottom:1px solid #1f2937;">
      <table cellpadding="0" cellspacing="0" width="100%">
        <tr>
          <!-- Gainers column -->
          <td width="46%" style="vertical-align:top;">
            <div style="color:#22c55e;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;padding-bottom:8px;border-bottom:1px solid #1f2937;margin-bottom:2px;">&#9650; Top Gainers</div>
            <table cellpadding="0" cellspacing="0" width="100%">
              ${buildMoverRows(gainers, "#22c55e", "+")}
            </table>
          </td>
          <!-- Spacer -->
          <td width="8%" style="min-width:16px;"></td>
          <!-- Losers column -->
          <td width="46%" style="vertical-align:top;">
            <div style="color:#ef4444;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;padding-bottom:8px;border-bottom:1px solid #1f2937;margin-bottom:2px;">&#9660; Top Losers</div>
            <table cellpadding="0" cellspacing="0" width="100%">
              ${buildMoverRows(losers, "#ef4444", "-")}
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>`;
}

// ── Summary item row ──────────────────────────────────────────────────────────

function buildItemRow(text: string, index: number): string {
  const { icon, cleanText } = extractCategory(text);
  const highlighted = highlightTickers(cleanText);
  return `
  <tr>
    <td style="padding:14px 0;border-bottom:1px solid #1f2937;vertical-align:top;">
      <table cellpadding="0" cellspacing="0" width="100%"><tr>
        <td style="width:28px;vertical-align:top;padding-top:1px;">
          <div style="width:22px;height:22px;background:#1f2937;border-radius:50%;text-align:center;line-height:22px;color:#f97316;font-weight:700;font-size:11px;">${index}</div>
        </td>
        <td style="width:20px;vertical-align:top;padding-top:1px;padding-left:6px;font-size:14px;line-height:22px;">${icon}</td>
        <td style="color:#d1d5db;font-size:14px;line-height:1.55;padding-left:8px;">${highlighted}</td>
      </tr></table>
    </td>
  </tr>`;
}

// ── Watch Tomorrow section ────────────────────────────────────────────────────

function renderWatchSection(raw: string): string {
  if (!raw) return "";

  const subHeaders = ["Earnings:", "Macro Data:", "Fed/Other:", "Watch Tomorrow"];
  const lines = raw.split("\n").map((l) => l.replace(/^[-•*]\s*/, "").trim()).filter(Boolean);

  let html = "";
  let inEarnings = false;
  let earningsTableOpen = false;

  for (const line of lines) {
    const isHeader = subHeaders.some((h) => line.startsWith(h));

    if (isHeader) {
      if (earningsTableOpen) {
        html += `</table>`;
        earningsTableOpen = false;
        inEarnings = false;
      }
      const label = line.replace(/:?\s*$/, "");
      html += `<div style="color:#f97316;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin:14px 0 6px;">${label}</div>`;
      inEarnings = line.startsWith("Earnings:");
      if (inEarnings) {
        earningsTableOpen = true;
        html += `<table cellpadding="0" cellspacing="4" width="100%">
          <tr>
            <th align="left" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 0 6px 0;font-weight:600;border-bottom:1px solid #1f2937;">Company</th>
            <th align="left" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 8px 6px;font-weight:600;border-bottom:1px solid #1f2937;">When</th>
            <th align="right" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 0 6px 0;font-weight:600;border-bottom:1px solid #1f2937;">EPS Est</th>
            <th align="right" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 0 6px 4px;font-weight:600;border-bottom:1px solid #1f2937;">Rev Est</th>
          </tr>`;
      }
      continue;
    }

    // Earnings table row: $TICKER (Name) — pre-market | EPS est $X.XX | Rev est $XB
    if (inEarnings && earningsTableOpen && line.startsWith("$")) {
      const tickerMatch = line.match(/^\$([A-Z]{1,5})/);
      const nameMatch = line.match(/\(([^)]+)\)/);
      const timingMatch = line.match(/(pre-market|after-close|during market hours|BMO|AMC|DMH)/i);
      const epsMatch = line.match(/EPS\s+est\s+\$([0-9.-]+)/i);
      const revMatch = line.match(/Rev\s+est\s+\$([0-9.]+[BMKbmk]?)/i);

      const sym = tickerMatch ? tickerMatch[1] : line.slice(0, 6);
      const name = nameMatch ? nameMatch[1].slice(0, 16) : sym;
      const t = timingMatch ? timingMatch[1].toLowerCase() : "";
      const timingLabel = t.includes("pre") || t === "bmo" ? "BMO" :
                          t.includes("after") || t === "amc" ? "AMC" :
                          t === "dmh" || t.includes("during") ? "DMH" : "—";
      const eps = epsMatch ? `$${epsMatch[1]}` : "—";
      const rev = revMatch ? `$${revMatch[1]}` : "—";

      html += `
        <tr>
          <td style="padding:5px 0;border-bottom:1px solid #111827;vertical-align:middle;">
            <div style="color:#f97316;font-weight:700;font-size:12px;font-family:'Courier New',monospace;">${sym}</div>
            <div style="color:#4b5563;font-size:10px;">${name}</div>
          </td>
          <td style="padding:5px 8px;border-bottom:1px solid #111827;vertical-align:middle;">
            <span style="background:#1f2937;color:#9ca3af;font-size:10px;padding:2px 6px;border-radius:3px;font-weight:600;white-space:nowrap;">${timingLabel}</span>
          </td>
          <td align="right" style="padding:5px 0;border-bottom:1px solid #111827;color:#e5e7eb;font-size:12px;font-weight:600;white-space:nowrap;">${eps}</td>
          <td align="right" style="padding:5px 4px;border-bottom:1px solid #111827;color:#9ca3af;font-size:12px;white-space:nowrap;">${rev}</td>
        </tr>`;
      continue;
    }

    if (earningsTableOpen && !line.startsWith("$")) {
      html += `</table>`;
      earningsTableOpen = false;
      inEarnings = false;
    }

    // Macro line: [High|Medium] TIME ET — EVENT (prev X, est Y)
    const macroMatch = line.match(/^\[(High|Medium|HIGH|MED)\]/i);
    if (macroMatch) {
      const isHigh = macroMatch[1].toUpperCase().startsWith("H");
      const impactColor = isHigh ? "#ef4444" : "#f59e0b";
      const cleanLine = highlightTickers(line.replace(/^\[[^\]]+\]\s*/, ""));
      html += `
        <table cellpadding="0" cellspacing="0" width="100%" style="margin-bottom:3px;">
          <tr>
            <td style="width:36px;vertical-align:top;padding-top:4px;">
              <span style="background:${impactColor}20;color:${impactColor};font-size:9px;font-weight:700;padding:2px 4px;border-radius:3px;white-space:nowrap;">${isHigh ? "HIGH" : "MED"}</span>
            </td>
            <td style="color:#9ca3af;font-size:13px;line-height:1.5;padding:3px 0;">${cleanLine}</td>
          </tr>
        </table>`;
      continue;
    }

    // Default plain line
    html += `<div style="color:#9ca3af;font-size:13px;line-height:1.7;padding:4px 0;margin-bottom:2px;">${highlightTickers(line)}</div>`;
  }

  if (earningsTableOpen) html += `</table>`;

  return html;
}

// ── Full email HTML builder ───────────────────────────────────────────────────

export function buildHtml(payload: EodSummaryPayload): string {
  const { marketDate, tape, rawText, items, gainers, losers, generatedAt } = payload;

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

  const itemsHtml =
    items.length > 0
      ? items.map((item) => buildItemRow(item.text, item.number)).join("")
      : rawText
      ? `<tr><td style="padding:20px 0;color:#9ca3af;font-size:14px;line-height:1.7;white-space:pre-line;">${highlightTickers(rawText)}</td></tr>`
      : `<tr><td style="padding:20px 0;color:#6b7280;">No summary data available.</td></tr>`;

  const watchRaw = extractWatchTomorrow(rawText);
  const watchBodyHtml = watchRaw ? renderWatchSection(watchRaw) : "";

  const watchHtml = watchRaw
    ? `
  <tr>
    <td style="background:#0d1117;padding:20px 32px 24px;border-top:1px solid #1f2937;">
      <div style="display:inline-block;background:#1c1007;border:1px solid #431407;border-radius:6px;padding:4px 12px;margin-bottom:2px;">
        <span style="color:#f97316;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">Watch Tomorrow</span>
      </div>
      ${watchBodyHtml}
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
        <td style="background:#0d1117;padding:20px 32px;border-bottom:1px solid #1f2937;">
          <table cellpadding="0" cellspacing="0" width="100%">
            <tr>${tapeHtml}</tr>
          </table>
        </td>
      </tr>

      <!-- Movers -->
      ${buildMoversSection(gainers ?? [], losers ?? [])}

      <!-- Summary Items -->
      <tr>
        <td style="background:#0d1117;padding:8px 32px 0;">
          <table width="100%" cellpadding="0" cellspacing="0">
            ${itemsHtml}
          </table>
        </td>
      </tr>

      <!-- Watch Tomorrow -->
      ${watchHtml}

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
  const cronSecret = process.env.CRON_SECRET;
  if (cronSecret) {
    const auth = request.headers.get("authorization");
    if (auth !== `Bearer ${cronSecret}`) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  const payload = await generateEodSummary();

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
    gainerCount: payload.gainers?.length ?? 0,
    loserCount: payload.losers?.length ?? 0,
    marketDate: payload.marketDate,
    generatedAt: payload.generatedAt,
    ...(result.error ? { error: result.error } : {}),
  });
}
