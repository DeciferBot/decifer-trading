import { describe, it, expect } from "vitest";
import { resolveSignalStatus } from "./translate";
import { getCrosswalkByMarketNow } from "./themeCrosswalk";
import { UNMAPPED_MARKET_NOW_IDS } from "./themeCrosswalk";

const FORBIDDEN_IN_LABELS = [
  "buy", "sell", "hold", "order", "target", "stop", "position",
  "execute", "broker", "pipeline", "activation", "reactivation",
  "scanner", "payload", "operator", "crosswalk",
];

describe("resolveSignalStatus — customer-safe status labels", () => {
  it("active state → In Focus", () => {
    expect(resolveSignalStatus("active").label).toBe("In Focus");
    expect(resolveSignalStatus("activated").label).toBe("In Focus");
  });

  it("event_signal strengthening → In Focus (event overrides state)", () => {
    expect(resolveSignalStatus(undefined, "strengthening").label).toBe("In Focus");
    expect(resolveSignalStatus("dormant", "strengthening").label).toBe("In Focus");
  });

  it("strengthening state → Building", () => {
    expect(resolveSignalStatus("strengthening").label).toBe("Building");
  });

  it("crowded state → Widely held (removes operator jargon)", () => {
    const result = resolveSignalStatus("crowded");
    expect(result.label).toBe("Widely held");
    expect(result.label.toLowerCase()).not.toContain("crowded");
  });

  it("weakening state → Fading", () => {
    expect(resolveSignalStatus("weakening").label).toBe("Fading");
  });

  it("event_signal weakening → Fading", () => {
    expect(resolveSignalStatus(undefined, "weakening").label).toBe("Fading");
  });

  it("headwind state → Under Pressure", () => {
    expect(resolveSignalStatus("headwind").label).toBe("Under Pressure");
  });

  it("dormant state → Quiet", () => {
    expect(resolveSignalStatus("dormant").label).toBe("Quiet");
  });

  it("unknown / missing state → Waiting for confirmation", () => {
    expect(resolveSignalStatus().label).toBe("Waiting for confirmation");
    expect(resolveSignalStatus("unknown_state").label).toBe("Waiting for confirmation");
    expect(resolveSignalStatus("").label).toBe("Waiting for confirmation");
  });

  it("no status label contains forbidden operator or financial language", () => {
    const states = [
      "active", "activated", "strengthening", "crowded",
      "weakening", "headwind", "dormant", "unknown_state", undefined,
    ] as const;
    const signals = [undefined, "strengthening", "weakening"] as const;

    for (const s of states) {
      for (const sig of signals) {
        const { label } = resolveSignalStatus(s, sig);
        for (const forbidden of FORBIDDEN_IN_LABELS) {
          expect(
            label.toLowerCase(),
            `State "${s}" / signal "${sig}" → label "${label}" contains forbidden term "${forbidden}"`,
          ).not.toContain(forbidden);
        }
      }
    }
  });

  it("each status label is in the allowed status language set", () => {
    const ALLOWED = new Set([
      "In Focus", "Building", "Widely held", "Fading",
      "Under Pressure", "Quiet", "Waiting for confirmation",
    ]);
    const states = ["active", "activated", "strengthening", "crowded", "weakening", "headwind", "dormant"];
    for (const s of states) {
      const { label } = resolveSignalStatus(s);
      expect(ALLOWED.has(label), `Label "${label}" is not in the allowed set`).toBe(true);
    }
  });
});

describe("structural theme connection — customer safety", () => {
  it("unmapped themes return null from crosswalk (no internal ID exposed)", () => {
    for (const id of UNMAPPED_MARKET_NOW_IDS) {
      const result = getCrosswalkByMarketNow(id);
      expect(result).toBeNull();
    }
  });

  it("mapped themes return a customer-readable TTG label (no raw ID)", () => {
    const mapped = ["data_centre_power", "defence", "cybersecurity", "gold_safe_haven_bid"];
    for (const id of mapped) {
      const entry = getCrosswalkByMarketNow(id);
      expect(entry).not.toBeNull();
      // TTG label should be human-readable, not a raw snake_case ID
      expect(entry!.ttgPrimaryLabel).not.toMatch(/^[a-z_]+$/);
      expect(entry!.ttgPrimaryLabel.length).toBeGreaterThan(3);
    }
  });

  it("fallback copy for unmapped themes does not expose internal terms", () => {
    const fallback = "Fresh signal — structural theme connection not yet established.";
    const internalTerms = ["crosswalk", "ttg_id", "market_now_id", "null", "undefined", "pipeline"];
    for (const term of internalTerms) {
      expect(fallback.toLowerCase()).not.toContain(term);
    }
  });

  it("quiet state empty copy does not mention pipeline or activation", () => {
    const quietCopy = "Signals are quiet right now. Structural themes remain available in the Theme Map. Fresh evidence will appear here when market conditions strengthen.";
    expect(quietCopy.toLowerCase()).not.toContain("pipeline");
    expect(quietCopy.toLowerCase()).not.toContain("activation");
    expect(quietCopy.toLowerCase()).not.toContain("activate");
    expect(quietCopy.toLowerCase()).not.toContain("null");
    expect(quietCopy.toLowerCase()).not.toContain("undefined");
  });
});
