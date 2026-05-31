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
  type OptionsFlowRow,
} from "@/app/api/eod-summary/route";

export const maxDuration = 90;

const RESEND_KEY = process.env.RESEND_API_KEY;
const RESEND_FROM =
  process.env.RESEND_FROM_EMAIL ?? "Decifer <noreply@decifertrading.com>";
const EOD_EMAIL_TO: string[] = (process.env.EOD_EMAIL_TO ?? "amit@decifer.io")
  .split(",")
  .map((e) => e.trim())
  .filter(Boolean);

// ── TTG symbol → theme map (compiled from data/intelligence/theme_graph/) ─────
// Short labels used in email chips. Update when TTG roster changes.

const TTG_THEME_SHORT: Record<string, string> = {
  "ai_energy_nuclear": "AI Energy",
  "glp1_metabolic_health": "GLP-1",
  "defence_rearmament": "Defence",
  "cybersecurity_digital_resilience": "Cybersecurity",
  "reshoring_industrial_capex": "Reshoring",
  "housing_rate_sensitivity": "Housing",
  "water_infrastructure": "Water Infra",
  "critical_minerals_copper": "Copper",
  "gold_real_assets": "Gold",
  "digital_assets_infrastructure": "Crypto Infra",
};

interface ThemeEntry { theme_id: string; exposure: string }

const TTG_SYMBOL_MAP: Record<string, ThemeEntry> = {
  NVDA: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  AMD: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  AVGO: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  MRVL: { theme_id: "ai_energy_nuclear", exposure: "supply_chain" },
  ANET: { theme_id: "ai_energy_nuclear", exposure: "supply_chain" },
  SMCI: { theme_id: "ai_energy_nuclear", exposure: "supply_chain" },
  DELL: { theme_id: "ai_energy_nuclear", exposure: "supply_chain" },
  VRT: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  ETN: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  PWR: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  EME: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  HUBB: { theme_id: "ai_energy_nuclear", exposure: "supply_chain" },
  CEG: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  VST: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  NEE: { theme_id: "ai_energy_nuclear", exposure: "2nd_order" },
  CCJ: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  UEC: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  UUUU: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  DNN: { theme_id: "ai_energy_nuclear", exposure: "2nd_order" },
  URA: { theme_id: "ai_energy_nuclear", exposure: "etf" },
  URNM: { theme_id: "ai_energy_nuclear", exposure: "etf" },
  LEU: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  BWXT: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  GEV: { theme_id: "ai_energy_nuclear", exposure: "direct" },
  SMR: { theme_id: "ai_energy_nuclear", exposure: "2nd_order" },
  NLR: { theme_id: "ai_energy_nuclear", exposure: "etf" },
  LLY: { theme_id: "glp1_metabolic_health", exposure: "direct" },
  NVO: { theme_id: "glp1_metabolic_health", exposure: "direct" },
  WST: { theme_id: "glp1_metabolic_health", exposure: "direct" },
  TMO: { theme_id: "glp1_metabolic_health", exposure: "supply_chain" },
  DHR: { theme_id: "glp1_metabolic_health", exposure: "supply_chain" },
  BDX: { theme_id: "glp1_metabolic_health", exposure: "supply_chain" },
  DXCM: { theme_id: "glp1_metabolic_health", exposure: "2nd_order" },
  PODD: { theme_id: "glp1_metabolic_health", exposure: "headwind" },
  UNH: { theme_id: "glp1_metabolic_health", exposure: "2nd_order" },
  MDLZ: { theme_id: "glp1_metabolic_health", exposure: "headwind" },
  HSY: { theme_id: "glp1_metabolic_health", exposure: "headwind" },
  KO: { theme_id: "glp1_metabolic_health", exposure: "headwind" },
  MCD: { theme_id: "glp1_metabolic_health", exposure: "headwind" },
  AMGN: { theme_id: "glp1_metabolic_health", exposure: "2nd_order" },
  LMT: { theme_id: "defence_rearmament", exposure: "direct" },
  RTX: { theme_id: "defence_rearmament", exposure: "direct" },
  NOC: { theme_id: "defence_rearmament", exposure: "direct" },
  GD: { theme_id: "defence_rearmament", exposure: "direct" },
  LHX: { theme_id: "defence_rearmament", exposure: "direct" },
  AVAV: { theme_id: "defence_rearmament", exposure: "direct" },
  KTOS: { theme_id: "defence_rearmament", exposure: "direct" },
  HII: { theme_id: "defence_rearmament", exposure: "direct" },
  RKLB: { theme_id: "defence_rearmament", exposure: "2nd_order" },
  PLTR: { theme_id: "defence_rearmament", exposure: "supply_chain" },
  ITA: { theme_id: "defence_rearmament", exposure: "etf" },
  XAR: { theme_id: "defence_rearmament", exposure: "etf" },
  PANW: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  CRWD: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  FTNT: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  ZS: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  NET: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  OKTA: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  CYBR: { theme_id: "cybersecurity_digital_resilience", exposure: "direct" },
  CIBR: { theme_id: "cybersecurity_digital_resilience", exposure: "etf" },
  HACK: { theme_id: "cybersecurity_digital_resilience", exposure: "etf" },
  AMAT: { theme_id: "reshoring_industrial_capex", exposure: "direct" },
  LRCX: { theme_id: "reshoring_industrial_capex", exposure: "direct" },
  KLAC: { theme_id: "reshoring_industrial_capex", exposure: "direct" },
  ASML: { theme_id: "reshoring_industrial_capex", exposure: "direct" },
  ROK: { theme_id: "reshoring_industrial_capex", exposure: "direct" },
  HON: { theme_id: "reshoring_industrial_capex", exposure: "supply_chain" },
  NUE: { theme_id: "reshoring_industrial_capex", exposure: "supply_chain" },
  VMC: { theme_id: "reshoring_industrial_capex", exposure: "supply_chain" },
  PAVE: { theme_id: "reshoring_industrial_capex", exposure: "etf" },
  DHI: { theme_id: "housing_rate_sensitivity", exposure: "direct" },
  LEN: { theme_id: "housing_rate_sensitivity", exposure: "direct" },
  PHM: { theme_id: "housing_rate_sensitivity", exposure: "direct" },
  NVR: { theme_id: "housing_rate_sensitivity", exposure: "direct" },
  BLDR: { theme_id: "housing_rate_sensitivity", exposure: "supply_chain" },
  OC: { theme_id: "housing_rate_sensitivity", exposure: "supply_chain" },
  HD: { theme_id: "housing_rate_sensitivity", exposure: "2nd_order" },
  LOW: { theme_id: "housing_rate_sensitivity", exposure: "2nd_order" },
  RKT: { theme_id: "housing_rate_sensitivity", exposure: "direct" },
  FNF: { theme_id: "housing_rate_sensitivity", exposure: "supply_chain" },
  ITB: { theme_id: "housing_rate_sensitivity", exposure: "etf" },
  XHB: { theme_id: "housing_rate_sensitivity", exposure: "etf" },
  AWK: { theme_id: "water_infrastructure", exposure: "direct" },
  WTRG: { theme_id: "water_infrastructure", exposure: "direct" },
  XYL: { theme_id: "water_infrastructure", exposure: "direct" },
  PNR: { theme_id: "water_infrastructure", exposure: "direct" },
  ITRI: { theme_id: "water_infrastructure", exposure: "direct" },
  ECL: { theme_id: "water_infrastructure", exposure: "direct" },
  PHO: { theme_id: "water_infrastructure", exposure: "etf" },
  FIW: { theme_id: "water_infrastructure", exposure: "etf" },
  FCX: { theme_id: "critical_minerals_copper", exposure: "direct" },
  COPX: { theme_id: "critical_minerals_copper", exposure: "etf" },
  SCCO: { theme_id: "critical_minerals_copper", exposure: "direct" },
  TECK: { theme_id: "critical_minerals_copper", exposure: "supply_chain" },
  ALB: { theme_id: "critical_minerals_copper", exposure: "direct" },
  SQM: { theme_id: "critical_minerals_copper", exposure: "direct" },
  MP: { theme_id: "critical_minerals_copper", exposure: "direct" },
  ICOP: { theme_id: "critical_minerals_copper", exposure: "etf" },
  LIT: { theme_id: "critical_minerals_copper", exposure: "etf" },
  NEM: { theme_id: "gold_real_assets", exposure: "direct" },
  GOLD: { theme_id: "gold_real_assets", exposure: "direct" },
  AEM: { theme_id: "gold_real_assets", exposure: "direct" },
  FNV: { theme_id: "gold_real_assets", exposure: "direct" },
  WPM: { theme_id: "gold_real_assets", exposure: "direct" },
  RGLD: { theme_id: "gold_real_assets", exposure: "direct" },
  GDX: { theme_id: "gold_real_assets", exposure: "etf" },
  GDXJ: { theme_id: "gold_real_assets", exposure: "etf" },
  IAU: { theme_id: "gold_real_assets", exposure: "etf" },
  IBIT: { theme_id: "digital_assets_infrastructure", exposure: "etf" },
  FBTC: { theme_id: "digital_assets_infrastructure", exposure: "etf" },
  GBTC: { theme_id: "digital_assets_infrastructure", exposure: "etf" },
  COIN: { theme_id: "digital_assets_infrastructure", exposure: "direct" },
  HOOD: { theme_id: "digital_assets_infrastructure", exposure: "2nd_order" },
  MSTR: { theme_id: "digital_assets_infrastructure", exposure: "direct" },
  MARA: { theme_id: "digital_assets_infrastructure", exposure: "direct" },
  RIOT: { theme_id: "digital_assets_infrastructure", exposure: "direct" },
  CLSK: { theme_id: "digital_assets_infrastructure", exposure: "2nd_order" },
  BLOK: { theme_id: "digital_assets_infrastructure", exposure: "etf" },
};

function getThemeLabel(symbol: string): string | null {
  const entry = TTG_SYMBOL_MAP[symbol];
  if (!entry) return null;
  return TTG_THEME_SHORT[entry.theme_id] ?? null;
}

function buildThemeChip(symbol: string): string {
  const label = getThemeLabel(symbol);
  if (!label) return "";
  return `<span style="display:inline-block;background:#1c2a1c;border:1px solid #2d4a2d;color:#86efac;font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;letter-spacing:0.3px;margin-top:2px;">${label}</span>`;
}

// ── Tape formatting ───────────────────────────────────────────────────────────

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v}%`;
}

function tapeColor(v: number | null, isVix = false): string {
  if (v === null) return "#6b7280";
  if (isVix) return v > 20 ? "#ef4444" : v > 15 ? "#f59e0b" : "#4ade80";
  return v >= 0 ? "#4ade80" : "#ef4444";
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

function highlightTickers(text: string, withTheme = false): string {
  return text.replace(/\$([A-Z]{1,5})\b/g, (_match, sym) => {
    const bold = `<strong style="color:#f97316;font-weight:700;">$${sym}</strong>`;
    if (!withTheme) return bold;
    const label = getThemeLabel(sym);
    if (!label) return bold;
    const chip = `<span style="display:inline-block;background:#1c2a1c;border:1px solid #2d4a2d;color:#86efac;font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;letter-spacing:0.3px;margin-left:3px;vertical-align:middle;">${label}</span>`;
    return bold + chip;
  });
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
            <div style="color:#6b7280;font-size:10px;overflow:hidden;white-space:nowrap;max-width:90px;text-overflow:ellipsis;">${m.name.slice(0, 20)}</div>
            ${buildThemeChip(m.symbol)}
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
  const highlighted = highlightTickers(cleanText, true);
  return `
  <tr>
    <td style="padding:14px 0;border-bottom:1px solid #1f2937;vertical-align:top;">
      <table cellpadding="0" cellspacing="0" width="100%"><tr>
        <td style="width:28px;vertical-align:top;padding-top:1px;">
          <div style="width:22px;height:22px;background:#1f2937;border-radius:50%;text-align:center;line-height:22px;color:#f97316;font-weight:700;font-size:11px;">${index}</div>
        </td>
        <td style="width:20px;vertical-align:top;padding-top:1px;padding-left:6px;font-size:14px;line-height:22px;">${icon}</td>
        <td style="color:#d1d5db;font-size:14px;line-height:1.6;padding-left:8px;">${highlighted}</td>
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
            <div style="color:#6b7280;font-size:10px;">${name}</div>
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

// ── Options flow section ──────────────────────────────────────────────────────

function buildOptionsFlowSection(rows: OptionsFlowRow[], source: string): string {
  if (!rows.length) return "";

  const sourceLabel = source === "friday_close" ? "Friday Close" : "Today";
  const rowsHtml = rows.map(r => {
    const side = r.unusual_calls && r.unusual_puts ? "BOTH"
      : r.unusual_calls ? "CALL" : "PUT";
    const sideColor = side === "CALL" ? "#22c55e" : side === "PUT" ? "#ef4444" : "#f59e0b";
    const exp = side === "CALL" ? r.call_expansion
      : side === "PUT" ? r.put_expansion
      : Math.max(r.call_expansion ?? 0, r.put_expansion ?? 0);
    const expStr = exp !== null ? `${exp.toFixed(1)}×` : "—";
    const theme = getThemeLabel(r.underlying);
    const themeChip = theme
      ? `<span style="display:inline-block;background:#1c2a1c;border:1px solid #2d4a2d;color:#86efac;font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;margin-left:4px;">${theme}</span>`
      : "";
    const totalVol = (r.call_volume + r.put_volume).toLocaleString();
    return `
    <tr>
      <td style="padding:6px 0;border-bottom:1px solid #0d1117;vertical-align:middle;">
        <span style="color:#f97316;font-weight:700;font-size:12px;font-family:'Courier New',monospace;">${r.underlying}</span>${themeChip}
      </td>
      <td style="padding:6px 8px;border-bottom:1px solid #0d1117;vertical-align:middle;">
        <span style="background:${sideColor}20;color:${sideColor};font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;">${side}</span>
      </td>
      <td style="padding:6px 0;border-bottom:1px solid #0d1117;color:#e5e7eb;font-size:12px;font-weight:700;text-align:right;white-space:nowrap;">${expStr}</td>
      <td style="padding:6px 0 6px 10px;border-bottom:1px solid #0d1117;color:#6b7280;font-size:11px;text-align:right;white-space:nowrap;">${totalVol} contracts</td>
    </tr>`;
  }).join("");

  return `
  <tr>
    <td style="background:#0d1117;padding:16px 32px 20px;border-top:1px solid #1f2937;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
        <div style="display:inline-block;background:#1a0f1a;border:1px solid #3d1f3d;border-radius:6px;padding:4px 12px;">
          <span style="color:#c084fc;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">&#9889; Unusual Options Flow</span>
        </div>
        <span style="color:#4b5563;font-size:10px;">${sourceLabel} &nbsp;&bull;&nbsp; ${rows.length} symbols</span>
      </div>
      <table cellpadding="0" cellspacing="0" width="100%">
        <thead>
          <tr>
            <th align="left" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 0 8px;font-weight:600;border-bottom:1px solid #1f2937;">Symbol</th>
            <th align="left" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 8px 8px;font-weight:600;border-bottom:1px solid #1f2937;">Side</th>
            <th align="right" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 0 8px;font-weight:600;border-bottom:1px solid #1f2937;">Expansion</th>
            <th align="right" style="color:#374151;font-size:9px;text-transform:uppercase;letter-spacing:0.8px;padding:0 0 8px 10px;font-weight:600;border-bottom:1px solid #1f2937;">Volume</th>
          </tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </td>
  </tr>`;
}

// ── Full email HTML builder ───────────────────────────────────────────────────

export function buildHtml(payload: EodSummaryPayload): string {
  const { marketDate, tape, rawText, items, gainers, losers, optionsFlow, optionsFlowSource, generatedAt } = payload;

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

      <!-- Options Flow -->
      ${buildOptionsFlowSection(optionsFlow ?? [], optionsFlowSource ?? "unavailable")}

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
          <div style="color:#6b7280;font-size:12px;line-height:1.6;">Generated ${generatedStr} &nbsp;·&nbsp; Decifer Trading &nbsp;·&nbsp; Paper account only</div>
          <div style="color:#4b5563;font-size:11px;margin-top:4px;">Data: FMP &amp; Alpaca &nbsp;·&nbsp; Synthesis: Claude Sonnet &nbsp;·&nbsp; Not financial advice.</div>
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
        to: EOD_EMAIL_TO,
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
