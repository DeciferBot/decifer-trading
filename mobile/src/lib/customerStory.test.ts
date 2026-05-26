import { describe, it, expect } from "vitest";
import { buildCustomerStory, type CustomerStory } from "./customerStory";
import type { MarketNowPayload } from "./customerApi";

function makePayload(overrides: Partial<MarketNowPayload> = {}): MarketNowPayload {
  return {
    market_mood: undefined,
    plain_english_summary: undefined,
    key_drivers: [],
    themes: [],
    what_changed: [],
    watch_next: [],
    key_events: [],
    freshness_timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("buildCustomerStory", () => {
  it("returns a CustomerStory object with required fields", () => {
    const story = buildCustomerStory(makePayload());
    expect(story).toHaveProperty("headline");
    expect(story).toHaveProperty("summary");
    expect(story).toHaveProperty("market_state");
    expect(story).toHaveProperty("freshness_label");
    expect(story).toHaveProperty("evidence_mode");
    expect(story).toHaveProperty("primary_drivers");
    expect(story).toHaveProperty("mapped_structural");
  });

  it("resolves monitoring state when no drivers and no themes", () => {
    const story = buildCustomerStory(makePayload());
    expect(story.market_state).toBe("monitoring");
  });

  it("resolves risk-on when futures_risk_on driver is present", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: ["futures_risk_on"] }));
    expect(story.market_state).toBe("risk-on");
  });

  it("resolves risk-off when futures_risk_off driver is present", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: ["futures_risk_off"] }));
    expect(story.market_state).toBe("risk-off");
  });

  it("resolves risk-on from market_mood string containing 'risk-on'", () => {
    const story = buildCustomerStory(makePayload({ market_mood: "Risk-on — de-escalation headline" }));
    expect(story.market_state).toBe("risk-on");
  });

  it("resolves mixed when both risk-on and risk-off drivers are present", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: ["futures_risk_on", "futures_risk_off"] }));
    expect(story.market_state).toBe("mixed");
  });

  it("generates a real headline when drivers are present", () => {
    const story = buildCustomerStory(makePayload({
      key_drivers: ["ai_capex_growth", "geopolitical_risk_rising"],
      themes: [{ theme: "data_centre_power", state: "active" }],
    }));
    expect(story.headline.length).toBeGreaterThan(10);
    expect(story.headline).not.toBe("Structural intelligence is gathering signal — check back during market hours");
  });

  it("does not use the generic 'Assessing market conditions' phrase as a headline", () => {
    const story = buildCustomerStory(makePayload({
      market_mood: "Assessing market conditions",
      key_drivers: ["ai_capex_growth"],
      themes: [{ theme: "data_centre_power", state: "active" }],
    }));
    expect(story.headline.toLowerCase()).not.toContain("assessing market");
  });

  it("does not use the fallback phrase as a summary when drivers are present", () => {
    const story = buildCustomerStory(makePayload({
      plain_english_summary: "Assessing market conditions",
      key_drivers: ["ai_capex_growth"],
    }));
    expect(story.summary.toLowerCase()).not.toContain("assessing market");
  });

  it("uses a real API summary when it is not the fallback phrase", () => {
    const realSummary = "AI infrastructure spending is accelerating led by hyperscaler capex.";
    const story = buildCustomerStory(makePayload({ plain_english_summary: realSummary }));
    expect(story.summary).toBe(realSummary);
  });

  it("maps AI themes to primary_drivers with TTG linkage", () => {
    const story = buildCustomerStory(makePayload({
      key_drivers: ["ai_capex_growth"],
    }));
    const d = story.primary_drivers[0];
    expect(d.label).toBe("AI Capex Growth");
    expect(d.linked_ttg_id).toBe("ai_energy_nuclear");
    expect(d.linked_ttg_label).toBe("AI Energy & Nuclear");
  });

  it("maps geopolitical_risk_rising driver with TTG linkage", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: ["geopolitical_risk_rising"] }));
    const d = story.primary_drivers[0];
    expect(d.label).toBe("Geopolitical Risk Rising");
    expect(d.linked_ttg_id).toBe("defence_rearmament");
  });

  it("builds mapped_structural from themes with crosswalk entries", () => {
    const story = buildCustomerStory(makePayload({
      themes: [
        { theme: "data_centre_power", state: "active" },
        { theme: "defence", state: "active" },
        { theme: "risk_on_rotation", state: "active" }, // no crosswalk
      ],
    }));
    const ttgIds = story.mapped_structural.map(m => m.ttgId);
    expect(ttgIds).toContain("ai_energy_nuclear");
    expect(ttgIds).toContain("defence_rearmament");
    // risk_on_rotation has no crosswalk entry
    expect(ttgIds).not.toContain(undefined);
    expect(story.mapped_structural.length).toBe(2);
  });

  it("deduplicates mapped_structural when multiple market_now themes map to the same TTG theme", () => {
    const story = buildCustomerStory(makePayload({
      themes: [
        { theme: "data_centre_power", state: "active" },
        { theme: "ai_compute_demand", state: "active" }, // also maps to ai_energy_nuclear
      ],
    }));
    const ttgIds = story.mapped_structural.map(m => m.ttgId);
    const uniqueTtg = [...new Set(ttgIds)];
    // Both themes map to ai_energy_nuclear, but each has its own marketNowId — both should appear
    expect(ttgIds.filter(id => id === "ai_energy_nuclear").length).toBe(2);
    expect(uniqueTtg.length).toBe(1);
  });

  it("correctly counts active, building, weakening, dormant themes", () => {
    const story = buildCustomerStory(makePayload({
      themes: [
        { theme: "data_centre_power", state: "active" },
        { theme: "defence", state: "active" },
        { theme: "cybersecurity", state: "strengthening" },
        { theme: "reits", state: "weakening" },
        { theme: "gold_safe_haven_bid", state: "dormant" },
      ],
    }));
    expect(story.active_theme_count).toBe(2);
    expect(story.building_theme_count).toBe(1);
    expect(story.weakening_theme_count).toBe(1);
    expect(story.dormant_theme_count).toBe(1);
  });

  it("sets has_live_events true when key_events are present", () => {
    const story = buildCustomerStory(makePayload({
      key_events: [{ title: "FOMC meeting" }],
    }));
    expect(story.has_live_events).toBe(true);
  });

  it("sets has_live_events false when no key_events", () => {
    const story = buildCustomerStory(makePayload({ key_events: [] }));
    expect(story.has_live_events).toBe(false);
  });

  it("sets evidence_mode to 'live' when key_events are present", () => {
    const story = buildCustomerStory(makePayload({ key_events: [{ title: "Test" }] }));
    expect(story.evidence_mode).toBe("live");
  });

  it("sets evidence_mode to 'live' when what_changed is present", () => {
    const story = buildCustomerStory(makePayload({ what_changed: ["Fed statement changed tone"] }));
    expect(story.evidence_mode).toBe("live");
  });

  it("sets evidence_mode to 'structural' when neither events nor what_changed", () => {
    const story = buildCustomerStory(makePayload());
    expect(story.evidence_mode).toBe("structural");
  });

  it("freshness_label shows 'Just updated' for very recent timestamps", () => {
    const story = buildCustomerStory(makePayload({
      freshness_timestamp: new Date().toISOString(),
    }));
    expect(story.freshness_label).toBe("Just updated");
  });

  it("freshness_label shows 'Freshness unknown' when timestamp is missing", () => {
    const story = buildCustomerStory(makePayload({ freshness_timestamp: undefined }));
    expect(story.freshness_label).toBe("Freshness unknown");
  });

  it("primary_drivers is capped at 5", () => {
    const story = buildCustomerStory(makePayload({
      key_drivers: ["ai_capex_growth", "geopolitical_risk_rising", "yields_falling", "gold_safe_haven_bid", "risk_on_rotation", "futures_risk_on"],
    }));
    expect(story.primary_drivers.length).toBeLessThanOrEqual(5);
  });

  it("contains no buy/sell/hold/order language in headline or summary", () => {
    const story = buildCustomerStory(makePayload({
      key_drivers: ["ai_capex_growth"],
      themes: [{ theme: "data_centre_power", state: "active" }],
    }));
    const text = `${story.headline} ${story.summary}`.toLowerCase();
    expect(text).not.toContain("buy");
    expect(text).not.toContain("sell");
    expect(text).not.toContain("hold");
    expect(text).not.toContain("order");
    expect(text).not.toContain("target");
    expect(text).not.toContain("stop loss");
    expect(text).not.toContain("position size");
  });

  it("contains no broker/execution language in headline or summary", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: ["futures_risk_on"] }));
    const text = `${story.headline} ${story.summary}`.toLowerCase();
    expect(text).not.toContain("ibkr");
    expect(text).not.toContain("alpaca");
    expect(text).not.toContain("broker");
    expect(text).not.toContain("execute");
    expect(text).not.toContain("trade execution");
  });

  it("monitoring fallback headline does not contain operator language", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: [], themes: [] }));
    expect(story.market_state).toBe("monitoring");
    expect(story.headline.toLowerCase()).not.toContain("pipeline");
    expect(story.headline.toLowerCase()).not.toContain("gathering signal");
    expect(story.headline.toLowerCase()).not.toContain("intelligence");
  });

  it("monitoring fallback summary does not contain pipeline language", () => {
    const story = buildCustomerStory(makePayload({ key_drivers: [], themes: [] }));
    expect(story.market_state).toBe("monitoring");
    expect(story.summary.toLowerCase()).not.toContain("pipeline");
    expect(story.summary.toLowerCase()).not.toContain("activated");
  });

  it("monitoring fallback with themes does not use 'under observation' wording", () => {
    const story = buildCustomerStory(makePayload({
      themes: [{ theme: "gold_safe_haven_bid", state: "dormant" }],
    }));
    expect(story.headline.toLowerCase()).not.toContain("under observation");
    expect(story.headline.toLowerCase()).not.toContain("pipeline");
  });
});
