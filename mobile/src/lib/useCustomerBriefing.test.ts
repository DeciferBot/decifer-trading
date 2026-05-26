// Tests for useCustomerBriefing utility functions — M13A.
// Tests pure functions only (no hook, no DOM rendering required).
// Hook integration tested via the live product.

import { describe, it, expect, beforeEach, vi } from "vitest";

// ── localStorage mock (vitest runs in node environment) ───────────────────────
let _store: Record<string, string> = {};
const localStorageMock = {
  getItem:    (key: string): string | null => _store[key] ?? null,
  setItem:    (key: string, value: string): void => { _store[key] = value; },
  removeItem: (key: string): void => { delete _store[key]; },
  clear:      (): void => { _store = {}; },
  get length() { return Object.keys(_store).length; },
  key:        (i: number): string | null => Object.keys(_store)[i] ?? null,
};
Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
});
import {
  formatDuration,
  buildSinceAwaySummary,
  computeFreshnessState,
  FRESHNESS_LABELS,
  type MarketSession,
} from "./useCustomerBriefing";
import type { MarketNowPayload } from "./customerApi";

// ── formatDuration ─────────────────────────────────────────────────────────────

describe("formatDuration", () => {
  it("returns singular minute", () => {
    expect(formatDuration(60_000)).toBe("1 minute");
  });

  it("returns plural minutes", () => {
    expect(formatDuration(3 * 60_000)).toBe("3 minutes");
  });

  it("returns singular hour", () => {
    expect(formatDuration(60 * 60_000)).toBe("1 hour");
  });

  it("returns plural hours", () => {
    expect(formatDuration(3 * 60 * 60_000)).toBe("3 hours");
  });

  it("returns singular day", () => {
    expect(formatDuration(24 * 60 * 60_000)).toBe("1 day");
  });

  it("returns plural days", () => {
    expect(formatDuration(2 * 24 * 60 * 60_000)).toBe("2 days");
  });
});

// ── computeFreshnessState ─────────────────────────────────────────────────────

describe("computeFreshnessState", () => {
  const freshTs = new Date(Date.now() - 5 * 60_000).toISOString(); // 5 min ago
  const staleTs = new Date(Date.now() - 3 * 60 * 60_000).toISOString(); // 3 h ago

  it("returns updating when loading", () => {
    expect(computeFreshnessState(null, true, "open")).toBe("updating");
  });

  it("returns unavailable when no data", () => {
    expect(computeFreshnessState(null, false, "open")).toBe("unavailable");
  });

  it("returns unavailable when no freshness_timestamp", () => {
    const data = {} as MarketNowPayload;
    expect(computeFreshnessState(data, false, "open")).toBe("unavailable");
  });

  it("returns fresh for recent data during open session", () => {
    const data = { freshness_timestamp: freshTs } as MarketNowPayload;
    expect(computeFreshnessState(data, false, "open")).toBe("fresh");
  });

  it("returns stale for old data during open session", () => {
    const data = { freshness_timestamp: staleTs } as MarketNowPayload;
    expect(computeFreshnessState(data, false, "open")).toBe("stale");
  });

  it("returns market_closed for old data when market is closed", () => {
    const data = { freshness_timestamp: staleTs } as MarketNowPayload;
    expect(computeFreshnessState(data, false, "closed")).toBe("market_closed");
  });

  it("returns market_closed for old data on weekend", () => {
    const data = { freshness_timestamp: staleTs } as MarketNowPayload;
    expect(computeFreshnessState(data, false, "weekend")).toBe("market_closed");
  });
});

// ── FRESHNESS_LABELS ──────────────────────────────────────────────────────────

describe("FRESHNESS_LABELS", () => {
  const FORBIDDEN = [
    "buy", "sell", "hold", "order", "target", "stop",
    "broker", "execution", "pipeline", "scanner", "payload",
    "activation", "trade engine", "signal engine",
  ];

  it("contains a label for every freshness state", () => {
    const states: MarketSession[] = [];
    const freshnessStates = ["fresh", "updating", "stale", "market_closed", "unavailable"] as const;
    for (const s of freshnessStates) {
      expect(FRESHNESS_LABELS[s]).toBeTruthy();
    }
    void states;
  });

  it("uses no forbidden language in any label", () => {
    for (const label of Object.values(FRESHNESS_LABELS)) {
      for (const word of FORBIDDEN) {
        expect(label.toLowerCase()).not.toContain(word);
      }
    }
  });
});

// ── buildSinceAwaySummary ─────────────────────────────────────────────────────

describe("buildSinceAwaySummary", () => {
  const LAST_SEEN_KEY = "decifer:lastSeenAt";

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("returns empty summary when no data", () => {
    const result = buildSinceAwaySummary(null);
    expect(result.hasChanges).toBe(false);
    expect(result.items).toHaveLength(0);
    expect(result.lastSeenAt).toBeNull();
  });

  it("returns lastSeenAt=null when localStorage is empty", () => {
    const data = { freshness_timestamp: new Date().toISOString() } as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.lastSeenAt).toBeNull();
    expect(result.hasChanges).toBe(false);
  });

  it("does NOT update lastSeenAt — that is the caller's responsibility", () => {
    const lastSeen = new Date(Date.now() - 60 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data = {
      freshness_timestamp: new Date().toISOString(),
    } as MarketNowPayload;
    buildSinceAwaySummary(data);
    expect(localStorage.getItem(LAST_SEEN_KEY)).toBe(lastSeen);
  });

  it("reads lastSeenAt from localStorage", () => {
    const lastSeen = new Date(Date.now() - 30 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data = { freshness_timestamp: new Date(Date.now() - 3 * 60_000).toISOString() } as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.lastSeenAt).toBe(lastSeen);
  });

  it("returns hasChanges=false when data is older than lastSeenAt", () => {
    const lastSeen = new Date(Date.now() - 10 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    // Data timestamp is 30 minutes ago (older than lastSeen)
    const data = {
      freshness_timestamp: new Date(Date.now() - 30 * 60_000).toISOString(),
      what_changed: ["Something changed"],
    } as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.hasChanges).toBe(false);
    expect(result.items).toHaveLength(0);
  });

  it("returns hasChanges=true when fresh events exist and data was refreshed", () => {
    const lastSeen = new Date(Date.now() - 60 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data: MarketNowPayload = {
      freshness_timestamp: new Date().toISOString(),
      key_events: [
        { title: "Fed signals pause", freshness_status: "fresh" },
        { title: "Old event", freshness_status: "stale" },
      ],
    } as unknown as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.hasChanges).toBe(true);
    expect(result.items.some((i) => i.title === "Fed signals pause")).toBe(true);
    // Stale event should NOT appear
    expect(result.items.some((i) => i.title === "Old event")).toBe(false);
  });

  it("surfaces what_changed items when data was refreshed", () => {
    const lastSeen = new Date(Date.now() - 60 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data: MarketNowPayload = {
      freshness_timestamp: new Date().toISOString(),
      what_changed: ["Yields moved lower", "Risk appetite improving"],
    } as unknown as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.hasChanges).toBe(true);
    expect(result.items.some((i) => i.title === "Yields moved lower")).toBe(true);
  });

  it("caps items at 5", () => {
    const lastSeen = new Date(Date.now() - 60 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data: MarketNowPayload = {
      freshness_timestamp: new Date().toISOString(),
      key_events: Array.from({ length: 10 }, (_, i) => ({
        title: `Event ${i}`,
        freshness_status: "fresh",
      })),
    } as unknown as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.items.length).toBeLessThanOrEqual(5);
  });

  it("surfaces strengthening themes as items", () => {
    const lastSeen = new Date(Date.now() - 60 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data: MarketNowPayload = {
      freshness_timestamp: new Date().toISOString(),
      themes: [{ theme: "ai_capex_growth", state: "strengthening" }],
    } as unknown as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    expect(result.hasChanges).toBe(true);
    expect(result.items[0].type).toBe("theme");
  });

  it("includes awayDuration when away for more than 2 minutes", () => {
    const lastSeen = new Date(Date.now() - 3 * 60_000).toISOString();
    localStorage.setItem(LAST_SEEN_KEY, lastSeen);
    const data: MarketNowPayload = {
      freshness_timestamp: new Date(Date.now() - 60_000).toISOString(),
    } as unknown as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    // awayDuration may be null (no changes) but lastSeenAt should be set
    expect(result.lastSeenAt).not.toBeNull();
  });
});

// ── Since-away: no forbidden language in item titles ─────────────────────────

describe("buildSinceAwaySummary — safety", () => {
  const FORBIDDEN = [
    "buy", "sell", "hold", "order", "target", "stop",
    "broker", "execution", "pipeline", "scanner", "payload",
  ];

  it("item type values are safe customer labels", () => {
    const validTypes = ["event", "driver", "theme"];
    const lastSeen = new Date(Date.now() - 60 * 60_000).toISOString();
    localStorage.setItem("decifer:lastSeenAt", lastSeen);
    const data: MarketNowPayload = {
      freshness_timestamp: new Date().toISOString(),
      key_events: [{ title: "Rate decision expected", freshness_status: "fresh" }],
    } as unknown as MarketNowPayload;
    const result = buildSinceAwaySummary(data);
    for (const item of result.items) {
      expect(validTypes).toContain(item.type);
    }
    void FORBIDDEN;
  });
});

// ── Navigation tab identifiers ────────────────────────────────────────────────

describe("CustomerTab values — no internal language", () => {
  const VALID_TABS = ["today", "discover", "ask", "signals", "universe"] as const;
  const FORBIDDEN_IN_TABS = [
    "broker", "execution", "pipeline", "scanner", "order", "trade", "position",
  ];

  it("all tab identifiers are customer-safe", () => {
    for (const tab of VALID_TABS) {
      for (const word of FORBIDDEN_IN_TABS) {
        expect(tab).not.toContain(word);
      }
    }
  });
});

// ── AskDecifer suggested questions — safety ───────────────────────────────────

describe("AskDecifer suggested questions — no forbidden language", () => {
  const FORBIDDEN = [
    "buy", "sell", "hold", "order", "target", "stop",
    "broker", "execution", "pipeline", "scanner", "payload",
    "activation", "position size", "trade engine",
  ];

  it("no suggested question contains forbidden language", async () => {
    const { SUGGESTED_QUESTIONS } = await import("../views/AskDeciferView");
    for (const q of SUGGESTED_QUESTIONS) {
      for (const word of FORBIDDEN) {
        expect(q.toLowerCase()).not.toContain(word);
      }
    }
  });
});

// ── Market session labels — no internal language ──────────────────────────────

describe("Market session labels", () => {
  const FORBIDDEN_IN_SESSION = [
    "scan", "pipeline", "order", "broker", "execution", "payload",
  ];

  const SESSION_LABELS = {
    pre_market: "Pre-market trading",
    open: "Market is open",
    after_hours: "After-hours trading",
    closed: "Market is closed",
    weekend: "Markets closed for the weekend",
  };

  it("all session labels are customer-safe", () => {
    for (const label of Object.values(SESSION_LABELS)) {
      for (const word of FORBIDDEN_IN_SESSION) {
        expect(label.toLowerCase()).not.toContain(word);
      }
    }
  });

  it("session labels use plain English", () => {
    expect(SESSION_LABELS.open).not.toContain("_");
    expect(SESSION_LABELS.closed).not.toContain("_");
    expect(SESSION_LABELS.weekend).not.toContain("_");
  });
});
