// ── Plain-English translation layer ───────────────────────────────────────────
// Everything the bot says in jargon gets converted here before reaching the UI.

export function translateSession(s: string): string {
  const map: Record<string, string> = {
    OPEN: "Market open",
    CLOSED: "Market closed",
    PRE: "Pre-market",
    AFTER_HOURS: "After hours",
    WEEKEND: "Weekend",
    UNKNOWN: "Checking...",
  };
  return map[s] ?? s;
}

export function translateRegime(r: string): string {
  const map: Record<string, string> = {
    TRENDING_UP:    "Bull market",
    BULL_TRENDING:  "Bull market",
    TRENDING_DOWN:  "Bear market",
    BEAR_TRENDING:  "Bear market",
    CHOPPY:         "Sideways market",
    RANGING:        "Sideways market",
    PANIC:          "High volatility",
    RECOVERY:       "Recovery mode",
    UNKNOWN:        "Reading the market...",
  };
  return map[r] ?? r;
}

export function translateVix(vix: number): { label: string; color: string } {
  if (vix < 15)  return { label: "Market is calm",        color: "text-emerald-400" };
  if (vix < 20)  return { label: "Mild uncertainty",      color: "text-emerald-400" };
  if (vix < 25)  return { label: "Some nervousness",      color: "text-amber-400"   };
  if (vix < 30)  return { label: "Elevated fear",         color: "text-amber-400"   };
  if (vix < 40)  return { label: "High volatility",       color: "text-rose-400"    };
  return              { label: "Extreme fear",            color: "text-rose-400"    };
}

export function translateDirection(d: string): string {
  return d === "SHORT" ? "Shorting ↓" : "Bought ↑";
}

export function translateTradeType(t: string): string {
  const map: Record<string, string> = {
    SWING:     "Multi-day",
    INTRADAY:  "Day trade",
    OVERNIGHT: "Overnight",
    SWING_LONG:  "Multi-day",
    SWING_SHORT: "Multi-day short",
  };
  return map[t] ?? t;
}

export function translateTheme(id: string): string {
  const map: Record<string, string> = {
    data_centre_power:         "Data Centres & Power",
    semiconductors:            "Semiconductors",
    ai_compute_demand:         "AI Computing",
    ai_compute_infrastructure: "AI Infrastructure",
    memory_storage:            "Memory & Storage",
    defence_aerospace:         "Defence & Aerospace",
    defence:                   "Defence & Aerospace",
    yields_falling:            "Falling Bond Yields",
    yields_rising:             "Rising Bond Yields",
    risk_on_rotation:          "Risk-On: Growth Stocks",
    gold_safe_haven_bid:       "Gold Safe Haven",
    credit_stress_easing:      "Credit Markets Easing",
    small_cap_risk_on:         "Small-Cap Rally",
    software_cloud:            "Software & Cloud",
    cybersecurity:             "Cybersecurity",
    mega_cap_platforms:        "Mega-Cap Tech",
    consumer_discretionary:    "Consumer Spending",
    travel_leisure:            "Travel & Leisure",
    defensive_healthcare:      "Defensive Healthcare",
    biotech:                   "Biotech",
    regional_banks:            "Regional Banks",
    infrastructure_reshoring:  "Infrastructure",
    copper_electrification:    "Copper & Clean Energy",
    reits:                     "Real Estate (REITs)",
    oil_supply_shock:          "Oil Supply Shock",
    reits_falling_yield:       "REITs Under Pressure",
  };
  return map[id] ?? id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

export function translateThemeState(state: string): { label: string; color: string } {
  const map: Record<string, { label: string; color: string }> = {
    activated:     { label: "Active",      color: "text-emerald-400 bg-emerald-400/10" },
    strengthening: { label: "Building",    color: "text-blue-400 bg-blue-400/10"       },
    crowded:       { label: "Crowded",     color: "text-amber-400 bg-amber-400/10"     },
    headwind:      { label: "Headwind",    color: "text-rose-400 bg-rose-400/10"       },
    dormant:       { label: "Quiet",       color: "text-slate-400 bg-slate-400/10"     },
  };
  return map[state] ?? { label: state, color: "text-slate-400 bg-slate-400/10" };
}

export function themeDescription(id: string): string {
  const map: Record<string, string> = {
    data_centre_power:         "AI and cloud demand is driving a huge buildout of data centres and electricity infrastructure. Companies that build, power, and cool these facilities are in a multi-year growth cycle.",
    semiconductors:            "Chip makers are benefiting from AI hardware demand. Memory, logic chips, and fab equipment are all in an upgrade cycle driven by the AI arms race.",
    ai_compute_infrastructure: "The physical hardware behind AI — servers, networking, custom silicon, and cooling — is being built at massive scale. This is the picks-and-shovels play of the AI boom.",
    memory_storage:            "AI training and inference require far more memory than traditional computing. HBM chips and high-capacity storage are in short supply and commanding premium prices.",
    defence:                   "Global defence budgets are rising. Aerospace and defence contractors are winning long-cycle government contracts as geopolitical tensions remain elevated.",
    software_cloud:            "Cloud platforms and software companies with recurring revenue are attracting capital. AI is accelerating product cycles and expanding addressable markets.",
    cybersecurity:             "Enterprise security spending is growing structurally. AI enables more sophisticated attacks, forcing every organisation to upgrade its defences.",
    mega_cap_platforms:        "The largest tech companies are compounding free cash flow, buying back stock at scale, and investing in AI. They benefit from scale advantages that smaller competitors cannot match.",
    consumer_discretionary:    "Consumer confidence is supporting spending on experiences and premium goods. Retail, leisure, and brand names are outperforming when the economic mood is positive.",
    travel_leisure:            "Airlines, hotels, and leisure companies are filling capacity as travel demand remains robust. Business and leisure travel are both recovering strongly.",
    defensive_healthcare:      "Large-cap pharma and managed care act as a safe harbour when markets are uncertain. Steady earnings and dividends attract capital in volatile periods.",
    biotech:                   "FDA approvals and clinical trial readouts create short-term catalysts. Individual biotech names can move significantly on binary data events.",
    regional_banks:            "Regional banks benefit from a steepening yield curve and growing loan demand. They tend to outperform when the economy is doing well and credit quality is healthy.",
    infrastructure_reshoring:  "US manufacturing and infrastructure are being rebuilt with policy support. Onshoring, grid upgrades, and domestic supply chains are all multi-year investment themes.",
    gold_safe_haven_bid:       "Investors are buying gold as a hedge against inflation, currency risk, or geopolitical stress. Gold tends to rise when confidence in paper assets falls.",
    copper_electrification:    "Copper is essential for EVs, power grids, and the energy transition. Rising demand meets constrained supply, supporting prices over the long term.",
    reits:                     "Real estate investment trusts benefit when interest rates fall, improving the spread between their dividend yields and bond rates.",
    small_cap_risk_on:         "Small caps tend to outperform when investors are confident and willing to take risk. They lead bull market rallies and are the first to recover from corrections.",
    risk_on_rotation:          "Capital is rotating from bonds and cash into equities as risk appetite improves. This favours growth stocks and cyclicals over defensives.",
    yields_falling:            "Falling bond yields reduce the discount rate for future earnings, boosting growth stock valuations and benefiting rate-sensitive sectors.",
    yields_rising:             "Rising yields compress growth stock valuations and make bond yields more competitive with equity returns.",
    oil_supply_shock:          "Oil supply disruptions are raising energy prices. This benefits producers and energy companies but increases costs for consumers and industrials.",
    reits_falling_yield:       "Rising interest rates are making REIT dividends less attractive compared to bonds, putting pressure on real estate valuations.",
    credit_stress_easing:      "Tightening credit spreads signal that recession fears are receding and corporate borrowing conditions are improving.",
  };
  return map[id] ?? `This theme is active based on current market conditions.`;
}

export function translateSetupType(s: string): string {
  const map: Record<string, string> = {
    mtf:       "Multi-timeframe momentum",
    breakout:  "Price breakout",
    squeeze:   "Volatility squeeze release",
    reversion: "Mean reversion",
    flow:      "Unusual options flow",
    news:      "News catalyst",
    overnight: "Overnight gap play",
    pead:      "Post-earnings drift",
  };
  return map[s] ?? (s ? s.replace(/_/g, " ") : "Signal-driven");
}

export function translateSignalDim(dim: string): string {
  const map: Record<string, string> = {
    trend:             "Trend strength",
    momentum:          "Momentum",
    squeeze:           "Volatility squeeze",
    flow:              "Unusual volume",
    breakout:          "Price breakout",
    mtf:               "Multi-timeframe",
    news:              "News catalyst",
    social:            "Social sentiment",
    reversion:         "Mean reversion",
    iv_skew:           "Options skew",
    pead:              "Earnings drift",
    short_squeeze:     "Short squeeze risk",
    overnight_drift:   "Overnight drift",
    analyst_revision:  "Analyst upgrade",
    insider_buying:    "Insider buying",
    catalyst:          "Catalyst event",
  };
  return map[dim] ?? dim;
}

export function translateThesisStatus(s: string): { label: string; color: string } {
  const map: Record<string, { label: string; color: string }> = {
    STRENGTHENING:    { label: "Getting stronger",   color: "text-emerald-400" },
    THESIS_INTACT:    { label: "On track",           color: "text-emerald-400" },
    INTACT:           { label: "On track",           color: "text-emerald-400" },
    INTACT_DEGRADED:  { label: "Slightly off track", color: "text-amber-400"   },
    THESIS_DECAYING:  { label: "Weakening",          color: "text-amber-400"   },
    DECAYING:         { label: "Fading",             color: "text-amber-400"   },
    PLAYED_OUT:       { label: "Played out",         color: "text-slate-400"   },
    BROKEN:           { label: "Thesis broken",      color: "text-rose-400"    },
    UNKNOWN:          { label: "Unknown",            color: "text-slate-500"   },
  };
  return map[s] ?? { label: s, color: "text-slate-400" };
}

/**
 * Strip the machine-readable wrapper from entry_thesis strings.
 * Raw format: "SWING LONG AMD | wrong_if: ... | setup: <readable text> | regime=... conv=... score=..."
 * Also strips inline technical annotations: key=value, key=WORD, "SYMBOL scores X with ..." openers.
 */
export function cleanThesis(raw: string): string {
  if (!raw) return raw;
  // Strip "ACTION SYMBOL | wrong_if: ... | setup: " prefix pattern
  let text = raw.replace(/^[A-Z _]+\|[^|]*\|\s*setup:\s*/i, "");
  // Strip trailing " | regime=..." and " | conv=..." metadata blocks
  text = text.replace(/\s*\|\s*(regime|conv|score|setup|wrong_if)=.*/i, "");
  // Strip "SYMBOL scores effective_score=N with " style openers (e.g. "NVDA scores effective_score=50 with ")
  text = text.replace(/^[A-Z0-9]+\s+scores\s+\S+=\S+\s+with\s+/i, "");
  // Strip all remaining key=value and key=WORD technical annotations (e.g. effective_score=50, trend=10, conv=HIGH)
  text = text.replace(/\b[a-z_]+=[A-Z0-9_.,-]+/gi, "");
  // Clean up double spaces and leading/trailing commas or semicolons left by stripping
  text = text.replace(/[,;]\s*[,;]/g, ",").replace(/^[,;\s]+|[,;\s]+$/g, "").replace(/\s{2,}/g, " ");
  // If stripping didn't meaningfully work, return original
  return text.trim() || raw;
}

export function translateConviction(score: number): string {
  if (score >= 70) return "Very strong signal";
  if (score >= 55) return "Strong signal";
  if (score >= 40) return "Good signal";
  if (score >= 28) return "Moderate signal";
  return "Weak signal";
}

export function fmtMoney(n: number, compact = false): string {
  if (compact) {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1,
    }).format(n ?? 0);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(n ?? 0);
}

export function fmtPct(n: number, showSign = true): string {
  const val = (n ?? 0).toFixed(2);
  return showSign && n >= 0 ? `+${val}%` : `${val}%`;
}

export function pnlColor(n: number): string {
  return n >= 0 ? "text-emerald-400" : "text-rose-400";
}

export function holdDuration(isoOrHms: string): string {
  if (!isoOrHms) return "";
  const d = new Date(isoOrHms);
  const mins = Math.floor((Date.now() - d.getTime()) / 60_000);
  if (isNaN(mins) || mins < 0) return "";
  if (mins < 60)   return `${mins}m`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h`;
  const days = Math.floor(mins / 1440);
  return days === 1 ? "1 day" : `${days} days`;
}
