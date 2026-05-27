// Pure-function model layer for name-level research cards.
// No React, no API calls, no Next.js dependencies. Fully testable in vitest.
// Customer-safe language only — no execution, broker, or trading-control terms.

import type { TtgSymbolCard, TtgThemeDetail, RadarItem } from "@/lib/customerApi";
import type { NamePriceEntry } from "@/lib/namePriceUtils";

// ── Types ─────────────────────────────────────────────────────────────────────

export type PriceActionTone = "positive" | "neutral" | "negative" | "unknown";

export type WatchType = "Catalyst watch" | "Structural watch" | "Market attention";

export interface ResearchPriceAction {
  tone: PriceActionTone;
  changePct: number | null;
  price: number | null;
  displayText: string;
}

export interface ResearchFundamentals {
  available: boolean;
  note: string;
}

export interface ResearchNameCard {
  symbol: string;
  companyName: string;
  logoUrl?: string;
  storyGroup: string;
  customerStory: string;
  priceAction: ResearchPriceAction;
  fundamentals: ResearchFundamentals;
  reasonToCare: string;
  watchType: WatchType;
  confidenceLanguage: string;
  isPressure: boolean;
  themeId: string;
  ttgThemeId: string;
  driverActive: boolean;
  riskNote: string | null;
}

export interface ResearchStoryGroup {
  storyLabel: string;
  themeId: string;
  driverActive: boolean;
  cards: ResearchNameCard[];
}

// ── Story label map ────────────────────────────────────────────────────────────

export const TTG_STORY_LABELS: Record<string, string> = {
  // Backend theme IDs (10 structural themes from the TTG)
  ai_energy_nuclear:                "Energy & Nuclear",   // fallback for unclassified ai_energy_nuclear cards
  defence_rearmament:               "Defence",
  cybersecurity_digital_resilience: "Cybersecurity",
  reshoring_industrial_capex:       "Industrial Reshoring",
  critical_minerals_copper:         "Critical Minerals",
  gold_real_assets:                 "Gold",
  glp1_metabolic_health:            "Healthcare Innovation",
  housing_rate_sensitivity:         "Rate-Sensitive Names",
  water_infrastructure:             "Water Infrastructure",
  digital_assets_infrastructure:    "Digital Assets",
  // Virtual split IDs — produced when ai_energy_nuclear is split by bucket in buildStoryGroups
  ai_energy_nuclear_ai:             "AI Infrastructure",
  ai_energy_nuclear_energy:         "Energy & Nuclear",
};

// Buckets that belong to the AI compute side of the ai_energy_nuclear theme
const AI_INFRA_BUCKETS = new Set(["ai_compute_accelerators_networking"]);

// ── Price action ──────────────────────────────────────────────────────────────

export function derivePriceActionTone(changePct: number | null): PriceActionTone {
  if (changePct === null || changePct === undefined) return "unknown";
  if (changePct > 0.75) return "positive";
  if (changePct < -0.75) return "negative";
  return "neutral";
}

export function buildPriceAction(entry?: NamePriceEntry | null): ResearchPriceAction {
  if (!entry || entry.changePct === null) {
    return {
      tone: "unknown",
      changePct: null,
      price: entry?.price ?? null,
      displayText: "Price updating…",
    };
  }
  const tone = derivePriceActionTone(entry.changePct);
  let displayText: string;
  if (tone === "positive") {
    displayText = `Up ${Math.abs(entry.changePct).toFixed(1)}% today`;
  } else if (tone === "negative") {
    displayText = `Down ${Math.abs(entry.changePct).toFixed(1)}% today`;
  } else {
    displayText = "Flat today";
  }
  return { tone, changePct: entry.changePct, price: entry.price, displayText };
}

// ── Watch type ────────────────────────────────────────────────────────────────

export function resolveWatchType(card: TtgSymbolCard): WatchType {
  const hint = card.route_hint.toLowerCase();
  const isInFocus = hint === "in focus";
  const isEtf = hint.includes("etf");
  if (card.driver_active && isInFocus) return "Catalyst watch";
  if (isInFocus || isEtf) return "Structural watch";
  return "Market attention";
}

// ── Confidence language ───────────────────────────────────────────────────────

function resolveConfidenceLanguage(exposureType: string): string {
  const map: Record<string, string> = {
    direct_beneficiary:       "Directly connected",
    supply_chain_beneficiary: "Supply chain exposure",
    second_order_beneficiary: "Indirect exposure",
    etf_basket:               "ETF basket exposure",
    pressure_or_negative:     "Potential pressure point",
  };
  return map[exposureType] ?? "Connected name";
}

// ── Logo URL (FMP public CDN — no API key required) ───────────────────────────

export function buildLogoUrl(symbol: string): string {
  return `https://images.financialmodelingprep.com/symbol/${symbol.toUpperCase()}.png`;
}

// ── Card builder ──────────────────────────────────────────────────────────────

export function buildResearchCard(
  card: TtgSymbolCard,
  priceMap: Map<string, NamePriceEntry>,
  storyLabel: string,
): ResearchNameCard {
  const priceEntry = priceMap.get(card.symbol) ?? null;
  return {
    symbol: card.symbol,
    companyName: card.label || card.symbol,
    logoUrl: buildLogoUrl(card.symbol),
    storyGroup: storyLabel,
    customerStory: card.theme_label,
    priceAction: buildPriceAction(priceEntry),
    fundamentals: { available: false, note: "Fundamentals not available." },
    reasonToCare: card.reason_to_care,
    watchType: resolveWatchType(card),
    confidenceLanguage: resolveConfidenceLanguage(card.exposure_type),
    isPressure: card.exposure_type === "pressure_or_negative",
    themeId: card.theme_id,
    ttgThemeId: card.theme_id,
    driverActive: card.driver_active,
    riskNote: card.risk_note ?? null,
  };
}

// ── Story groups ──────────────────────────────────────────────────────────────

export function buildStoryGroups(
  themes: TtgThemeDetail[],
  priceMap: Map<string, NamePriceEntry>,
): ResearchStoryGroup[] {
  // Expand themes — ai_energy_nuclear is split into AI Infrastructure and Energy & Nuclear
  const expanded: TtgThemeDetail[] = [];
  for (const theme of themes) {
    if (theme.theme_id === "ai_energy_nuclear") {
      const aiSymbols = theme.symbols.filter(s => AI_INFRA_BUCKETS.has(s.bucket_id));
      const energySymbols = theme.symbols.filter(s => !AI_INFRA_BUCKETS.has(s.bucket_id));
      if (aiSymbols.length > 0) {
        expanded.push({ ...theme, theme_id: "ai_energy_nuclear_ai", symbols: aiSymbols, symbol_count: aiSymbols.length });
      }
      if (energySymbols.length > 0) {
        expanded.push({ ...theme, theme_id: "ai_energy_nuclear_energy", symbols: energySymbols, symbol_count: energySymbols.length });
      }
    } else {
      expanded.push(theme);
    }
  }

  const groups: ResearchStoryGroup[] = expanded
    .map(theme => {
      const storyLabel = TTG_STORY_LABELS[theme.theme_id] ?? theme.label;
      const cards = theme.symbols.map(card => buildResearchCard(card, priceMap, storyLabel));
      return {
        storyLabel,
        themeId: theme.theme_id,
        driverActive: theme.driver_active,
        cards,
      };
    })
    .filter(g => g.cards.length > 0);

  return groups.sort((a, b) => {
    if (a.driverActive && !b.driverActive) return -1;
    if (!a.driverActive && b.driverActive) return 1;
    return 0;
  });
}

// ── Radar group (live intelligence items) ─────────────────────────────────────

export function buildRadarCards(
  radarItems: RadarItem[],
  priceMap: Map<string, NamePriceEntry>,
): ResearchNameCard[] {
  return radarItems.map(item => ({
    symbol: item.symbol,
    companyName: item.symbol,
    logoUrl: buildLogoUrl(item.symbol),
    storyGroup: "On the radar",
    customerStory: "Current market attention",
    priceAction: buildPriceAction(priceMap.get(item.symbol) ?? null),
    fundamentals: { available: false, note: "Fundamentals snapshot not available yet." },
    reasonToCare: item.reason_to_watch,
    watchType: "Market attention",
    confidenceLanguage: "Live intelligence",
    isPressure: false,
    themeId: "",
    ttgThemeId: "",
    driverActive: false,
    riskNote: null,
  }));
}

// ── Fundamentals response type (mirrors /api/name-fundamentals shape) ─────────

export interface NameFundamentalsResponse {
  symbol: string;
  ts: string;
  profile?: {
    companyName?: string;
    sector?: string;
    industry?: string;
    marketCap?: number;
  };
  fundamentals?: {
    revenue?: number;
    eps?: number;
    peRatio?: number;
    grossMargin?: number;
    revenueGrowth?: number;
  };
  analyst?: {
    consensus?: string;
    priceTarget?: number;
    ratingCount?: number;
  };
  available: boolean;
  source: "fmp" | "none";
}

// ── Market cap formatting ─────────────────────────────────────────────────────

export function formatMarketCap(marketCap?: number): string {
  if (!marketCap || marketCap <= 0) return "Market cap not available.";
  if (marketCap >= 1e12) return `$${(marketCap / 1e12).toFixed(1)} trillion`;
  if (marketCap >= 1e9) return `$${Math.round(marketCap / 1e9)} billion`;
  if (marketCap >= 1e6) return `$${Math.round(marketCap / 1e6)} million`;
  return `$${marketCap.toLocaleString()}`;
}

// ── Company identity line ─────────────────────────────────────────────────────

export function buildCompanyLine(
  symbol: string,
  profile?: NameFundamentalsResponse["profile"],
  storyGroup?: string,
): string {
  if (!profile?.companyName) {
    const storyPart = storyGroup ? ` in the ${storyGroup} story` : "";
    return `${symbol} is on our radar${storyPart}.`;
  }
  const industryPart =
    profile.sector && profile.industry
      ? ` · ${profile.industry}`
      : profile.sector
        ? ` · ${profile.sector}`
        : "";
  const capPart = profile.marketCap ? ` · ${formatMarketCap(profile.marketCap)}` : "";
  return `${profile.companyName}${industryPart}${capPart}.`;
}

// ── Fundamentals context line ─────────────────────────────────────────────────

export function buildFundamentalsLine(
  fundamentals?: NameFundamentalsResponse["fundamentals"],
): string {
  if (!fundamentals) {
    return "Fundamentals context is not available for this name.";
  }

  const parts: string[] = [];

  // Revenue growth first — most forward-looking
  if (fundamentals.revenueGrowth != null) {
    const pct = Math.abs(fundamentals.revenueGrowth * 100).toFixed(0);
    const direction = fundamentals.revenueGrowth >= 0 ? "growing" : "contracting";
    parts.push(`revenue ${direction} ${pct}% year over year`);
  }
  // Gross margin
  if (fundamentals.grossMargin != null && fundamentals.grossMargin > 0) {
    const marginPct = (fundamentals.grossMargin * 100).toFixed(0);
    parts.push(`gross margin around ${marginPct}%`);
  }
  // EPS
  if (fundamentals.eps != null) {
    const epsStr =
      fundamentals.eps >= 0
        ? `$${fundamentals.eps.toFixed(2)}`
        : `-$${Math.abs(fundamentals.eps).toFixed(2)}`;
    parts.push(`trailing earnings per share ${epsStr}`);
  }
  // Valuation last
  if (fundamentals.peRatio != null && fundamentals.peRatio > 0) {
    parts.push(`trading at approximately ${fundamentals.peRatio.toFixed(0)}× trailing earnings`);
  }

  if (parts.length === 0) {
    return "Detailed financial context is not available from the current data source.";
  }

  let joined: string;
  if (parts.length === 1) {
    joined = parts[0];
  } else if (parts.length === 2) {
    joined = `${parts[0]}, and ${parts[1]}`;
  } else {
    joined = `${parts.slice(0, -1).join(", ")}, and ${parts[parts.length - 1]}`;
  }
  const cap = joined.charAt(0).toUpperCase() + joined.slice(1);
  return `${cap}. Trailing figures — may not reflect recent guidance.`;
}

// ── Analyst context line ──────────────────────────────────────────────────────

function normaliseConsensus(raw?: string): string | null {
  if (!raw) return null;
  const r = raw.toLowerCase();
  if (r.includes("strong buy") || r.includes("outperform")) return "broadly positive";
  if (r.includes("buy") || r.includes("overweight")) return "generally positive";
  if (r.includes("neutral") || r.includes("hold") || r.includes("equal weight")) return "mixed";
  if (r.includes("underweight") || r.includes("underperform") || r.includes("sell")) return "cautious";
  return null;
}

export function buildAnalystLine(
  analyst?: NameFundamentalsResponse["analyst"],
): string {
  if (!analyst || (!analyst.consensus && !analyst.priceTarget && !analyst.ratingCount)) {
    return "Analyst context is not available — the story here leans more on price action and theme exposure.";
  }

  const coverageParts: string[] = [];
  if (analyst.ratingCount) coverageParts.push(`${analyst.ratingCount} analysts on record`);
  const sentiment = normaliseConsensus(analyst.consensus);
  if (sentiment) coverageParts.push(`sentiment ${sentiment}`);

  const sentence1 = coverageParts.length > 0
    ? `Analyst coverage shows ${coverageParts.join(", ")}.`
    : "";

  const sentence2 = analyst.priceTarget
    ? `Price context is around $${analyst.priceTarget.toFixed(0)}.`
    : "";

  if (!sentence1 && !sentence2) {
    return "Some analyst context is on record, though a detailed breakdown is not available.";
  }

  const caveat = "Market context only — not a recommendation.";
  return [sentence1, sentence2, caveat].filter(Boolean).join(" ");
}

// ── Connection phrase map (used by buildWhyItMattersNow) ─────────────────────

const CONNECTION_PHRASES: Record<string, string> = {
  "Directly connected":       "appears as a direct exposure",
  "Supply chain exposure":    "has supply chain exposure",
  "Indirect exposure":        "has indirect exposure",
  "ETF basket exposure":      "is part of a broader basket",
  "Potential pressure point": "may face headwinds",
};

// ── Company-aware narrative intro ─────────────────────────────────────────────

export function buildWhyItMattersNow(
  symbol: string,
  storyGroup: string,
  reasonToCare: string,
  options: {
    companyName?: string;
    confidenceLanguage?: string;
    watchType?: WatchType;
    driverActive?: boolean;
  } = {},
): string {
  const label = TTG_STORY_LABELS[storyGroup] ?? storyGroup;
  const name =
    options.companyName && options.companyName !== symbol ? options.companyName : symbol;
  const connectionPhrase =
    (options.confidenceLanguage && CONNECTION_PHRASES[options.confidenceLanguage]) ??
    "is connected";

  const intro = `${name} ${connectionPhrase} within the ${label} story.`;

  const driverNote = options.driverActive ? " The underlying driver is currently active." : "";

  const watchNote =
    options.watchType === "Catalyst watch"
      ? " A catalyst event may be in play for this name."
      : options.watchType === "Structural watch"
        ? " The connection is structural — longer-term exposure rather than a near-term event."
        : "";

  return `${intro} ${reasonToCare}${driverNote}${watchNote}`.trim();
}

// ── Company-aware risk note ───────────────────────────────────────────────────

export function buildRiskNoteLine(
  symbol: string,
  riskNote: string,
  companyName?: string,
): string {
  // Only prefix when a real company name distinct from the symbol is available
  const label = companyName && companyName !== symbol ? companyName : null;
  if (!label) return riskNote;
  // Don't double-prefix if the note already names the company or symbol
  const noteLC = riskNote.toLowerCase();
  if (noteLC.includes(label.toLowerCase()) || noteLC.includes(symbol.toLowerCase())) {
    return riskNote;
  }
  return `For ${label}: ${riskNote}`;
}

// ── Contextual Ask questions ──────────────────────────────────────────────────

export function buildDetailQuestions(
  symbol: string,
  storyGroup: string,
  companyName?: string,
): string[] {
  const label = TTG_STORY_LABELS[storyGroup] ?? storyGroup;
  const name = companyName && companyName !== symbol ? companyName : symbol;
  return [
    `Why is ${name} connected to the ${label} story?`,
    `What could weaken the ${symbol} setup from here?`,
    `Are ${symbol}'s current moves driven by fundamentals or broader market conditions?`,
    `How does ${symbol} compare with other names in the ${label} space?`,
  ];
}

// ── Fresh price merge ─────────────────────────────────────────────────────────

export function mergeFreshPrice(
  fresh: NamePriceEntry | null,
  existing: ResearchPriceAction,
): ResearchPriceAction {
  if (!fresh || fresh.changePct === null) return existing;
  return buildPriceAction(fresh);
}

// ── Price freshness label ─────────────────────────────────────────────────────

export function buildPriceFreshnessLabel(ts: string | null): string {
  if (!ts) return "";
  try {
    const ms = new Date(ts).getTime();
    if (isNaN(ms)) return "";
    const ageMins = Math.floor((Date.now() - ms) / 60000);
    if (ageMins < 2) return "Live";
    return `${ageMins}m ago`;
  } catch {
    return "";
  }
}

// ── Priority symbol list (for price fetch) ────────────────────────────────────

export function prioritySymbols(themes: TtgThemeDetail[], limit: number): string[] {
  const seen = new Set<string>();
  const result: string[] = [];

  const push = (sym: string) => {
    if (!seen.has(sym) && result.length < limit) {
      seen.add(sym);
      result.push(sym);
    }
  };

  // Tier 1: driver_active + in_focus
  for (const t of themes) {
    for (const s of t.symbols) {
      if (s.driver_active && s.route_hint.toLowerCase() === "in focus") push(s.symbol);
    }
  }
  // Tier 2: in_focus
  for (const t of themes) {
    for (const s of t.symbols) {
      if (s.route_hint.toLowerCase() === "in focus") push(s.symbol);
    }
  }
  // Tier 3: remainder
  for (const t of themes) {
    for (const s of t.symbols) push(s.symbol);
  }

  return result;
}
