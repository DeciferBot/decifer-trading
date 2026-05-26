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
  ai_energy_nuclear:           "AI Infrastructure & Energy",
  defence_rearmament:          "Defence & Security",
  cybersecurity_digital_resilience: "Cybersecurity",
  reshoring_industrial_capex:  "Industrial Reshoring & Capex",
  critical_minerals_copper:    "Critical Minerals",
  gold_real_assets:            "Gold & Real Assets",
  glp1_metabolic_health:       "Healthcare Innovation",
  housing_rate_sensitivity:    "Rate-Sensitive Names",
  water_infrastructure:        "Water Infrastructure",
  digital_assets_infrastructure: "Digital Assets",
};

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
      displayText: "Live price confirmation not available yet.",
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
    storyGroup: storyLabel,
    customerStory: card.theme_label,
    priceAction: buildPriceAction(priceEntry),
    fundamentals: { available: false, note: "Fundamentals snapshot not available yet." },
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
  const groups: ResearchStoryGroup[] = themes
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
    storyGroup: "Live Intelligence",
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
