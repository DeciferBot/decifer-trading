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
    quality_cash_flow:         "Quality & Cash Flow",
    defensive_quality:         "Quality Defensives",
    small_caps:                "Small Caps",
    consumer_discretionary_strength: "Consumer Spending",
    biotech_risk_on:           "Biotech",
    gold_precious_metals:      "Gold & Precious Metals",
    energy:                    "Energy",
    banks:                     "Banks",
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
    quality_cash_flow:         "Companies with strong balance sheets and predictable free cash flow attract capital when growth expectations are uncertain. Quality is a defensive factor that tends to outperform in late-cycle environments.",
    defensive_quality:         "High-quality defensives — large-cap consumer staples, utilities, and healthcare — hold value when risk appetite fades. They offer steady dividends and earnings visibility when growth is scarce.",
    small_caps:                "Small-cap stocks are sensitive to domestic economic conditions and credit availability. They tend to lead in early bull markets and lag during stress periods.",
    consumer_discretionary_strength: "Consumer confidence is supporting spending on non-essential goods and experiences. Retail, leisure, and brand names outperform when employment is strong and real wages are positive.",
    biotech_risk_on:           "Biotech names move on clinical trial data, FDA decisions, and earnings beats. In a risk-on environment, investors are willing to pay for optionality on binary data readouts.",
    gold_precious_metals:      "Precious metals attract capital as a store of value during periods of currency weakness, inflation, or geopolitical stress. Gold is the classic safe haven when confidence in fiat assets declines.",
    energy:                    "Energy stocks track oil and gas prices, which are driven by supply-demand dynamics, geopolitical disruptions, and OPEC policy. They tend to outperform during inflationary periods.",
    banks:                     "Bank earnings benefit from a steeper yield curve and growing loan demand. Credit quality and net interest margin are the key drivers of bank stock performance.",
  };
  return map[id] ?? `This theme is active based on current market conditions.`;
}

export function themeInvalidation(id: string): string {
  const map: Record<string, string> = {
    data_centre_power:         "Hyperscaler capex guidance cut, utility grid capacity constraints, or an AI demand slowdown.",
    semiconductors:            "AI hardware spending pause, inventory cycle reversal, or geopolitical supply restrictions on chip exports.",
    ai_compute_infrastructure: "Financing stress for neocloud operators, power cost spike, or a slowdown in GPU demand.",
    memory_storage:            "Memory oversupply, HBM pricing reversal, or a reduction in AI model training runs.",
    defence:                   "Geopolitical de-escalation, defence budget cuts, or a significant peace agreement.",
    software_cloud:            "Growth deceleration in cloud spend, margin compression from AI investment, or rising competition.",
    cybersecurity:             "Budget freezes across enterprise security, or a risk-off rotation away from high-multiple tech.",
    mega_cap_platforms:        "Antitrust action, regulatory pressure, or a meaningful slowdown in advertising or cloud revenue.",
    consumer_discretionary_strength: "Rising unemployment, falling real wages, or consumer credit stress.",
    travel_leisure:            "Recession fears, rising fuel costs, or a resurgence of travel restrictions.",
    defensive_healthcare:      "Risk-on rotation into growth assets, drug pricing legislation, or clinical trial failures.",
    biotech:                   "Risk-off rotation, FDA rejection, or a broad deterioration in clinical trial success rates.",
    biotech_risk_on:           "Risk-off rotation, FDA rejection, or a broad deterioration in clinical trial success rates.",
    regional_banks:            "Yield curve flattening, credit quality deterioration, or rising deposit outflows.",
    infrastructure_reshoring:  "Policy reversal on tariffs or industrial subsidies, or a capital expenditure freeze.",
    gold_safe_haven_bid:       "Risk-on rotation, dollar strength, or a meaningful decline in inflation expectations.",
    gold_precious_metals:      "Risk-on rotation, dollar strength, or a meaningful decline in inflation expectations.",
    copper_electrification:    "China demand slowdown, EV adoption deceleration, or a global growth recession.",
    reits:                     "Interest rate reversal (yields rising again), or a deterioration in occupancy rates.",
    small_cap_risk_on:         "Risk-off rotation, credit tightening, or a recession signal from leading indicators.",
    risk_on_rotation:          "VIX spike, credit spread widening, or a hawkish Fed policy surprise.",
    yields_falling:            "Inflation re-acceleration forcing the Fed to hold rates higher for longer.",
    yields_rising:             "Inflation surprise to the downside, or a Fed pivot to rate cuts.",
    oil_supply_shock:          "OPEC output increase, demand destruction from a global slowdown, or US shale supply response.",
    reits_falling_yield:       "Yields stabilising or falling, making REIT dividends competitive again.",
    credit_stress_easing:      "Credit spread widening on recession fears, corporate defaults, or a banking stress event.",
    quality_cash_flow:         "Risk-on rotation into growth names, or a sharp improvement in macro conditions.",
    defensive_quality:         "Broad risk-on rally where investors rotate out of defensives into cyclicals.",
    small_caps:                "Credit tightening, dollar strength, or a domestic recession signal.",
    energy:                    "OPEC production increase, demand slowdown, or a rapid transition away from fossil fuels.",
    banks:                     "Yield curve flattening, rising credit losses, or a regulatory tightening cycle.",
  };
  return map[id] ?? "A reversal of the current macro driver, or a loss of event-driven evidence supporting this theme.";
}

export function driverExplanation(label: string): string {
  const l = label.toLowerCase();
  if (l.includes("ai capital") || l.includes("ai capex"))
    return "Companies building AI infrastructure are seeing sustained investment demand from hyperscalers.";
  if (l.includes("ai compute demand"))
    return "Demand for GPU compute and AI processing power continues to outpace available supply.";
  if (l.includes("geopolit"))
    return "Elevated geopolitical risk is driving capital toward defence, energy, and safe-haven assets.";
  if (l.includes("small-cap") || l.includes("small cap"))
    return "Healthy risk appetite is showing up in small-cap outperformance — investors are taking on more risk.";
  if (l.includes("futures") && l.includes("risk-on"))
    return "US equity futures signal a positive open — institutional positioning leans constructive overnight.";
  if (l.includes("futures") && l.includes("risk-off"))
    return "US equity futures signal caution — institutional positioning is defensive heading into the session.";
  if (l.includes("futures") && l.includes("risk"))
    return "Futures markets are providing direction on overnight institutional positioning.";
  if (l.includes("yields rising") || (l.includes("bond") && l.includes("ris")))
    return "Rising yields tighten financial conditions and compress valuations for growth and rate-sensitive stocks.";
  if (l.includes("yields falling") || (l.includes("bond") && l.includes("fall")))
    return "Falling yields reduce the discount rate on future earnings, supporting growth stocks and REITs.";
  if (l.includes("gold") || l.includes("safe-haven"))
    return "Safe-haven demand for gold signals that investors are hedging against macro or geopolitical uncertainty.";
  if (l.includes("credit") && l.includes("eas"))
    return "Tightening credit spreads signal that default risk is receding and corporate borrowing conditions are improving.";
  if (l.includes("risk-on rotation") || l.includes("rotation"))
    return "Capital is rotating from defensive assets into growth and cyclicals as risk appetite improves.";
  if (l.includes("oil"))
    return "Oil supply dynamics are affecting energy prices and influencing inflation and consumer spending.";
  return "This factor is shaping current market conditions and sector performance.";
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

// ── Signal status resolver for Signals tab ─────────────────────────────────────
// Maps internal theme state/signal strings to customer-safe status labels.
// No buy/sell/hold/activation/broker language. Used by SignalsTab and its tests.

export interface SignalStatus {
  label: string;
  color: string;
  dotColor: string;
}

export function resolveSignalStatus(state?: string, signal?: string): SignalStatus {
  const s   = state  ?? "";
  const sig = signal ?? "";
  if (s === "activated" || s === "active" || sig === "strengthening")
    return { label: "In Focus",                 color: "#34d399", dotColor: "#10b981" };
  if (s === "strengthening")
    return { label: "Building",                 color: "#60a5fa", dotColor: "#3b82f6" };
  if (s === "crowded")
    return { label: "Widely held",              color: "#fbbf24", dotColor: "#f59e0b" };
  if (sig === "weakening" || s === "weakening")
    return { label: "Fading",                   color: "#fbbf24", dotColor: "#f59e0b" };
  if (s === "headwind")
    return { label: "Under Pressure",           color: "#f87171", dotColor: "#ef4444" };
  if (s === "dormant")
    return { label: "Quiet",                    color: "#475569", dotColor: "#334155" };
  return   { label: "Waiting for confirmation", color: "#64748b", dotColor: "#475569" };
}

/** Format a timestamp as New York time — e.g. "2:15 PM New York Time" */
export function fmtNYTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }) + " New York Time";
}

/** Format a timestamp as the device's local time — e.g. "10:15 PM Local Time" */
export function fmtLocalTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }) + " Local Time";
}

/** Format the current date + both clocks for page headers */
export function fmtHeaderDate(): { date: string; nyTime: string; localTime: string } {
  const now = new Date();
  return {
    date: now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }),
    nyTime: now.toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }) + " New York Time",
    localTime: now.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }) + " Local Time",
  };
}
