import { describe, it, expect } from "vitest";
import {
  DRIVER_DICTIONARY,
  RISK_FLAG_DICTIONARY,
  ROLE_DICTIONARY,
  getScoreBand,
  explainDriverEvidence,
  getDormantThemeActivationNote,
  buildMarketStory,
  formatRouteHint,
  ALL_DRIVER_IDS,
} from "./intelligence";

// ── Type guard — no raw schema names in display labels ───────────────────────

const RAW_SCHEMA_PATTERNS = [
  /active_shadow_inferred/,
  /static_shadow/,
  /sprint\d/,
  /^[a-z]+_[a-z]+_[a-z]+$/,  // triple_underscore_ids should not appear as display labels
];

describe("DRIVER_DICTIONARY", () => {
  it("covers all known driver IDs", () => {
    const ALL_EXPECTED = [
      "ai_capex_growth", "ai_compute_demand", "geopolitical_risk_rising",
      "small_cap_risk_on", "yields_falling", "yields_rising",
      "credit_stress_easing", "credit_stress_rising", "risk_on_rotation",
      "risk_off_rotation", "gold_safe_haven_bid", "oil_supply_shock",
    ];
    for (const id of ALL_EXPECTED) {
      expect(DRIVER_DICTIONARY[id], `Missing driver: ${id}`).toBeDefined();
    }
  });

  it("every entry has required fields", () => {
    for (const [id, info] of Object.entries(DRIVER_DICTIONARY)) {
      expect(info.displayLabel, `${id} missing displayLabel`).toBeTruthy();
      expect(info.shortMeaning, `${id} missing shortMeaning`).toBeTruthy();
      expect(info.traderMeaning, `${id} missing traderMeaning`).toBeTruthy();
      expect(info.proxySymbol, `${id} missing proxySymbol`).toBeTruthy();
      expect(info.invalidationTrigger, `${id} missing invalidationTrigger`).toBeTruthy();
      expect(Array.isArray(info.affectedThemes), `${id} affectedThemes should be array`).toBe(true);
    }
  });

  it("display labels contain no raw schema patterns", () => {
    for (const [id, info] of Object.entries(DRIVER_DICTIONARY)) {
      for (const pattern of RAW_SCHEMA_PATTERNS) {
        expect(info.displayLabel, `${id} displayLabel exposes schema: ${pattern}`).not.toMatch(pattern);
      }
    }
  });

  it("tailwind/headwind/neutral classification is set", () => {
    const headwinds = ["yields_rising", "credit_stress_rising", "risk_off_rotation"];
    const tailwinds = ["ai_capex_growth", "ai_compute_demand", "small_cap_risk_on", "yields_falling"];
    for (const id of headwinds) {
      expect(DRIVER_DICTIONARY[id]?.sentimentType, `${id} should be headwind`).toBe("headwind");
    }
    for (const id of tailwinds) {
      expect(DRIVER_DICTIONARY[id]?.sentimentType, `${id} should be tailwind`).toBe("tailwind");
    }
  });
});

// ── RISK_FLAG_DICTIONARY ──────────────────────────────────────────────────────

describe("RISK_FLAG_DICTIONARY", () => {
  it("every flag has required fields", () => {
    for (const [flag, info] of Object.entries(RISK_FLAG_DICTIONARY)) {
      expect(info.displayLabel, `${flag} missing displayLabel`).toBeTruthy();
      expect(info.traderMeaning, `${flag} missing traderMeaning`).toBeTruthy();
      expect(["low", "medium", "high"], `${flag} bad severity`).toContain(info.severity);
      expect(["blocks", "reduces_confidence", "warns"], `${flag} bad effect`).toContain(info.effect);
      expect(info.removalTrigger, `${flag} missing removalTrigger`).toBeTruthy();
    }
  });

  it("covers all backend risk flag IDs used in the system", () => {
    const KNOWN_FLAGS = [
      "valuation", "crowding", "capex_delay", "credit_stress",
      "power_demand_disappointment", "memory_cycle_risk", "commodity_pricing",
      "speculative_growth", "financing_risk", "budget_risk", "de_escalation",
    ];
    for (const flag of KNOWN_FLAGS) {
      expect(RISK_FLAG_DICTIONARY[flag], `Missing flag: ${flag}`).toBeDefined();
    }
  });
});

// ── ROLE_DICTIONARY ───────────────────────────────────────────────────────────

describe("ROLE_DICTIONARY", () => {
  it("covers all backend role values", () => {
    expect(ROLE_DICTIONARY["direct_beneficiary"]).toBeDefined();
    expect(ROLE_DICTIONARY["second_order_beneficiary"]).toBeDefined();
    expect(ROLE_DICTIONARY["etf_proxy"]).toBeDefined();
  });

  it("every role has a display label and color", () => {
    for (const [role, info] of Object.entries(ROLE_DICTIONARY)) {
      expect(info.displayLabel, `${role} missing displayLabel`).toBeTruthy();
      expect(info.color, `${role} missing color`).toBeTruthy();
      expect(info.bgColor, `${role} missing bgColor`).toBeTruthy();
    }
  });
});

// ── getScoreBand ──────────────────────────────────────────────────────────────

describe("getScoreBand", () => {
  it("returns correct bands for boundary values", () => {
    expect(getScoreBand(0.80).label).toBe("High Conviction");
    expect(getScoreBand(0.79).label).toBe("Strong Watchlist");
    expect(getScoreBand(0.65).label).toBe("Strong Watchlist");
    expect(getScoreBand(0.64).label).toBe("Developing");
    expect(getScoreBand(0.50).label).toBe("Developing");
    expect(getScoreBand(0.49).label).toBe("Low Confidence");
    expect(getScoreBand(0.00).label).toBe("Low Confidence");
  });

  it("every band has color, bgColor, borderColor", () => {
    for (const conf of [0.9, 0.7, 0.55, 0.3]) {
      const band = getScoreBand(conf);
      expect(band.color).toBeTruthy();
      expect(band.bgColor).toBeTruthy();
      expect(band.borderColor).toBeTruthy();
      expect(band.meaning).toBeTruthy();
    }
  });
});

// ── explainDriverEvidence ─────────────────────────────────────────────────────

const MOCK_EVIDENCE = {
  smh_5d_ret: 0.036,
  nvda_5d_ret: -0.044,
  ief_5d_ret: 0.004,
  uso_5d_ret: -0.049,
  spy_5d_ret: 0.009,
  ita_5d_ret: 0.037,
  uvxy_5d_ret: -0.080,
  hyg_5d_ret: 0.006,
  lqd_5d_ret: 0.005,
  gld_5d_ret: -0.008,
  iwm_5d_ret: 0.027,
};

describe("explainDriverEvidence", () => {
  it("returns all required fields for every known driver", () => {
    for (const id of Object.keys(DRIVER_DICTIONARY)) {
      const ev = explainDriverEvidence(id, MOCK_EVIDENCE, true);
      expect(ev.measurement, `${id} missing measurement`).toBeTruthy();
      expect(ev.interpretation, `${id} missing interpretation`).toBeTruthy();
      expect(ev.causalChain, `${id} missing causalChain`).toBeTruthy();
      expect(ev.threshold, `${id} missing threshold`).toBeTruthy();
      expect(["confirming", "warning", "inactive"]).toContain(ev.status);
      expect(Array.isArray(ev.affectedThemeLabels)).toBe(true);
    }
  });

  it("active status reflects isActive flag", () => {
    const active   = explainDriverEvidence("ai_capex_growth", MOCK_EVIDENCE, true);
    const inactive = explainDriverEvidence("ai_capex_growth", MOCK_EVIDENCE, false);
    expect(active.status).not.toBe("inactive");
    expect(inactive.status).toBe("inactive");
  });

  it("formats percentage measurements with sign", () => {
    const ev = explainDriverEvidence("ai_capex_growth", MOCK_EVIDENCE, true);
    expect(ev.measurement).toMatch(/[+-]\d+\.\d+%/);
  });

  it("unknown driver returns graceful fallback", () => {
    const ev = explainDriverEvidence("nonexistent_driver", MOCK_EVIDENCE, false);
    expect(ev.measurement).toBeTruthy();
    expect(ev.status).toBe("inactive");
  });

  it("geopolitical driver computes ITA vs SPY spread correctly", () => {
    const ev = explainDriverEvidence("geopolitical_risk_rising", MOCK_EVIDENCE, true);
    expect(ev.measurement).toContain("ITA");
    expect(ev.measurement).toContain("SPY");
    // Spread should be ita - spy = 0.037 - 0.009 = 0.028 → +2.80%
    expect(ev.measurement).toContain("+2.80%");
  });

  it("small_cap driver computes IWM vs SPY spread correctly", () => {
    const ev = explainDriverEvidence("small_cap_risk_on", MOCK_EVIDENCE, true);
    // Spread should be iwm - spy = 0.027 - 0.009 = 0.018 → +1.80%
    expect(ev.measurement).toContain("+1.80%");
  });
});

// ── getDormantThemeActivationNote ─────────────────────────────────────────────

describe("getDormantThemeActivationNote", () => {
  it("returns note mentioning missing drivers for dormant theme", () => {
    const note = getDormantThemeActivationNote("semiconductors", []);
    expect(note).toContain("Would activate when");
    expect(note).toMatch(/AI Infrastructure Spending|AI Compute Demand/);
  });

  it("returns driver-already-active note when drivers present", () => {
    const note = getDormantThemeActivationNote("semiconductors", ["ai_capex_growth", "ai_compute_demand"]);
    expect(note).toContain("appear active");
  });

  it("returns graceful fallback for unknown theme", () => {
    const note = getDormantThemeActivationNote("some_unknown_theme", []);
    expect(note).toBeTruthy();
  });
});

// ── buildMarketStory ──────────────────────────────────────────────────────────

describe("buildMarketStory", () => {
  const themes = [
    { theme_id: "data_centre_power", state: "activated", direction: "tailwind", confidence: 0.45, active_drivers: ["ai_capex_growth"] },
    { theme_id: "semiconductors",    state: "activated", direction: "tailwind", confidence: 0.70, active_drivers: ["ai_capex_growth"] },
    { theme_id: "defence_aerospace", state: "activated", direction: "tailwind", confidence: 0.55, active_drivers: ["geopolitical_risk_rising"] },
    { theme_id: "software_cloud",    state: "dormant",   direction: "tailwind", confidence: 0.10, active_drivers: [] },
  ];

  it("produces risk-on sentiment when AI + breadth drivers active", () => {
    const story = buildMarketStory(
      ["ai_capex_growth", "ai_compute_demand", "small_cap_risk_on"],
      themes, [], "BULL_TRENDING",
    );
    expect(story.overallSentiment).toBe("risk-on");
  });

  it("produces risk-off sentiment when risk_off_rotation active", () => {
    const story = buildMarketStory(
      ["risk_off_rotation", "gold_safe_haven_bid", "credit_stress_rising"],
      [], [], null,
    );
    expect(story.overallSentiment).toBe("risk-off");
  });

  it("produces neutral sentiment with no active drivers", () => {
    const story = buildMarketStory([], [], [], null);
    expect(story.overallSentiment).toBe("neutral");
  });

  it("headline references active forces", () => {
    const story = buildMarketStory(
      ["ai_capex_growth", "geopolitical_risk_rising"],
      themes, [], "BULL_TRENDING",
    );
    expect(story.headline).toContain("AI infrastructure demand");
  });

  it("populates bullets for AI drivers", () => {
    const story = buildMarketStory(
      ["ai_capex_growth", "ai_compute_demand"],
      themes, [], null,
    );
    const aiMatch = story.bullets.some(b => b.toLowerCase().includes("ai infrastructure"));
    expect(aiMatch).toBe(true);
  });

  it("includes breadth bullet when small_cap_risk_on active", () => {
    const story = buildMarketStory(["small_cap_risk_on"], themes, [], null);
    const breadthMatch = story.bullets.some(b => b.toLowerCase().includes("small cap") || b.toLowerCase().includes("breadth"));
    expect(breadthMatch).toBe(true);
  });

  it("all required fields are present and non-empty", () => {
    const story = buildMarketStory(
      ["ai_capex_growth", "geopolitical_risk_rising", "small_cap_risk_on"],
      themes, [], "BULL_TRENDING",
    );
    expect(story.headline).toBeTruthy();
    expect(story.expectation).toBeTruthy();
    expect(story.attention).toBeTruthy();
    expect(story.tradingMode).toBeTruthy();
    expect(Array.isArray(story.bullets)).toBe(true);
    expect(Array.isArray(story.risks)).toBe(true);
  });

  it("mentions active tailwind themes in attention", () => {
    const story = buildMarketStory(
      ["ai_capex_growth"],
      themes.filter(t => t.state === "activated"), [], null,
    );
    // Attention should mention at least one active theme
    expect(story.attention).not.toBe("No active tailwind themes — system is in observation mode.");
  });

  it("headwind themes appear in bullets", () => {
    const themesWithHeadwind = [
      ...themes,
      { theme_id: "reits_falling_yield", state: "activated", direction: "headwind", confidence: 0.6, active_drivers: ["yields_rising"] },
    ];
    const story = buildMarketStory(["yields_rising"], themesWithHeadwind, [], null);
    const hwMatch = story.bullets.some(b => b.toLowerCase().includes("pressure") || b.toLowerCase().includes("avoid"));
    expect(hwMatch).toBe(true);
  });

  it("trading mode changes for risk-off sentiment", () => {
    const story = buildMarketStory(["risk_off_rotation"], [], [], null);
    expect(story.tradingMode.toLowerCase()).toContain("reduce");
  });
});

// ── formatRouteHint ───────────────────────────────────────────────────────────

describe("formatRouteHint", () => {
  it("returns Watchlist for empty/undefined hints", () => {
    expect(formatRouteHint(undefined)).toBe("Watchlist");
    expect(formatRouteHint([])).toBe("Watchlist");
  });

  it("returns Position or swing for position+swing", () => {
    expect(formatRouteHint(["position", "swing", "watchlist"])).toBe("Position or swing");
  });

  it("returns Swing trade for swing only", () => {
    expect(formatRouteHint(["swing"])).toBe("Swing trade");
  });

  it("returns Position trade for position only", () => {
    expect(formatRouteHint(["position"])).toBe("Position trade");
  });
});

// ── No raw schema names exposed ───────────────────────────────────────────────

describe("No raw schema names exposed in display layer", () => {
  const FORBIDDEN_PATTERNS = [
    "active_shadow_inferred",
    "static_shadow",
    "sprint4b",
    "sprint37",
    "freshness_status",
    "schema_version",
    "live_output_changed",
    "broker_called",
    "llm_used",
  ];

  it("DRIVER_DICTIONARY display labels contain no forbidden schema strings", () => {
    const allLabels = Object.values(DRIVER_DICTIONARY)
      .flatMap(d => [d.displayLabel, d.shortMeaning, d.traderMeaning]);
    for (const label of allLabels) {
      for (const forbidden of FORBIDDEN_PATTERNS) {
        expect(label).not.toContain(forbidden);
      }
    }
  });

  it("RISK_FLAG_DICTIONARY display labels contain no forbidden schema strings", () => {
    const allLabels = Object.values(RISK_FLAG_DICTIONARY)
      .flatMap(r => [r.displayLabel, r.traderMeaning]);
    for (const label of allLabels) {
      for (const forbidden of FORBIDDEN_PATTERNS) {
        expect(label).not.toContain(forbidden);
      }
    }
  });
});
