// Customer-safe synthesis layer — builds a CustomerStory from MarketNowPayload.
// Separate from the operator-side buildMarketStory() in intelligence.ts which takes
// IntelTheme[] (operator format). This adapter works with the customer API payload only.
// No broker imports, no execution logic, no financial advice language.

import type { MarketNowPayload, ThemeItem } from "./customerApi";
import {
  getCrosswalkByMarketNow,
  getTtgIdForMarketNow,
  getMarketNowIdsByTtg,
  getAllCrosswalkEntries,
  type CrosswalkEntry,
} from "./themeCrosswalk";
import { translateTheme, driverExplanation } from "./translate";

export interface PrimaryDriver {
  label: string;
  explanation: string;
  linked_market_now_ids: string[];
  linked_ttg_id: string | null;
  linked_ttg_label: string | null;
}

export interface MappedStructural {
  marketNowId: string;
  marketNowLabel: string;
  ttgId: string;
  ttgLabel: string;
  relationship: string;
}

export interface CustomerStory {
  headline: string;
  summary: string;
  market_state: "risk-on" | "monitoring" | "risk-off" | "mixed";
  freshness_label: string;
  evidence_mode: "live" | "structural" | "last-known";
  primary_drivers: PrimaryDriver[];
  active_theme_count: number;
  building_theme_count: number;
  weakening_theme_count: number;
  dormant_theme_count: number;
  mapped_structural: MappedStructural[];
  what_changed: string[];
  watch_next: string[];
  has_live_events: boolean;
}

// Driver ID to display label map — customer-friendly language, no internal IDs
const DRIVER_LABELS: Record<string, string> = {
  ai_capex_growth:          "AI Infrastructure Spending",
  ai_compute_demand:        "AI Compute Demand",
  geopolitical_risk_rising: "Geopolitical Risk",
  small_cap_risk_on:        "Improving Risk Appetite",
  futures_risk_on:          "Overnight Market Optimism",
  futures_risk_off:         "Overnight Market Caution",
  yields_falling:           "Falling Interest Rates",
  yields_rising:            "Rising Interest Rates",
  risk_on_rotation:         "Growth Stock Rotation",
  gold_safe_haven_bid:      "Gold Safe-Haven Demand",
  credit_stress_easing:     "Easing Credit Conditions",
  oil_supply_shock:         "Oil Supply Disruption",
  smh_tactical_weakness:    "Semiconductor Near-Term Fatigue",
  reits_falling_yield:      "Rising Yields Pressuring Real Estate",
};

// Maps driver IDs to which market_now theme IDs they activate
const DRIVER_TO_MARKET_NOW: Record<string, string[]> = {
  ai_capex_growth: ["data_centre_power", "semiconductors", "ai_compute_infrastructure", "memory_storage"],
  ai_compute_demand: ["ai_compute_demand", "ai_compute_infrastructure", "data_centre_power"],
  geopolitical_risk_rising: ["defence", "defence_aerospace", "cybersecurity", "gold_safe_haven_bid"],
  small_cap_risk_on: ["small_cap_risk_on", "small_caps", "biotech_risk_on"],
  futures_risk_on: ["risk_on_rotation", "small_cap_risk_on"],
  futures_risk_off: [],
  yields_falling: ["yields_falling", "reits", "regional_banks"],
  yields_rising: ["yields_rising", "reits_falling_yield"],
  risk_on_rotation: ["consumer_discretionary", "travel_leisure", "mega_cap_platforms"],
  gold_safe_haven_bid: ["gold_safe_haven_bid", "gold_precious_metals"],
  credit_stress_easing: ["regional_banks", "risk_on_rotation"],
  oil_supply_shock: ["energy", "oil_supply_shock", "travel_leisure"],
};

// Concise driver explanation for customer display
const DRIVER_EXPLANATIONS: Record<string, string> = {
  ai_capex_growth: "Hyperscaler and enterprise AI spending is accelerating, driving demand for compute, power, and infrastructure.",
  ai_compute_demand: "Model training and inference are scaling rapidly, activating energy, semiconductor, and data centre names.",
  geopolitical_risk_rising: "Elevated international tensions are increasing defence budgets and gold safe-haven demand.",
  small_cap_risk_on: "Risk appetite is shifting toward smaller companies, signalling improving growth sentiment.",
  futures_risk_on: "Equity futures are pointing higher, indicating positive overnight risk appetite.",
  futures_risk_off: "Equity futures are under pressure, indicating cautious overnight risk sentiment.",
  yields_falling: "Falling bond yields benefit rate-sensitive sectors including housing and infrastructure.",
  yields_rising: "Rising yields are creating headwinds for rate-sensitive names and long-duration assets.",
  risk_on_rotation: "Market participants are rotating into higher-beta cyclical and growth names.",
  gold_safe_haven_bid: "Real asset demand is elevated as investors seek protection from uncertainty.",
  credit_stress_easing: "Credit conditions are loosening, reducing risk premiums for financials and cyclicals.",
  oil_supply_shock: "Oil supply disruption is affecting energy names and creating cost pressure for travel.",
  smh_tactical_weakness: "Semiconductor momentum is showing near-term fatigue despite the structural AI demand backdrop.",
  reits_falling_yield: "Yield compression is benefiting real estate and infrastructure via cap rate improvement.",
};

function resolveMarketState(
  themes: ThemeItem[],
  key_drivers: string[],
  market_mood?: string,
): "risk-on" | "monitoring" | "risk-off" | "mixed" {
  const activeThemes = themes.filter(t => t.state === "active" || t.state === "activated" || t.state === "strengthening");
  const hasRiskOn = key_drivers.some(d =>
    ["futures_risk_on", "risk_on_rotation", "small_cap_risk_on", "credit_stress_easing"].includes(d),
  );
  const hasRiskOff = key_drivers.some(d =>
    ["futures_risk_off", "yields_rising", "oil_supply_shock"].includes(d),
  );

  if (market_mood) {
    const m = market_mood.toLowerCase();
    // "mixed" must be checked before "risk-on" — a string like
    // "Mixed — risk-on momentum with active headwinds" is explicitly mixed,
    // not purely risk-on.
    if (m.includes("mixed")) return "mixed";
    if (m.includes("risk-off") || m.includes("risk off")) return "risk-off";
    if (m.includes("risk-on") || m.includes("risk on")) return "risk-on";
    // Regime label passthrough from the manifest (e.g. "Trending up", "Risk-on —
    // equities trending higher") when the driver heuristic is bypassed.
    if (m.includes("trending up") || m.includes("uptrend")) return "risk-on";
    if (m.includes("trending down") || m.includes("downtrend") || m.includes("bear market")) return "risk-off";
    if (m.includes("range-bound") || m.includes("choppy") || m.includes("neutral")) return "monitoring";
  }

  if (hasRiskOn && hasRiskOff) return "mixed";
  if (hasRiskOn) return "risk-on";
  if (hasRiskOff) return "risk-off";
  if (activeThemes.length >= 3) return "risk-on";
  return "monitoring";
}

function synthesiseHeadline(
  market_state: "risk-on" | "monitoring" | "risk-off" | "mixed",
  key_drivers: string[],
  activeThemeCount: number,
): string {
  const topDrivers = key_drivers.slice(0, 2).map(d => DRIVER_LABELS[d] ?? d).filter(Boolean);

  if (market_state === "risk-on") {
    if (topDrivers.length >= 2) {
      return `${topDrivers[0]} and ${topDrivers[1]} are the primary forces today.`;
    }
    if (topDrivers.length === 1) {
      return `${topDrivers[0]} is the primary force today.`;
    }
    return `${activeThemeCount} structural theme${activeThemeCount !== 1 ? "s" : ""} are active.`;
  }

  if (market_state === "risk-off") {
    if (topDrivers.length >= 1) {
      return `${topDrivers[0]} is generating caution across markets.`;
    }
    return "Defensive signals are dominant — risk appetite is reduced.";
  }

  if (market_state === "mixed") {
    return "Conflicting signals — risk-on and risk-off forces are both active.";
  }

  // monitoring
  if (activeThemeCount > 0) {
    return `${activeThemeCount} structural theme${activeThemeCount !== 1 ? "s" : ""} in focus — no dominant force confirmed yet.`;
  }
  return "No dominant market force has emerged today — markets are quiet.";
}

function synthesiseSummary(
  market_state: "risk-on" | "monitoring" | "risk-off" | "mixed",
  key_drivers: string[],
  plain_english_summary: string | undefined,
  activeThemeCount: number,
  mappedStructural: MappedStructural[],
): string {
  // Prefer the API summary if it looks like real content (not the fallback phrase)
  const fallbackPhrases = ["assessing market", "gathering", "check back"];
  const apiSummary = plain_english_summary?.trim();
  if (apiSummary && !fallbackPhrases.some(f => apiSummary.toLowerCase().includes(f))) {
    return apiSummary;
  }

  const driverLabels = key_drivers.slice(0, 2).map(d => DRIVER_LABELS[d] ?? d).filter(Boolean);
  const structLabels = [...new Set(mappedStructural.map(m => m.ttgLabel))].slice(0, 3);

  if (market_state === "risk-on") {
    if (driverLabels.length > 0 && structLabels.length > 0) {
      const dText = driverLabels.length === 1 ? driverLabels[0] : `${driverLabels[0]} and ${driverLabels[1]}`;
      return `${dText} ${driverLabels.length === 1 ? "is" : "are"} driving activity in ${structLabels.join(", ")} names.`;
    }
    if (driverLabels.length > 0) {
      const dText = driverLabels.length === 1 ? driverLabels[0] : `${driverLabels[0]} and ${driverLabels[1]}`;
      return `${dText} ${driverLabels.length === 1 ? "is" : "are"} the active force — structural themes are building.`;
    }
    return `${activeThemeCount} structural theme${activeThemeCount !== 1 ? "s" : ""} are active. Check the Theme Map for details.`;
  }

  if (market_state === "risk-off") {
    const dText = driverLabels[0] ?? "Defensive conditions";
    return `${dText} is creating caution. Structural themes remain valid context — market conditions are challenging near-term.`;
  }

  if (market_state === "mixed") {
    return "The market is showing conflicting signals — some forces are supportive while others are cautionary.";
  }

  return "Structural themes are in focus. Check the Theme Map for context on active names.";
}

// Pattern-match a human-readable driver label to a TTG structural theme ID.
// Used when the API returns labels instead of internal IDs.
function ttgIdFromDriverLabel(label: string): string | null {
  const l = label.toLowerCase();
  if (l.includes("ai") || l.includes("data centre") || l.includes("data center") || l.includes("compute") || l.includes("semiconductor") || l.includes("memory") || l.includes("capex"))
    return "ai_energy_nuclear";
  if (l.includes("defence") || l.includes("defense") || l.includes("geopolit") || l.includes("rearm") || l.includes("aerospace"))
    return "defence_rearmament";
  if (l.includes("cyber") || l.includes("software") || l.includes("cloud"))
    return "cybersecurity_digital_resilience";
  if (l.includes("reshoring") || l.includes("industrial") || l.includes("onshoring"))
    return "reshoring_industrial_capex";
  if (l.includes("copper") || l.includes("mineral") || l.includes("electrif"))
    return "critical_minerals_copper";
  if (l.includes("gold") || l.includes("real asset") || l.includes("precious"))
    return "gold_real_assets";
  if (l.includes("glp") || l.includes("biotech") || l.includes("health") || l.includes("metabolic"))
    return "glp1_metabolic_health";
  if (l.includes("housing") || l.includes("reit") || l.includes("yield") || l.includes("rate"))
    return "housing_rate_sensitivity";
  return null;
}

function buildPrimaryDrivers(key_drivers: string[]): PrimaryDriver[] {
  return key_drivers.slice(0, 5).map(driverRaw => {
    // key_drivers may be internal IDs ("ai_capex_growth") or human-readable labels
    // from the live API ("AI capital spending cycle expanding"). Handle both.
    const byId = DRIVER_TO_MARKET_NOW[driverRaw] ?? [];

    let primaryTtgId: string | null = null;
    let linkedMarketNowIds: string[] = byId;

    if (byId.length > 0) {
      // ID-based lookup succeeded — derive TTG from market_now IDs
      const ttgIds = byId.map(getTtgIdForMarketNow).filter((id): id is string => id !== null);
      primaryTtgId = ttgIds[0] ?? null;
    } else {
      // Label-based: pattern-match directly to TTG
      primaryTtgId = ttgIdFromDriverLabel(driverRaw);
      if (primaryTtgId) {
        linkedMarketNowIds = getMarketNowIdsByTtg(primaryTtgId).map(e => e.marketNowId);
      }
    }

    const ttgLabel = primaryTtgId
      ? (getAllCrosswalkEntries().find(e => e.ttgPrimary === primaryTtgId)?.ttgPrimaryLabel ?? null)
      : null;

    return {
      label: DRIVER_LABELS[driverRaw] ?? driverRaw,
      explanation: DRIVER_EXPLANATIONS[driverRaw] ?? driverExplanation(driverRaw),
      linked_market_now_ids: linkedMarketNowIds,
      linked_ttg_id: primaryTtgId,
      linked_ttg_label: ttgLabel,
    };
  });
}

function buildMappedStructural(themes: ThemeItem[]): MappedStructural[] {
  const seen = new Set<string>();
  const result: MappedStructural[] = [];

  for (const theme of themes) {
    const entry: CrosswalkEntry | null = getCrosswalkByMarketNow(theme.theme);
    if (!entry) continue;
    const key = `${theme.theme}:${entry.ttgPrimary}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({
      marketNowId: theme.theme,
      marketNowLabel: translateTheme(theme.theme),
      ttgId: entry.ttgPrimary,
      ttgLabel: entry.ttgPrimaryLabel,
      relationship: entry.relationship,
    });
  }

  return result;
}

function resolveFreshnessLabel(freshness_timestamp?: string): string {
  if (!freshness_timestamp) return "Freshness unknown";
  try {
    const ts = new Date(freshness_timestamp);
    const now = new Date();
    const diffMin = Math.round((now.getTime() - ts.getTime()) / 60000);
    if (diffMin < 2) return "Just updated";
    if (diffMin < 60) return `Updated ${diffMin}m ago`;
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return `Updated ${diffH}h ago`;
    return `Updated ${Math.round(diffH / 24)}d ago`;
  } catch {
    return "Freshness unknown";
  }
}

function resolveEvidenceMode(
  key_events: MarketNowPayload["key_events"],
  what_changed: string[],
): "live" | "structural" | "last-known" {
  if (key_events && key_events.length > 0) return "live";
  if (what_changed && what_changed.length > 0) return "live";
  return "structural";
}

export function buildCustomerStory(payload: MarketNowPayload): CustomerStory {
  const themes: ThemeItem[] = payload.themes ?? [];
  const key_drivers: string[] = payload.key_drivers ?? [];
  const what_changed = payload.what_changed ?? [];
  const watch_next = payload.watch_next ?? [];
  const key_events = payload.key_events ?? [];

  const activeThemes = themes.filter(t => t.state === "active" || t.state === "activated");
  const buildingThemes = themes.filter(t => t.state === "strengthening");
  const weakeningThemes = themes.filter(t => t.state === "weakening" || t.state === "headwind");
  const dormantThemes = themes.filter(
    t => !t.state || (t.state !== "active" && t.state !== "activated" && t.state !== "strengthening" && t.state !== "weakening" && t.state !== "headwind"),
  );

  const market_state = resolveMarketState(themes, key_drivers, payload.market_mood);
  const mappedStructural = buildMappedStructural(themes);
  const primary_drivers = buildPrimaryDrivers(key_drivers);

  const headline = synthesiseHeadline(market_state, key_drivers, activeThemes.length);
  const summary = synthesiseSummary(
    market_state,
    key_drivers,
    payload.plain_english_summary,
    activeThemes.length,
    mappedStructural,
  );

  return {
    headline,
    summary,
    market_state,
    freshness_label: resolveFreshnessLabel(payload.freshness_timestamp),
    evidence_mode: resolveEvidenceMode(key_events, what_changed),
    primary_drivers,
    active_theme_count: activeThemes.length,
    building_theme_count: buildingThemes.length,
    weakening_theme_count: weakeningThemes.length,
    dormant_theme_count: dormantThemes.length,
    mapped_structural: mappedStructural,
    what_changed,
    watch_next,
    has_live_events: key_events.length > 0,
  };
}
