// Tests for customerBriefingModel — M13B.
// Pure-function tests. No DOM. No React.

import { describe, it, expect } from "vitest";
import {
  buildCustomerRegime,
  buildCustomerMarketStory,
  buildCustomerForces,
  buildConnectionTree,
  buildContextualSuggestions,
  containsProhibitedTerm,
  resolveForceThemeLabel,
  normalizeForceId,
  type CustomerMarketRegime,
  type CustomerMarketStory,
} from "./customerBriefingModel";
import type { MarketNowPayload, TtgTheme } from "./customerApi";
import type { CustomerStory } from "./customerStory";

// ── Helpers ────────────────────────────────────────────────────────────────────

function makePayload(overrides: Partial<MarketNowPayload> = {}): MarketNowPayload {
  return {
    market_regime_label: undefined,
    plain_english_summary: undefined,
    key_drivers: [],
    active_themes: [],
    themes: [],
    what_changed: [],
    watch_next: [],
    key_events: [],
    known_conflicts: [],
    freshness_timestamp: new Date().toISOString(),
    ...overrides,
  };
}

function makeStory(overrides: Partial<CustomerStory> = {}): CustomerStory {
  return {
    headline: "AI Capex Growth is driving 3 active themes",
    summary: "Hyperscalers are accelerating AI infrastructure investment.",
    market_state: "risk-on",
    freshness_label: "Just updated",
    evidence_mode: "structural",
    primary_drivers: [],
    active_theme_count: 3,
    building_theme_count: 0,
    weakening_theme_count: 0,
    dormant_theme_count: 5,
    mapped_structural: [],
    what_changed: [],
    watch_next: [],
    has_live_events: false,
    ...overrides,
  };
}

// ── buildCustomerRegime ────────────────────────────────────────────────────────

describe("buildCustomerRegime", () => {
  it("maps risk-on to correct label and green accent", () => {
    const regime = buildCustomerRegime("risk-on");
    expect(regime.state).toBe("risk-on");
    expect(regime.label).toBe("Risk-On");
    expect(regime.accentColor).toBe("#10b981");
    expect(regime.description).toBeTruthy();
  });

  it("maps risk-off to correct label and red accent", () => {
    const regime = buildCustomerRegime("risk-off");
    expect(regime.state).toBe("risk-off");
    expect(regime.label).toBe("Risk-Off");
    expect(regime.accentColor).toBe("#ef4444");
  });

  it("maps mixed to amber accent", () => {
    const regime = buildCustomerRegime("mixed");
    expect(regime.state).toBe("mixed");
    expect(regime.accentColor).toBe("#f59e0b");
  });

  it("maps monitoring to neutral accent", () => {
    const regime = buildCustomerRegime("monitoring");
    expect(regime.state).toBe("monitoring");
    expect(regime.accentColor).toBe("#6b7280");
  });

  it("regime description contains no prohibited terms", () => {
    for (const state of ["risk-on", "risk-off", "mixed", "monitoring"] as const) {
      const regime = buildCustomerRegime(state);
      expect(containsProhibitedTerm(regime.description)).toBe(false);
    }
  });
});

// ── buildCustomerMarketStory ───────────────────────────────────────────────────

describe("buildCustomerMarketStory", () => {
  it("returns a story with regime, macro_label, headline, summary", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const story   = makeStory({ market_state: "risk-on" });
    const ms      = buildCustomerMarketStory(payload, story);

    expect(ms.regime.state).toBe("risk-on");
    expect(ms.macro_label).toBeTruthy();
    expect(ms.headline).toBe(story.headline);
    expect(ms.summary).toBe(story.summary);
  });

  it("macro_label reflects top driver when available", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const story   = makeStory();
    const ms      = buildCustomerMarketStory(payload, story);
    expect(ms.macro_label).toContain("AI infrastructure");
  });

  it("macro_label uses market_mood when short and clean", () => {
    const payload = makePayload({
      key_drivers: [],
      market_mood: "Relief rally: markets are bouncing",
    });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "monitoring" }));
    expect(ms.macro_label).toContain("Relief rally");
  });

  it("sanitises prohibited terms from market_mood", () => {
    const payload = makePayload({
      key_drivers: [],
      market_mood: "Entry candidate signals are trade-ready today",
    });
    const ms = buildCustomerMarketStory(payload, makeStory());
    expect(ms.macro_label).not.toMatch(/trade-ready/i);
    expect(ms.macro_label).not.toMatch(/entry candidate/i);
  });

  it("builds supporting bullets from primary_drivers", () => {
    const story = makeStory({
      primary_drivers: [
        {
          label: "AI Capex Growth",
          explanation: "Hyperscalers are accelerating spending. This is the key driver.",
          linked_market_now_ids: [],
          linked_ttg_id: null,
          linked_ttg_label: null,
        },
        {
          label: "Geopolitical Risk",
          explanation: "International tensions are rising. Defence budgets are up.",
          linked_market_now_ids: [],
          linked_ttg_id: null,
          linked_ttg_label: null,
        },
      ],
    });
    const ms = buildCustomerMarketStory(makePayload(), story);
    expect(ms.supporting_bullets.length).toBeGreaterThan(0);
    expect(ms.supporting_bullets.length).toBeLessThanOrEqual(3);
  });

  it("caution comes from first known_conflict", () => {
    const payload = makePayload({
      known_conflicts: [
        "Headline read is positive but market reaction is negative.",
        "A second conflict.",
      ],
    });
    const ms = buildCustomerMarketStory(payload, makeStory());
    expect(ms.caution).toBe("Headline read is positive but market reaction is negative.");
  });

  it("caution is null when no known_conflicts", () => {
    const ms = buildCustomerMarketStory(makePayload(), makeStory());
    expect(ms.caution).toBeNull();
  });

  it("watch_next comes from first watch_next item", () => {
    const payload = makePayload({ watch_next: ["Watch for Fed commentary.", "Watch bond yields."] });
    const ms = buildCustomerMarketStory(payload, makeStory());
    expect(ms.watch_next).toBe("Watch for Fed commentary.");
  });

  it("watch_next falls back to what_to_watch when watch_next empty", () => {
    const payload = makePayload({ what_to_watch: ["Monitor credit spreads."] });
    const ms = buildCustomerMarketStory(payload, makeStory());
    expect(ms.watch_next).toBe("Monitor credit spreads.");
  });

  it("watch_next is null when neither field is present", () => {
    const ms = buildCustomerMarketStory(makePayload(), makeStory());
    expect(ms.watch_next).toBeNull();
  });

  it("has_live_events reflects story.has_live_events", () => {
    const ms = buildCustomerMarketStory(makePayload(), makeStory({ has_live_events: true }));
    expect(ms.has_live_events).toBe(true);
  });

  it("market regime renders customer-safe (no prohibited terms in any field)", () => {
    const payload = makePayload({ key_drivers: ["geopolitical_risk_rising"] });
    const story   = makeStory({ market_state: "mixed" });
    const ms      = buildCustomerMarketStory(payload, story);
    const allText = [ms.macro_label, ms.headline, ms.summary, ...ms.supporting_bullets].join(" ");
    expect(containsProhibitedTerm(allText)).toBe(false);
  });
});

// ── buildCustomerForces ───────────────────────────────────────────────────────

describe("buildCustomerForces", () => {
  it("active forces match key_drivers", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising"] });
    const { active } = buildCustomerForces(payload);
    expect(active).toHaveLength(2);
    expect(active.map(f => f.id)).toContain("ai_capex_growth");
    expect(active.map(f => f.id)).toContain("geopolitical_risk_rising");
  });

  it("all active forces have is_active = true", () => {
    const payload = makePayload({ key_drivers: ["futures_risk_on", "yields_falling"] });
    const { active } = buildCustomerForces(payload);
    expect(active.every(f => f.is_active)).toBe(true);
  });

  it("dormant forces have is_active = false", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const { dormant } = buildCustomerForces(payload);
    expect(dormant.every(f => !f.is_active)).toBe(true);
  });

  it("dormant forces do not include active forces", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const { dormant } = buildCustomerForces(payload);
    expect(dormant.map(f => f.id)).not.toContain("ai_capex_growth");
  });

  it("active forces render customer-safe language (no prohibited terms)", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "gold_safe_haven_bid"] });
    const { active } = buildCustomerForces(payload);
    for (const f of active) {
      const text = [f.label, f.why_it_matters, f.market_impact, f.risk_to_monitor].join(" ");
      expect(containsProhibitedTerm(text)).toBe(false);
    }
  });

  it("each active force has connected_theme_labels matching connected_theme_ids", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const { active } = buildCustomerForces(payload);
    for (const f of active) {
      expect(f.connected_theme_labels.length).toBe(f.connected_theme_ids.length);
    }
  });

  it("forces with no key_drivers: active is empty, dormant has all forces", () => {
    const { active, dormant } = buildCustomerForces(makePayload());
    expect(active).toHaveLength(0);
    expect(dormant.length).toBeGreaterThan(0);
  });

  it("normalises human-readable driver labels", () => {
    const payload = makePayload({ key_drivers: ["AI capital spending cycle expanding"] });
    const { active } = buildCustomerForces(payload);
    expect(active.map(f => f.id)).toContain("ai_capex_growth");
  });

  it("evidence basis is 'Futures signal' for futures forces", () => {
    const payload = makePayload({ key_drivers: ["futures_risk_on"] });
    const { active } = buildCustomerForces(payload);
    expect(active[0].evidence_basis).toBe("Futures signal");
  });

  it("evidence basis is 'Fresh evidence' when key_events present", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth"],
      key_events: [{ title: "NVDA beat" }],
    });
    const { active } = buildCustomerForces(payload);
    expect(active[0].evidence_basis).toBe("Fresh evidence");
  });
});

// ── buildConnectionTree ────────────────────────────────────────────────────────

describe("buildConnectionTree", () => {
  it("returns one node per active force", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising"] });
    const tree = buildConnectionTree(payload);
    expect(tree).toHaveLength(2);
    expect(tree.map(n => n.force_id)).toContain("ai_capex_growth");
  });

  it("each node has force_label and themes array", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const [node] = buildConnectionTree(payload);
    expect(node.force_label).toBe("AI Infrastructure Spending");
    expect(Array.isArray(node.themes)).toBe(true);
    expect(node.themes.length).toBeGreaterThan(0);
  });

  it("theme labels are customer-safe (no underscores)", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const [node] = buildConnectionTree(payload);
    for (const theme of node.themes) {
      expect(theme.theme_label).not.toContain("_");
    }
  });

  it("driver_active is true when TTG theme has driver_active=true", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const ttgThemes: TtgTheme[] = [
      {
        theme_id: "data_centre_power",
        label: "Data Centre Power",
        plain_english_description: "AI power demand.",
        status: "active",
        driver_ids: ["ai_capex_growth"],
        driver_active: true,
        risk_note: null,
      },
    ];
    const [node] = buildConnectionTree(payload, ttgThemes);
    const dcpTheme = node.themes.find(t => t.theme_id === "data_centre_power");
    expect(dcpTheme?.driver_active).toBe(true);
  });

  it("driver_active is false when no TTG data provided", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const [node] = buildConnectionTree(payload);
    expect(node.themes.every(t => !t.driver_active)).toBe(true);
  });

  it("returns empty array when no active forces", () => {
    expect(buildConnectionTree(makePayload())).toHaveLength(0);
  });

  it("force → theme → label path renders no prohibited terms", () => {
    const payload = makePayload({ key_drivers: ["geopolitical_risk_rising"] });
    const [node] = buildConnectionTree(payload);
    const text = [node.force_label, ...node.themes.map(t => t.theme_label)].join(" ");
    expect(containsProhibitedTerm(text)).toBe(false);
  });
});

// ── buildContextualSuggestions ────────────────────────────────────────────────

describe("buildContextualSuggestions", () => {
  it("returns an array of up to 8 strings", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "futures_risk_on"] });
    const qs = buildContextualSuggestions(payload);
    expect(qs.length).toBeLessThanOrEqual(8);
    expect(qs.length).toBeGreaterThan(0);
  });

  it("generates AI capex question when that driver is active", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const qs = buildContextualSuggestions(payload);
    expect(qs.some(q => q.toLowerCase().includes("ai infrastructure") || q.toLowerCase().includes("ai"))).toBe(true);
  });

  it("generates geopolitical question when that driver is active", () => {
    const payload = makePayload({ key_drivers: ["geopolitical_risk_rising"] });
    const qs = buildContextualSuggestions(payload);
    expect(qs.some(q => q.toLowerCase().includes("geopolit"))).toBe(true);
  });

  it("includes conflict question when known_conflicts is non-empty", () => {
    const payload = makePayload({
      key_drivers: [],
      known_conflicts: ["Price is positive but market rejected it."],
    });
    const qs = buildContextualSuggestions(payload);
    expect(qs.some(q => q.toLowerCase().includes("conflict"))).toBe(true);
  });

  it("falls back to static questions when no drivers or themes", () => {
    const qs = buildContextualSuggestions(makePayload());
    expect(qs.length).toBeGreaterThan(0);
    expect(typeof qs[0]).toBe("string");
  });

  it("no question contains prohibited terms", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "futures_risk_on", "gold_safe_haven_bid"],
      themes: [{ theme: "data_centre_power", state: "active" }],
    });
    const qs = buildContextualSuggestions(payload);
    for (const q of qs) {
      expect(containsProhibitedTerm(q)).toBe(false);
    }
  });

  it("no duplicate questions in output", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "ai_compute_demand", "futures_risk_on"] });
    const qs = buildContextualSuggestions(payload);
    expect(new Set(qs).size).toBe(qs.length);
  });
});

// ── containsProhibitedTerm ────────────────────────────────────────────────────

describe("containsProhibitedTerm", () => {
  it("detects 'trade-ready'", () => {
    expect(containsProhibitedTerm("This setup is trade-ready")).toBe(true);
  });

  it("detects 'entry candidate'", () => {
    expect(containsProhibitedTerm("an entry candidate for tomorrow")).toBe(true);
  });

  it("detects 'payload'", () => {
    expect(containsProhibitedTerm("build the payload now")).toBe(true);
  });

  it("detects 'scanner' case-insensitively", () => {
    expect(containsProhibitedTerm("run the Scanner")).toBe(true);
  });

  it("allows safe customer language", () => {
    expect(containsProhibitedTerm("AI infrastructure is driving attention today")).toBe(false);
    expect(containsProhibitedTerm("Why is gold in demand right now?")).toBe(false);
    expect(containsProhibitedTerm("Risk-on environment is supporting growth themes")).toBe(false);
  });
});

// ── normalizeForceId ──────────────────────────────────────────────────────────

describe("normalizeForceId", () => {
  it("passes through known IDs unchanged", () => {
    expect(normalizeForceId("ai_capex_growth")).toBe("ai_capex_growth");
    expect(normalizeForceId("geopolitical_risk_rising")).toBe("geopolitical_risk_rising");
  });

  it("normalises AI capex human label", () => {
    expect(normalizeForceId("AI capital spending cycle expanding")).toBe("ai_capex_growth");
  });

  it("normalises geopolitical label", () => {
    expect(normalizeForceId("Geopolitical tensions elevated")).toBe("geopolitical_risk_rising");
  });

  it("normalises yields falling label", () => {
    expect(normalizeForceId("Bond yields falling sharply")).toBe("yields_falling");
  });

  it("normalises futures risk-on label", () => {
    expect(normalizeForceId("Futures risk-on overnight")).toBe("futures_risk_on");
  });
});

// ── resolveForceThemeLabel ────────────────────────────────────────────────────

describe("resolveForceThemeLabel", () => {
  it("maps known theme IDs to human labels", () => {
    expect(resolveForceThemeLabel("data_centre_power")).toBe("Data Centres & Power");
    expect(resolveForceThemeLabel("semiconductors")).toBe("Semiconductors");
    expect(resolveForceThemeLabel("defence")).toBe("Defence");
  });

  it("falls back to title-cased ID for unknown themes", () => {
    const label = resolveForceThemeLabel("some_new_theme");
    expect(label).toBe("Some New Theme");
    expect(label).not.toContain("_");
  });
});

// ── Safety: no prohibited terms in force model output ─────────────────────────

describe("Force model safety", () => {
  it("no active force why_it_matters contains prohibited terms", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "gold_safe_haven_bid", "yields_rising"] });
    const { active } = buildCustomerForces(payload);
    for (const f of active) {
      expect(containsProhibitedTerm(f.why_it_matters)).toBe(false);
      expect(containsProhibitedTerm(f.market_impact)).toBe(false);
      expect(containsProhibitedTerm(f.risk_to_monitor)).toBe(false);
    }
  });

  it("no dormant force label contains prohibited terms", () => {
    const { dormant } = buildCustomerForces(makePayload());
    for (const f of dormant) {
      expect(containsProhibitedTerm(f.label)).toBe(false);
    }
  });
});
