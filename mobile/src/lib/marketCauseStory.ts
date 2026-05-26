// Customer-facing market cause story adapter.
// Converts active market drivers into MarketCauseCard objects for the
// "What is moving markets today" section in the Theme Map and Today tab.
// No internal IDs exposed in rendered text. No buy/sell/hold/order/broker language.

import type { MarketNowPayload } from "./customerApi";
import { getAllCrosswalkEntries, getMarketNowIdsByTtg } from "./themeCrosswalk";

export interface CauseConnectedTheme {
  ttgId: string;
  ttgLabel: string;
}

export interface MarketCauseCard {
  cause_label: string;
  what_happened: string;
  market_impact: string;
  connected_themes: CauseConnectedTheme[];
  evidence_basis: string;
  risk_to_monitor: string;
  connected_names_count: number;
  has_fresh_evidence: boolean;
  // Navigation targets — internal only, never rendered directly to the customer
  primary_ttg_id: string | null;
  primary_market_now_id: string | null;
}

// ── Customer-friendly cause labels (more descriptive than driver IDs) ──────────

const CAUSE_LABELS: Record<string, string> = {
  ai_capex_growth:          "AI Infrastructure Spending",
  ai_compute_demand:         "AI Compute Demand",
  geopolitical_risk_rising:  "Geopolitical Risk",
  small_cap_risk_on:         "Improving Risk Appetite",
  futures_risk_on:           "Overnight Market Optimism",
  futures_risk_off:          "Overnight Market Caution",
  yields_falling:            "Falling Interest Rates",
  yields_rising:             "Rising Interest Rates",
  risk_on_rotation:          "Growth Stock Rotation",
  gold_safe_haven_bid:       "Gold Safe-Haven Demand",
  credit_stress_easing:      "Easing Credit Conditions",
  oil_supply_shock:          "Oil Supply Disruption",
  smh_tactical_weakness:     "Semiconductor Near-Term Fatigue",
  reits_falling_yield:       "Rising Yields Pressuring Real Estate",
};

// ── What happened — one factual sentence per driver ───────────────────────────

const CAUSE_WHAT_HAPPENED: Record<string, string> = {
  ai_capex_growth:
    "Hyperscalers and enterprises are accelerating AI infrastructure investment, committing to record data centre build-outs.",
  ai_compute_demand:
    "Demand for AI model training and inference compute is scaling rapidly, outpacing available capacity.",
  geopolitical_risk_rising:
    "Elevated international tensions are sustaining higher defence budgets and safe-haven demand.",
  small_cap_risk_on:
    "Risk appetite is broadening to smaller companies, signalling improving growth and credit sentiment.",
  futures_risk_on:
    "Equity index futures are pointing higher, indicating positive overnight institutional positioning.",
  futures_risk_off:
    "Equity index futures are under pressure, indicating cautious overnight institutional positioning.",
  yields_falling:
    "Bond yields are declining as rate-cut expectations build or safe-haven demand rises.",
  yields_rising:
    "Bond yields are climbing, reflecting stronger inflation expectations or increased government borrowing.",
  risk_on_rotation:
    "Investors are moving out of defensive and cash positions into higher-growth and cyclical sectors.",
  gold_safe_haven_bid:
    "Demand for gold and real assets is elevated as investors seek a hedge against macro uncertainty.",
  credit_stress_easing:
    "Credit spreads are tightening, signalling improving corporate borrowing conditions and receding recession risk.",
  oil_supply_shock:
    "Oil supply disruption is elevating energy prices, affecting fuel costs and broader inflation expectations.",
  smh_tactical_weakness:
    "Semiconductor momentum is showing short-term fatigue despite the structural AI demand backdrop.",
  reits_falling_yield:
    "Rising long-term yields are compressing the yield spread that makes real estate attractive.",
};

// ── Market impact — one sentence explaining how markets are responding ─────────

const CAUSE_MARKET_IMPACT: Record<string, string> = {
  ai_capex_growth:
    "Benefiting energy, power infrastructure, semiconductor, and data centre names connected to the AI buildout.",
  ai_compute_demand:
    "Activating AI Energy & Nuclear, Semiconductor, and Data Centre structural themes.",
  geopolitical_risk_rising:
    "Benefiting defence contractors, cybersecurity, and gold; creating a risk premium in energy names.",
  small_cap_risk_on:
    "Small-cap and growth-oriented names are gaining as broader market breadth improves.",
  futures_risk_on:
    "Growth and cyclical themes are supported heading into the session.",
  futures_risk_off:
    "Defensive positioning is favoured — growth and higher-beta themes are facing near-term pressure.",
  yields_falling:
    "Benefiting rate-sensitive sectors including housing, real estate, and long-duration growth stocks.",
  yields_rising:
    "Creating headwinds for real estate, housing, and growth names with compressed valuations.",
  risk_on_rotation:
    "Consumer discretionary, mega-cap technology, and travel names are attracting capital.",
  gold_safe_haven_bid:
    "Gold and precious metals names are in demand; supporting the Gold & Real Assets structural theme.",
  credit_stress_easing:
    "Reducing risk premiums for financials, cyclicals, and credit-sensitive names.",
  oil_supply_shock:
    "Energy producers are benefiting while cost-sensitive industries — including airlines — face headwinds.",
  smh_tactical_weakness:
    "Near-term caution on semiconductor momentum names, even as the AI structural thesis remains intact.",
  reits_falling_yield:
    "Real estate investment trusts and rate-sensitive infrastructure names are under near-term pressure.",
};

// ── Risk to monitor — what could weaken this cause ────────────────────────────

const CAUSE_RISK: Record<string, string> = {
  ai_capex_growth:
    "A meaningful hyperscaler capex guidance cut or an AI demand slowdown would weaken this driver.",
  ai_compute_demand:
    "A slowdown in AI model training runs or a GPU demand pause would reduce the intensity of this story.",
  geopolitical_risk_rising:
    "A significant peace agreement, ceasefire, or de-escalation event would reduce the risk premium.",
  small_cap_risk_on:
    "A credit tightening event, recession signal, or broad risk-off rotation could reverse small-cap leadership.",
  futures_risk_on:
    "Overnight news events, economic data surprises, or Asian market weakness could reverse the signal.",
  futures_risk_off:
    "Positive economic data or a recovery in risk appetite during the session could neutralise this.",
  yields_falling:
    "Inflation re-acceleration forcing rates to stay elevated longer would reverse this driver.",
  yields_rising:
    "A downside inflation surprise or a shift in central bank guidance toward lower rates would weaken this.",
  risk_on_rotation:
    "A volatility spike, credit spread widening, or hawkish central bank communication could halt the rotation.",
  gold_safe_haven_bid:
    "Risk-on rotation, dollar strength, or declining inflation expectations would reduce gold demand.",
  credit_stress_easing:
    "A corporate default wave, banking stress event, or recession signal would widen spreads again.",
  oil_supply_shock:
    "Production increases, demand destruction from a global slowdown, or supply chain normalisation would ease prices.",
  smh_tactical_weakness:
    "Renewed AI-driven demand data or a positive earnings catalyst could reverse near-term semiconductor weakness.",
  reits_falling_yield:
    "Yields stabilising or reversing lower would restore yield spread and reduce pressure on real estate.",
};

// ── Driver → connected TTG structural theme IDs ───────────────────────────────

const DRIVER_CONNECTED_TTG: Record<string, string[]> = {
  ai_capex_growth:         ["ai_energy_nuclear"],
  ai_compute_demand:       ["ai_energy_nuclear"],
  geopolitical_risk_rising:["defence_rearmament", "cybersecurity_digital_resilience", "gold_real_assets"],
  small_cap_risk_on:       [],
  futures_risk_on:         [],
  futures_risk_off:        [],
  yields_falling:          ["housing_rate_sensitivity"],
  yields_rising:           ["housing_rate_sensitivity"],
  risk_on_rotation:        [],
  gold_safe_haven_bid:     ["gold_real_assets"],
  credit_stress_easing:    [],
  oil_supply_shock:        [],
  smh_tactical_weakness:   ["ai_energy_nuclear"],
  reits_falling_yield:     ["housing_rate_sensitivity"],
};

// ── Driver → connected market_now theme IDs (for names count + navigation) ────

const DRIVER_TO_MARKET_NOW_IDS: Record<string, string[]> = {
  ai_capex_growth:         ["data_centre_power", "semiconductors", "ai_compute_infrastructure", "memory_storage"],
  ai_compute_demand:       ["ai_compute_demand", "ai_compute_infrastructure", "data_centre_power"],
  geopolitical_risk_rising:["defence", "defence_aerospace", "cybersecurity", "gold_safe_haven_bid"],
  small_cap_risk_on:       ["small_cap_risk_on", "small_caps", "biotech_risk_on"],
  futures_risk_on:         ["risk_on_rotation", "small_cap_risk_on"],
  futures_risk_off:        [],
  yields_falling:          ["yields_falling", "reits", "regional_banks"],
  yields_rising:           ["yields_rising", "reits_falling_yield"],
  risk_on_rotation:        ["consumer_discretionary", "travel_leisure", "mega_cap_platforms"],
  gold_safe_haven_bid:     ["gold_safe_haven_bid", "gold_precious_metals"],
  credit_stress_easing:    ["regional_banks", "risk_on_rotation"],
  oil_supply_shock:        ["energy", "oil_supply_shock", "travel_leisure"],
  smh_tactical_weakness:   ["semiconductors"],
  reits_falling_yield:     ["reits_falling_yield", "reits"],
};

// ── TTG structural theme → short "Connected to..." context sentence ───────────

const TTG_CAUSE_CONTEXT: Record<string, string> = {
  ai_energy_nuclear:
    "Connected to AI infrastructure spending and data centre power demand.",
  defence_rearmament:
    "Connected to geopolitical risk and rising global defence budgets.",
  cybersecurity_digital_resilience:
    "Connected to cyber threat escalation and enterprise security investment.",
  reshoring_industrial_capex:
    "Connected to US industrial policy and domestic supply chain investment.",
  critical_minerals_copper:
    "Connected to electrification demand and the global energy transition.",
  gold_real_assets:
    "Connected to safe-haven demand, inflation hedging, and real rate compression.",
  glp1_metabolic_health:
    "Connected to GLP-1 drug adoption and healthcare innovation.",
  housing_rate_sensitivity:
    "Connected to interest rate expectations and mortgage affordability.",
  water_infrastructure:
    "Connected to infrastructure investment and resource scarcity themes.",
  digital_assets_infrastructure:
    "Connected to blockchain infrastructure and digital asset adoption.",
};

// ── Label normalizer ──────────────────────────────────────────────────────────
// key_drivers in the payload can be either internal IDs ("ai_capex_growth")
// or human-readable labels ("AI capital spending cycle expanding").
// Normalise to internal IDs before any lookup.

function normalizeDriverId(raw: string): string {
  if (CAUSE_LABELS[raw]) return raw; // exact internal-ID match
  const l = raw.toLowerCase();
  if (l.includes("ai capital") || l.includes("ai capex") || l.includes("ai infrastructure spending"))
    return "ai_capex_growth";
  if (l.includes("ai compute demand") || (l.includes("compute") && l.includes("demand")))
    return "ai_compute_demand";
  if (l.includes("geopolit") || l.includes("geopolitical"))
    return "geopolitical_risk_rising";
  if (l.includes("small-cap") || l.includes("small cap") || l.includes("small caps"))
    return "small_cap_risk_on";
  if (l.includes("futures") && (l.includes("risk-on") || l.includes("risk on") || l.includes("positive")))
    return "futures_risk_on";
  if (l.includes("futures") && (l.includes("risk-off") || l.includes("risk off") || l.includes("caution")))
    return "futures_risk_off";
  if ((l.includes("yield") || l.includes("bond")) && (l.includes("fall") || l.includes("lower") || l.includes("declin")))
    return "yields_falling";
  if ((l.includes("yield") || l.includes("bond")) && (l.includes("ris") || l.includes("higher") || l.includes("climb")))
    return "yields_rising";
  if (l.includes("rotation") || (l.includes("risk") && l.includes("on") && l.includes("rotation")))
    return "risk_on_rotation";
  if (l.includes("gold") && (l.includes("safe") || l.includes("haven") || l.includes("demand")))
    return "gold_safe_haven_bid";
  if (l.includes("credit") && (l.includes("eas") || l.includes("improv") || l.includes("spread")))
    return "credit_stress_easing";
  if (l.includes("oil") || l.includes("energy supply"))
    return "oil_supply_shock";
  if (l.includes("semiconductor") && (l.includes("weak") || l.includes("fatigue") || l.includes("fading")))
    return "smh_tactical_weakness";
  if (l.includes("reit") && (l.includes("yield") || l.includes("pressure") || l.includes("falling")))
    return "reits_falling_yield";
  return raw; // unchanged — will be filtered out if not in CAUSE_LABELS
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function getTtgLabel(ttgId: string): string {
  return (
    getAllCrosswalkEntries().find(e => e.ttgPrimary === ttgId)?.ttgPrimaryLabel ??
    ttgId.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
  );
}

function resolveConnectedThemes(driverId: string): CauseConnectedTheme[] {
  const ttgIds = DRIVER_CONNECTED_TTG[driverId] ?? [];
  return ttgIds.map(ttgId => ({
    ttgId,
    ttgLabel: getTtgLabel(ttgId),
  }));
}

function resolvePrimaryTtgId(driverId: string): string | null {
  const ttgIds = DRIVER_CONNECTED_TTG[driverId] ?? [];
  return ttgIds[0] ?? null;
}

function resolvePrimaryMarketNowId(driverId: string): string | null {
  const primaryTtgId = resolvePrimaryTtgId(driverId);
  if (primaryTtgId) {
    return getMarketNowIdsByTtg(primaryTtgId)[0]?.marketNowId ?? null;
  }
  // Fallback: use first market_now ID directly associated with this driver
  return DRIVER_TO_MARKET_NOW_IDS[driverId]?.[0] ?? null;
}

function resolveEvidenceBasis(driverId: string, hasFreshEvents: boolean): string {
  if (driverId === "futures_risk_on" || driverId === "futures_risk_off") return "Futures signal";
  if (hasFreshEvents) return "Fresh event evidence";
  return "Macro driver active";
}

function resolveConnectedNamesCount(
  driverId: string,
  payload: MarketNowPayload,
): number {
  const connectedMarketNowIds = new Set(DRIVER_TO_MARKET_NOW_IDS[driverId] ?? []);
  if (connectedMarketNowIds.size === 0) return 0;

  let count = 0;
  for (const r of payload.radar ?? []) {
    if (r.theme_link && connectedMarketNowIds.has(r.theme_link)) count++;
  }
  for (const u of payload.universe_snapshot ?? []) {
    if (connectedMarketNowIds.has(u.theme_id)) count++;
  }
  return count;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Returns the market impact sentence for a given driver ID.
 * Used by TodayTab to add a second line to driver cards.
 */
export function getCauseMarketImpact(driverId: string): string {
  return CAUSE_MARKET_IMPACT[driverId] ?? "";
}

/**
 * Returns a short "Connected to..." context sentence for a TTG structural theme ID.
 * Used by ThemeMapTab to add cause context below theme card labels.
 */
export function getTtgCauseContext(ttgId: string): string {
  return TTG_CAUSE_CONTEXT[ttgId] ?? "";
}

// ── Driver story clusters ─────────────────────────────────────────────────────
// When ≥2 drivers from the same cluster are active, buildCauseGroups() merges
// them into one display card instead of showing redundant top-level cards.

const DRIVER_CLUSTERS: Array<{
  id: string;
  label: string;
  drivers: string[];
  mergedNarrative: string;
}> = [
  {
    id: "ai_infrastructure",
    label: "AI Infrastructure & Compute Demand",
    drivers: ["ai_capex_growth", "ai_compute_demand"],
    mergedNarrative:
      "Both AI infrastructure spending and compute demand are accelerating simultaneously. Hyperscalers are committing to record data centre build-outs while demand for model training and inference compute is outpacing available capacity.",
  },
  {
    id: "risk_on_momentum",
    label: "Broad Risk-On Environment",
    drivers: ["futures_risk_on", "risk_on_rotation", "small_cap_risk_on"],
    mergedNarrative:
      "Multiple risk-on signals are active simultaneously, pointing to improving market sentiment across equities and smaller companies. Growth and cyclical themes are in focus heading into the session.",
  },
];

export interface MarketCauseGroup {
  group_label: string;
  cluster_id: string | null;
  cards: MarketCauseCard[];
  display_card: MarketCauseCard;
  is_cluster: boolean;
  driver_count: number;
}

function dedupeConnectedThemes(themes: CauseConnectedTheme[]): CauseConnectedTheme[] {
  return [...new Map(themes.map(t => [t.ttgId, t])).values()];
}

/**
 * Groups MarketCauseCards by story cluster.
 * When ≥2 drivers from the same cluster are active they are merged into one
 * display card with a combined narrative. Single-driver groups pass through
 * unchanged. Order matches the original key_drivers order.
 */
export function buildCauseGroups(payload: MarketNowPayload): MarketCauseGroup[] {
  const keyDrivers = payload.key_drivers ?? [];
  const normalizedDrivers = keyDrivers
    .map(normalizeDriverId)
    .filter(d => Boolean(CAUSE_LABELS[d]));

  if (normalizedDrivers.length === 0) return [];

  const cards = buildMarketCauseCards(payload);
  const cardByDriver = new Map<string, MarketCauseCard>();
  for (const driverId of normalizedDrivers) {
    const card = cards.find(c => c.cause_label === CAUSE_LABELS[driverId]);
    if (card) cardByDriver.set(driverId, card);
  }

  const usedDrivers = new Set<string>();
  const groups: MarketCauseGroup[] = [];

  // Try to form clusters first
  for (const cluster of DRIVER_CLUSTERS) {
    const matchingDrivers = cluster.drivers.filter(
      d => cardByDriver.has(d) && !usedDrivers.has(d),
    );
    if (matchingDrivers.length < 2) continue;

    const clusterCards = matchingDrivers.map(d => cardByDriver.get(d)!);
    matchingDrivers.forEach(d => usedDrivers.add(d));

    const mergedCard: MarketCauseCard = {
      ...clusterCards[0],
      cause_label: cluster.label,
      what_happened: cluster.mergedNarrative,
      market_impact: [...new Set(clusterCards.map(c => c.market_impact))].join(" "),
      connected_themes: dedupeConnectedThemes(clusterCards.flatMap(c => c.connected_themes)),
      connected_names_count: Math.max(...clusterCards.map(c => c.connected_names_count)),
      has_fresh_evidence: clusterCards.some(c => c.has_fresh_evidence),
    };

    groups.push({
      group_label: cluster.label,
      cluster_id: cluster.id,
      cards: clusterCards,
      display_card: mergedCard,
      is_cluster: true,
      driver_count: matchingDrivers.length,
    });
  }

  // Remaining individual drivers (preserve original order)
  for (const driverId of normalizedDrivers) {
    if (usedDrivers.has(driverId)) continue;
    const card = cardByDriver.get(driverId);
    if (!card) continue;
    usedDrivers.add(driverId);
    groups.push({
      group_label: card.cause_label,
      cluster_id: null,
      cards: [card],
      display_card: card,
      is_cluster: false,
      driver_count: 1,
    });
  }

  return groups;
}

/**
 * Converts MarketNowPayload key_drivers into a list of MarketCauseCards.
 * Returns at most 6 cards. Returns [] when no drivers are active.
 */
export function buildMarketCauseCards(payload: MarketNowPayload): MarketCauseCard[] {
  const keyDrivers = payload.key_drivers ?? [];
  const hasFreshEvents = (payload.key_events?.length ?? 0) > 0;

  const cards: MarketCauseCard[] = keyDrivers
    .map(normalizeDriverId)
    .filter(d => CAUSE_LABELS[d] && CAUSE_WHAT_HAPPENED[d])
    .slice(0, 6)
    .map(driverId => {
      const primaryTtgId = resolvePrimaryTtgId(driverId);
      return {
        cause_label:          CAUSE_LABELS[driverId],
        what_happened:        CAUSE_WHAT_HAPPENED[driverId],
        market_impact:        CAUSE_MARKET_IMPACT[driverId] ?? "",
        connected_themes:     resolveConnectedThemes(driverId),
        evidence_basis:       resolveEvidenceBasis(driverId, hasFreshEvents),
        risk_to_monitor:      CAUSE_RISK[driverId] ?? "",
        connected_names_count: resolveConnectedNamesCount(driverId, payload),
        has_fresh_evidence:   hasFreshEvents && driverId !== "futures_risk_on" && driverId !== "futures_risk_off",
        primary_ttg_id:       primaryTtgId,
        primary_market_now_id: resolvePrimaryMarketNowId(driverId),
      };
    });

  return cards;
}
