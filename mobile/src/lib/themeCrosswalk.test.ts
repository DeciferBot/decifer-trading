import { describe, it, expect } from "vitest";
import {
  getCrosswalkByMarketNow,
  getTtgIdForMarketNow,
  getMarketNowIdsByTtg,
  getAllCrosswalkEntries,
  UNMAPPED_MARKET_NOW_IDS,
  UNMAPPED_TTG_IDS,
} from "./themeCrosswalk";

const TTG_IDS = new Set([
  "ai_energy_nuclear",
  "glp1_metabolic_health",
  "defence_rearmament",
  "cybersecurity_digital_resilience",
  "reshoring_industrial_capex",
  "housing_rate_sensitivity",
  "water_infrastructure",
  "critical_minerals_copper",
  "gold_real_assets",
  "digital_assets_infrastructure",
]);

describe("themeCrosswalk", () => {
  it("all crosswalk entries have valid TTG primary IDs", () => {
    for (const entry of getAllCrosswalkEntries()) {
      expect(TTG_IDS.has(entry.ttgPrimary), `${entry.marketNowId} → unknown TTG ID: ${entry.ttgPrimary}`).toBe(true);
    }
  });

  it("all crosswalk entries have non-empty relationship descriptions", () => {
    for (const entry of getAllCrosswalkEntries()) {
      expect(entry.relationship.length).toBeGreaterThan(20);
    }
  });

  it("getCrosswalkByMarketNow returns null for unknown IDs", () => {
    expect(getCrosswalkByMarketNow("some_unknown_id")).toBeNull();
  });

  it("getCrosswalkByMarketNow returns entry for known IDs", () => {
    const entry = getCrosswalkByMarketNow("data_centre_power");
    expect(entry).not.toBeNull();
    expect(entry!.ttgPrimary).toBe("ai_energy_nuclear");
  });

  it("getTtgIdForMarketNow returns null for unknown IDs", () => {
    expect(getTtgIdForMarketNow("risk_on_rotation")).toBeNull();
    expect(getTtgIdForMarketNow("nonexistent")).toBeNull();
  });

  it("getTtgIdForMarketNow returns correct TTG ID for AI themes", () => {
    expect(getTtgIdForMarketNow("data_centre_power")).toBe("ai_energy_nuclear");
    expect(getTtgIdForMarketNow("ai_compute_demand")).toBe("ai_energy_nuclear");
    expect(getTtgIdForMarketNow("memory_storage")).toBe("ai_energy_nuclear");
    expect(getTtgIdForMarketNow("ai_compute_infrastructure")).toBe("ai_energy_nuclear");
    expect(getTtgIdForMarketNow("semiconductors")).toBe("ai_energy_nuclear");
  });

  it("getTtgIdForMarketNow returns correct TTG ID for defence themes", () => {
    expect(getTtgIdForMarketNow("defence")).toBe("defence_rearmament");
    expect(getTtgIdForMarketNow("defence_aerospace")).toBe("defence_rearmament");
  });

  it("getTtgIdForMarketNow returns correct TTG ID for cybersecurity", () => {
    expect(getTtgIdForMarketNow("cybersecurity")).toBe("cybersecurity_digital_resilience");
    expect(getTtgIdForMarketNow("software_cloud")).toBe("cybersecurity_digital_resilience");
  });

  it("getTtgIdForMarketNow returns correct TTG ID for gold themes", () => {
    expect(getTtgIdForMarketNow("gold_safe_haven_bid")).toBe("gold_real_assets");
    expect(getTtgIdForMarketNow("gold_precious_metals")).toBe("gold_real_assets");
  });

  it("getTtgIdForMarketNow returns correct TTG ID for healthcare themes", () => {
    expect(getTtgIdForMarketNow("biotech")).toBe("glp1_metabolic_health");
    expect(getTtgIdForMarketNow("biotech_risk_on")).toBe("glp1_metabolic_health");
    expect(getTtgIdForMarketNow("defensive_healthcare")).toBe("glp1_metabolic_health");
  });

  it("getTtgIdForMarketNow returns correct TTG ID for rate-sensitive themes", () => {
    expect(getTtgIdForMarketNow("reits")).toBe("housing_rate_sensitivity");
    expect(getTtgIdForMarketNow("yields_falling")).toBe("housing_rate_sensitivity");
  });

  it("getTtgIdForMarketNow returns correct TTG ID for other structural themes", () => {
    expect(getTtgIdForMarketNow("infrastructure_reshoring")).toBe("reshoring_industrial_capex");
    expect(getTtgIdForMarketNow("copper_electrification")).toBe("critical_minerals_copper");
  });

  it("getMarketNowIdsByTtg returns all market_now IDs for ai_energy_nuclear", () => {
    const entries = getMarketNowIdsByTtg("ai_energy_nuclear");
    const ids = entries.map(e => e.marketNowId);
    expect(ids).toContain("data_centre_power");
    expect(ids).toContain("ai_compute_demand");
    expect(ids).toContain("memory_storage");
    expect(ids).toContain("semiconductors");
    expect(entries.length).toBeGreaterThanOrEqual(4);
  });

  it("getMarketNowIdsByTtg returns entries for secondary mappings (semiconductors → reshoring)", () => {
    const reshoring = getMarketNowIdsByTtg("reshoring_industrial_capex");
    const ids = reshoring.map(e => e.marketNowId);
    expect(ids).toContain("infrastructure_reshoring");
    expect(ids).toContain("semiconductors"); // secondary
  });

  it("UNMAPPED_MARKET_NOW_IDS does not overlap with crosswalk entries", () => {
    const mappedIds = new Set(getAllCrosswalkEntries().map(e => e.marketNowId));
    for (const id of UNMAPPED_MARKET_NOW_IDS) {
      expect(mappedIds.has(id), `${id} is in both UNMAPPED and CROSSWALK`).toBe(false);
    }
  });

  it("UNMAPPED_TTG_IDS are truly absent from crosswalk primaries", () => {
    const ttgPrimaries = new Set(getAllCrosswalkEntries().map(e => e.ttgPrimary));
    for (const id of UNMAPPED_TTG_IDS) {
      expect(ttgPrimaries.has(id), `${id} is in UNMAPPED_TTG_IDS but also a crosswalk primary`).toBe(false);
    }
  });

  it("every TTG structural theme either has crosswalk entries or is in UNMAPPED_TTG_IDS", () => {
    const ttgPrimaries = new Set(getAllCrosswalkEntries().map(e => e.ttgPrimary));
    for (const ttgId of TTG_IDS) {
      const hasCrosswalk = ttgPrimaries.has(ttgId);
      const isUnmapped = UNMAPPED_TTG_IDS.has(ttgId);
      expect(hasCrosswalk || isUnmapped, `TTG ID "${ttgId}" is neither mapped nor in UNMAPPED_TTG_IDS`).toBe(true);
    }
  });

  it("no duplicate marketNowId entries in crosswalk", () => {
    const ids = getAllCrosswalkEntries().map(e => e.marketNowId);
    const unique = new Set(ids);
    expect(unique.size).toBe(ids.length);
  });
});
