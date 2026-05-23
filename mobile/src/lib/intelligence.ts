// ── Market Intelligence Translation Layer ─────────────────────────────────────
// All backend schema IDs are contained here. UI components import helpers only.
// Raw field names (active_shadow_inferred, etc.) never reach JSX.

import { translateTheme, translateRegime } from "./translate";

// ── Core types ────────────────────────────────────────────────────────────────

export interface IntelTheme {
  theme_id: string;
  state: string;
  direction: string;
  confidence: number;
  candidate_count?: number;
  active_drivers?: string[];
  risk_flags?: string[];
  invalidation_rules?: string[];
  route_bias?: string;
  evidence?: string[];
  horizon?: string;
  used_live_data?: boolean;
  freshness_status?: string;
}

export interface IntelCandidate {
  symbol: string;
  theme: string;
  role: string;
  reason_to_care: string;
  reason?: string;
  confidence: number;
  risk_flags?: string[];
  route_hint?: string[];
  driver?: string;
  confirmation_required?: string[];
}

export interface IntelEvidence {
  smh_5d_ret?: number;
  nvda_5d_ret?: number;
  ief_5d_ret?: number;
  uso_5d_ret?: number;
  spy_5d_ret?: number;
  ita_5d_ret?: number;
  uvxy_5d_ret?: number;
  hyg_5d_ret?: number;
  lqd_5d_ret?: number;
  gld_5d_ret?: number;
  iwm_5d_ret?: number;
  [key: string]: number | string | undefined;
}

export interface IntelMarketMap {
  active_drivers: string[];
  blocked_conditions?: string[];
  mode?: string;
  evidence?: IntelEvidence;
}

export interface IntelResponse {
  ts?: string;
  themes?: IntelTheme[];
  candidates?: IntelCandidate[];
  market_map?: IntelMarketMap;
  theme_summary?: { activated?: number; total_themes?: number };
  universe_summary?: Record<string, unknown>;
}

export interface DriverInfo {
  displayLabel: string;
  shortMeaning: string;
  traderMeaning: string;
  whyItMatters: string;
  proxySymbol: string;
  positiveInterpretation: string;
  invalidationTrigger: string;
  affectedThemes: string[];
  sentimentType: "tailwind" | "headwind" | "neutral";
}

export interface DriverEvidence {
  measurement: string;
  interpretation: string;
  causalChain: string;
  threshold: string;
  status: "confirming" | "warning" | "inactive";
  affectedThemeLabels: string[];
}

export interface RiskFlagInfo {
  displayLabel: string;
  traderMeaning: string;
  severity: "low" | "medium" | "high";
  effect: "blocks" | "reduces_confidence" | "warns";
  removalTrigger: string;
}

export interface ScoreBand {
  label: string;
  meaning: string;
  color: string;
  bgColor: string;
  borderColor: string;
}

export interface MarketStory {
  headline: string;
  bullets: string[];
  expectation: string;
  attention: string;
  risks: string[];
  tradingMode: string;
  overallSentiment: "risk-on" | "mixed" | "risk-off" | "neutral";
}

// ── Driver Dictionary ─────────────────────────────────────────────────────────

export const DRIVER_DICTIONARY: Record<string, DriverInfo> = {
  ai_capex_growth: {
    displayLabel: "AI Infrastructure Spending",
    shortMeaning: "Semiconductor leadership is intact — AI capex is being confirmed by chip stocks",
    traderMeaning:
      "The system uses SMH (semiconductor ETF) as the primary proxy for AI infrastructure spending. When SMH has not fallen more than 8% over 5 days, it is treated as evidence that hyperscalers and enterprises are still committing to AI capacity buildout.",
    whyItMatters:
      "AI capex is the engine driving semiconductor demand, data-centre power infrastructure, memory, and compute hardware. If this signal holds, all AI-adjacent sectors are well-supported.",
    proxySymbol: "SMH",
    positiveInterpretation:
      "Semiconductor and AI infrastructure names are supported. Favour data-centre power, chips, memory, and compute infrastructure.",
    invalidationTrigger:
      "SMH falls more than 8% over 5 days, or major cloud platforms cut AI capex guidance.",
    affectedThemes: ["data_centre_power", "semiconductors", "ai_compute_infrastructure", "memory_storage"],
    sentimentType: "tailwind",
  },
  ai_compute_demand: {
    displayLabel: "AI Compute Demand",
    shortMeaning: "NVDA is holding its level — GPU and AI accelerator demand is active",
    traderMeaning:
      "NVDA (Nvidia) serves as the primary proxy for AI compute demand. If NVDA has not fallen more than 5% over 5 days, the system treats AI compute demand as intact, reflecting continued demand for accelerators from cloud platforms and enterprises.",
    whyItMatters:
      "Nvidia's price action directly reflects GPU and AI accelerator purchasing activity. A breakdown warns that enterprises are slowing AI compute investments.",
    proxySymbol: "NVDA",
    positiveInterpretation:
      "Continue favouring AI compute, chip design, and infrastructure names while NVDA holds its level.",
    invalidationTrigger: "NVDA falls more than 5% over 5 days.",
    affectedThemes: ["ai_compute_infrastructure", "semiconductors", "data_centre_power"],
    sentimentType: "tailwind",
  },
  geopolitical_risk_rising: {
    displayLabel: "Defence Sector Leading",
    shortMeaning: "ITA is outperforming SPY — investors are pricing in geopolitical risk",
    traderMeaning:
      "ITA (US defence and aerospace ETF) is being measured against SPY. When ITA outperforms SPY by more than 2% over 5 days, it signals that investors are rotating into defence names — typically driven by elevated geopolitical risk or rising defence budget expectations.",
    whyItMatters:
      "Defence sector outperformance is evidence that the market is pricing elevated geopolitical risk or expecting increased government spending on defence. This supports aerospace and defence contractors.",
    proxySymbol: "ITA vs SPY",
    positiveInterpretation: "Defence-linked names have a tailwind from sector leadership and geopolitical risk pricing.",
    invalidationTrigger: "ITA stops outperforming SPY — sector leadership fades.",
    affectedThemes: ["defence_aerospace"],
    sentimentType: "tailwind",
  },
  small_cap_risk_on: {
    displayLabel: "Market Breadth Broadening",
    shortMeaning: "Small caps are outperforming large caps — participation is widening",
    traderMeaning:
      "IWM (Russell 2000 small-cap ETF) is compared against SPY. When IWM outperforms by more than 1.5% over 5 days, it confirms that market participation is broadening beyond a handful of mega-cap technology names.",
    whyItMatters:
      "A narrow market led by only a few stocks is fragile. Broadening breadth — small caps keeping pace with large caps — increases confidence that the rally has durable support.",
    proxySymbol: "IWM vs SPY",
    positiveInterpretation:
      "Risk-on continuation is more credible. Favour setups with broad market confirmation over narrow mega-cap plays.",
    invalidationTrigger: "IWM underperforms SPY — breadth narrows back to mega-cap tech.",
    affectedThemes: ["small_cap_risk_on", "risk_on_rotation"],
    sentimentType: "tailwind",
  },
  yields_falling: {
    displayLabel: "Falling Bond Yields",
    shortMeaning: "Yields are declining — growth stocks and rate-sensitive sectors benefit",
    traderMeaning:
      "IEF (7–10 year Treasury ETF) rising over 5 days means bond yields are falling. Lower yields reduce the discount rate applied to future earnings, mechanically boosting valuations for growth, software, and long-duration names.",
    whyItMatters:
      "Falling yields improve the relative attractiveness of equities vs bonds and reduce borrowing costs. REITs, software-as-a-service, and high-growth names are direct beneficiaries.",
    proxySymbol: "IEF",
    positiveInterpretation: "Rate-sensitive growth names and REITs are supported. Favour duration over defensives.",
    invalidationTrigger: "IEF falls — yields reverse higher.",
    affectedThemes: ["yields_falling", "reits", "software_cloud"],
    sentimentType: "tailwind",
  },
  credit_stress_easing: {
    displayLabel: "Credit Markets Improving",
    shortMeaning: "High-yield spreads are tightening — corporate risk appetite is healthy",
    traderMeaning:
      "HYG (high-yield ETF) outperforming LQD (investment-grade ETF) indicates that credit spreads are tightening. This means investors are demanding less extra yield to hold riskier corporate debt — a sign of improving confidence in corporate credit quality.",
    whyItMatters:
      "Easing credit conditions broadly support risk assets. When junk bonds perform well, it signals the market is not pricing a near-term recession or credit event, increasing confidence in equity continuation.",
    proxySymbol: "HYG vs LQD",
    positiveInterpretation: "Risk appetite is healthy. Favour growth and cyclical exposure over defensives.",
    invalidationTrigger: "HYG weakens relative to LQD — spreads start widening again.",
    affectedThemes: ["credit_stress_easing", "risk_on_rotation"],
    sentimentType: "tailwind",
  },
  risk_on_rotation: {
    displayLabel: "Risk-On Rotation Confirmed",
    shortMeaning: "Volatility falling, SPY rising — capital is moving into risk assets",
    traderMeaning:
      "UVXY (short-term volatility product) falling while SPY rises is a dual-signal confirmation that investors are selling hedges and buying risk. This is one of the cleanest risk-on confirmation signals.",
    whyItMatters:
      "When volatility is being sold while equities rise, it confirms conviction behind the move. It is not just equities drifting up — active rotation into risk is occurring.",
    proxySymbol: "UVXY + SPY",
    positiveInterpretation: "Risk assets broadly favoured. Continuation setups have the edge across most equity sectors.",
    invalidationTrigger: "UVXY spikes and SPY weakens — risk-off rotation begins.",
    affectedThemes: ["risk_on_rotation", "small_cap_risk_on", "consumer_discretionary"],
    sentimentType: "tailwind",
  },
  yields_rising: {
    displayLabel: "Rising Yields Pressure",
    shortMeaning: "Bond yields climbing — pressure on growth stocks and rate-sensitive sectors",
    traderMeaning:
      "IEF (Treasury ETF) declining over 5 days means bond yields are rising. Higher yields compress the discount rate for future earnings and make bonds more competitive as an alternative to equities.",
    whyItMatters:
      "Rising yields pressure long-duration growth names, REITs, and high-multiple software. They also increase borrowing costs for leveraged companies.",
    proxySymbol: "IEF",
    positiveInterpretation: "None — this is a headwind. Reduce duration, avoid high-multiple growth and REITs.",
    invalidationTrigger: "IEF stabilises or rises — yields stop climbing.",
    affectedThemes: ["yields_rising", "reits_falling_yield"],
    sentimentType: "headwind",
  },
  credit_stress_rising: {
    displayLabel: "Credit Stress Emerging",
    shortMeaning: "High-yield spreads widening — risk appetite may be deteriorating",
    traderMeaning:
      "HYG underperforming LQD means investors are demanding more yield to hold riskier corporate debt. Widening spreads warn that corporate credit conditions are tightening and risk appetite may be weakening.",
    whyItMatters:
      "Credit stress is often an early warning signal. When junk bonds sell off, equity markets often follow. This is one of the most reliable risk-off early indicators.",
    proxySymbol: "HYG vs LQD",
    positiveInterpretation: "None — reduce exposure to speculative and highly leveraged names.",
    invalidationTrigger: "HYG recovers and the spread stops widening.",
    affectedThemes: [],
    sentimentType: "headwind",
  },
  risk_off_rotation: {
    displayLabel: "Risk-Off Rotation",
    shortMeaning: "Volatility rising, SPY falling — capital is moving to safety",
    traderMeaning:
      "UVXY rising while SPY falls is a dual-signal confirmation that investors are buying protection and selling equities. This is the opposite of risk-on — active defensive rotation.",
    whyItMatters:
      "When volatility is being bought while equities fall, the defensive move is intentional, not just noise. Speculative and high-multiple names are hit hardest in risk-off environments.",
    proxySymbol: "UVXY + SPY",
    positiveInterpretation: "None — reduce risk, favour defensives or hold cash.",
    invalidationTrigger: "UVXY falls back and SPY stabilises.",
    affectedThemes: ["defensive_healthcare", "gold_safe_haven_bid"],
    sentimentType: "headwind",
  },
  gold_safe_haven_bid: {
    displayLabel: "Safe-Haven Demand (Gold)",
    shortMeaning: "Gold is rising — investors are hedging against uncertainty",
    traderMeaning:
      "GLD (gold ETF) rising more than 3% over 5 days signals that investors are actively buying safe-haven assets. This typically reflects inflation hedging, currency risk concerns, or geopolitical uncertainty.",
    whyItMatters:
      "A gold bid can coexist with equity strength, but it warns that some investors are reducing confidence in risk assets. If gold strength accelerates alongside equity weakness, it can amplify a downturn.",
    proxySymbol: "GLD",
    positiveInterpretation: "Gold names and defensives may outperform. Monitor for equity risk-off follow-through.",
    invalidationTrigger: "GLD falls — safe-haven demand fades.",
    affectedThemes: ["gold_safe_haven_bid", "defensive_healthcare"],
    sentimentType: "neutral",
  },
  oil_supply_shock: {
    displayLabel: "Energy Supply Shock",
    shortMeaning: "Oil prices surging — energy names benefit, consumers are pressured",
    traderMeaning:
      "USO (oil ETF) rising sharply over 5 days signals an oil supply disruption. Energy producers benefit from higher prices, but airlines, transports, consumer discretionary, and industrials face higher input costs.",
    whyItMatters:
      "An energy shock raises inflation expectations, squeezes consumer budgets, and can weigh on the broader market while lifting energy names.",
    proxySymbol: "USO",
    positiveInterpretation: "Energy producers may benefit. Most other sectors face cost pressure from the shock.",
    invalidationTrigger: "USO falls back — oil supply concerns ease.",
    affectedThemes: ["oil_supply_shock"],
    sentimentType: "neutral",
  },
};

// ── Risk Flag Dictionary ───────────────────────────────────────────────────────

export const RISK_FLAG_DICTIONARY: Record<string, RiskFlagInfo> = {
  valuation: {
    displayLabel: "Valuation Risk",
    traderMeaning: "This name may be priced for perfection. A miss on growth expectations can trigger a sharp selloff even when the underlying thesis is intact.",
    severity: "medium",
    effect: "reduces_confidence",
    removalTrigger: "Earnings acceleration, multiple compression to reasonable levels, or improved price-to-growth ratio.",
  },
  crowding: {
    displayLabel: "Crowded Trade",
    traderMeaning: "Heavy institutional positioning. When crowded trades reverse, the move is outsized because everyone is heading for the same exit.",
    severity: "medium",
    effect: "reduces_confidence",
    removalTrigger: "Positioning data showing reduced crowding, or price holding firmly after a major shakeout.",
  },
  capex_delay: {
    displayLabel: "AI Capex Delay Risk",
    traderMeaning: "If hyperscalers delay or reduce AI infrastructure investment, demand for data-centre power and compute equipment can fall quickly — even if the long-term thesis remains intact.",
    severity: "high",
    effect: "reduces_confidence",
    removalTrigger: "Reaffirmed AI capex spending guidance from major cloud platforms (AWS, Azure, Google Cloud).",
  },
  credit_stress: {
    displayLabel: "Credit Sensitivity",
    traderMeaning: "This name's thesis depends on accessible credit. Tightening credit conditions or rising borrowing costs directly weaken the investment case.",
    severity: "medium",
    effect: "warns",
    removalTrigger: "Stable or easing high-yield credit spreads.",
  },
  power_demand_disappointment: {
    displayLabel: "Power Demand Slowdown",
    traderMeaning: "AI data-centre power demand could disappoint if compute efficiency improves faster than expected or buildout timelines are pushed out.",
    severity: "medium",
    effect: "reduces_confidence",
    removalTrigger: "Continued hyperscaler capacity announcements and confirmed power purchase agreements.",
  },
  memory_cycle_risk: {
    displayLabel: "Memory Pricing Cycle",
    traderMeaning: "Memory stocks are highly cyclical. Supply can build quickly and pricing can reverse sharply even when demand looks healthy. One bad quarter can unwind months of gains.",
    severity: "high",
    effect: "reduces_confidence",
    removalTrigger: "Improving memory pricing data, positive guidance from DRAM/NAND makers, or confirmed supply discipline.",
  },
  commodity_pricing: {
    displayLabel: "Commodity Pricing Pressure",
    traderMeaning: "Input cost volatility can compress margins if revenue growth slows. Pricing power is the key variable to watch.",
    severity: "medium",
    effect: "warns",
    removalTrigger: "Stable commodity prices or demonstrated pricing power in earnings guidance.",
  },
  speculative_growth: {
    displayLabel: "Speculative Growth Profile",
    traderMeaning: "High-growth, high-risk. This name's valuation depends on sustained execution and market confidence — both can reverse quickly on any disappointment.",
    severity: "high",
    effect: "reduces_confidence",
    removalTrigger: "Profitable revenue growth, reduced multiple, or confirmed execution milestones.",
  },
  financing_risk: {
    displayLabel: "Financing Risk",
    traderMeaning: "Depends on external capital. If funding conditions tighten or access to credit narrows, the entire thesis can unravel quickly regardless of business progress.",
    severity: "high",
    effect: "reduces_confidence",
    removalTrigger: "Secured long-term financing, improved operating cash flow, or easing capital market conditions.",
  },
  budget_risk: {
    displayLabel: "Defence Budget Risk",
    traderMeaning: "Government defence spending allocations shift with political priorities. A budget cut or programme reallocation directly impacts revenue forecasts.",
    severity: "medium",
    effect: "warns",
    removalTrigger: "Confirmed multi-year contract wins or sustained defence spending commitments.",
  },
  de_escalation: {
    displayLabel: "Geopolitical De-escalation Risk",
    traderMeaning: "If geopolitical tensions ease, the premium in defence-related names would compress. Investors pricing geopolitical risk would rotate out.",
    severity: "medium",
    effect: "warns",
    removalTrigger: "Sustained defence sector outperformance independent of geopolitical headlines.",
  },
  geopolitical_sensitivity: {
    displayLabel: "Geopolitical Sensitivity",
    traderMeaning: "Direct exposure to geopolitical developments. Escalation helps; de-escalation hurts. Position sizing should reflect this binary sensitivity.",
    severity: "medium",
    effect: "warns",
    removalTrigger: "Diversified revenue base or long-cycle contracts independent of near-term geopolitical events.",
  },
  rate_sensitivity: {
    displayLabel: "Interest Rate Sensitivity",
    traderMeaning: "Rising interest rates compress this name's valuation or increase its borrowing costs directly. A rate move in the wrong direction can meaningfully change the thesis.",
    severity: "medium",
    effect: "reduces_confidence",
    removalTrigger: "Stable or declining bond yields.",
  },
  liquidity: {
    displayLabel: "Liquidity Risk",
    traderMeaning: "Thinner trading volume can mean wider spreads and harder fills. Entry and exit timing matter more than in liquid names.",
    severity: "low",
    effect: "warns",
    removalTrigger: "Improved volume or confirmed spread check at execution.",
  },
  etf_tracking_error: {
    displayLabel: "ETF Tracking Risk",
    traderMeaning: "Sector ETF proxies may not perfectly track the underlying theme. Leveraged ETFs carry long-term decay risk from daily rebalancing.",
    severity: "low",
    effect: "warns",
    removalTrigger: "Use direct beneficiaries where available for better theme precision.",
  },
};

// ── Role & Route Dictionaries ─────────────────────────────────────────────────

export const ROLE_DICTIONARY: Record<string, { displayLabel: string; meaning: string; color: string; bgColor: string }> = {
  direct_beneficiary: {
    displayLabel: "Direct Beneficiary",
    meaning: "Core business directly benefits from the active theme driver",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10",
  },
  second_order_beneficiary: {
    displayLabel: "Secondary Beneficiary",
    meaning: "Benefits indirectly — theme tailwind may support demand but is not the primary driver",
    color: "text-blue-400",
    bgColor: "bg-blue-500/10",
  },
  etf_proxy: {
    displayLabel: "ETF Proxy",
    meaning: "Sector ETF — broad theme exposure, less precise than direct company beneficiaries",
    color: "text-amber-400",
    bgColor: "bg-amber-500/10",
  },
};

export const ROUTE_HINT_LABEL: Record<string, string> = {
  position: "Position trade",
  swing: "Swing trade",
  watchlist: "Watchlist",
};

// Canonical ordered list of all known market driver IDs
export const ALL_DRIVER_IDS = [
  "ai_capex_growth",
  "ai_compute_demand",
  "geopolitical_risk_rising",
  "small_cap_risk_on",
  "yields_falling",
  "yields_rising",
  "credit_stress_easing",
  "credit_stress_rising",
  "risk_on_rotation",
  "risk_off_rotation",
  "gold_safe_haven_bid",
  "oil_supply_shock",
] as const;

// Which drivers could activate a dormant theme (reverse lookup)
export const THEME_ACTIVATION_DRIVERS: Record<string, string[]> = {
  data_centre_power:         ["ai_capex_growth"],
  semiconductors:            ["ai_capex_growth", "ai_compute_demand"],
  ai_compute_infrastructure: ["ai_capex_growth", "ai_compute_demand"],
  memory_storage:            ["ai_capex_growth", "ai_compute_demand"],
  defence_aerospace:         ["geopolitical_risk_rising"],
  defence:                   ["geopolitical_risk_rising"],
  small_cap_risk_on:         ["small_cap_risk_on"],
  risk_on_rotation:          ["risk_on_rotation", "small_cap_risk_on", "credit_stress_easing"],
  gold_safe_haven_bid:       ["gold_safe_haven_bid"],
  credit_stress_easing:      ["credit_stress_easing"],
  yields_falling:            ["yields_falling"],
  yields_rising:             ["yields_rising"],
  reits_falling_yield:       ["yields_rising"],
  reits:                     ["yields_falling"],
  software_cloud:            ["yields_falling", "ai_capex_growth"],
  cybersecurity:             ["ai_capex_growth"],
  mega_cap_platforms:        ["ai_capex_growth", "risk_on_rotation"],
  consumer_discretionary:    ["small_cap_risk_on", "risk_on_rotation"],
  travel_leisure:            ["risk_on_rotation", "small_cap_risk_on"],
  defensive_healthcare:      ["risk_off_rotation", "gold_safe_haven_bid"],
  biotech:                   ["risk_on_rotation"],
  regional_banks:            ["yields_rising", "risk_on_rotation"],
  infrastructure_reshoring:  ["risk_on_rotation", "small_cap_risk_on"],
  copper_electrification:    ["risk_on_rotation", "small_cap_risk_on"],
  oil_supply_shock:          ["oil_supply_shock"],
};

// ── Score Bands ────────────────────────────────────────────────────────────────

export function getScoreBand(confidence: number): ScoreBand {
  const pct = confidence * 100;
  if (pct >= 80)
    return {
      label: "High Conviction",
      meaning: "Trade-ready if entry confirms. Theme is active and signal is strong.",
      color: "text-emerald-400",
      bgColor: "bg-emerald-500/10",
      borderColor: "border-emerald-500/20",
    };
  if (pct >= 65)
    return {
      label: "Strong Watchlist",
      meaning: "Well-supported by the theme. Needs entry confirmation before adding.",
      color: "text-blue-400",
      bgColor: "bg-blue-500/10",
      borderColor: "border-blue-500/20",
    };
  if (pct >= 50)
    return {
      label: "Developing",
      meaning: "Theme active but confidence is partial. Monitor for improvement.",
      color: "text-amber-400",
      bgColor: "bg-amber-500/10",
      borderColor: "border-amber-500/20",
    };
  return {
    label: "Low Confidence",
    meaning: "Weak theme fit or elevated risk. Avoid unless conditions improve significantly.",
    color: "text-slate-500",
    bgColor: "bg-slate-500/10",
    borderColor: "border-slate-500/20",
  };
}

// ── Route hint → trade readiness label ───────────────────────────────────────

export function formatRouteHint(hints: string[] | undefined): string {
  if (!hints || hints.length === 0) return "Watchlist";
  if (hints.includes("position") && hints.includes("swing")) return "Position or swing";
  if (hints.includes("position")) return "Position trade";
  if (hints.includes("swing")) return "Swing trade";
  return "Watchlist";
}

// ── Driver evidence explainer ─────────────────────────────────────────────────

function pctStr(v: number): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export function explainDriverEvidence(
  driverId: string,
  evidence: IntelEvidence,
  isActive: boolean,
): DriverEvidence {
  const smh  = (evidence.smh_5d_ret  ?? 0) as number;
  const nvda = (evidence.nvda_5d_ret ?? 0) as number;
  const ief  = (evidence.ief_5d_ret  ?? 0) as number;
  const uso  = (evidence.uso_5d_ret  ?? 0) as number;
  const ita  = (evidence.ita_5d_ret  ?? 0) as number;
  const spy  = (evidence.spy_5d_ret  ?? 0) as number;
  const uvxy = (evidence.uvxy_5d_ret ?? 0) as number;
  const hyg  = (evidence.hyg_5d_ret  ?? 0) as number;
  const lqd  = (evidence.lqd_5d_ret  ?? 0) as number;
  const gld  = (evidence.gld_5d_ret  ?? 0) as number;
  const iwm  = (evidence.iwm_5d_ret  ?? 0) as number;

  const driverInfo = DRIVER_DICTIONARY[driverId];
  const affectedThemeLabels = (driverInfo?.affectedThemes ?? []).map(translateTheme);

  switch (driverId) {
    case "ai_capex_growth":
      return {
        measurement: `SMH is ${pctStr(smh)} over the past 5 trading days`,
        interpretation: isActive
          ? `SMH has not broken down below the −8% threshold, confirming that AI infrastructure spending is still intact. The semiconductor ETF is being used as a proxy for broader AI capex activity.`
          : `SMH is near or below the breakdown threshold — the AI infrastructure spending signal is not currently active.`,
        causalChain: `SMH reflects aggregated semiconductor demand → semiconductor demand is a leading indicator of AI capex → if SMH holds, AI infrastructure spending is assumed to be intact → themes for data-centre power, chips, memory, and compute activate.`,
        threshold: `Activation: SMH 5-day return must be above −8%`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    case "ai_compute_demand":
      return {
        measurement: `NVDA is ${pctStr(nvda)} over the past 5 trading days`,
        interpretation: isActive
          ? `NVDA has not broken down below the −5% threshold — AI compute demand is treated as active. Nvidia's price action is used as the most direct proxy for AI GPU and accelerator demand.`
          : `NVDA has fallen below the −5% threshold — the AI compute demand signal is not currently active.`,
        causalChain: `NVDA revenue is driven by AI accelerator sales → NVDA price reflects forward GPU demand expectations → if NVDA holds, hyperscalers and enterprises are still buying compute capacity → AI compute infrastructure and semiconductor themes activate.`,
        threshold: `Activation: NVDA 5-day return must be above −5%`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    case "geopolitical_risk_rising": {
      const spread = ita - spy;
      return {
        measurement: `ITA ${pctStr(ita)} vs SPY ${pctStr(spy)} — defence outperformance: ${pctStr(spread)}`,
        interpretation: isActive
          ? `ITA (US defence and aerospace ETF) is outperforming SPY by ${pctStr(spread)} over 5 days. Investors are rotating into defence names, suggesting elevated geopolitical risk is being priced in.`
          : `ITA is not outperforming SPY by the required margin — the geopolitical/defence bid is not confirmed at this level.`,
        causalChain: `ITA outperforms SPY → capital is moving into defence stocks → defence leadership signals elevated geopolitical risk pricing OR expectation of increased government defence spending → defence and aerospace theme activates.`,
        threshold: `Activation: ITA must outperform SPY by more than +2% over 5 days`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    }
    case "small_cap_risk_on": {
      const spread = iwm - spy;
      return {
        measurement: `IWM ${pctStr(iwm)} vs SPY ${pctStr(spy)} — small-cap outperformance: ${pctStr(spread)}`,
        interpretation: isActive
          ? `IWM is outperforming SPY by ${pctStr(spread)} over 5 days. Small-cap leadership confirms that market participation is broadening beyond mega-cap tech — a healthy sign for continuation.`
          : `IWM is not outperforming SPY by the required margin — broad market breadth is not confirmed.`,
        causalChain: `IWM outperforms SPY → small-cap stocks are leading large-cap → participation is widening beyond a narrow group → risk appetite is broadly healthy → small-cap risk-on and risk-on rotation themes activate.`,
        threshold: `Activation: IWM must outperform SPY by more than +1.5% over 5 days`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    }
    case "yields_rising":
      return {
        measurement: `IEF (Treasury ETF) is ${pctStr(ief)} over the past 5 trading days`,
        interpretation: isActive
          ? `IEF has declined, confirming that bond yields are rising. Higher yields compress growth valuations and increase borrowing costs for leveraged companies.`
          : `IEF has not declined enough — yields are not rising to the activation threshold.`,
        causalChain: `IEF falls → Treasury prices falling means bond yields are rising → higher yields raise the discount rate for future earnings → growth stock and REIT valuations compress → rising yields and REIT pressure themes activate.`,
        threshold: `Activation: IEF 5-day return must be below −0.5%`,
        status: isActive ? "warning" : "inactive",
        affectedThemeLabels,
      };
    case "yields_falling":
      return {
        measurement: `IEF (Treasury ETF) is ${pctStr(ief)} over the past 5 trading days`,
        interpretation: isActive
          ? `IEF has risen, confirming that bond yields are falling. Lower yields reduce the discount rate for future earnings, supporting growth and rate-sensitive names.`
          : `IEF has not risen enough — yields are not falling to the activation threshold.`,
        causalChain: `IEF rises → Treasury prices rising means bond yields are falling → lower discount rate boosts long-duration growth valuations → REIT spreads vs bonds improve → falling yields and REIT opportunity themes activate.`,
        threshold: `Activation: IEF 5-day return must be above +0.5%`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    case "oil_supply_shock":
      return {
        measurement: `USO (oil ETF) is ${pctStr(uso)} over the past 5 trading days`,
        interpretation: isActive
          ? `USO has risen sharply, signalling an oil supply disruption. Energy producers benefit, but airlines, transports, and consumer names face rising costs.`
          : `USO has not risen enough — no oil supply shock signal is active.`,
        causalChain: `USO spikes → oil prices rising sharply → supply disruption inferred → energy producers benefit from higher prices → airlines/transports/consumers face higher costs → oil supply shock theme activates, travel/leisure theme faces headwind.`,
        threshold: `Activation: USO 5-day return must be above +5%`,
        status: isActive ? "warning" : "inactive",
        affectedThemeLabels,
      };
    case "credit_stress_rising": {
      const spread = hyg - lqd;
      return {
        measurement: `HYG ${pctStr(hyg)} vs LQD ${pctStr(lqd)} — spread: ${pctStr(spread)}`,
        interpretation: isActive
          ? `HYG is underperforming LQD — high-yield credit spreads are widening. Investors are demanding more yield to hold riskier debt, warning of deteriorating corporate credit conditions.`
          : `HYG and LQD are moving broadly in line — credit spreads are not widening materially.`,
        causalChain: `HYG underperforms LQD → high-yield spreads widen → corporate credit conditions tightening → risk appetite for leveraged/speculative names falls → early warning for potential equity risk-off.`,
        threshold: `Activation: HYG-LQD 5-day spread must be below −0.5%`,
        status: isActive ? "warning" : "inactive",
        affectedThemeLabels,
      };
    }
    case "credit_stress_easing": {
      const spread = hyg - lqd;
      return {
        measurement: `HYG ${pctStr(hyg)} vs LQD ${pctStr(lqd)} — spread: ${pctStr(spread)}`,
        interpretation: isActive
          ? `HYG is outperforming LQD — credit spreads are tightening. Investors are comfortable holding riskier corporate debt, signalling improving risk appetite.`
          : `Credit spreads are not tightening enough to confirm a credit easing signal.`,
        causalChain: `HYG outperforms LQD → high-yield spreads compress → corporate borrowing conditions improving → recession and credit event risk is being priced out → risk-on rotation and credit easing themes activate.`,
        threshold: `Activation: HYG-LQD 5-day spread must be above +0.3%`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    }
    case "risk_on_rotation":
      return {
        measurement: `UVXY ${pctStr(uvxy)} and SPY ${pctStr(spy)} over the past 5 trading days`,
        interpretation: isActive
          ? `Volatility (UVXY) is falling while SPY is rising — this dual signal confirms that investors are selling hedges and rotating into equities. A clean risk-on confirmation.`
          : `Volatility and SPY are not simultaneously confirming a risk-on rotation at the required thresholds.`,
        causalChain: `UVXY falls (hedges being sold) AND SPY rises → investors are actively moving from protection into equities → broad risk appetite is confirmed → risk-on rotation and small-cap participation themes benefit.`,
        threshold: `Activation: UVXY < −5% AND SPY > +1% over 5 days`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    case "risk_off_rotation":
      return {
        measurement: `UVXY ${pctStr(uvxy)} and SPY ${pctStr(spy)} over the past 5 trading days`,
        interpretation: isActive
          ? `Volatility (UVXY) is rising while SPY is falling — investors are buying protection and selling equities. Confirmed risk-off rotation.`
          : `UVXY and SPY are not confirming a risk-off rotation.`,
        causalChain: `UVXY rises (protection being bought) AND SPY falls → investors are actively reducing equity exposure → risk-off rotation confirmed → defensive healthcare and safe-haven themes benefit.`,
        threshold: `Activation: UVXY > +10% AND SPY < −1% over 5 days`,
        status: isActive ? "warning" : "inactive",
        affectedThemeLabels,
      };
    case "gold_safe_haven_bid":
      return {
        measurement: `GLD (gold ETF) is ${pctStr(gld)} over the past 5 trading days`,
        interpretation: isActive
          ? `GLD has risen significantly, signalling active safe-haven buying. Investors are hedging against inflation, currency risk, or geopolitical uncertainty.`
          : `GLD has not risen enough — safe-haven demand is not active at the current threshold.`,
        causalChain: `GLD rises significantly → investors are buying gold as a hedge → safe-haven demand is being expressed → this can coexist with equity strength but warns of underlying caution → gold and defensive themes activate.`,
        threshold: `Activation: GLD 5-day return must be above +3%`,
        status: isActive ? "confirming" : "inactive",
        affectedThemeLabels,
      };
    default:
      return {
        measurement: "Evidence data not available",
        interpretation: "Signal data is not available for this driver in the current market state.",
        causalChain: "No causal chain data available.",
        threshold: "See market data source",
        status: "inactive",
        affectedThemeLabels: [],
      };
  }
}

// ── Dormant theme activation explanation ─────────────────────────────────────

export function getDormantThemeActivationNote(themeId: string, activeDrivers: string[]): string {
  const neededDrivers = THEME_ACTIVATION_DRIVERS[themeId] ?? [];
  if (neededDrivers.length === 0) return "Activation conditions are not currently measurable.";
  const missingDrivers = neededDrivers.filter(d => !activeDrivers.includes(d));
  if (missingDrivers.length === 0) return "Drivers appear active — theme may activate on next update.";
  const labels = missingDrivers.map(d => DRIVER_DICTIONARY[d]?.displayLabel ?? d).join(", ");
  return `Would activate when: ${labels}.`;
}

// ── Market Story Builder ───────────────────────────────────────────────────────

function joinList(items: string[]): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0];
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

export function buildMarketStory(
  activeDrivers: string[],
  themes: IntelTheme[],
  candidates: IntelCandidate[],
  regimeStr: string | null,
): MarketStory {
  const activeTailwinds = themes.filter(
    t => ["activated", "strengthening"].includes(t.state) && t.direction !== "headwind",
  );
  const activeHeadwinds = themes.filter(
    t => t.direction === "headwind" && t.state !== "dormant",
  );

  const hasAICapex   = activeDrivers.includes("ai_capex_growth");
  const hasAICompute = activeDrivers.includes("ai_compute_demand");
  const hasDefence   = activeDrivers.includes("geopolitical_risk_rising");
  const hasBreadth   = activeDrivers.includes("small_cap_risk_on");
  const hasRiskOn    = activeDrivers.includes("risk_on_rotation");
  const hasRiskOff   = activeDrivers.includes("risk_off_rotation");
  const hasSafeHvn   = activeDrivers.includes("gold_safe_haven_bid");
  const hasCredit    = activeDrivers.includes("credit_stress_rising");
  const hasYldRise   = activeDrivers.includes("yields_rising");
  const hasYldFall   = activeDrivers.includes("yields_falling");
  const hasCreditE   = activeDrivers.includes("credit_stress_easing");

  const hasAI = hasAICapex || hasAICompute;

  // Determine sentiment
  const isRiskOn  = (hasAI || hasBreadth || hasRiskOn || hasCreditE) && !hasCredit && !hasRiskOff;
  const isRiskOff = hasRiskOff || (hasCredit && hasSafeHvn);
  const isMixed   = isRiskOn && (hasCredit || hasYldRise || hasSafeHvn);

  let overallSentiment: "risk-on" | "mixed" | "risk-off" | "neutral";
  if (isRiskOff)      overallSentiment = "risk-off";
  else if (isMixed)   overallSentiment = "mixed";
  else if (isRiskOn)  overallSentiment = "risk-on";
  else                overallSentiment = "neutral";

  // Headline
  const forces: string[] = [];
  if (hasAI)      forces.push("AI infrastructure demand");
  if (hasDefence) forces.push("defence sector leadership");
  if (hasBreadth) forces.push("broadening small-cap participation");
  if (hasYldFall) forces.push("falling bond yields");
  if (hasCreditE) forces.push("easing credit conditions");

  const regimeLabel = regimeStr ? translateRegime(regimeStr) : null;
  const prefix = regimeLabel ? `${regimeLabel} — ` : "";

  let headline = "";
  if (overallSentiment === "risk-on" && forces.length > 0) {
    headline = `${prefix}${joinList(forces)} supporting the move.`;
  } else if (overallSentiment === "risk-on") {
    headline = `${prefix}risk appetite intact.`;
  } else if (overallSentiment === "risk-off") {
    headline = `${prefix}defensive signals are active — reduce risk exposure.`;
  } else if (overallSentiment === "mixed") {
    headline = `${prefix}cross-currents present — tailwinds exist but headwinds limit upside.`;
  } else if (activeTailwinds.length === 0) {
    headline = `No dominant market force confirmed. System is in observation mode.`;
  } else {
    headline = `${prefix}${activeTailwinds.length} theme${activeTailwinds.length !== 1 ? "s" : ""} in play.`;
  }

  // Bullets
  const bullets: string[] = [];
  if (hasAICapex && hasAICompute) {
    bullets.push("AI infrastructure spending and compute demand are both confirmed — semiconductor, data-centre power, and memory names are well-supported.");
  } else if (hasAICapex) {
    bullets.push("AI infrastructure spending is confirmed — SMH (semiconductor ETF) is holding above its breakdown level.");
  } else if (hasAICompute) {
    bullets.push("AI compute demand is confirmed — NVDA has not broken down.");
  }
  if (hasDefence)  bullets.push("Defence sector is outperforming the broad market — geopolitical risk is being priced in.");
  if (hasBreadth)  bullets.push("Small caps are outperforming large caps — participation is broadening beyond mega-cap technology.");
  if (hasYldFall)  bullets.push("Falling bond yields are a tailwind for growth stocks, software, and rate-sensitive sectors.");
  if (hasCreditE)  bullets.push("Credit spreads are tightening — corporate borrowing conditions are improving.");
  if (activeHeadwinds.length > 0) {
    const hwNames = activeHeadwinds.map(t => translateTheme(t.theme_id)).join(", ");
    bullets.push(`Active pressure in: ${hwNames} — avoid or reduce these sectors.`);
  }
  if (hasYldRise)  bullets.push("Rising bond yields are a headwind for growth stocks and rate-sensitive sectors.");
  if (hasSafeHvn)  bullets.push("Gold is rising — some investors are hedging. Watch for equity risk-off follow-through.");

  // Expectation
  let expectation = "";
  if (overallSentiment === "risk-on" && activeTailwinds.length >= 2) {
    const topThemes = activeTailwinds.slice(0, 3).map(t => translateTheme(t.theme_id)).join(", ");
    expectation = `Continuation setups have the edge while theme leadership holds. Strongest opportunity in ${topThemes}. Pullbacks in high-quality leaders are more attractive than reversal trades.`;
  } else if (overallSentiment === "risk-on") {
    expectation = "Risk appetite is intact. Favour continuation setups in confirmed themes.";
  } else if (overallSentiment === "risk-off") {
    expectation = "Defensive positioning is favoured. Reduce exposure to speculative and high-multiple names. Wait for risk-off signals to clear before adding.";
  } else if (overallSentiment === "mixed") {
    expectation = "Cross-currents suggest a selective approach. Favour high-quality setups in active tailwind themes. Avoid sectors with active headwinds.";
  } else {
    expectation = "No strong directional signal. Wait for theme confirmation before adding new positions.";
  }

  // Attention
  const attention =
    activeTailwinds.length > 0
      ? `Strongest opportunity in ${activeTailwinds.map(t => translateTheme(t.theme_id)).join(", ")}.`
      : "No active tailwind themes — system is in observation mode.";

  // Risks
  const risks: string[] = [];
  if (hasAI)      risks.push("Semiconductor breakdown — if SMH or NVDA loses key support, AI-related confidence drops immediately.");
  if (hasBreadth) risks.push("Breadth fade — if IWM underperforms SPY, the market narrows back to mega-cap tech and continuation confidence falls.");
  if (!hasYldRise) risks.push("Yield spike — a sharp move higher in bond yields would pressure growth valuations and rate-sensitive names.");
  if (!hasCredit)  risks.push("Credit deterioration — HYG underperforming LQD is an early warning of worsening risk appetite.");
  if (hasSafeHvn) risks.push("Gold strength is active — watch for equity risk-off follow-through if the gold bid accelerates.");
  if (hasDefence) risks.push("Geopolitical de-escalation — reduced geopolitical tension would compress the defence premium.");

  // Trading mode
  const routeBiases = activeTailwinds.map(t => t.route_bias).filter(Boolean);
  let tradingMode = "";
  if (overallSentiment === "risk-off") {
    tradingMode = "All expressions reduced. Options blocked. Wait for risk-off signals to clear before adding.";
  } else if (routeBiases.some(r => r?.includes("position_or_swing"))) {
    tradingMode = "Stock-only — swing and position bias. Options require confirmed unusual flow before entering.";
  } else if (routeBiases.some(r => r?.includes("swing"))) {
    tradingMode = "Swing trade bias. Prefer multi-day setups over intraday.";
  } else if (overallSentiment === "risk-on") {
    tradingMode = "Standard swing and position approach. Follow signal quality and theme confirmation.";
  } else {
    tradingMode = "Selective approach only. Wait for strong signal confirmation before entering.";
  }

  return { headline, bullets, expectation, attention, risks, tradingMode, overallSentiment };
}
