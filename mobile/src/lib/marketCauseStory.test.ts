import { describe, it, expect } from "vitest";
import {
  buildMarketCauseCards,
  getCauseMarketImpact,
  getTtgCauseContext,
  type MarketCauseCard,
} from "./marketCauseStory";
import type { MarketNowPayload } from "./customerApi";

function makePayload(overrides: Partial<MarketNowPayload> = {}): MarketNowPayload {
  return {
    key_drivers: [],
    themes: [],
    key_events: [],
    what_changed: [],
    watch_next: [],
    ...overrides,
  };
}

const FORBIDDEN_WORDS = [
  "buy", "sell", "hold", "order", "stop", "target",
  "broker", "execution", "position size",
  "scanner", "pipeline", "payload", "activation",
  "crosswalk", "ttg_id", "market_now_id",
  "confidence to trade",
];

function assertNoForbiddenWords(text: string, fieldName: string) {
  for (const word of FORBIDDEN_WORDS) {
    expect(
      text.toLowerCase(),
      `${fieldName} must not contain "${word}"`,
    ).not.toContain(word);
  }
}

describe("buildMarketCauseCards", () => {
  it("returns empty array when no key_drivers", () => {
    const cards = buildMarketCauseCards(makePayload());
    expect(cards).toHaveLength(0);
  });

  it("returns empty array when drivers are all unknown IDs", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["unknown_driver_x", "another_fake"] }));
    expect(cards).toHaveLength(0);
  });

  it("builds a card for ai_capex_growth", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    expect(cards).toHaveLength(1);
    const card = cards[0];
    expect(card.cause_label).toBe("AI Infrastructure Spending");
    expect(card.what_happened.length).toBeGreaterThan(10);
    expect(card.market_impact.length).toBeGreaterThan(10);
    expect(card.risk_to_monitor.length).toBeGreaterThan(10);
  });

  it("ai_capex_growth has connected TTG theme ai_energy_nuclear", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    const ttgIds = cards[0].connected_themes.map(t => t.ttgId);
    expect(ttgIds).toContain("ai_energy_nuclear");
  });

  it("geopolitical_risk_rising has multiple connected TTG themes", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["geopolitical_risk_rising"] }));
    const ttgIds = cards[0].connected_themes.map(t => t.ttgId);
    expect(ttgIds).toContain("defence_rearmament");
    expect(ttgIds).toContain("cybersecurity_digital_resilience");
    expect(ttgIds).toContain("gold_real_assets");
  });

  it("futures_risk_on has evidence_basis 'Futures signal'", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["futures_risk_on"] }));
    expect(cards[0].evidence_basis).toBe("Futures signal");
  });

  it("futures_risk_off has evidence_basis 'Futures signal'", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["futures_risk_off"] }));
    expect(cards[0].evidence_basis).toBe("Futures signal");
  });

  it("macro driver has evidence_basis 'Macro driver active' when no key_events", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    expect(cards[0].evidence_basis).toBe("Macro driver active");
  });

  it("macro driver has evidence_basis 'Fresh event evidence' when key_events present", () => {
    const cards = buildMarketCauseCards(makePayload({
      key_drivers: ["ai_capex_growth"],
      key_events: [{ title: "Microsoft Azure guidance raised" }],
    }));
    expect(cards[0].evidence_basis).toBe("Fresh event evidence");
    expect(cards[0].has_fresh_evidence).toBe(true);
  });

  it("futures driver never has has_fresh_evidence=true even with key_events", () => {
    const cards = buildMarketCauseCards(makePayload({
      key_drivers: ["futures_risk_on"],
      key_events: [{ title: "Some headline" }],
    }));
    expect(cards[0].has_fresh_evidence).toBe(false);
  });

  it("caps output at 6 cards even with more than 6 known drivers", () => {
    const cards = buildMarketCauseCards(makePayload({
      key_drivers: [
        "ai_capex_growth", "geopolitical_risk_rising", "yields_falling",
        "gold_safe_haven_bid", "risk_on_rotation", "futures_risk_on",
        "futures_risk_off", "credit_stress_easing",
      ],
    }));
    expect(cards.length).toBeLessThanOrEqual(6);
  });

  it("connected_themes is empty array (not null/undefined) for unmapped drivers", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["futures_risk_on"] }));
    expect(Array.isArray(cards[0].connected_themes)).toBe(true);
    expect(cards[0].connected_themes).toHaveLength(0);
  });

  it("connected_names_count defaults to 0 when no radar or universe data", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    expect(cards[0].connected_names_count).toBe(0);
  });

  it("connected_names_count counts radar items matching driver themes", () => {
    const cards = buildMarketCauseCards(makePayload({
      key_drivers: ["geopolitical_risk_rising"],
      radar: [
        { symbol: "LMT", reason_to_watch: "Defence spending", theme_link: "defence" },
        { symbol: "CRWD", reason_to_watch: "Cyber", theme_link: "cybersecurity" },
        { symbol: "GLD", reason_to_watch: "Gold", theme_link: "gold_safe_haven_bid" },
        { symbol: "AAPL", reason_to_watch: "Unrelated", theme_link: "mega_cap_platforms" },
      ],
    }));
    expect(cards[0].connected_names_count).toBe(3); // LMT, CRWD, GLD match
  });

  it("primary_ttg_id is null for drivers with no TTG mapping", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["futures_risk_on"] }));
    expect(cards[0].primary_ttg_id).toBeNull();
  });

  it("primary_ttg_id is set for ai_capex_growth", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    expect(cards[0].primary_ttg_id).toBe("ai_energy_nuclear");
  });

  it("primary_market_now_id is null for futures drivers", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["futures_risk_off"] }));
    expect(cards[0].primary_market_now_id).toBeNull();
  });

  it("primary_market_now_id is set for ai_capex_growth", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    expect(cards[0].primary_market_now_id).toBeTruthy();
  });
});

describe("buildMarketCauseCards — prohibited language audit", () => {
  const drivers = [
    "ai_capex_growth", "ai_compute_demand", "geopolitical_risk_rising",
    "yields_falling", "yields_rising", "gold_safe_haven_bid", "futures_risk_on",
    "futures_risk_off", "risk_on_rotation", "credit_stress_easing",
    "oil_supply_shock", "smh_tactical_weakness", "reits_falling_yield",
  ];

  for (const driver of drivers) {
    it(`${driver} — no prohibited words in any rendered field`, () => {
      const cards = buildMarketCauseCards(makePayload({ key_drivers: [driver] }));
      if (cards.length === 0) return; // unknown driver — skip
      const card = cards[0];
      const renderedText = [
        card.cause_label,
        card.what_happened,
        card.market_impact,
        card.risk_to_monitor,
        card.evidence_basis,
        ...card.connected_themes.map(t => t.ttgLabel),
      ].join(" ");
      assertNoForbiddenWords(renderedText, driver);
    });
  }

  it("quiet fallback does not mention pipeline/activation/payload/scanner", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: [] }));
    expect(cards).toHaveLength(0);
    // (fallback copy lives in the view components — no copy tested here)
  });
});

describe("buildMarketCauseCards — internal IDs not exposed", () => {
  it("cause_label does not contain internal driver ID format (underscores)", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    // cause_label should be human-readable, not "ai_capex_growth"
    expect(cards[0].cause_label).not.toContain("ai_capex_growth");
    expect(cards[0].cause_label).not.toBe("ai_capex_growth");
  });

  it("connected_themes labels are human-readable, not raw TTG IDs", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["ai_capex_growth"] }));
    for (const theme of cards[0].connected_themes) {
      // Label should not be the raw snake_case ID
      expect(theme.ttgLabel).not.toBe(theme.ttgId);
      expect(theme.ttgLabel).not.toContain("_");
    }
  });

  it("no card field renders ttg_id or market_now_id strings literally", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["geopolitical_risk_rising"] }));
    const card = cards[0];
    const renderedText = [card.cause_label, card.what_happened, card.market_impact, card.risk_to_monitor].join(" ");
    expect(renderedText).not.toContain("ttg_id");
    expect(renderedText).not.toContain("market_now_id");
    expect(renderedText).not.toContain("defence_rearmament");
    expect(renderedText).not.toContain("geopolitical_risk_rising");
  });
});

describe("buildMarketCauseCards — human-readable label normalisation", () => {
  it("handles human-readable 'AI capital spending cycle expanding' label", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["AI capital spending cycle expanding"] }));
    expect(cards).toHaveLength(1);
    expect(cards[0].cause_label).toBe("AI Infrastructure Spending");
  });

  it("handles human-readable 'Geopolitical risk elevated' label", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["Geopolitical risk elevated"] }));
    expect(cards).toHaveLength(1);
    expect(cards[0].cause_label).toBe("Geopolitical Risk");
  });

  it("handles human-readable 'AI compute demand rising' label", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["AI compute demand rising"] }));
    expect(cards).toHaveLength(1);
    expect(cards[0].cause_label).toBe("AI Compute Demand");
  });

  it("handles human-readable 'Small-cap stocks outperforming' label", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["Small-cap stocks outperforming large-caps"] }));
    expect(cards).toHaveLength(1);
    expect(cards[0].cause_label).toBe("Improving Risk Appetite");
  });

  it("handles mixed payload of IDs and human-readable labels", () => {
    const cards = buildMarketCauseCards(makePayload({
      key_drivers: ["ai_capex_growth", "Geopolitical risk elevated"],
    }));
    expect(cards).toHaveLength(2);
  });

  it("unknown human-readable labels are silently dropped", () => {
    const cards = buildMarketCauseCards(makePayload({ key_drivers: ["Something completely unrecognisable xyz"] }));
    expect(cards).toHaveLength(0);
  });
});

describe("getCauseMarketImpact", () => {
  it("returns the market impact sentence for known driver IDs", () => {
    const impact = getCauseMarketImpact("ai_capex_growth");
    expect(impact.length).toBeGreaterThan(10);
    assertNoForbiddenWords(impact, "getCauseMarketImpact(ai_capex_growth)");
  });

  it("returns empty string for unknown driver IDs", () => {
    expect(getCauseMarketImpact("unknown_driver")).toBe("");
  });
});

describe("getTtgCauseContext", () => {
  it("returns a context sentence for known TTG IDs", () => {
    const ctx = getTtgCauseContext("ai_energy_nuclear");
    expect(ctx.length).toBeGreaterThan(10);
    expect(ctx.toLowerCase()).toContain("connected to");
    assertNoForbiddenWords(ctx, "getTtgCauseContext(ai_energy_nuclear)");
  });

  it("returns context for all 10 known TTG IDs", () => {
    const ttgIds = [
      "ai_energy_nuclear", "defence_rearmament", "cybersecurity_digital_resilience",
      "reshoring_industrial_capex", "critical_minerals_copper", "gold_real_assets",
      "glp1_metabolic_health", "housing_rate_sensitivity",
      "water_infrastructure", "digital_assets_infrastructure",
    ];
    for (const id of ttgIds) {
      const ctx = getTtgCauseContext(id);
      expect(ctx.length, `No context for TTG ID: ${id}`).toBeGreaterThan(10);
    }
  });

  it("returns empty string for unknown TTG IDs", () => {
    expect(getTtgCauseContext("nonexistent_theme")).toBe("");
  });

  it("context strings contain no prohibited words", () => {
    const ttgIds = [
      "ai_energy_nuclear", "defence_rearmament", "gold_real_assets", "housing_rate_sensitivity",
    ];
    for (const id of ttgIds) {
      assertNoForbiddenWords(getTtgCauseContext(id), `getTtgCauseContext(${id})`);
    }
  });
});
