// Tests for askDeciferModel — pure-function coverage.
// No DOM. No API calls. No React.

import { describe, it, expect } from "vitest";
import {
  buildSystemPrompt,
  extractUniverseSymbols,
  type AskNewsItem,
  type MacroEvent,
} from "./askDeciferModel";
import type { MarketNowPayload, TtgTheme } from "./customerApi";

// ── Fixtures ───────────────────────────────────────────────────────────────

function makePayload(overrides: Partial<MarketNowPayload> = {}): MarketNowPayload {
  return {
    market_regime_label: "BULL_TRENDING",
    market_mood: "Risk-on — broad participation",
    plain_english_summary: "Markets are rallying on AI momentum.",
    key_drivers: ["ai_compute_demand", "yields_falling"],
    active_themes: ["ai_infrastructure"],
    themes: [{ theme: "AI Infrastructure", state: "active", event_signal: "NVDA earnings beat" }],
    key_events: [
      {
        title: "NVDA beats estimates",
        summary_plain_english: "Revenue guidance raised 15%.",
        likely_positive_exposures: ["NVDA", "AMD"],
        likely_negative_exposures: [],
      },
    ],
    sectors: [{ name: "Technology", mood: "bullish", reasons: ["AI capex cycle"] }],
    radar: [{ symbol: "NVDA", reason_to_watch: "AI compute demand proxy" }],
    universe_snapshot: [
      { symbol: "NVDA", company_name: "NVIDIA", theme_id: "ai_infrastructure", why_connected: "Direct AI compute beneficiary", transmission: "tailwind" },
      { symbol: "AMD", company_name: "Advanced Micro Devices", theme_id: "ai_infrastructure", why_connected: "Competing GPU architecture", transmission: "tailwind" },
    ],
    risk_notes: ["Valuation stretched at current multiples"],
    what_to_watch: ["Fed speakers Thursday"],
    known_conflicts: ["Price drivers still bullish but bond market diverging"],
    ...overrides,
  };
}

function makeTtgTheme(overrides: Partial<TtgTheme> = {}): TtgTheme {
  return {
    theme_id: "ai_infrastructure",
    label: "AI Infrastructure",
    plain_english_description: "Data centres, power, and compute buildout for AI.",
    status: "active",
    driver_ids: ["ai_compute_demand"],
    driver_active: true,
    risk_note: "Power grid capacity constraints",
    ...overrides,
  };
}

const NO_NEWS: AskNewsItem[] = [];

// ── extractUniverseSymbols ─────────────────────────────────────────────────

describe("extractUniverseSymbols", () => {
  it("returns empty set when marketNow is null", () => {
    expect(extractUniverseSymbols(null).size).toBe(0);
  });

  it("includes universe_snapshot symbols", () => {
    const symbols = extractUniverseSymbols(makePayload());
    expect(symbols.has("NVDA")).toBe(true);
    expect(symbols.has("AMD")).toBe(true);
  });

  it("includes radar symbols", () => {
    const payload = makePayload({ universe_snapshot: [] });
    const symbols = extractUniverseSymbols(payload);
    expect(symbols.has("NVDA")).toBe(true);
  });

  it("deduplicates across universe and radar", () => {
    const symbols = extractUniverseSymbols(makePayload());
    // NVDA appears in both universe_snapshot and radar — Set deduplicates
    expect(symbols.has("NVDA")).toBe(true);
  });

  it("handles missing universe_snapshot and radar gracefully", () => {
    const symbols = extractUniverseSymbols(makePayload({ universe_snapshot: undefined, radar: undefined }));
    expect(symbols.size).toBe(0);
  });
});

// ── buildSystemPrompt — scope and guard instructions ──────────────────────

describe("buildSystemPrompt — scope guards", () => {
  it("contains the out-of-scope deflection instruction", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("That's outside what I cover");
  });

  it("explicitly forbids trade recommendations", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("trade recommendations");
  });

  it("explicitly forbids broker/account references", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("account information");
  });

  it("lists execution-layer prohibited terms", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("trade-ready");
    expect(prompt).toContain("entry candidate");
    expect(prompt).toContain("scanner");
  });

  it("scopes discussion to universe symbols only", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("NOT in the universe snapshot or radar");
  });
});

// ── buildSystemPrompt — market context ───────────────────────────────────

describe("buildSystemPrompt — market context", () => {
  it("includes regime label", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("BULL_TRENDING");
  });

  it("includes market mood", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("Risk-on — broad participation");
  });

  it("includes plain english summary", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("Markets are rallying on AI momentum.");
  });

  it("includes active drivers", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("ai_compute_demand");
    expect(prompt).toContain("yields_falling");
  });

  it("includes key events with exposures", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("NVDA beats estimates");
    expect(prompt).toContain("Revenue guidance raised 15%.");
    expect(prompt).toContain("Positive exposure: NVDA, AMD");
  });

  it("includes sectors with mood", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("Technology");
    expect(prompt).toContain("bullish");
  });

  it("includes radar symbols with reason", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("NVDA: AI compute demand proxy");
  });

  it("includes universe snapshot", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("NVDA (NVIDIA)");
    expect(prompt).toContain("Direct AI compute beneficiary");
    expect(prompt).toContain("[tailwind]");
  });

  it("includes known conflicts", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("Price drivers still bullish but bond market diverging");
  });

  it("includes risk notes", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("Valuation stretched at current multiples");
  });

  it("includes what_to_watch", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).toContain("Fed speakers Thursday");
  });

  it("falls back to active_themes string list when themes array absent", () => {
    const prompt = buildSystemPrompt(makePayload({ themes: [] }), [], NO_NEWS);
    expect(prompt).toContain("ai_infrastructure");
  });

  it("includes what_changed when present", () => {
    const prompt = buildSystemPrompt(
      makePayload({ what_changed: ["Oil spiked 3% on supply cut news"] }),
      [],
      NO_NEWS,
    );
    expect(prompt).toContain("Oil spiked 3% on supply cut news");
  });
});

// ── buildSystemPrompt — TTG themes ───────────────────────────────────────

describe("buildSystemPrompt — TTG themes", () => {
  it("includes active TTG theme label and description", () => {
    const prompt = buildSystemPrompt(makePayload(), [makeTtgTheme()], NO_NEWS);
    expect(prompt).toContain("AI Infrastructure");
    expect(prompt).toContain("Data centres, power, and compute buildout for AI.");
  });

  it("includes TTG theme risk note", () => {
    const prompt = buildSystemPrompt(makePayload(), [makeTtgTheme()], NO_NEWS);
    expect(prompt).toContain("Power grid capacity constraints");
  });

  it("separates active and dormant TTG themes", () => {
    const themes = [
      makeTtgTheme({ driver_active: true, label: "AI Infrastructure" }),
      makeTtgTheme({ driver_active: false, label: "Gold Safe Haven", theme_id: "gold_real_assets" }),
    ];
    const prompt = buildSystemPrompt(makePayload(), themes, NO_NEWS);
    expect(prompt).toContain("Theme Transmission Graph — Active");
    expect(prompt).toContain("Theme Transmission Graph — Structural");
    expect(prompt).toContain("Gold Safe Haven");
  });

  it("omits TTG section when no themes provided", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).not.toContain("Theme Transmission Graph");
  });
});

// ── buildSystemPrompt — news ──────────────────────────────────────────────

describe("buildSystemPrompt — universe news", () => {
  it("includes news items with symbol and age", () => {
    const news: AskNewsItem[] = [
      { title: "NVDA raises guidance again", symbol: "NVDA", minutesAgo: 45, summary: "CEO cites AI demand.", source: "reuters" },
    ];
    const prompt = buildSystemPrompt(makePayload(), [], news);
    expect(prompt).toContain("[NVDA] NVDA raises guidance again");
    expect(prompt).toContain("45m ago");
    expect(prompt).toContain("CEO cites AI demand.");
  });

  it("formats age in hours when > 60 minutes", () => {
    const news: AskNewsItem[] = [
      { title: "AMD launches MI400", symbol: "AMD", minutesAgo: 180, summary: "", source: "barrons" },
    ];
    const prompt = buildSystemPrompt(makePayload(), [], news);
    expect(prompt).toContain("3h ago");
  });

  it("omits news section when no items", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).not.toContain("Recent News");
  });

  it("includes news section header when items present", () => {
    const news: AskNewsItem[] = [
      { title: "NVDA reports", symbol: "NVDA", minutesAgo: 10, summary: "", source: "cnbc" },
    ];
    const prompt = buildSystemPrompt(makePayload(), [], news);
    expect(prompt).toContain("Recent News (universe symbols only)");
  });
});

// ── buildSystemPrompt — macro events ─────────────────────────────────────

function makeMacroEvent(overrides: Partial<MacroEvent> = {}): MacroEvent {
  return {
    event_id: "abc123",
    recorded_at: "2026-05-31T12:00:00Z",
    headline: "Iran turns Strait of Hormuz into toll booth — $2M per tanker",
    event_type: "infrastructure_disruption",
    event_summary: "Iran's IRGC is charging tankers to transit Hormuz. The strait was not closed — it was privatised.",
    direction_of_risk: "risk_off",
    drivers_implicated: ["oil_supply_shock", "geopolitical_risk_rising"],
    theme_impacts: [
      { theme: "Defence Rearmament", direction: "tailwind", confidence: 0.9, reasoning: "Geopolitical risk premium" },
      { theme: "Travel & Leisure", direction: "headwind", confidence: 0.8, reasoning: "Oil cost pass-through" },
      { theme: "Gold Real Assets", direction: "tailwind", confidence: 0.7, reasoning: "Safe haven bid" },
    ],
    affected_domains: ["oil", "defence", "supply_chain"],
    price_confirmation_signals: ["USO > +4% 5-day return", "ITA outperforms SPY by > 2%"],
    confidence: 0.88,
    ...overrides,
  };
}

describe("buildSystemPrompt — macro events", () => {
  it("includes macro events section when events provided", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("Macro Events");
  });

  it("includes event type formatted as plain English", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("infrastructure disruption");
  });

  it("includes event summary", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("strait was not closed");
  });

  it("includes risk direction", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("risk_off");
  });

  it("includes drivers implicated", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("oil_supply_shock");
    expect(prompt).toContain("geopolitical_risk_rising");
  });

  it("includes affected domains", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("oil");
    expect(prompt).toContain("defence");
  });

  it("includes high-confidence theme impacts only (>= 0.6)", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("Defence Rearmament (tailwind)");
    expect(prompt).toContain("Travel & Leisure (headwind)");
    expect(prompt).toContain("Gold Real Assets (tailwind)");
  });

  it("excludes low-confidence theme impacts (< 0.6)", () => {
    const event = makeMacroEvent({
      theme_impacts: [
        { theme: "Crypto Infrastructure", direction: "tailwind", confidence: 0.3, reasoning: "weak signal" },
      ],
    });
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [event]);
    expect(prompt).not.toContain("Crypto Infrastructure");
  });

  it("includes price confirmation signals", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("USO > +4% 5-day return");
  });

  it("omits macro events section when no events", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, []);
    expect(prompt).not.toContain("Macro Events");
  });

  it("omits macro events section when param not provided (backward compat)", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS);
    expect(prompt).not.toContain("Macro Events");
  });

  it("includes confidence percentage", () => {
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, [makeMacroEvent()]);
    expect(prompt).toContain("88% confidence");
  });

  it("handles multiple events", () => {
    const events = [
      makeMacroEvent({ event_type: "infrastructure_disruption", event_summary: "Hormuz toll." }),
      makeMacroEvent({ event_type: "central_bank_rate_decision", event_summary: "Fed holds rates.", drivers_implicated: ["yields_falling"] }),
    ];
    const prompt = buildSystemPrompt(makePayload(), [], NO_NEWS, events);
    expect(prompt).toContain("infrastructure disruption");
    expect(prompt).toContain("central bank rate decision");
    expect(prompt).toContain("Hormuz toll.");
    expect(prompt).toContain("Fed holds rates.");
  });
});

// ── buildSystemPrompt — null marketNow (degraded) ─────────────────────────

describe("buildSystemPrompt — null marketNow", () => {
  it("returns a prompt even with null marketNow", () => {
    const prompt = buildSystemPrompt(null, [], NO_NEWS);
    expect(prompt.length).toBeGreaterThan(100);
  });

  it("includes unavailability note when marketNow is null", () => {
    const prompt = buildSystemPrompt(null, [], NO_NEWS);
    expect(prompt).toContain("temporarily unavailable");
  });

  it("still includes scope and guard instructions when marketNow is null", () => {
    const prompt = buildSystemPrompt(null, [], NO_NEWS);
    expect(prompt).toContain("That's outside what I cover");
  });
});
