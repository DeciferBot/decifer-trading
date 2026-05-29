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
  regime_explanation: string | null;
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

// ── Regime explanation — the WHY behind the badge ─────────────────────────────

// Plain-English explanations for when a negative force is active but price action
// looks constructive (or vice versa). These explain the *tension*, not just the label.
const NEGATIVE_DRIVER_EXPLANATIONS: Record<string, string> = {
  geopolitical_risk_rising:
    "Defence stocks are outpacing the broader market — investors are hedging geopolitical risk even as equities hold up. That divergence is the risk-off signal, not the headline index level.",
  oil_supply_shock:
    "Oil is falling sharply, pointing to a potential supply disruption or de-escalation in a conflict zone. Energy names are under pressure regardless of the broader market direction.",
  credit_stress_rising:
    "Credit spreads are widening — corporate borrowing conditions are tightening. That can be an early stress signal even when equity indices look calm.",
  yields_rising:
    "Bond yields are climbing, raising the cost of capital. That's a structural headwind for growth stocks even if the market is resilient near-term.",
  smh_tactical_weakness:
    "Semiconductor momentum is fading near-term despite the broader AI narrative — the sector is digesting recent gains.",
  reits_falling_yield:
    "Rising long-term yields are compressing REIT dividend spreads, putting pressure on real estate valuations.",
};

const POSITIVE_DRIVER_EXPLANATIONS: Record<string, string> = {
  risk_on_rotation:
    "Capital is rotating into growth and cyclical names — a sign of improving confidence even as some structural headwinds remain.",
  futures_risk_on:
    "Equity futures pointed higher overnight, suggesting institutional buyers positioned constructively heading into the session.",
  yields_falling:
    "Falling yields are reducing the discount rate on future earnings, which supports long-duration growth stocks and rate-sensitive sectors.",
  ai_capex_growth:
    "Hyperscaler AI infrastructure commitments are driving sustained demand for power, semiconductors, and data centre capacity.",
  ai_compute_demand:
    "AI compute demand is accelerating — GPU and data centre capacity remain in short supply relative to what model training requires.",
  small_cap_risk_on:
    "Risk appetite is broadening to smaller companies — a sign the rally has wider participation than mega-cap names alone.",
  credit_stress_easing:
    "Credit spreads are tightening, reducing perceived default risk and improving broader borrowing conditions.",
  gold_safe_haven_bid:
    "Gold demand is elevated as investors seek a store of value amid macro uncertainty or currency risk.",
};

const NEGATIVE_DRIVER_SET = new Set(Object.keys(NEGATIVE_DRIVER_EXPLANATIONS));
const POSITIVE_DRIVER_SET = new Set(Object.keys(POSITIVE_DRIVER_EXPLANATIONS));

export function buildRegimeExplanation(
  payload: MarketNowPayload,
  story: CustomerStory,
  tape?: TapeSnapshot,
): string | null {
  // 1. Known conflicts from the backend are the highest-quality authored copy — use them first.
  const conflict = (payload.known_conflicts ?? [])[0];
  if (conflict && conflict.length > 10) {
    return conflict.length > 240 ? conflict.slice(0, 237) + "…" : conflict;
  }

  const drivers = payload.key_drivers ?? [];
  const state = story.market_state;
  const spyUp = tape?.spy_pct != null && tape.spy_pct > 0.1;
  const spyDown = tape?.spy_pct != null && tape.spy_pct < -0.1;

  // 2. Regime-tape contradiction: label says risk-off but price action is constructive.
  if (state === "risk-off" && spyUp) {
    const negDriver = drivers.find(d => NEGATIVE_DRIVER_SET.has(d));
    if (negDriver) return NEGATIVE_DRIVER_EXPLANATIONS[negDriver];
    return "Markets are up today but a structural headwind is active — the risk-off signal is coming from under the surface, not the headline index.";
  }

  // 3. Label says risk-on but price is falling — explain what's supporting the call.
  if (state === "risk-on" && spyDown) {
    const posDriver = drivers.find(d => POSITIVE_DRIVER_SET.has(d));
    if (posDriver) return POSITIVE_DRIVER_EXPLANATIONS[posDriver];
    return "Structural risk-on signals are active even as equities pull back — the underlying driver may reflect institutional positioning ahead of price.";
  }

  // 4. Mixed: both positive and negative forces are in play — explain the tension.
  if (state === "mixed") {
    const negDriver = drivers.find(d => NEGATIVE_DRIVER_SET.has(d));
    const posDriver = drivers.find(d => POSITIVE_DRIVER_SET.has(d));
    if (negDriver && posDriver) {
      const neg = NEGATIVE_DRIVER_EXPLANATIONS[negDriver];
      const pos = POSITIVE_DRIVER_EXPLANATIONS[posDriver];
      // First sentence of positive driver + lead-in to negative
      return `${pos.split(".")[0]}. At the same time, ${neg.charAt(0).toLowerCase() + neg.slice(1)}`;
    }
    if (negDriver) return NEGATIVE_DRIVER_EXPLANATIONS[negDriver];
    if (posDriver) return POSITIVE_DRIVER_EXPLANATIONS[posDriver];
  }

  // 5. Reinforcing — explain the top driver briefly.
  const topDriver = drivers[0];
  if (topDriver && POSITIVE_DRIVER_EXPLANATIONS[topDriver]) return POSITIVE_DRIVER_EXPLANATIONS[topDriver];
  if (topDriver && NEGATIVE_DRIVER_EXPLANATIONS[topDriver]) return NEGATIVE_DRIVER_EXPLANATIONS[topDriver];

  return null;
}

export function buildCustomerMarketStory(
  payload: MarketNowPayload,
  story: CustomerStory,
  tape?: TapeSnapshot,
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
    regime_explanation: buildRegimeExplanation(payload, story, tape),
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

// ── Tape Snapshot ─────────────────────────────────────────────────────────────
// Derived from /api/market-tape. Passed to buildNarrativeParagraph so the
// opening sentence can reference real multi-asset price-action context.

export interface TapeSnapshot {
  spy_pct:   number | null;
  qqq_pct:   number | null;
  dia_pct:   number | null;  // DIA ETF — Dow proxy
  iwm_pct:   number | null;  // IWM — small-cap breadth proxy
  tlt_pct:   number | null;
  gld_pct:   number | null;
  uso_pct:   number | null;
  dxy_pct:   number | null;  // UUP ETF — US dollar proxy
  es_pct:    number | null;  // ESUSD — S&P futures
  nq_pct:    number | null;  // NQUSD — Nasdaq futures
  vix_level: number | null;
  // Sector ETFs
  xlf_pct:   number | null;  // Financials
  xlk_pct:   number | null;  // Technology
  xle_pct:   number | null;  // Energy
  xlv_pct:   number | null;  // Health Care
  xli_pct:   number | null;  // Industrials
  xlu_pct:   number | null;  // Utilities
  xlb_pct:   number | null;  // Materials
  xlre_pct:  number | null;  // Real Estate
}

// ── Narrative Paragraph ────────────────────────────────────────────────────────

const NARRATIVE_FALLBACK_PHRASES = [
  "assessing market", "gathering", "check back",
  "structural themes are in focus", "no dominant", "markets are being monitored",
];

function isCleanApiSummary(text: string | undefined): boolean {
  if (!text || text.trim().length < 30) return false;
  const lower = text.toLowerCase();
  if (NARRATIVE_FALLBACK_PHRASES.some(f => lower.includes(f))) return false;
  if (containsProhibitedTerm(text)) return false;
  return true;
}

function formatSignedPct(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

// ── Multi-scenario tape analysis ───────────────────────────────────────────────

type TapeScenario =
  | "narrow_rally"    // QQQ up, IWM materially lagging
  | "tech_led"        // QQQ outperforms SPY by ≥ 0.5%, both positive
  | "broad_risk_on"   // SPY + QQQ both > 0.4%, VIX contained
  | "defensive"       // SPY weak, bonds or gold catching a bid
  | "broad_risk_off"  // SPY materially negative
  | "quiet"           // All moves small
  | "spy_basic";      // Directional but no other pattern detected

function detectTapeScenario(tape: TapeSnapshot): TapeScenario {
  const spy = tape.spy_pct;
  const qqq = tape.qqq_pct;
  const iwm = tape.iwm_pct;
  const tlt = tape.tlt_pct;
  const gld = tape.gld_pct;
  const vix = tape.vix_level;

  if (spy == null) return "spy_basic";

  // Narrow rally: Nasdaq up but small caps lagging or negative
  if (qqq != null && iwm != null && qqq > 0.4 && iwm < -0.1) return "narrow_rally";

  // Tech-led: QQQ outperforms SPY by 0.5%+, both positive
  if (qqq != null && spy > 0 && qqq - spy >= 0.5) return "tech_led";

  // Broad risk-on: SPY and QQQ both positive, VIX contained
  if (qqq != null && spy > 0.4 && qqq > 0.4 && (vix == null || vix < 18)) return "broad_risk_on";

  // Defensive: SPY under pressure but bonds or gold catching a bid
  const defensiveBid = (tlt != null && tlt > 0.3) || (gld != null && gld > 0.4);
  if (spy < -0.3 && defensiveBid) return "defensive";

  // Broad risk-off: SPY materially negative
  if (spy < -0.4) return "broad_risk_off";

  // Quiet tape: no dominant directional move
  if (Math.abs(spy) <= 0.15 && (qqq == null || Math.abs(qqq) <= 0.2)) return "quiet";

  return "spy_basic";
}

interface TapeOpenerResult {
  sentence: string;
  scenario: TapeScenario;
}

function buildTapeOpener(tape: TapeSnapshot): TapeOpenerResult | null {
  if (tape.spy_pct == null) return null;

  const scenario = detectTapeScenario(tape);
  const spy = formatSignedPct(tape.spy_pct);
  const qqq = tape.qqq_pct != null ? formatSignedPct(tape.qqq_pct) : null;
  const vix = tape.vix_level;

  let sentence: string;

  switch (scenario) {
    case "narrow_rally": {
      const qqqStr = qqq ?? "higher";
      sentence = `Markets are not just moving higher today. The leadership is concentrated in growth and technology — the Nasdaq is up ${qqqStr} while small caps are lagging, making the rally narrower than the headline index suggests.`;
      break;
    }
    case "tech_led": {
      sentence = `Equities are advancing, but the move is led by technology. The Nasdaq is up ${qqq ?? "meaningfully"} versus the broader S&P 500 at ${spy} — a growth-concentrated session.`;
      break;
    }
    case "broad_risk_on": {
      let s = `Equities are advancing broadly today — the S&P 500 is ${spy} and the Nasdaq is ${qqq ?? "also higher"}`;
      if (vix != null && vix < 15) s += `, with volatility contained at ${vix.toFixed(0)}`;
      else if (vix != null && vix < 18) s += `, while volatility remains moderate`;
      sentence = s + ".";
      break;
    }
    case "defensive": {
      const defensivePart =
        tape.tlt_pct != null && tape.tlt_pct > 0.3 ? "bonds are rallying" :
        tape.gld_pct != null && tape.gld_pct > 0.4 ? "gold is gaining" :
        "defensive assets are holding up";
      sentence = `Equities are under pressure — the S&P 500 is ${spy} — but ${defensivePart}, suggesting investors are rotating toward safety rather than liquidating broadly.`;
      break;
    }
    case "broad_risk_off": {
      let s = `Equities are under broad pressure today — the S&P 500 is ${spy}`;
      if (vix != null && vix >= 25) s += `, and elevated VIX at ${vix.toFixed(0)} signals heightened uncertainty`;
      else if (vix != null && vix >= 20) s += `, with VIX at ${vix.toFixed(0)} reflecting unease`;
      sentence = s + ".";
      break;
    }
    case "quiet": {
      sentence = `Markets are quiet today — the S&P 500 is little changed at ${spy}, with no dominant directional move.`;
      break;
    }
    default: {
      const dir =
        tape.spy_pct > 0.4  ? "gaining ground" :
        tape.spy_pct < -0.4 ? "under pressure" :
        "broadly flat";
      let s = `Broad equities are ${dir} today — the S&P 500 is ${spy}`;
      if (vix != null) {
        if (vix < 15)      s += `, and volatility is contained at ${vix.toFixed(0)}`;
        else if (vix < 20) s += `, with volatility at moderate levels`;
        else if (vix < 25) s += `, while VIX at ${vix.toFixed(0)} reflects some unease`;
        else               s += `, and elevated VIX at ${vix.toFixed(0)} signals heightened uncertainty`;
      }
      sentence = s + ".";
    }
  }

  return { sentence, scenario };
}

const REGIME_OPENERS: Record<string, string> = {
  "risk-on":    "Risk appetite is constructive today.",
  "risk-off":   "Caution is dominating markets today.",
  "mixed":      "Markets are sending mixed signals today.",
  "monitoring": "Markets are in a watchful phase today.",
};

const DRIVER_MIDDLE: Record<string, string> = {
  ai_capex_growth:
    "AI infrastructure spending is the dominant story, keeping data centres, semiconductors, and power names in focus.",
  ai_compute_demand:
    "AI compute demand is accelerating, drawing capital into chips, data centres, and cooling names.",
  geopolitical_risk_rising:
    "Elevated geopolitical risk is sustaining defence budget growth and safe-haven demand.",
  futures_risk_on:
    "Equity futures pointed higher heading into the session, adding to the constructive backdrop for growth names.",
  futures_risk_off:
    "Cautious equity futures are creating near-term headwinds for higher-beta names.",
  yields_falling:
    "Falling bond yields are supportive for rate-sensitive sectors — housing, real estate, and long-duration growth names are benefiting.",
  yields_rising:
    "Rising bond yields are creating headwinds for rate-sensitive areas and compressing valuations for long-duration growth names.",
  risk_on_rotation:
    "Capital is rotating out of defensive positions into growth and cyclical names.",
  gold_safe_haven_bid:
    "Safe-haven demand is elevated — gold is in focus as investors seek protection from macro uncertainty.",
  credit_stress_easing:
    "Easing credit conditions are reducing risk premiums, with financials and cyclicals benefiting.",
  small_cap_risk_on:
    "Risk appetite is broadening to smaller companies — a sign of improving credit and growth sentiment.",
  oil_supply_shock:
    "Oil supply disruption is elevating energy prices, adding an inflation watchpoint to today's session.",
  smh_tactical_weakness:
    "Semiconductor momentum is showing near-term fatigue, even as the structural AI demand story remains intact.",
  reits_falling_yield:
    "Rising long-term yields are compressing REIT valuations, creating near-term pressure on real estate names.",
};

// Alternative sentences used when the macro label already names AI infrastructure,
// to prevent the same concept appearing twice in adjacent sections.
const AI_SECTOR_ALTERNATIVE =
  "Data centres, semiconductors, and power infrastructure names are in sustained focus as hyperscalers commit to record capital spending.";
const AI_CLUSTER_ALTERNATIVE =
  "The buildout is drawing capital across data centres, power infrastructure, semiconductors, and memory — both training compute and inference capacity are in demand.";

function macroLabelMentionsAI(macroLabel?: string): boolean {
  if (!macroLabel) return false;
  const l = macroLabel.toLowerCase();
  return (
    l.includes("ai infrastructure") ||
    l.includes("ai capex") ||
    l.includes("ai compute") ||
    l.includes("ai spending") ||
    l.includes("ai buildout")
  );
}

const RISK_ON_SET = new Set([
  "futures_risk_on", "risk_on_rotation", "small_cap_risk_on", "credit_stress_easing",
]);
const RISK_OFF_SET = new Set([
  "yields_rising", "futures_risk_off", "oil_supply_shock", "reits_falling_yield",
]);

function buildDriverMiddleSentence(activeIds: string[], macroLabel?: string): string | null {
  if (activeIds.length === 0) return null;

  // AI cluster — swap to sector-focused copy if macro label already names AI
  if (activeIds.includes("ai_capex_growth") && activeIds.includes("ai_compute_demand")) {
    return macroLabelMentionsAI(macroLabel)
      ? AI_CLUSTER_ALTERNATIVE
      : "AI infrastructure spending and compute demand are reinforcing each other — capital is flowing into data centres, semiconductors, and power infrastructure.";
  }

  // Single AI capex driver — swap to sector-focused copy if macro label already names AI
  if (activeIds[0] === "ai_capex_growth") {
    return macroLabelMentionsAI(macroLabel)
      ? AI_SECTOR_ALTERNATIVE
      : (DRIVER_MIDDLE["ai_capex_growth"] ?? null);
  }

  // Broad risk-on cluster
  const riskOnActive = activeIds.filter(d => RISK_ON_SET.has(d));
  if (riskOnActive.length >= 2) {
    return "Multiple risk-on signals are active simultaneously — growth and cyclical themes are broadly in focus.";
  }

  // Conflicting forces
  const hasRiskOn = activeIds.some(d => RISK_ON_SET.has(d));
  const hasRiskOff = activeIds.some(d => RISK_OFF_SET.has(d));
  if (hasRiskOn && hasRiskOff) {
    const onId  = activeIds.find(d => RISK_ON_SET.has(d))!;
    const offId = activeIds.find(d => RISK_OFF_SET.has(d))!;
    return `${FORCE_LABELS[onId]} is providing support, while ${FORCE_LABELS[offId].toLowerCase()} is acting as a counterweight.`;
  }

  return DRIVER_MIDDLE[activeIds[0]] ?? null;
}

function buildBreadthSentence(
  activeIds: string[],
  state: string,
  tape?: TapeSnapshot,
  tapeScenario?: TapeScenario,
): string | null {
  // IWM/QQQ breadth already expressed in these opener scenarios — skip repeating
  const breadthInOpener = tapeScenario === "narrow_rally" || tapeScenario === "tech_led";

  // Dollar strength — add context unless breadth is already the headline
  if (!breadthInOpener && tape?.dxy_pct != null && tape.dxy_pct > 0.4) {
    return "The US dollar is strengthening today, adding a headwind for commodity prices and emerging-market assets.";
  }

  // Oil pressure
  if (tape?.uso_pct != null && tape.uso_pct > 1.5) {
    return "Oil prices are moving sharply higher today — worth monitoring as a potential inflation signal.";
  }

  // Bond move — skip if defensive scenario already covered it in the opener
  if (tapeScenario !== "defensive" && tape?.tlt_pct != null) {
    if (tape.tlt_pct > 0.3)  return "Bonds are rallying — easing yield pressure on rate-sensitive names.";
    if (tape.tlt_pct < -0.4) return "Bond prices are falling — worth watching for how far yields move.";
  }

  // Small-cap outperformance not yet covered by opener
  if (
    !breadthInOpener &&
    tape?.iwm_pct != null &&
    tape?.spy_pct != null &&
    tape.iwm_pct - tape.spy_pct > 0.6
  ) {
    return "Small caps are outperforming the broader market today — a sign of improving risk appetite and breadth.";
  }

  // Theme-based breadth for risk-on when tape context is limited
  const seenLabels = new Set<string>();
  const sectorLabels: string[] = [];
  for (const id of activeIds.slice(0, 3)) {
    for (const themeId of (FORCE_THEMES[id] ?? [])) {
      const label = THEME_LABELS[themeId];
      if (label && !seenLabels.has(label)) {
        seenLabels.add(label);
        sectorLabels.push(label);
      }
    }
  }

  if (state === "risk-on" && sectorLabels.length >= 2) {
    const parts = sectorLabels.slice(0, 3);
    const joined =
      parts.length === 1 ? parts[0] :
      parts.length === 2 ? `${parts[0]} and ${parts[1]}` :
      `${parts[0]}, ${parts[1]}, and ${parts[2]}`;
    return `The story extends into ${joined}.`;
  }

  return null;
}

/**
 * Generates a 2–3 sentence natural-language market briefing paragraph.
 * Prefers `plain_english_summary` from the API when it is clean and substantive;
 * otherwise synthesises from active drivers and multi-asset tape context.
 * Safe for customer display — never exposes raw driver IDs or forbidden terms.
 */
export function buildNarrativeParagraph(
  payload: MarketNowPayload,
  story: CustomerMarketStory,
  tape?: TapeSnapshot,
): string {
  if (isCleanApiSummary(payload.plain_english_summary)) {
    const base = payload.plain_english_summary!.trim();
    return base.endsWith(".") ? base : base + ".";
  }

  const activeIds = (payload.key_drivers ?? [])
    .map(normalizeForceId)
    .filter(id => Boolean(FORCE_LABELS[id]));

  const parts: string[] = [];

  const tapeResult = tape ? buildTapeOpener(tape) : null;
  const opener = tapeResult?.sentence ?? REGIME_OPENERS[story.regime.state] ?? "Markets are active today.";
  const tapeScenario = tapeResult?.scenario ?? undefined;
  parts.push(opener);

  const middle = buildDriverMiddleSentence(activeIds, story.macro_label);
  if (middle) parts.push(middle);

  const breadth = buildBreadthSentence(activeIds, story.regime.state, tape, tapeScenario);
  if (breadth) parts.push(breadth);

  return parts.join(" ");
}

// ── Where Decifer Is Looking ───────────────────────────────────────────────────

export interface WhereLookingName {
  symbol: string;
  reason: string;
  theme_label: string;
  exposure_type?: string; // "Direct" | "Supply chain" | "ETF" | undefined
}

export interface WhereLooking {
  /** Deduplicated human-readable sector/theme labels from active drivers. */
  stories: string[];
  /** Up to 5 names from radar / universe_snapshot connected to active drivers. */
  names: WhereLookingName[];
  empty: boolean;
}

// Strips the boilerplate template prefix from radar reason strings:
// "VRT is a direct beneficiary of Data Centre Power: [insight]" → "[insight]"
function distillReason(raw: string): string {
  const colonIdx = raw.indexOf(": ");
  if (colonIdx > 0 && colonIdx < raw.length - 15) {
    return raw.slice(colonIdx + 2).trim();
  }
  return raw.trim();
}

// Maps raw exposure_type strings to a short customer-readable chip label.
function parseExposureChip(raw: string): string | undefined {
  const lower = raw.toLowerCase();
  if (lower.includes("etf") || lower.includes("sector-level")) return "ETF";
  if (lower.includes("second-order") || lower.includes("supply_chain") || lower.includes("supply chain") || lower.includes("indirect")) return "Supply chain";
  if (lower.includes("direct")) return "Direct";
  return undefined;
}

/**
 * Fallback path used when TTG is unavailable.
 * Buckets radar/universe items by theme, caps at 2 per theme, and applies
 * a day-based rotation so VRT isn't always top.
 */
export function buildWhereLooking(payload: MarketNowPayload): WhereLooking {
  const activeIds = (payload.key_drivers ?? [])
    .map(normalizeForceId)
    .filter(id => Boolean(FORCE_LABELS[id]));

  if (activeIds.length === 0) return { stories: [], names: [], empty: true };

  // Story labels from FORCE_THEMES, deduplicated
  const seenStories = new Set<string>();
  const stories: string[] = [];
  for (const id of activeIds) {
    for (const themeId of (FORCE_THEMES[id] ?? [])) {
      const label = THEME_LABELS[themeId];
      if (label && !seenStories.has(label)) {
        seenStories.add(label);
        stories.push(label);
      }
    }
  }

  const activeThemeIds = new Set(activeIds.flatMap(id => FORCE_THEMES[id] ?? []));

  // Collect all matching names bucketed by theme
  const seenSymbols = new Set<string>();
  const byTheme: Record<string, WhereLookingName[]> = {};

  const collect = (symbol: string, rawReason: string, themeId: string | null | undefined) => {
    if (!themeId || !activeThemeIds.has(themeId) || seenSymbols.has(symbol)) return;
    seenSymbols.add(symbol);
    const label = THEME_LABELS[themeId] ?? themeId.replace(/_/g, " ");
    if (!byTheme[themeId]) byTheme[themeId] = [];
    byTheme[themeId].push({
      symbol,
      reason: distillReason(rawReason),
      theme_label: label,
      exposure_type: parseExposureChip(rawReason),
    });
  };

  for (const r of (payload.radar ?? [])) collect(r.symbol, r.reason_to_watch, r.theme_link);
  for (const u of (payload.universe_snapshot ?? [])) collect(u.symbol, u.why_connected, u.theme_id);

  // Round-robin across themes with a day-based offset, max 2 per theme
  const dayOffset = Math.floor(Date.now() / 86400000) % 7;
  const themeKeys = Object.keys(byTheme);
  const names: WhereLookingName[] = [];
  const countByTheme: Record<string, number> = {};

  for (let round = 0; round < 4 && names.length < 5; round++) {
    for (const theme of themeKeys) {
      if (names.length >= 5) break;
      if ((countByTheme[theme] ?? 0) >= 2) continue;
      const bucket = byTheme[theme];
      const idx = (round + dayOffset) % bucket.length;
      const candidate = bucket[idx];
      if (candidate && !names.find(n => n.symbol === candidate.symbol)) {
        names.push(candidate);
        countByTheme[theme] = (countByTheme[theme] ?? 0) + 1;
      }
    }
  }

  return {
    stories: stories.slice(0, 5),
    names,
    empty: stories.length === 0 && names.length === 0,
  };
}

// ── What Could Change ──────────────────────────────────────────────────────────

const GENERIC_RISKS = [
  "Watch for unexpected central bank guidance changes that could shift rate expectations.",
  "Geopolitical developments or earnings surprises can quickly alter sector leadership.",
];

/**
 * Returns 2–3 customer-safe risk bullets based on active drivers and any known
 * conflicts.  Falls back to generic watchpoints when no drivers are active.
 */
export function buildWhatCouldChange(payload: MarketNowPayload): string[] {
  const activeIds = (payload.key_drivers ?? [])
    .map(normalizeForceId)
    .filter(id => Boolean(FORCE_LABELS[id]));

  const risks: string[] = [];

  for (const id of activeIds.slice(0, 2)) {
    const risk = FORCE_RISK[id];
    if (risk && !risks.includes(risk)) risks.push(risk);
  }

  const conflict = (payload.known_conflicts ?? [])[0];
  if (conflict && !risks.includes(conflict) && risks.length < 3) {
    risks.push(conflict);
  }

  return risks.length > 0 ? risks.slice(0, 3) : GENERIC_RISKS.slice();
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
