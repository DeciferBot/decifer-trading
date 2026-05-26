// Static deterministic crosswalk from market_now theme IDs to TTG structural theme IDs.
// No runtime fuzzy matching — this is a curated editorial mapping.
//
// market_now IDs = live intelligence pipeline themes (23 total, from /api/market-now)
// TTG IDs = 10 structural themes in the Theme Transmission Graph (/api/intelligence/themes)
//
// Some market_now themes do not map to any TTG structural theme (unmapped) and some TTG
// structural themes have no market_now equivalent yet (digital_assets_infrastructure,
// water_infrastructure).

export interface CrosswalkEntry {
  marketNowId: string;
  marketNowLabel: string;
  ttgPrimary: string;
  ttgPrimaryLabel: string;
  ttgSecondary?: string[];
  relationship: string;
}

const CROSSWALK: CrosswalkEntry[] = [
  {
    marketNowId: "data_centre_power",
    marketNowLabel: "Data Centre & Power",
    ttgPrimary: "ai_energy_nuclear",
    ttgPrimaryLabel: "AI Energy & Nuclear",
    relationship: "Data centre power demand is the primary energy driver behind AI Energy & Nuclear — every GPU cluster needs reliable, scalable power, which activates the nuclear and grid infrastructure sub-themes.",
  },
  {
    marketNowId: "semiconductors",
    marketNowLabel: "Semiconductors",
    ttgPrimary: "ai_energy_nuclear",
    ttgPrimaryLabel: "AI Energy & Nuclear",
    ttgSecondary: ["reshoring_industrial_capex"],
    relationship: "AI chip demand (NVDA, AMD, TSM) is the compute layer for AI Energy. Semiconductor fab reshoring also connects to the Reshoring & Industrial Capex theme.",
  },
  {
    marketNowId: "ai_compute_demand",
    marketNowLabel: "AI Compute Demand",
    ttgPrimary: "ai_energy_nuclear",
    ttgPrimaryLabel: "AI Energy & Nuclear",
    relationship: "AI compute demand is the primary demand driver activating the AI Energy & Nuclear structural theme — model training and inference scale require enormous power.",
  },
  {
    marketNowId: "ai_compute_infrastructure",
    marketNowLabel: "AI Compute Infrastructure",
    ttgPrimary: "ai_energy_nuclear",
    ttgPrimaryLabel: "AI Energy & Nuclear",
    relationship: "AI compute infrastructure (hyperscaler capex, data centre builds, networking) is directly linked to AI Energy & Nuclear — infrastructure spending is what converts compute demand into power demand.",
  },
  {
    marketNowId: "memory_storage",
    marketNowLabel: "Memory & Storage",
    ttgPrimary: "ai_energy_nuclear",
    ttgPrimaryLabel: "AI Energy & Nuclear",
    relationship: "High-bandwidth memory (HBM) and storage are essential layers of AI compute stacks — memory demand signals AI infrastructure buildout, which connects to the power/energy theme.",
  },
  {
    marketNowId: "defence_aerospace",
    marketNowLabel: "Defence & Aerospace",
    ttgPrimary: "defence_rearmament",
    ttgPrimaryLabel: "Defence Rearmament",
    relationship: "Defence aerospace is the primary beneficiary sector within the Defence Rearmament theme — NATO spending targets and geopolitical risk drive aerospace and defence procurement.",
  },
  {
    marketNowId: "defence",
    marketNowLabel: "Defence",
    ttgPrimary: "defence_rearmament",
    ttgPrimaryLabel: "Defence Rearmament",
    relationship: "The defence driver is the direct activator of the Defence Rearmament structural theme — rising geopolitical risk translates directly to budget increases across defence primes.",
  },
  {
    marketNowId: "cybersecurity",
    marketNowLabel: "Cybersecurity",
    ttgPrimary: "cybersecurity_digital_resilience",
    ttgPrimaryLabel: "Cybersecurity & Digital Resilience",
    relationship: "Cybersecurity is a one-to-one match to the Cybersecurity & Digital Resilience structural theme — the same geopolitical and regulatory tailwinds activating the live driver directly benefit the TTG basket.",
  },
  {
    marketNowId: "software_cloud",
    marketNowLabel: "Software & Cloud",
    ttgPrimary: "cybersecurity_digital_resilience",
    ttgPrimaryLabel: "Cybersecurity & Digital Resilience",
    relationship: "Cloud security spend is the intersection between software/cloud and the Cybersecurity structural theme — enterprise cloud migration drives mandatory security uplift.",
  },
  {
    marketNowId: "infrastructure_reshoring",
    marketNowLabel: "Infrastructure & Reshoring",
    ttgPrimary: "reshoring_industrial_capex",
    ttgPrimaryLabel: "Reshoring & Industrial Capex",
    relationship: "Infrastructure and reshoring is a one-to-one match — CHIPS Act, IRA, and onshoring of critical manufacturing are the core thesis behind the Reshoring & Industrial Capex structural theme.",
  },
  {
    marketNowId: "copper_electrification",
    marketNowLabel: "Copper & Electrification",
    ttgPrimary: "critical_minerals_copper",
    ttgPrimaryLabel: "Critical Minerals & Copper",
    relationship: "Copper electrification is the core demand driver for the Critical Minerals & Copper structural theme — grid upgrades, EV charging, and AI data centre power all require massive copper intensity.",
  },
  {
    marketNowId: "gold_safe_haven_bid",
    marketNowLabel: "Gold Safe Haven Bid",
    ttgPrimary: "gold_real_assets",
    ttgPrimaryLabel: "Gold & Real Assets",
    relationship: "The gold safe-haven bid is the primary demand driver activating the Gold & Real Assets structural theme — central bank buying, real-rate compression, and geopolitical risk all channel into gold.",
  },
  {
    marketNowId: "gold_precious_metals",
    marketNowLabel: "Gold & Precious Metals",
    ttgPrimary: "gold_real_assets",
    ttgPrimaryLabel: "Gold & Real Assets",
    relationship: "Gold and precious metals is a direct match to the Gold & Real Assets structural theme — the same supply/demand and macro tailwinds connect both.",
  },
  {
    marketNowId: "biotech",
    marketNowLabel: "Biotech",
    ttgPrimary: "glp1_metabolic_health",
    ttgPrimaryLabel: "GLP-1 & Metabolic Health",
    relationship: "Biotech is the broadest category under which GLP-1 and metabolic health sits — the GLP-1 drug cycle is the highest-conviction biotech structural theme right now.",
  },
  {
    marketNowId: "biotech_risk_on",
    marketNowLabel: "Biotech Risk-On",
    ttgPrimary: "glp1_metabolic_health",
    ttgPrimaryLabel: "GLP-1 & Metabolic Health",
    relationship: "Biotech risk-on conditions (FDA pipeline, trial catalysts) directly benefit the GLP-1 structural theme — weight loss drug adoption is the secular driver underneath the risk-on signal.",
  },
  {
    marketNowId: "defensive_healthcare",
    marketNowLabel: "Defensive Healthcare",
    ttgPrimary: "glp1_metabolic_health",
    ttgPrimaryLabel: "GLP-1 & Metabolic Health",
    relationship: "Defensive healthcare as a regime rotation connects to GLP-1 & Metabolic Health as the highest-conviction structural healthcare theme — GLP-1 names offer both growth and healthcare defensive characteristics.",
  },
  {
    marketNowId: "reits",
    marketNowLabel: "REITs",
    ttgPrimary: "housing_rate_sensitivity",
    ttgPrimaryLabel: "Housing & Rate Sensitivity",
    relationship: "REITs are directly linked to housing and rate sensitivity — REIT valuations are mechanically tied to long-rate levels, making them the primary beneficiary of any rates-falling regime.",
  },
  {
    marketNowId: "yields_falling",
    marketNowLabel: "Yields Falling",
    ttgPrimary: "housing_rate_sensitivity",
    ttgPrimaryLabel: "Housing & Rate Sensitivity",
    relationship: "Falling yields are the primary macro activator for the Housing & Rate Sensitivity structural theme — lower mortgage rates and cap rate compression both improve the outlook for rate-sensitive assets.",
  },
];

// market_now theme IDs that have no TTG structural counterpart yet
export const UNMAPPED_MARKET_NOW_IDS = new Set([
  "risk_on_rotation",
  "small_cap_risk_on",
  "yields_rising",
  "reits_falling_yield",
  "mega_cap_platforms",
  "consumer_discretionary",
  "consumer_discretionary_strength",
  "travel_leisure",
  "regional_banks",
  "banks",
  "energy",
  "oil_supply_shock",
  "quality_cash_flow",
  "defensive_quality",
  "small_caps",
  "futures_risk_on",
  "futures_risk_off",
  "credit_stress_easing",
]);

// TTG structural theme IDs that have no market_now counterpart yet
export const UNMAPPED_TTG_IDS = new Set([
  "water_infrastructure",
  "digital_assets_infrastructure",
]);

export function getCrosswalkByMarketNow(marketNowId: string): CrosswalkEntry | null {
  return CROSSWALK.find(e => e.marketNowId === marketNowId) ?? null;
}

export function getTtgIdForMarketNow(marketNowId: string): string | null {
  return getCrosswalkByMarketNow(marketNowId)?.ttgPrimary ?? null;
}

export function getMarketNowIdsByTtg(ttgId: string): CrosswalkEntry[] {
  return CROSSWALK.filter(e => e.ttgPrimary === ttgId || (e.ttgSecondary ?? []).includes(ttgId));
}

export function getAllCrosswalkEntries(): CrosswalkEntry[] {
  return CROSSWALK;
}
