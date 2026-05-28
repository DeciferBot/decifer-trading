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

// ── buildNarrativeParagraph ───────────────────────────────────────────────────

import {
  buildNarrativeParagraph,
  buildWhereLooking,
  buildWhatCouldChange,
  type TapeSnapshot,
} from "./customerBriefingModel";

function makeTape(overrides: Partial<TapeSnapshot> = {}): TapeSnapshot {
  return {
    spy_pct:   null,
    qqq_pct:   null,
    dia_pct:   null,
    iwm_pct:   null,
    tlt_pct:   null,
    gld_pct:   null,
    uso_pct:   null,
    dxy_pct:   null,
    vix_level: null,
    ...overrides,
  };
}

describe("buildNarrativeParagraph", () => {
  it("returns a non-empty string in all cases", () => {
    const ms = buildCustomerMarketStory(makePayload(), makeStory());
    const para = buildNarrativeParagraph(makePayload(), ms);
    expect(typeof para).toBe("string");
    expect(para.length).toBeGreaterThan(20);
  });

  it("uses clean plain_english_summary from API when available", () => {
    const payload = makePayload({ plain_english_summary: "Markets are gaining as AI spending accelerates across data centres and power names." });
    const ms = buildCustomerMarketStory(payload, makeStory());
    const para = buildNarrativeParagraph(payload, ms);
    expect(para).toContain("AI spending");
  });

  it("rejects API summary containing prohibited terms", () => {
    const payload = makePayload({
      plain_english_summary: "Entry candidate signals are trade-ready today.",
      key_drivers: ["ai_capex_growth"],
    });
    const ms = buildCustomerMarketStory(payload, makeStory());
    const para = buildNarrativeParagraph(payload, ms);
    expect(para).not.toMatch(/trade-ready/i);
    expect(para).not.toMatch(/entry candidate/i);
  });

  it("rejects API summary containing fallback phrases and synthesises instead", () => {
    const payload = makePayload({
      plain_english_summary: "Markets are being monitored for emerging themes.",
      key_drivers: ["ai_capex_growth"],
    });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms);
    // Should NOT echo the fallback phrase verbatim
    expect(para).not.toContain("markets are being monitored");
  });

  it("graceful degradation — no drivers, no tape → returns regime opener", () => {
    const ms = buildCustomerMarketStory(makePayload(), makeStory({ market_state: "monitoring" }));
    const para = buildNarrativeParagraph(makePayload(), ms);
    expect(para.length).toBeGreaterThan(10);
    expect(containsProhibitedTerm(para)).toBe(false);
  });

  it("integrates tape context — SPY up and low VIX", () => {
    const tape = makeTape({ spy_pct: 0.8, vix_level: 13 });
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    expect(para).toMatch(/S&P 500/i);
    expect(para).toMatch(/\+0\.8%/);
    expect(para).toMatch(/contained/i);
  });

  it("integrates tape context — SPY down and elevated VIX", () => {
    const tape = makeTape({ spy_pct: -1.2, vix_level: 28 });
    const payload = makePayload({ key_drivers: ["futures_risk_off"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-off" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    expect(para).toMatch(/-1\.2%/);
    expect(para).toMatch(/VIX/i);
  });

  it("AI cluster produces a unified sector-focused sentence (not two separate AI sentences)", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "ai_compute_demand"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms);
    const lc = para.toLowerCase();
    // When macro_label already names AI infrastructure, the paragraph must pivot to concrete
    // sector copy rather than repeating the same concept. Both variants cover data centres.
    expect(
      lc.includes("data centre") || lc.includes("semiconductor") || lc.includes("buildout"),
    ).toBe(true);
    // Must not say "AI infrastructure" twice in adjacent sections
    const aiInfraCount = (lc.match(/ai infrastructure/g) ?? []).length;
    expect(aiInfraCount).toBeLessThanOrEqual(1);
  });

  it("mixed signals produce a balanced sentence mentioning support and counterweight", () => {
    const payload = makePayload({ key_drivers: ["risk_on_rotation", "yields_rising"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "mixed" }));
    const para = buildNarrativeParagraph(payload, ms);
    const lc = para.toLowerCase();
    // Should reference both sides
    expect(lc.includes("support") || lc.includes("rotation") || lc.includes("growth")).toBe(true);
    expect(lc.includes("counterweight") || lc.includes("yields") || lc.includes("headwind")).toBe(true);
  });

  it("output contains no raw driver IDs (underscored machine IDs)", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "gold_safe_haven_bid"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms);
    // Should not contain ID-style strings like "ai_capex_growth"
    expect(para).not.toMatch(/[a-z]+_[a-z]+_[a-z]+/);
  });

  it("output never contains prohibited terms", () => {
    const drivers = ["ai_capex_growth", "geopolitical_risk_rising", "futures_risk_on", "yields_rising", "gold_safe_haven_bid"];
    for (const d of drivers) {
      const payload = makePayload({ key_drivers: [d] });
      const ms = buildCustomerMarketStory(payload, makeStory());
      const para = buildNarrativeParagraph(payload, ms);
      expect(containsProhibitedTerm(para)).toBe(false);
    }
  });

  it("ends with a full stop", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms);
    expect(para.trimEnd()).toMatch(/\.$/);
  });
});

// ── buildWhereLooking ─────────────────────────────────────────────────────────

describe("buildWhereLooking", () => {
  it("returns empty when no active drivers", () => {
    const result = buildWhereLooking(makePayload());
    expect(result.empty).toBe(true);
    expect(result.stories).toHaveLength(0);
    expect(result.names).toHaveLength(0);
  });

  it("returns story labels for active drivers", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const { stories, empty } = buildWhereLooking(payload);
    expect(empty).toBe(false);
    expect(stories.length).toBeGreaterThan(0);
  });

  it("deduplicates story labels when two drivers share a theme", () => {
    // ai_capex_growth and ai_compute_demand both connect to data_centre_power
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "ai_compute_demand"] });
    const { stories } = buildWhereLooking(payload);
    expect(new Set(stories).size).toBe(stories.length);
  });

  it("no story label is a raw underscore ID", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising"] });
    const { stories } = buildWhereLooking(payload);
    for (const s of stories) {
      expect(s).not.toMatch(/[a-z]+_[a-z]+/);
    }
  });

  it("matches radar names to active themes", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth"],
      radar: [
        { symbol: "NVDA", reason_to_watch: "AI compute leader", theme_link: "semiconductors" },
        { symbol: "MSFT", reason_to_watch: "Cloud AI growth", theme_link: "ai_compute_infrastructure" },
        { symbol: "UNRELATED", reason_to_watch: "Unrelated stock", theme_link: "unrelated_theme" },
      ],
    });
    const { names } = buildWhereLooking(payload);
    const syms = names.map(n => n.symbol);
    expect(syms).toContain("NVDA");
    expect(syms).toContain("MSFT");
    expect(syms).not.toContain("UNRELATED");
  });

  it("falls back to universe_snapshot when radar has no matches", () => {
    const payload = makePayload({
      key_drivers: ["gold_safe_haven_bid"],
      radar: [],
      universe_snapshot: [
        { symbol: "GLD", company_name: "SPDR Gold Shares", theme_id: "gold_safe_haven_bid", why_connected: "Primary gold ETF", transmission: "tailwind" },
      ],
    });
    const { names } = buildWhereLooking(payload);
    expect(names.map(n => n.symbol)).toContain("GLD");
  });

  it("limits names to 5 even with many matches", () => {
    const radarItems = Array.from({ length: 10 }, (_, i) => ({
      symbol: `SYM${i}`,
      reason_to_watch: "AI infrastructure name",
      theme_link: "semiconductors",
    }));
    const payload = makePayload({ key_drivers: ["ai_capex_growth"], radar: radarItems });
    const { names } = buildWhereLooking(payload);
    expect(names.length).toBeLessThanOrEqual(5);
  });

  it("no name reason contains prohibited terms", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth"],
      radar: [{ symbol: "NVDA", reason_to_watch: "Market intelligence context only.", theme_link: "semiconductors" }],
    });
    const { names } = buildWhereLooking(payload);
    for (const n of names) {
      expect(containsProhibitedTerm(n.reason)).toBe(false);
    }
  });

  it("limits stories to 5", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "gold_safe_haven_bid", "yields_falling", "risk_on_rotation"] });
    const { stories } = buildWhereLooking(payload);
    expect(stories.length).toBeLessThanOrEqual(5);
  });
});

// ── buildWhatCouldChange ──────────────────────────────────────────────────────

describe("buildWhatCouldChange", () => {
  it("returns an array", () => {
    const result = buildWhatCouldChange(makePayload());
    expect(Array.isArray(result)).toBe(true);
  });

  it("returns generic risks when no active drivers", () => {
    const result = buildWhatCouldChange(makePayload());
    expect(result.length).toBeGreaterThan(0);
    expect(result[0].length).toBeGreaterThan(10);
  });

  it("includes driver-specific risk for active driver", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const result = buildWhatCouldChange(payload);
    expect(result.some(r => r.toLowerCase().includes("capex") || r.toLowerCase().includes("hyperscaler"))).toBe(true);
  });

  it("appends known_conflict as risk item", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth"],
      known_conflicts: ["Price action is positive but bond markets are not confirming."],
    });
    const result = buildWhatCouldChange(payload);
    expect(result.some(r => r.includes("Price action is positive"))).toBe(true);
  });

  it("returns at most 3 items", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "yields_rising"],
      known_conflicts: ["An extra conflict."],
    });
    const result = buildWhatCouldChange(payload);
    expect(result.length).toBeLessThanOrEqual(3);
  });

  it("no item contains prohibited terms", () => {
    const drivers = ["ai_capex_growth", "geopolitical_risk_rising", "yields_rising", "gold_safe_haven_bid"];
    const payload = makePayload({ key_drivers: drivers });
    const result = buildWhatCouldChange(payload);
    for (const r of result) {
      expect(containsProhibitedTerm(r)).toBe(false);
    }
  });

  it("no item is a raw underscore ID", () => {
    const payload = makePayload({ key_drivers: ["futures_risk_on", "credit_stress_easing"] });
    const result = buildWhatCouldChange(payload);
    for (const r of result) {
      expect(r).not.toMatch(/^[a-z]+_[a-z]+/);
    }
  });
});

// ── Sprint M14B: Multi-asset tape narrative tests ─────────────────────────────

describe("buildNarrativeParagraph — M14B tape scenarios", () => {
  it("QQQ outperforming SPY by 0.5%+ creates tech-led narrative", () => {
    const tape = makeTape({ spy_pct: 0.5, qqq_pct: 1.2, iwm_pct: 0.3 });
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    // Should mention tech concentration, not just "gaining ground"
    expect(lc.includes("technology") || lc.includes("nasdaq") || lc.includes("tech")).toBe(true);
    expect(lc.includes("s&p 500")).toBe(true);
  });

  it("QQQ up but IWM negative creates narrow rally narrative", () => {
    const tape = makeTape({ spy_pct: 0.6, qqq_pct: 0.9, iwm_pct: -0.3 });
    const payload = makePayload({ key_drivers: [] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    expect(lc.includes("small cap") || lc.includes("lagging") || lc.includes("narrow")).toBe(true);
    expect(lc.includes("nasdaq") || lc.includes("technology") || lc.includes("growth")).toBe(true);
  });

  it("SPY down, TLT firm creates defensive narrative", () => {
    const tape = makeTape({ spy_pct: -0.7, qqq_pct: -0.6, tlt_pct: 0.5 });
    const payload = makePayload({ key_drivers: [] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-off" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    expect(lc.includes("bond") || lc.includes("safety") || lc.includes("defensive")).toBe(true);
  });

  it("SPY down, GLD firm creates defensive narrative via gold", () => {
    const tape = makeTape({ spy_pct: -0.5, tlt_pct: 0.1, gld_pct: 0.6 });
    const payload = makePayload({ key_drivers: [] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-off" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    expect(lc.includes("gold") || lc.includes("safety") || lc.includes("defensive")).toBe(true);
  });

  it("DXY strength appears in breadth context (non-breadth-in-opener scenario)", () => {
    const tape = makeTape({ spy_pct: 0.2, qqq_pct: 0.3, dxy_pct: 0.6 });
    const payload = makePayload({ key_drivers: [] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "monitoring" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    expect(lc.includes("dollar") || lc.includes("dxy") || lc.includes("uup") || lc.includes("currency")).toBe(true);
  });

  it("missing IWM and DXY (null) does not crash", () => {
    const tape = makeTape({ spy_pct: 0.5, qqq_pct: 0.7, iwm_pct: null, dxy_pct: null });
    const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    expect(() => buildNarrativeParagraph(payload, ms, tape)).not.toThrow();
    const para = buildNarrativeParagraph(payload, ms, tape);
    expect(para.length).toBeGreaterThan(20);
  });

  it("all-null tape snapshot does not crash", () => {
    const tape = makeTape();
    const ms = buildCustomerMarketStory(makePayload(), makeStory());
    expect(() => buildNarrativeParagraph(makePayload(), ms, tape)).not.toThrow();
  });

  it("broad risk-on (SPY + QQQ both up, low VIX) produces positive framing", () => {
    const tape = makeTape({ spy_pct: 0.8, qqq_pct: 0.9, iwm_pct: 0.6, vix_level: 13 });
    const payload = makePayload({ key_drivers: [] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    expect(lc.includes("broadly") || lc.includes("advancing") || lc.includes("positive")).toBe(true);
  });

  it("quiet tape (small moves) produces restrained framing", () => {
    const tape = makeTape({ spy_pct: 0.05, qqq_pct: 0.08, vix_level: 16 });
    const payload = makePayload({ key_drivers: [] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "monitoring" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    const lc = para.toLowerCase();
    expect(lc.includes("quiet") || lc.includes("little changed") || lc.includes("no dominant")).toBe(true);
  });
});

// ── Sprint M14B: AI Infrastructure deduplication tests ───────────────────────

describe("buildNarrativeParagraph — AI Infrastructure deduplication", () => {
  it("when macro_label mentions AI infrastructure, middle sentence pivots to sectors", () => {
    const payload = makePayload({
      key_drivers: ["ai_capex_growth"],
      plain_english_summary: undefined,
    });
    const story = makeStory({ market_state: "risk-on" });
    // Force a macro label that names AI infrastructure
    const ms = buildCustomerMarketStory(payload, story);
    // macro_label for ai_capex_growth = "AI infrastructure buildout is the primary driver"
    const para = buildNarrativeParagraph(payload, ms);
    // The word "infrastructure" should appear at most once in the result
    const matches = (para.toLowerCase().match(/ai infrastructure/g) ?? []).length;
    expect(matches).toBeLessThanOrEqual(1);
  });

  it("AI cluster with macro label mentioning AI uses alternative sector copy", () => {
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "ai_compute_demand"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms);
    // Should mention sectors concretely
    const lc = para.toLowerCase();
    expect(lc.includes("data centre") || lc.includes("semiconductor") || lc.includes("infrastructure")).toBe(true);
    // Should not just repeat "AI infrastructure" twice
    const aiInfraCount = (lc.match(/ai infrastructure/g) ?? []).length;
    expect(aiInfraCount).toBeLessThanOrEqual(1);
  });

  it("non-AI driver does not trigger AI alternative copy", () => {
    const payload = makePayload({ key_drivers: ["geopolitical_risk_rising"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "mixed" }));
    const para = buildNarrativeParagraph(payload, ms);
    expect(para.toLowerCase()).not.toContain("data centres, semiconductors");
    expect(para.toLowerCase()).toContain("geopolit");
  });

  it("no prohibited terms in any AI-driver narrative output", () => {
    const aiDrivers = ["ai_capex_growth", "ai_compute_demand"];
    const payload = makePayload({ key_drivers: aiDrivers });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
    const para = buildNarrativeParagraph(payload, ms);
    expect(containsProhibitedTerm(para)).toBe(false);
  });
});

// ── Sprint M14B: Safety constraints ──────────────────────────────────────────

describe("M14B safety — no broker/execution language in narrative", () => {
  const BROKER_TERMS = [
    "order", "broker", "account", "portfolio", "p&l", "pnl", "profit",
    "loss", "execute", "execution", "trade entry", "position entry",
    "buy", "sell", "long", "short",
  ];

  it("no broker or execution language appears in any tape scenario narrative", () => {
    const scenarios: TapeSnapshot[] = [
      makeTape({ spy_pct: 0.8, qqq_pct: 1.2, iwm_pct: -0.3 }), // narrow_rally
      makeTape({ spy_pct: 0.4, qqq_pct: 1.0, iwm_pct: 0.3 }),  // tech_led
      makeTape({ spy_pct: 0.6, qqq_pct: 0.7, vix_level: 12 }),  // broad_risk_on
      makeTape({ spy_pct: -0.6, tlt_pct: 0.5 }),                 // defensive
      makeTape({ spy_pct: -0.9, vix_level: 26 }),                // broad_risk_off
      makeTape({ spy_pct: 0.05, qqq_pct: 0.08 }),                // quiet
    ];
    for (const tape of scenarios) {
      const payload = makePayload({ key_drivers: ["ai_capex_growth"] });
      const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "risk-on" }));
      const para = buildNarrativeParagraph(payload, ms, tape);
      const lc = para.toLowerCase();
      for (const term of BROKER_TERMS) {
        // Allow "long-duration" which is a bond market term, not a trade direction
        if (term === "long") {
          expect(lc.includes("long ") && !lc.includes("long-duration")).toBe(false);
        } else {
          expect(lc.includes(term)).toBe(false);
        }
      }
    }
  });

  it("no prohibited rendered terms in any tape scenario narrative", () => {
    const tape = makeTape({ spy_pct: 0.5, qqq_pct: 0.9, iwm_pct: -0.2, dxy_pct: 0.5 });
    const payload = makePayload({ key_drivers: ["ai_capex_growth", "geopolitical_risk_rising"] });
    const ms = buildCustomerMarketStory(payload, makeStory({ market_state: "mixed" }));
    const para = buildNarrativeParagraph(payload, ms, tape);
    expect(containsProhibitedTerm(para)).toBe(false);
  });
});

// ── Sprint M14B: CustomerBottomNav label safety ───────────────────────────────

import { NAV_ITEMS } from "../components/CustomerBottomNav";

describe("CustomerBottomNav — M14B", () => {
  it("NAV_ITEMS contains a Today label", () => {
    const todayItem = NAV_ITEMS.find(n => n.id === "today");
    expect(todayItem).toBeDefined();
    expect(todayItem?.label).toBe("Today");
  });

  it("all nav labels are non-empty strings", () => {
    for (const item of NAV_ITEMS) {
      expect(typeof item.label).toBe("string");
      expect(item.label.length).toBeGreaterThan(0);
    }
  });

  it("no nav label contains prohibited terms", () => {
    for (const item of NAV_ITEMS) {
      expect(containsProhibitedTerm(item.label)).toBe(false);
    }
  });

  it("has exactly 5 nav items", () => {
    expect(NAV_ITEMS).toHaveLength(5);
  });

  it("Ask is the center item", () => {
    const center = NAV_ITEMS.find(n => n.center);
    expect(center?.id).toBe("ask");
  });
});
