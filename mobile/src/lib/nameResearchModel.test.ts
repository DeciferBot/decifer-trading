import { describe, it, expect } from "vitest";
import {
  derivePriceActionTone,
  buildPriceAction,
  resolveWatchType,
  buildResearchCard,
  buildStoryGroups,
  buildRadarCards,
  prioritySymbols,
  TTG_STORY_LABELS,
  formatMarketCap,
  buildCompanyLine,
  buildFundamentalsLine,
  buildAnalystLine,
  buildDetailQuestions,
  buildWhyItMattersNow,
  buildRiskNoteLine,
  buildShortInterestLine,
  mergeFreshPrice,
  buildPriceFreshnessLabel,
  type NameFundamentalsResponse,
} from "./nameResearchModel";
import { parseSymbols, MAX_SYMBOLS } from "./namePriceUtils";
import type { TtgSymbolCard, TtgThemeDetail, RadarItem } from "./customerApi";
import type { NamePriceEntry } from "./namePriceUtils";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeCard(overrides: Partial<TtgSymbolCard> = {}): TtgSymbolCard {
  return {
    symbol: "NVDA",
    label: "NVIDIA",
    theme_id: "ai_energy_nuclear",
    theme_label: "AI Energy & Nuclear",
    bucket_id: "ai_compute_accelerators_networking",
    bucket_label: "AI Compute Accelerators & Networking",
    exposure_type: "direct_beneficiary",
    confidence: 0.9,
    reason_to_care: "Dominant AI chip supplier benefiting from hyperscaler capex growth.",
    reason_path: ["AI Infrastructure Spending", "Data Centre Power", "NVDA"],
    evidence_basis_label: "company_profile",
    route_hint: "In focus",
    status: "active",
    risk_note: null,
    driver_active: false,
    theme_risk_note: null,
    ...overrides,
  };
}

function makeTheme(overrides: Partial<TtgThemeDetail> = {}): TtgThemeDetail {
  return {
    theme_id: "ai_energy_nuclear",
    label: "AI Energy & Nuclear",
    plain_english_description: "Power and compute for the AI buildout.",
    status: "active",
    driver_ids: ["ai_capex_growth"],
    driver_active: false,
    risk_note: null,
    symbols: [makeCard()],
    symbol_count: 1,
    ...overrides,
  };
}

const emptyPriceMap = new Map<string, NamePriceEntry>();

// ── derivePriceActionTone ─────────────────────────────────────────────────────

describe("derivePriceActionTone", () => {
  it("returns positive when changePct > 0.75", () => {
    expect(derivePriceActionTone(1.5)).toBe("positive");
    expect(derivePriceActionTone(0.76)).toBe("positive");
  });

  it("returns negative when changePct < -0.75", () => {
    expect(derivePriceActionTone(-1.0)).toBe("negative");
    expect(derivePriceActionTone(-0.76)).toBe("negative");
  });

  it("returns neutral when changePct is between -0.75 and 0.75 inclusive", () => {
    expect(derivePriceActionTone(0)).toBe("neutral");
    expect(derivePriceActionTone(0.5)).toBe("neutral");
    expect(derivePriceActionTone(-0.5)).toBe("neutral");
    expect(derivePriceActionTone(0.75)).toBe("neutral");
    expect(derivePriceActionTone(-0.75)).toBe("neutral");
  });

  it("returns unknown when changePct is null", () => {
    expect(derivePriceActionTone(null)).toBe("unknown");
  });
});

// ── buildPriceAction ──────────────────────────────────────────────────────────

describe("buildPriceAction", () => {
  it("returns unknown with fallback text when no entry provided", () => {
    const pa = buildPriceAction(null);
    expect(pa.tone).toBe("unknown");
    expect(pa.displayText).toBe("Price updating…");
    expect(pa.changePct).toBeNull();
    expect(pa.price).toBeNull();
  });

  it("returns unknown with fallback text when changePct is null", () => {
    const pa = buildPriceAction({ symbol: "NVDA", price: 900, changePct: null });
    expect(pa.tone).toBe("unknown");
    expect(pa.displayText).toBe("Price updating…");
    expect(pa.price).toBe(900);
  });

  it("formats positive tone as 'Up X.X% today'", () => {
    const pa = buildPriceAction({ symbol: "NVDA", price: 900, changePct: 2.3 });
    expect(pa.tone).toBe("positive");
    expect(pa.displayText).toBe("Up 2.3% today");
  });

  it("formats negative tone as 'Down X.X% today'", () => {
    const pa = buildPriceAction({ symbol: "NVDA", price: 900, changePct: -1.8 });
    expect(pa.tone).toBe("negative");
    expect(pa.displayText).toBe("Down 1.8% today");
  });

  it("formats neutral tone as 'Flat today'", () => {
    const pa = buildPriceAction({ symbol: "NVDA", price: 900, changePct: 0.2 });
    expect(pa.tone).toBe("neutral");
    expect(pa.displayText).toBe("Flat today");
  });

  it("does not use fallback phrase when price data is available", () => {
    const pa = buildPriceAction({ symbol: "NVDA", price: 900, changePct: 1.5 });
    expect(pa.displayText).not.toContain("not available");
  });
});

// ── resolveWatchType ──────────────────────────────────────────────────────────

describe("resolveWatchType", () => {
  it("returns 'Catalyst watch' when driver_active and in focus", () => {
    const card = makeCard({ driver_active: true, route_hint: "In focus" });
    expect(resolveWatchType(card)).toBe("Catalyst watch");
  });

  it("returns 'Structural watch' when in focus but driver not active", () => {
    const card = makeCard({ driver_active: false, route_hint: "In focus" });
    expect(resolveWatchType(card)).toBe("Structural watch");
  });

  it("returns 'Structural watch' for ETF route", () => {
    const card = makeCard({ driver_active: false, route_hint: "ETF route" });
    expect(resolveWatchType(card)).toBe("Structural watch");
  });

  it("returns 'Market attention' for On the radar", () => {
    const card = makeCard({ route_hint: "On the radar" });
    expect(resolveWatchType(card)).toBe("Market attention");
  });

  it("returns 'Market attention' for Monitor only", () => {
    const card = makeCard({ route_hint: "Monitor only" });
    expect(resolveWatchType(card)).toBe("Market attention");
  });
});

// ── buildResearchCard ─────────────────────────────────────────────────────────

describe("buildResearchCard", () => {
  it("includes all required fields", () => {
    const card = buildResearchCard(makeCard(), emptyPriceMap, "AI Infrastructure");
    expect(card).toHaveProperty("symbol");
    expect(card).toHaveProperty("companyName");
    expect(card).toHaveProperty("storyGroup");
    expect(card).toHaveProperty("priceAction");
    expect(card).toHaveProperty("reasonToCare");
    expect(card).toHaveProperty("watchType");
    expect(card).toHaveProperty("confidenceLanguage");
    expect(card).toHaveProperty("isPressure");
  });

  it("sets isPressure true for pressure_or_negative exposure type", () => {
    const card = buildResearchCard(makeCard({ exposure_type: "pressure_or_negative" }), emptyPriceMap, "Test");
    expect(card.isPressure).toBe(true);
  });

  it("sets isPressure false for beneficiary exposure types", () => {
    const card = buildResearchCard(makeCard({ exposure_type: "direct_beneficiary" }), emptyPriceMap, "Test");
    expect(card.isPressure).toBe(false);
  });

  it("uses price from priceMap when available", () => {
    const priceMap = new Map<string, NamePriceEntry>([
      ["NVDA", { symbol: "NVDA", price: 950, changePct: 2.1 }],
    ]);
    const card = buildResearchCard(makeCard(), priceMap, "Test");
    expect(card.priceAction.price).toBe(950);
    expect(card.priceAction.changePct).toBe(2.1);
    expect(card.priceAction.tone).toBe("positive");
  });

  it("contains no forbidden execution language in generated fields", () => {
    const card = buildResearchCard(makeCard(), emptyPriceMap, "AI Infrastructure");
    const generatedText = [
      card.watchType,
      card.confidenceLanguage,
      card.priceAction.displayText,
      card.storyGroup,
    ].join(" ").toLowerCase();

    const forbidden = ["buy", "sell", "order", "execution", "broker", "account", "stop loss",
      "pipeline", "handoff", "scanner", "raw score", "p&l"];
    for (const word of forbidden) {
      expect(generatedText).not.toContain(word);
    }
  });

  it("does not expose raw theme IDs in customer-facing string fields", () => {
    const card = buildResearchCard(makeCard(), emptyPriceMap, "AI Infrastructure");
    const customerText = [card.storyGroup, card.watchType, card.confidenceLanguage].join(" ");
    expect(customerText).not.toContain("ai_energy_nuclear");
    expect(customerText).not.toContain("direct_beneficiary");
  });
});

// ── buildStoryGroups ──────────────────────────────────────────────────────────

describe("buildStoryGroups", () => {
  it("returns an empty array when no themes have symbols", () => {
    const theme = makeTheme({ symbols: [] });
    const groups = buildStoryGroups([theme], emptyPriceMap);
    expect(groups).toHaveLength(0);
  });

  it("groups AI compute symbols under AI Infrastructure", () => {
    const theme = makeTheme({ symbols: [makeCard(), makeCard({ symbol: "AMD" })] });
    const groups = buildStoryGroups([theme], emptyPriceMap);
    expect(groups).toHaveLength(1);
    expect(groups[0].storyLabel).toBe("AI Infrastructure");
    expect(groups[0].cards).toHaveLength(2);
  });

  it("uses TTG_STORY_LABELS for known theme IDs", () => {
    const theme = makeTheme({ theme_id: "defence_rearmament", label: "Defence" });
    const groups = buildStoryGroups([theme], emptyPriceMap);
    expect(groups[0].storyLabel).toBe("Defence");
  });

  it("falls back to theme label for unknown theme IDs", () => {
    const theme = makeTheme({ theme_id: "unknown_theme_xyz", label: "Fallback Label" });
    const groups = buildStoryGroups([theme], emptyPriceMap);
    expect(groups[0].storyLabel).toBe("Fallback Label");
  });

  it("sorts driver-active groups before inactive groups", () => {
    const active = makeTheme({ theme_id: "ai_energy_nuclear", driver_active: true });
    const inactive = makeTheme({
      theme_id: "gold_real_assets",
      label: "Gold",
      driver_active: false,
      symbols: [makeCard({ symbol: "GLD", theme_id: "gold_real_assets" })],
    });
    const groups = buildStoryGroups([inactive, active], emptyPriceMap);
    expect(groups[0].driverActive).toBe(true);
    expect(groups[1].driverActive).toBe(false);
  });

  it("injects price data when priceMap is populated", () => {
    const priceMap = new Map<string, NamePriceEntry>([
      ["NVDA", { symbol: "NVDA", price: 900, changePct: -2.0 }],
    ]);
    const groups = buildStoryGroups([makeTheme()], priceMap);
    expect(groups[0].cards[0].priceAction.tone).toBe("negative");
  });
});

// ── buildRadarCards ───────────────────────────────────────────────────────────

describe("buildRadarCards", () => {
  const radarItem: RadarItem = {
    symbol: "TSLA",
    reason_to_watch: "Strong momentum following earnings surprise.",
    theme_link: null,
  };

  it("builds a card with Market attention watch type", () => {
    const cards = buildRadarCards([radarItem], emptyPriceMap);
    expect(cards).toHaveLength(1);
    expect(cards[0].watchType).toBe("Market attention");
    expect(cards[0].symbol).toBe("TSLA");
  });

  it("returns empty array for empty radar input", () => {
    expect(buildRadarCards([], emptyPriceMap)).toHaveLength(0);
  });
});

// ── prioritySymbols ───────────────────────────────────────────────────────────

describe("prioritySymbols", () => {
  it("prioritises driver_active + in_focus symbols first", () => {
    const theme = makeTheme({
      symbols: [
        makeCard({ symbol: "A", route_hint: "Monitor only", driver_active: false }),
        makeCard({ symbol: "B", route_hint: "In focus", driver_active: true }),
        makeCard({ symbol: "C", route_hint: "In focus", driver_active: false }),
      ],
    });
    const syms = prioritySymbols([theme], 10);
    expect(syms[0]).toBe("B");
  });

  it("respects the limit", () => {
    const symbols = Array.from({ length: 10 }, (_, i) => makeCard({ symbol: `SYM${i}` }));
    const theme = makeTheme({ symbols });
    const result = prioritySymbols([theme], 3);
    expect(result).toHaveLength(3);
  });

  it("deduplicates symbols appearing in multiple themes", () => {
    const t1 = makeTheme({ theme_id: "ai_energy_nuclear", symbols: [makeCard({ symbol: "NVDA" })] });
    const t2 = makeTheme({
      theme_id: "gold_real_assets",
      symbols: [makeCard({ symbol: "NVDA" }), makeCard({ symbol: "GLD" })],
    });
    const result = prioritySymbols([t1, t2], 50);
    expect(result.filter(s => s === "NVDA")).toHaveLength(1);
  });
});

// ── parseSymbols (from namePriceUtils) ───────────────────────────────────────

describe("parseSymbols", () => {
  it("returns empty array for null input", () => {
    expect(parseSymbols(null)).toHaveLength(0);
  });

  it("returns empty array for undefined input", () => {
    expect(parseSymbols(undefined)).toHaveLength(0);
  });

  it("returns empty array for empty string", () => {
    expect(parseSymbols("")).toHaveLength(0);
  });

  it("uppercases symbols", () => {
    expect(parseSymbols("nvda,aapl")).toEqual(["NVDA", "AAPL"]);
  });

  it("filters out invalid symbols", () => {
    const result = parseSymbols("NVDA,invalid symbol!,AAPL,toolongforsymbol1234");
    expect(result).toContain("NVDA");
    expect(result).toContain("AAPL");
    expect(result).not.toContain("invalid symbol!");
    expect(result).not.toContain("toolongforsymbol1234");
  });

  it("allows dots and hyphens in symbols", () => {
    expect(parseSymbols("BRK.B,GOLD-X")).toEqual(["BRK.B", "GOLD-X"]);
  });

  it("caps at MAX_SYMBOLS", () => {
    const many = Array.from({ length: 60 }, (_, i) => `SY${i}`).join(",");
    expect(parseSymbols(many)).toHaveLength(MAX_SYMBOLS);
  });

  it("trims whitespace around symbols", () => {
    expect(parseSymbols(" NVDA , AAPL ")).toEqual(["NVDA", "AAPL"]);
  });
});

// ── TTG_STORY_LABELS safety ───────────────────────────────────────────────────

describe("TTG_STORY_LABELS", () => {
  it("covers all 10 structural theme IDs", () => {
    const expectedIds = [
      "ai_energy_nuclear",
      "defence_rearmament",
      "cybersecurity_digital_resilience",
      "reshoring_industrial_capex",
      "critical_minerals_copper",
      "gold_real_assets",
      "glp1_metabolic_health",
      "housing_rate_sensitivity",
      "water_infrastructure",
      "digital_assets_infrastructure",
    ];
    for (const id of expectedIds) {
      expect(TTG_STORY_LABELS[id]).toBeDefined();
    }
  });

  it("contains no raw ID-style underscored keys in the label values", () => {
    for (const label of Object.values(TTG_STORY_LABELS)) {
      expect(label).not.toMatch(/^[a-z_]+$/);
    }
  });

  it("contains no forbidden execution language in labels", () => {
    const forbidden = ["buy", "sell", "order", "execution", "broker", "account", "stop loss", "ttg"];
    for (const label of Object.values(TTG_STORY_LABELS)) {
      for (const word of forbidden) {
        expect(label.toLowerCase()).not.toContain(word);
      }
    }
  });
});

// ── formatMarketCap ───────────────────────────────────────────────────────────

describe("formatMarketCap", () => {
  it("formats trillions with one decimal", () => {
    expect(formatMarketCap(3.2e12)).toBe("$3.2 trillion");
  });

  it("formats billions as rounded integer", () => {
    expect(formatMarketCap(200e9)).toBe("$200 billion");
  });

  it("formats millions as rounded integer", () => {
    expect(formatMarketCap(500e6)).toBe("$500 million");
  });

  it("returns unavailable message for zero", () => {
    expect(formatMarketCap(0)).toContain("not available");
  });

  it("returns unavailable message for undefined", () => {
    expect(formatMarketCap(undefined)).toContain("not available");
  });

  it("returns unavailable message for negative values", () => {
    expect(formatMarketCap(-1e9)).toContain("not available");
  });
});

// ── buildCompanyLine ──────────────────────────────────────────────────────────

describe("buildCompanyLine", () => {
  it("returns a fallback sentence when no profile provided", () => {
    const line = buildCompanyLine("NVDA");
    expect(line).toContain("NVDA");
    expect(line).toMatch(/[.!?]$/);
  });

  it("includes story group in fallback when provided", () => {
    const line = buildCompanyLine("NVDA", undefined, "AI Infrastructure");
    expect(line).toContain("AI Infrastructure");
  });

  it("uses company name when available", () => {
    const line = buildCompanyLine("NVDA", { companyName: "NVIDIA Corporation" });
    expect(line).toContain("NVIDIA Corporation");
  });

  it("includes industry when both sector and industry present", () => {
    const line = buildCompanyLine("NVDA", { companyName: "NVIDIA", sector: "Technology", industry: "Semiconductors" });
    expect(line).toContain("Semiconductors");
  });

  it("includes sector when industry missing", () => {
    const line = buildCompanyLine("NVDA", { companyName: "NVIDIA", sector: "Technology" });
    expect(line).toContain("Technology");
  });

  it("includes formatted market cap when present", () => {
    const line = buildCompanyLine("NVDA", { companyName: "NVIDIA", marketCap: 3.2e12 });
    expect(line).toContain("trillion");
  });

  it("produces a complete sentence ending with a period", () => {
    const line = buildCompanyLine("NVDA", { companyName: "NVIDIA" });
    expect(line).toMatch(/\.$/);
  });

  it("contains no forbidden execution language", () => {
    const line = buildCompanyLine("NVDA", { companyName: "NVIDIA", sector: "Technology" }, "AI Infrastructure");
    const forbidden = ["buy", "sell", "order", "target", "stop", "broker", "account", "conviction"];
    for (const w of forbidden) {
      expect(line.toLowerCase()).not.toContain(w);
    }
  });

  it("does not expose raw theme IDs in output", () => {
    const line = buildCompanyLine("NVDA", undefined, "AI Infrastructure");
    expect(line).not.toContain("ai_energy_nuclear");
  });
});

// ── buildFundamentalsLine ─────────────────────────────────────────────────────

describe("buildFundamentalsLine", () => {
  it("returns unavailable fallback when no data provided", () => {
    const line = buildFundamentalsLine();
    expect(line).toContain("not available");
    expect(line).toMatch(/[.!?]$/);
  });

  it("returns unavailable fallback when empty object provided", () => {
    const line = buildFundamentalsLine({});
    expect(line).toContain("not available");
  });

  it("includes PE ratio when positive", () => {
    const line = buildFundamentalsLine({ peRatio: 35 });
    expect(line).toContain("35");
    expect(line).toContain("earnings");
  });

  it("includes gross margin when positive", () => {
    const line = buildFundamentalsLine({ grossMargin: 0.74 });
    expect(line).toContain("74%");
  });

  it("includes revenue growth when positive", () => {
    const line = buildFundamentalsLine({ revenueGrowth: 0.22 });
    expect(line).toContain("growing");
    expect(line).toContain("22%");
  });

  it("handles negative or zero PE gracefully — does not show it", () => {
    const line = buildFundamentalsLine({ peRatio: -5 });
    expect(line).not.toContain("-5");
    expect(line).toContain("not available");
  });

  it("uses 'contracting' language for negative revenue growth", () => {
    const line = buildFundamentalsLine({ revenueGrowth: -0.15 });
    expect(line).toContain("contracting");
    expect(line).toContain("15%");
  });

  it("includes trailing data caveat when data is available", () => {
    const line = buildFundamentalsLine({ peRatio: 30 });
    expect(line.toLowerCase()).toContain("trailing");
  });

  it("returns a complete sentence", () => {
    const line = buildFundamentalsLine({ peRatio: 30, grossMargin: 0.60 });
    expect(line).toMatch(/[.!?]$/);
  });

  it("contains no forbidden execution language", () => {
    const line = buildFundamentalsLine({ peRatio: 35, grossMargin: 0.7 });
    const forbidden = ["buy", "sell", "order", "broker", "account", "conviction", "confidence score", "stop loss"];
    for (const w of forbidden) {
      expect(line.toLowerCase()).not.toContain(w);
    }
  });
});

// ── buildAnalystLine ──────────────────────────────────────────────────────────

describe("buildAnalystLine", () => {
  it("returns unavailable fallback when no analyst data", () => {
    const line = buildAnalystLine();
    expect(line).toContain("not available");
    expect(line).toMatch(/[.!?]$/);
  });

  it("returns unavailable fallback when analyst object has no useful fields", () => {
    const line = buildAnalystLine({} as NameFundamentalsResponse["analyst"]);
    expect(line).toContain("not available");
  });

  it("includes rating count when present", () => {
    const line = buildAnalystLine({ ratingCount: 42 });
    expect(line).toContain("42");
  });

  it("translates strong buy consensus to customer-safe language", () => {
    const line = buildAnalystLine({ consensus: "Strong Buy", ratingCount: 30 });
    expect(line.toLowerCase()).not.toContain("strong buy");
    expect(line.toLowerCase()).toContain("positive");
  });

  it("translates hold/neutral consensus without using forbidden words", () => {
    const line = buildAnalystLine({ consensus: "Hold", ratingCount: 20 });
    expect(line.toLowerCase()).not.toContain(" hold");
    expect(line.toLowerCase()).not.toContain("buy");
    expect(line.toLowerCase()).not.toContain("sell");
  });

  it("translates sell consensus to cautious language", () => {
    const line = buildAnalystLine({ consensus: "Underweight", ratingCount: 15 });
    expect(line.toLowerCase()).not.toContain("underweight");
    expect(line.toLowerCase()).toContain("cautious");
  });

  it("labels price target as context, not recommendation", () => {
    const line = buildAnalystLine({ priceTarget: 180 });
    expect(line.toLowerCase()).not.toContain("buy");
    expect(line.toLowerCase()).not.toContain("sell");
    expect(line.toLowerCase()).toContain("context");
  });

  it("includes 'not a recommendation' framing", () => {
    const line = buildAnalystLine({ ratingCount: 40, priceTarget: 200 });
    expect(line.toLowerCase()).toContain("not a recommendation");
  });

  it("returns a complete sentence", () => {
    const line = buildAnalystLine({ ratingCount: 42, priceTarget: 180 });
    expect(line).toMatch(/[.!?]$/);
  });
});

// ── buildDetailQuestions ──────────────────────────────────────────────────────

describe("buildDetailQuestions", () => {
  const SYMBOL = "NVDA";
  const STORY = "AI Infrastructure";

  it("returns at least 3 questions", () => {
    expect(buildDetailQuestions(SYMBOL, STORY).length).toBeGreaterThanOrEqual(3);
  });

  it("all questions end with ?", () => {
    for (const q of buildDetailQuestions(SYMBOL, STORY)) {
      expect(q).toMatch(/\?$/);
    }
  });

  it("includes the symbol in at least one question", () => {
    const qs = buildDetailQuestions(SYMBOL, STORY);
    expect(qs.some(q => q.includes(SYMBOL))).toBe(true);
  });

  it("includes the story group label in at least one question", () => {
    const qs = buildDetailQuestions(SYMBOL, STORY);
    expect(qs.some(q => q.includes(STORY))).toBe(true);
  });

  it("does not expose raw theme IDs", () => {
    const qs = buildDetailQuestions(SYMBOL, "ai_energy_nuclear");
    const text = qs.join(" ");
    expect(text).not.toContain("ai_energy_nuclear");
  });

  it("contains no forbidden execution or trading language", () => {
    const forbidden = ["buy", "sell", "order", "position", "entry", "exit", "broker", "stop loss"];
    for (const q of buildDetailQuestions(SYMBOL, STORY)) {
      for (const w of forbidden) {
        expect(q.toLowerCase()).not.toContain(w);
      }
    }
  });

  it("each question is a non-empty string", () => {
    for (const q of buildDetailQuestions(SYMBOL, STORY)) {
      expect(q.trim().length).toBeGreaterThan(10);
    }
  });
});

// ── buildDetailQuestions — M13F extensions ────────────────────────────────────

describe("buildDetailQuestions — companyName param", () => {
  it("uses company name in first question when provided and differs from symbol", () => {
    const qs = buildDetailQuestions("NVDA", "AI Infrastructure", "NVIDIA Corporation");
    expect(qs[0]).toContain("NVIDIA Corporation");
    expect(qs[0]).not.toContain("NVDA");
  });

  it("falls back to symbol when companyName equals symbol", () => {
    const qs = buildDetailQuestions("NVDA", "AI Infrastructure", "NVDA");
    expect(qs[0]).toContain("NVDA");
  });

  it("falls back to symbol when companyName is undefined", () => {
    const qs = buildDetailQuestions("NVDA", "AI Infrastructure", undefined);
    expect(qs[0]).toContain("NVDA");
  });

  it("returns 4 questions regardless of companyName", () => {
    expect(buildDetailQuestions("NVDA", "AI Infrastructure", "NVIDIA").length).toBe(4);
    expect(buildDetailQuestions("NVDA", "AI Infrastructure").length).toBe(4);
  });

  it("contains no forbidden execution language with company name present", () => {
    const forbidden = ["buy", "sell", "order", "position", "entry", "exit", "broker", "stop loss"];
    for (const q of buildDetailQuestions("NVDA", "AI Infrastructure", "NVIDIA Corporation")) {
      for (const w of forbidden) {
        expect(q.toLowerCase()).not.toContain(w);
      }
    }
  });
});

// ── mergeFreshPrice ───────────────────────────────────────────────────────────

describe("mergeFreshPrice", () => {
  const existingPositive = buildPriceAction({ symbol: "NVDA", price: 900, changePct: 2.0 });
  const existingUnknown  = buildPriceAction(null);

  it("returns existing when fresh is null", () => {
    const result = mergeFreshPrice(null, existingPositive);
    expect(result).toBe(existingPositive);
  });

  it("returns existing when fresh changePct is null", () => {
    const result = mergeFreshPrice({ symbol: "NVDA", price: 910, changePct: null }, existingPositive);
    expect(result).toBe(existingPositive);
  });

  it("returns fresh price action when changePct is positive", () => {
    const fresh = { symbol: "NVDA", price: 950, changePct: 3.5 };
    const result = mergeFreshPrice(fresh, existingUnknown);
    expect(result.tone).toBe("positive");
    expect(result.price).toBe(950);
    expect(result.changePct).toBe(3.5);
    expect(result.displayText).toBe("Up 3.5% today");
  });

  it("returns fresh price action when changePct is negative", () => {
    const fresh = { symbol: "NVDA", price: 850, changePct: -2.1 };
    const result = mergeFreshPrice(fresh, existingPositive);
    expect(result.tone).toBe("negative");
    expect(result.displayText).toBe("Down 2.1% today");
  });

  it("overrides an unknown existing tone with a known fresh tone", () => {
    const fresh = { symbol: "NVDA", price: 920, changePct: 0.1 };
    const result = mergeFreshPrice(fresh, existingUnknown);
    expect(result.tone).toBe("neutral");
    expect(result.displayText).toBe("Flat today");
  });
});

// ── buildPriceFreshnessLabel ──────────────────────────────────────────────────

describe("buildPriceFreshnessLabel", () => {
  it("returns empty string for null", () => {
    expect(buildPriceFreshnessLabel(null)).toBe("");
  });

  it("returns 'Live' for a timestamp less than 2 minutes ago", () => {
    const ts = new Date(Date.now() - 30_000).toISOString(); // 30 sec ago
    expect(buildPriceFreshnessLabel(ts)).toBe("Live");
  });

  it("returns 'Live' for a timestamp 1 minute ago", () => {
    const ts = new Date(Date.now() - 60_000).toISOString();
    expect(buildPriceFreshnessLabel(ts)).toBe("Live");
  });

  it("returns 'Xm ago' for a timestamp 5 minutes ago", () => {
    const ts = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(buildPriceFreshnessLabel(ts)).toBe("5m ago");
  });

  it("returns empty string for an unparseable timestamp", () => {
    expect(buildPriceFreshnessLabel("not-a-date")).toBe("");
  });

  it("contains no forbidden execution language", () => {
    const ts = new Date(Date.now() - 10_000).toISOString();
    const label = buildPriceFreshnessLabel(ts);
    const forbidden = ["buy", "sell", "order", "broker", "account"];
    for (const w of forbidden) {
      expect(label.toLowerCase()).not.toContain(w);
    }
  });
});

// ── buildFundamentalsLine — M13F extensions ───────────────────────────────────

describe("buildFundamentalsLine — EPS and revenueGrowth", () => {
  it("includes EPS when positive and available", () => {
    const line = buildFundamentalsLine({ eps: 4.85 });
    expect(line).toContain("$4.85");
    expect(line).toContain("earnings per share");
  });

  it("includes EPS with negative sign when negative", () => {
    const line = buildFundamentalsLine({ eps: -0.32 });
    expect(line).toContain("-$0.32");
  });

  it("does not include EPS line when eps is absent", () => {
    const line = buildFundamentalsLine({ peRatio: 30 });
    expect(line).not.toContain("earnings per share");
  });

  it("shows growing revenue when revenueGrowth is positive", () => {
    const line = buildFundamentalsLine({ revenueGrowth: 0.18, grossMargin: 0.6 });
    expect(line).toContain("growing");
    expect(line).toContain("18%");
  });

  it("does not fabricate revenue growth when absent", () => {
    const line = buildFundamentalsLine({ peRatio: 25, grossMargin: 0.55 });
    expect(line).not.toContain("growing");
    expect(line).not.toContain("contracting");
  });

  it("combines PE, grossMargin, revenueGrowth, and EPS in one sentence", () => {
    const line = buildFundamentalsLine({
      peRatio: 35,
      grossMargin: 0.74,
      revenueGrowth: 0.22,
      eps: 3.50,
    });
    expect(line).toContain("35");
    expect(line).toContain("74%");
    expect(line).toContain("22%");
    expect(line).toContain("$3.50");
  });

  it("contains no forbidden execution language with EPS present", () => {
    const line = buildFundamentalsLine({ eps: 5.00, revenueGrowth: 0.10 });
    const forbidden = ["buy", "sell", "order", "broker", "account", "stop loss", "conviction"];
    for (const w of forbidden) {
      expect(line.toLowerCase()).not.toContain(w);
    }
  });
});

// ── buildWhyItMattersNow ──────────────────────────────────────────────────────

describe("buildWhyItMattersNow", () => {
  const SYMBOL = "NVDA";
  const STORY  = "AI Infrastructure";
  const REASON = "Dominant AI chip supplier benefiting from hyperscaler capex growth.";

  it("uses company name when provided and differs from symbol", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, {
      companyName: "NVIDIA Corporation",
    });
    expect(line).toContain("NVIDIA Corporation");
    expect(line.startsWith("NVIDIA Corporation")).toBe(true);
  });

  it("falls back to symbol when companyName equals symbol", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, { companyName: SYMBOL });
    expect(line.startsWith("NVDA")).toBe(true);
  });

  it("falls back to symbol when companyName is undefined", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON);
    expect(line.startsWith("NVDA")).toBe(true);
  });

  it("maps 'Directly connected' to a direct exposure phrase", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, {
      confidenceLanguage: "Directly connected",
    });
    expect(line).toContain("direct exposure");
  });

  it("maps 'Supply chain exposure' to supply chain phrasing", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, {
      confidenceLanguage: "Supply chain exposure",
    });
    expect(line).toContain("supply chain exposure");
  });

  it("adds driver note when driverActive is true", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, { driverActive: true });
    expect(line.toLowerCase()).toContain("driver");
    expect(line.toLowerCase()).toContain("active");
  });

  it("does not add driver note when driverActive is false", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, { driverActive: false });
    expect(line.toLowerCase()).not.toContain("driver is currently active");
  });

  it("translates raw theme IDs via TTG_STORY_LABELS", () => {
    const line = buildWhyItMattersNow(SYMBOL, "ai_energy_nuclear", REASON);
    expect(line).not.toContain("ai_energy_nuclear");
    expect(line).toContain("Energy & Nuclear");
  });

  it("adds catalyst note for Catalyst watch type", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, { watchType: "Catalyst watch" });
    expect(line.toLowerCase()).toContain("catalyst");
  });

  it("adds structural note for Structural watch type", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, { watchType: "Structural watch" });
    expect(line.toLowerCase()).toContain("structural");
  });

  it("contains no forbidden execution language", () => {
    const forbidden = ["buy", "sell", "order", "position", "entry", "exit", "broker",
      "account", "stop loss", "recommendation", "p&l"];
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON, {
      companyName: "NVIDIA Corporation",
      confidenceLanguage: "Directly connected",
      driverActive: true,
    });
    for (const w of forbidden) {
      expect(line.toLowerCase()).not.toContain(w);
    }
  });

  it("always ends with a non-empty string", () => {
    const line = buildWhyItMattersNow(SYMBOL, STORY, REASON);
    expect(line.trim().length).toBeGreaterThan(10);
  });
});

// ── buildRiskNoteLine ─────────────────────────────────────────────────────────

describe("buildRiskNoteLine", () => {
  it("returns riskNote as-is when company name is not provided", () => {
    const note = "Valuation is stretched relative to near-term catalysts.";
    expect(buildRiskNoteLine("NVDA", note)).toBe(note);
  });

  it("prefixes with company name when name is available and not in note", () => {
    const note = "Valuation is stretched relative to near-term catalysts.";
    const result = buildRiskNoteLine("NVDA", note, "NVIDIA Corporation");
    expect(result.startsWith("For NVIDIA Corporation:")).toBe(true);
    expect(result).toContain(note);
  });

  it("does NOT prefix if company name already appears in the note", () => {
    const note = "NVIDIA Corporation faces margin pressure from rising memory costs.";
    const result = buildRiskNoteLine("NVDA", note, "NVIDIA Corporation");
    expect(result).toBe(note);
  });

  it("does NOT prefix if symbol already appears in the note", () => {
    const note = "NVDA earnings guidance may disappoint relative to elevated expectations.";
    const result = buildRiskNoteLine("NVDA", note, "NVIDIA Corporation");
    expect(result).toBe(note);
  });

  it("returns note as-is when companyName equals symbol (redundant prefix)", () => {
    const note = "Valuation is stretched relative to near-term catalysts.";
    // companyName === symbol → label is null → no prefix
    const result = buildRiskNoteLine("NVDA", note, "NVDA");
    expect(result).toBe(note);
  });

  it("contains no forbidden execution language in generated prefix", () => {
    const note = "Sector rotation may reduce attention on this name.";
    const result = buildRiskNoteLine("MSFT", note, "Microsoft Corporation");
    const forbidden = ["buy", "sell", "order", "stop loss", "broker"];
    for (const w of forbidden) {
      expect(result.toLowerCase()).not.toContain(w);
    }
  });
});

describe("buildShortInterestLine", () => {
  it("returns null when shortInterest is undefined", () => {
    expect(buildShortInterestLine(undefined)).toBeNull();
  });

  it("returns null when shortFloatPct is null", () => {
    expect(buildShortInterestLine({ shortFloatPct: null as unknown as number, date: "2026-06-01" })).toBeNull();
  });

  it("returns null when shortFloatPct is below 10", () => {
    expect(buildShortInterestLine({ shortFloatPct: 9.9, date: "2026-06-01" })).toBeNull();
  });

  it("returns null at exactly 0", () => {
    expect(buildShortInterestLine({ shortFloatPct: 0, date: "2026-06-01" })).toBeNull();
  });

  it("returns elevated copy for 10–19% float short", () => {
    const result = buildShortInterestLine({ shortFloatPct: 15, date: "2026-06-01" });
    expect(result).not.toBeNull();
    expect(result).toContain("15%");
    expect(result).toContain("elevated");
  });

  it("returns squeeze-mechanism copy for ≥20% float short", () => {
    const result = buildShortInterestLine({ shortFloatPct: 25, date: "2026-06-01" });
    expect(result).not.toBeNull();
    expect(result).toContain("25%");
    expect(result).toContain("cover");
  });

  it("rounds to nearest integer", () => {
    const result = buildShortInterestLine({ shortFloatPct: 22.7, date: "2026-06-01" });
    expect(result).toContain("23%");
  });

  it("contains no forbidden execution language", () => {
    const result = buildShortInterestLine({ shortFloatPct: 25, date: "2026-06-01" }) ?? "";
    const forbidden = ["order", "broker", "place a trade", "execute"];
    for (const w of forbidden) {
      expect(result.toLowerCase()).not.toContain(w);
    }
  });
});
