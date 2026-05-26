// CustomerBriefingModel — M13B.
// Maps operator intelligence (MarketNowPayload + CustomerStory) into
// customer-safe typed objects consumed by views.
// Pure functions. No React. No broker/execution imports.

import type { MarketNowPayload, TtgTheme } from "./customerApi";
import type { CustomerStory } from "./customerStory";

// ── Customer Market Regime ─────────────────────────────────────────────────────

export interface CustomerMarketRegime {
  state: "risk-on" | "monitoring" | "risk-off" | "mixed";
  label: string;
  description: string;
  accentColor: string;
}

const REGIME_CONFIG: Record<string, CustomerMarketRegime> = {
  "risk-on": {
    state: "risk-on",
    label: "Risk-On",
    description: "Growth and cyclical themes are gaining — risk appetite is constructive.",
    accentColor: "#10b981",
  },
  "risk-off": {
    state: "risk-off",
    label: "Risk-Off",
    description: "Investors are reducing risk exposure — defensive positioning is favoured.",
    accentColor: "#ef4444",
  },
  mixed: {
    state: "mixed",
    label: "Mixed Signals",
    description: "Conflicting forces are active — risk-on and risk-off drivers are both present.",
    accentColor: "#f59e0b",
  },
  monitoring: {
    state: "monitoring",
    label: "Monitoring",
    description: "Structural themes are in focus — no dominant market force has emerged yet.",
    accentColor: "#6b7280",
  },
};

export function buildCustomerRegime(
  state: "risk-on" | "monitoring" | "risk-off" | "mixed",
): CustomerMarketRegime {
  return REGIME_CONFIG[state] ?? REGIME_CONFIG.monitoring;
}

// ── Customer Market Story ──────────────────────────────────────────────────────

export interface CustomerMarketStory {
  regime: CustomerMarketRegime;
  macro_label: string;
  headline: string;
  summary: string;
  supporting_bullets: string[];
  caution: string | null;
  watch_next: string | null;
  evidence_mode: "live" | "structural" | "last-known";
  has_live_events: boolean;
}

const MACRO_LABELS: Record<string, string> = {
  ai_capex_growth:          "AI infrastructure buildout is the primary driver",
  ai_compute_demand:         "AI compute demand is shaping attention",
  geopolitical_risk_rising:  "Geopolitical risk is elevated",
  futures_risk_on:           "Overnight futures point higher",
  futures_risk_off:          "Overnight futures signal caution",
  yields_falling:            "Falling yields are supporting risk",
  yields_rising:             "Rising yields are creating headwinds",
  risk_on_rotation:          "Capital is rotating into growth",
  gold_safe_haven_bid:       "Safe-haven demand is elevated",
  credit_stress_easing:      "Credit conditions are improving",
  small_cap_risk_on:         "Risk appetite is broadening to smaller companies",
  oil_supply_shock:          "Oil supply is disrupted",
  smh_tactical_weakness:     "Semiconductor momentum is fading near-term",
  reits_falling_yield:       "Rising yields are pressuring real estate",
};

// Operator terms to strip from market_mood before surfacing to customer
const UNSAFE_SUBSTRINGS = [
  "trade-ready", "entry candidate", "position", "swing", "scan",
  "activation", "payload", "sonnet", "claude", "model name",
  "execution", "broker", "order",
];

function sanitiseMacroLabel(raw: string): string {
  let text = raw;
  for (const term of UNSAFE_SUBSTRINGS) {
    text = text.replace(new RegExp(term, "gi"), "").trim();
  }
  return text.replace(/\s{2,}/g, " ").trim();
}

function resolveMacroLabel(
  key_drivers: string[],
  market_mood: string | undefined,
  market_state: string,
): string {
  if (market_mood && market_mood.length < 120) {
    const sanitised = sanitiseMacroLabel(market_mood);
    if (sanitised.length > 10) return sanitised;
  }
  const topDriver = key_drivers[0];
  if (topDriver && MACRO_LABELS[topDriver]) return MACRO_LABELS[topDriver];
  if (market_state === "risk-on") return "Risk appetite is constructive today";
  if (market_state === "risk-off") return "Risk appetite is cautious today";
  if (market_state === "mixed") return "Conflicting forces are active today";
  return "Markets are being monitored for emerging themes";
}

function buildSupportingBullets(
  story: CustomerStory,
  payload: MarketNowPayload,
): string[] {
  const bullets: string[] = [];
  for (const d of story.primary_drivers.slice(0, 3)) {
    const short = d.explanation.split(".")[0].trim();
    if (short && !bullets.includes(short)) bullets.push(short);
    if (bullets.length === 3) break;
  }
  // Fallback: what_changed items
  if (bullets.length < 2) {
    for (const wc of (payload.what_changed ?? []).slice(0, 3)) {
      if (!bullets.includes(wc) && wc.length < 140) bullets.push(wc);
      if (bullets.length === 3) break;
    }
  }
  return bullets.slice(0, 3);
}

export function buildCustomerMarketStory(
  payload: MarketNowPayload,
  story: CustomerStory,
): CustomerMarketStory {
  const regime = buildCustomerRegime(story.market_state);
  const macro_label = resolveMacroLabel(
    payload.key_drivers ?? [],
    payload.market_mood,
    story.market_state,
  );
  const caution = (payload.known_conflicts ?? [])[0] ?? null;
  const watchItems = (payload.watch_next ?? []).length
    ? payload.watch_next ?? []
    : (payload.what_to_watch ?? []);
  const watch_next = watchItems[0] ?? null;

  return {
    regime,
    macro_label,
    headline: story.headline,
    summary: story.summary,
    supporting_bullets: buildSupportingBullets(story, payload),
    caution,
    watch_next,
    evidence_mode: story.evidence_mode,
    has_live_events: story.has_live_events,
  };
}

// ── Customer Market Forces ─────────────────────────────────────────────────────

export interface CustomerMarketForce {
  id: string;
  label: string;
  is_active: boolean;
  why_it_matters: string;
  market_impact: string;
  risk_to_monitor: string;
  evidence_basis: string;
  connected_theme_ids: string[];
  connected_theme_labels: string[];
}

const ALL_FORCE_IDS = [
  "ai_capex_growth",
  "ai_compute_demand",
  "geopolitical_risk_rising",
  "futures_risk_on",
  "futures_risk_off",
  "yields_falling",
  "yields_rising",
  "risk_on_rotation",
  "gold_safe_haven_bid",
  "credit_stress_easing",
  "small_cap_risk_on",
  "oil_supply_shock",
  "smh_tactical_weakness",
  "reits_falling_yield",
] as const;

const FORCE_LABELS: Record<string, string> = {
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

const FORCE_WHY: Record<string, string> = {
  ai_capex_growth:
    "Hyperscalers and enterprises are committing to record AI infrastructure investment. Power, compute, and memory are in sustained demand across the capital cycle.",
  ai_compute_demand:
    "AI model training and inference are scaling rapidly. Demand for GPU compute and data centre capacity is outpacing available supply.",
  geopolitical_risk_rising:
    "Elevated international tensions are driving defence budgets higher and sustaining safe-haven demand across gold and defence names.",
  small_cap_risk_on:
    "Risk appetite is broadening to smaller companies — a sign of improving growth and credit sentiment across the market.",
  futures_risk_on:
    "US equity futures are pointing higher, signalling positive overnight institutional positioning heading into the session.",
  futures_risk_off:
    "US equity futures are under pressure, signalling cautious overnight institutional positioning heading into the session.",
  yields_falling:
    "Bond yields are declining as rate expectations shift lower. Rate-sensitive sectors and long-duration growth stocks benefit from a falling discount rate.",
  yields_rising:
    "Bond yields are climbing, creating headwinds for growth stocks and rate-sensitive sectors by raising the discount rate on future earnings.",
  risk_on_rotation:
    "Capital is rotating from defensive assets and cash into growth and cyclical names as confidence in the expansion improves.",
  gold_safe_haven_bid:
    "Gold demand is elevated as investors seek a store of value amid macro uncertainty, currency risk, or geopolitical stress.",
  credit_stress_easing:
    "Credit spreads are tightening, reducing the perceived risk of corporate defaults and improving broader borrowing conditions.",
  oil_supply_shock:
    "Oil supply is disrupted, raising energy prices and affecting inflation expectations and consumer purchasing power.",
  smh_tactical_weakness:
    "Semiconductor momentum is showing near-term fatigue — price action is softer than the structural AI demand backdrop would suggest.",
  reits_falling_yield:
    "Rising long-term yields are compressing REIT dividend yield spreads, putting pressure on real estate valuations.",
};

const FORCE_IMPACT: Record<string, string> = {
  ai_capex_growth:         "Power infrastructure, semiconductors, data centres, and memory names are supported.",
  ai_compute_demand:       "AI energy, semiconductor, and data centre themes are gaining attention.",
  geopolitical_risk_rising:"Defence contractors, cybersecurity, and gold are seeing elevated interest.",
  small_cap_risk_on:       "Small-cap and growth-oriented names are attracting capital as breadth improves.",
  futures_risk_on:         "Growth and cyclical themes are supported heading into the session.",
  futures_risk_off:        "Defensive positioning is favoured — growth themes face near-term pressure.",
  yields_falling:          "Housing, real estate, and long-duration growth stocks are supported.",
  yields_rising:           "REITs, housing, and long-duration growth names are facing headwinds.",
  risk_on_rotation:        "Consumer discretionary, mega-cap technology, and travel names are attracting capital.",
  gold_safe_haven_bid:     "Gold and precious metals names are in demand as safe-haven interest rises.",
  credit_stress_easing:    "Financials, cyclicals, and credit-sensitive names see improved conditions.",
  oil_supply_shock:        "Energy producers benefit; airlines and cost-sensitive industrials face pressure.",
  smh_tactical_weakness:   "Near-term caution on semiconductor momentum, even as the structural AI case remains intact.",
  reits_falling_yield:     "REITs and rate-sensitive infrastructure names are under near-term pressure.",
};

const FORCE_RISK: Record<string, string> = {
  ai_capex_growth:
    "A hyperscaler capex guidance cut or AI demand slowdown would weaken this force.",
  ai_compute_demand:
    "A slowdown in AI model training runs or a GPU demand pause would reduce the intensity of this story.",
  geopolitical_risk_rising:
    "A significant ceasefire, de-escalation event, or peace agreement would reduce this risk premium.",
  small_cap_risk_on:
    "Credit tightening, a recession signal, or broad risk-off rotation could reverse small-cap leadership.",
  futures_risk_on:
    "Overnight news events, economic data surprises, or Asian market weakness could reverse this signal.",
  futures_risk_off:
    "Positive economic data or a recovery in risk appetite during the session could neutralise this.",
  yields_falling:
    "Inflation re-acceleration forcing rates to stay elevated longer would reverse this driver.",
  yields_rising:
    "A downside inflation surprise or a shift in central bank guidance toward cuts would weaken this.",
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
    "Yields stabilising or reversing lower would restore yield spreads and reduce pressure on real estate.",
};

const FORCE_THEMES: Record<string, string[]> = {
  ai_capex_growth:         ["data_centre_power", "semiconductors", "ai_compute_infrastructure", "memory_storage"],
  ai_compute_demand:       ["ai_compute_infrastructure", "data_centre_power"],
  geopolitical_risk_rising:["defence", "cybersecurity", "gold_safe_haven_bid"],
  small_cap_risk_on:       ["small_cap_risk_on"],
  futures_risk_on:         ["risk_on_rotation"],
  futures_risk_off:        [],
  yields_falling:          ["reits", "regional_banks"],
  yields_rising:           ["reits_falling_yield"],
  risk_on_rotation:        ["consumer_discretionary", "mega_cap_platforms", "travel_leisure"],
  gold_safe_haven_bid:     ["gold_safe_haven_bid", "gold_precious_metals"],
  credit_stress_easing:    ["regional_banks"],
  oil_supply_shock:        ["energy"],
  smh_tactical_weakness:   ["semiconductors"],
  reits_falling_yield:     ["reits_falling_yield"],
};

const THEME_LABELS: Record<string, string> = {
  data_centre_power:         "Data Centres & Power",
  semiconductors:            "Semiconductors",
  ai_compute_infrastructure: "AI Infrastructure",
  memory_storage:            "Memory & Storage",
  defence:                   "Defence",
  cybersecurity:             "Cybersecurity",
  gold_safe_haven_bid:       "Gold Safe Haven",
  gold_precious_metals:      "Gold & Precious Metals",
  small_cap_risk_on:         "Small-Cap Rally",
  risk_on_rotation:          "Risk-On Rotation",
  reits:                     "Real Estate (REITs)",
  reits_falling_yield:       "REITs Under Pressure",
  regional_banks:            "Regional Banks",
  consumer_discretionary:    "Consumer Spending",
  mega_cap_platforms:        "Mega-Cap Tech",
  travel_leisure:            "Travel & Leisure",
  energy:                    "Energy",
};

export function resolveForceThemeLabel(themeId: string): string {
  return THEME_LABELS[themeId] ?? themeId.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

export function normalizeForceId(raw: string): string {
  if (FORCE_LABELS[raw]) return raw;
  const l = raw.toLowerCase();
  if (l.includes("ai capital") || l.includes("ai capex") || l.includes("ai infrastructure spending"))
    return "ai_capex_growth";
  if (l.includes("compute demand") || (l.includes("compute") && l.includes("demand")))
    return "ai_compute_demand";
  if (l.includes("geopolit")) return "geopolitical_risk_rising";
  if (l.includes("small-cap") || l.includes("small cap")) return "small_cap_risk_on";
  if (l.includes("futures") && (l.includes("risk-on") || l.includes("positive"))) return "futures_risk_on";
  if (l.includes("futures") && (l.includes("risk-off") || l.includes("caution"))) return "futures_risk_off";
  if ((l.includes("yield") || l.includes("bond")) && (l.includes("fall") || l.includes("lower"))) return "yields_falling";
  if ((l.includes("yield") || l.includes("bond")) && (l.includes("ris") || l.includes("higher"))) return "yields_rising";
  if (l.includes("rotation")) return "risk_on_rotation";
  if (l.includes("gold") && l.includes("haven")) return "gold_safe_haven_bid";
  if (l.includes("credit") && l.includes("eas")) return "credit_stress_easing";
  if (l.includes("oil")) return "oil_supply_shock";
  if (l.includes("semiconductor") && l.includes("weak")) return "smh_tactical_weakness";
  if (l.includes("reit") && l.includes("yield")) return "reits_falling_yield";
  return raw;
}

function resolveEvidenceBasis(forceId: string, hasFreshEvents: boolean): string {
  if (forceId === "futures_risk_on" || forceId === "futures_risk_off") return "Futures signal";
  if (hasFreshEvents) return "Fresh evidence";
  return "Active macro driver";
}

export function buildCustomerForces(
  payload: MarketNowPayload,
): { active: CustomerMarketForce[]; dormant: CustomerMarketForce[] } {
  const hasFreshEvents = (payload.key_events?.length ?? 0) > 0;
  const rawActive = (payload.key_drivers ?? [])
    .map(normalizeForceId)
    .filter(id => Boolean(FORCE_LABELS[id]));
  const activeSet = new Set(rawActive);

  const active: CustomerMarketForce[] = rawActive.map(id => ({
    id,
    label: FORCE_LABELS[id],
    is_active: true,
    why_it_matters: FORCE_WHY[id] ?? "",
    market_impact: FORCE_IMPACT[id] ?? "",
    risk_to_monitor: FORCE_RISK[id] ?? "",
    evidence_basis: resolveEvidenceBasis(id, hasFreshEvents),
    connected_theme_ids: FORCE_THEMES[id] ?? [],
    connected_theme_labels: (FORCE_THEMES[id] ?? []).map(resolveForceThemeLabel),
  }));

  const dormant: CustomerMarketForce[] = ALL_FORCE_IDS
    .filter(id => !activeSet.has(id))
    .map(id => ({
      id,
      label: FORCE_LABELS[id],
      is_active: false,
      why_it_matters: FORCE_WHY[id] ?? "",
      market_impact: FORCE_IMPACT[id] ?? "",
      risk_to_monitor: FORCE_RISK[id] ?? "",
      evidence_basis: "Quiet",
      connected_theme_ids: FORCE_THEMES[id] ?? [],
      connected_theme_labels: (FORCE_THEMES[id] ?? []).map(resolveForceThemeLabel),
    }));

  return { active, dormant };
}

// ── Connection Tree ────────────────────────────────────────────────────────────

export interface CustomerConnectionTheme {
  theme_id: string;
  theme_label: string;
  driver_active: boolean;
}

export interface CustomerConnectionNode {
  force_id: string;
  force_label: string;
  themes: CustomerConnectionTheme[];
}

export function buildConnectionTree(
  payload: MarketNowPayload,
  ttgThemes?: TtgTheme[],
): CustomerConnectionNode[] {
  const ttgActiveIds = new Set(
    (ttgThemes ?? []).filter(t => t.driver_active).map(t => t.theme_id),
  );
  const activeForceIds = (payload.key_drivers ?? [])
    .map(normalizeForceId)
    .filter(id => Boolean(FORCE_LABELS[id]));

  return activeForceIds.map(forceId => ({
    force_id: forceId,
    force_label: FORCE_LABELS[forceId],
    themes: (FORCE_THEMES[forceId] ?? []).map(themeId => ({
      theme_id: themeId,
      theme_label: resolveForceThemeLabel(themeId),
      driver_active: ttgActiveIds.has(themeId),
    })),
  }));
}

// ── Context-Aware Suggested Questions ─────────────────────────────────────────

const STATIC_QUESTIONS = [
  "What changed since I was away?",
  "Explain today's market mood simply.",
  "What should I watch next?",
  "Which themes are quiet today?",
];

const FORCE_QUESTIONS: Record<string, string> = {
  ai_capex_growth:         "What is behind the AI infrastructure spending story today?",
  ai_compute_demand:       "Why is AI compute demand affecting markets right now?",
  geopolitical_risk_rising:"How is geopolitical risk shaping the market today?",
  futures_risk_on:         "Why are futures pointing higher — what does that mean for today?",
  futures_risk_off:        "Why are futures pointing lower and what should I watch for?",
  yields_falling:          "How are falling interest rates affecting different sectors today?",
  yields_rising:           "Which areas are most affected by rising interest rates?",
  risk_on_rotation:        "What is growth stock rotation and why is it happening now?",
  gold_safe_haven_bid:     "Why is gold in demand right now and what does that signal?",
  credit_stress_easing:    "What does easing credit mean for the market today?",
  small_cap_risk_on:       "What does improving risk appetite in smaller companies signal?",
  oil_supply_shock:        "How is oil supply disruption affecting markets today?",
  smh_tactical_weakness:   "Why is semiconductor momentum fading despite the AI story?",
  reits_falling_yield:     "Why are rising yields putting pressure on real estate?",
};

export function buildContextualSuggestions(payload: MarketNowPayload): string[] {
  const questions: string[] = [];
  const activeForceIds = (payload.key_drivers ?? [])
    .map(normalizeForceId)
    .filter(id => Boolean(FORCE_LABELS[id]));

  for (const id of activeForceIds.slice(0, 3)) {
    const q = FORCE_QUESTIONS[id];
    if (q && !questions.includes(q)) questions.push(q);
  }

  // Active theme question
  const activeThemes = (payload.themes ?? []).filter(
    t => t.state === "active" || t.state === "activated",
  );
  if (activeThemes.length > 0) {
    const label = resolveForceThemeLabel(activeThemes[0].theme);
    const q = `Which names are connected to ${label}?`;
    if (!questions.includes(q)) questions.push(q);
  }

  // Conflict question
  if ((payload.known_conflicts ?? []).length > 0) {
    const q = "What are the conflicting signals in today's briefing?";
    if (!questions.includes(q)) questions.push(q);
  }

  // Fill to 8 from static list
  for (const q of STATIC_QUESTIONS) {
    if (questions.length >= 8) break;
    if (!questions.includes(q)) questions.push(q);
  }

  return questions.slice(0, 8);
}

// ── Prohibited customer-facing language check ──────────────────────────────────
// Narrow list — only unambiguous operator/execution terms

// "apex" deliberately excluded — it is a substring of "capex", causing false matches.
// "activation" excluded — matches "reactivation" and similar compound words.
// Safety is primarily enforced at the Python saas_intelligence_output layer.
const PROHIBITED_RENDERED_TERMS = [
  "trade-ready",
  "entry candidate",
  "position entry",
  "preferred trade mode",
  "scanner",
  "payload",
  "market_now_id",
] as const;

export function containsProhibitedTerm(text: string): boolean {
  const lower = text.toLowerCase();
  return PROHIBITED_RENDERED_TERMS.some(t => lower.includes(t));
}
