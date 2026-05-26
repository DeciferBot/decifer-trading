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
    bucket_id: "ai_compute",
    bucket_label: "AI Compute",
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
    expect(pa.displayText).toBe("Live price confirmation not available yet.");
    expect(pa.changePct).toBeNull();
    expect(pa.price).toBeNull();
  });

  it("returns unknown with fallback text when changePct is null", () => {
    const pa = buildPriceAction({ symbol: "NVDA", price: 900, changePct: null });
    expect(pa.tone).toBe("unknown");
    expect(pa.displayText).toBe("Live price confirmation not available yet.");
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
    const card = buildResearchCard(makeCard(), emptyPriceMap, "AI Infrastructure & Energy");
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
    const card = buildResearchCard(makeCard(), emptyPriceMap, "AI Infrastructure & Energy");
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
    const card = buildResearchCard(makeCard(), emptyPriceMap, "AI Infrastructure & Energy");
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

  it("groups symbols under their theme story label", () => {
    const theme = makeTheme({ symbols: [makeCard(), makeCard({ symbol: "AMD" })] });
    const groups = buildStoryGroups([theme], emptyPriceMap);
    expect(groups).toHaveLength(1);
    expect(groups[0].storyLabel).toBe("AI Infrastructure & Energy");
    expect(groups[0].cards).toHaveLength(2);
  });

  it("uses TTG_STORY_LABELS for known theme IDs", () => {
    const theme = makeTheme({ theme_id: "defence_rearmament", label: "Defence" });
    const groups = buildStoryGroups([theme], emptyPriceMap);
    expect(groups[0].storyLabel).toBe("Defence & Security");
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
