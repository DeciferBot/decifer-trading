"use client";
// Shared customer briefing hook — M13A.
// Centralises data fetching, clock state, freshness, since-away, story synthesis.
// Used by CustomerApp; individual views receive derived state as props.

import { useState, useCallback, useEffect, useRef } from "react";
import { fetchMarketNow, fetchTtgThemes, getIntelligenceApiBase, type MarketNowPayload, type TtgTheme } from "@/lib/customerApi";
import { buildCustomerStory, type CustomerStory } from "@/lib/customerStory";
import { buildMarketCauseCards, type MarketCauseCard } from "@/lib/marketCauseStory";
import {
  buildCustomerForces,
  buildConnectionTree,
  type CustomerMarketForce,
  type CustomerConnectionNode,
  type MacroEventContext,
} from "@/lib/customerBriefingModel";

async function fetchMacroContext(): Promise<MacroEventContext | null> {
  try {
    const base = getIntelligenceApiBase();
    const res = await fetch(`${base}/api/intelligence/macro-context`, { cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// ── Market clock ──────────────────────────────────────────────────────────────

export type MarketSession =
  | "pre_market"
  | "open"
  | "after_hours"
  | "closed"
  | "weekend";

export interface MarketClockState {
  localTime: string;
  newYorkTime: string;
  session: MarketSession;
  sessionLabel: string;
  greeting: string;
}

export function computeMarketClock(): MarketClockState {
  const now = new Date();

  const localTime = now.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });

  const newYorkTime = now.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });

  const nyDayOfWeek = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "long",
  }).format(now);

  const nyParts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  })
    .formatToParts(now)
    .reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {});

  const nyH = parseInt(nyParts.hour ?? "0", 10);
  const nyM = parseInt(nyParts.minute ?? "0", 10);
  const nyMin = nyH * 60 + nyM;
  const isWeekend = nyDayOfWeek === "Saturday" || nyDayOfWeek === "Sunday";

  let session: MarketSession;
  if (isWeekend) {
    session = "weekend";
  } else if (nyMin >= 4 * 60 && nyMin < 9 * 60 + 30) {
    session = "pre_market";
  } else if (nyMin >= 9 * 60 + 30 && nyMin < 16 * 60) {
    session = "open";
  } else if (nyMin >= 16 * 60 && nyMin < 20 * 60) {
    session = "after_hours";
  } else {
    session = "closed";
  }

  const SESSION_LABELS: Record<MarketSession, string> = {
    pre_market: "Pre-market trading",
    open: "Market is open",
    after_hours: "After-hours trading",
    closed: "Market is closed",
    weekend: "Markets closed for the weekend",
  };

  const h = now.getHours();
  const greeting = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";

  return { localTime, newYorkTime, session, sessionLabel: SESSION_LABELS[session], greeting };
}

// ── Freshness state ───────────────────────────────────────────────────────────

export type FreshnessState =
  | "fresh"
  | "updating"
  | "stale"
  | "market_closed"
  | "unavailable";

export const FRESHNESS_LABELS: Record<FreshnessState, string> = {
  fresh: "Fresh",
  updating: "Updating...",
  stale: "Stale — refresh view",
  market_closed: "Last session",
  unavailable: "Unavailable",
};

export function computeFreshnessState(
  data: MarketNowPayload | null,
  loading: boolean,
  session: MarketSession,
): FreshnessState {
  if (loading) return "updating";
  if (!data) return "unavailable";
  const ts = data.freshness_timestamp;
  if (!ts) return "unavailable";
  const ageMin = (Date.now() - new Date(ts).getTime()) / 60_000;
  if (ageMin > 120) {
    return session === "closed" || session === "weekend" ? "market_closed" : "stale";
  }
  return "fresh";
}

// ── Since you were away ───────────────────────────────────────────────────────

export interface SinceAwayItem {
  type: "event" | "driver" | "theme";
  title: string;
  detail?: string;
}

export interface SinceAwaySummary {
  hasChanges: boolean;
  items: SinceAwayItem[];
  lastSeenAt: string | null;
  awayDuration: string | null;
}

const LAST_SEEN_KEY = "decifer:lastSeenAt";

export function formatDuration(ms: number): string {
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins} minute${mins !== 1 ? "s" : ""}`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs !== 1 ? "s" : ""}`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days !== 1 ? "s" : ""}`;
}

export function buildSinceAwaySummary(
  data: MarketNowPayload | null,
): SinceAwaySummary {
  if (typeof localStorage === "undefined") {
    return { hasChanges: false, items: [], lastSeenAt: null, awayDuration: null };
  }

  const rawLastSeen = localStorage.getItem(LAST_SEEN_KEY);
  const lastSeenAt = rawLastSeen ?? null;

  if (!data || !lastSeenAt) {
    return { hasChanges: false, items: [], lastSeenAt, awayDuration: null };
  }

  const lastSeenMs = new Date(lastSeenAt).getTime();
  const dataTs = data.freshness_timestamp
    ? new Date(data.freshness_timestamp).getTime()
    : 0;
  const awayMs = Date.now() - lastSeenMs;
  const awayDuration = awayMs > 2 * 60_000 ? formatDuration(awayMs) : null;

  // Only surface changes if data was refreshed after the user was last here
  if (dataTs <= lastSeenMs) {
    return { hasChanges: false, items: [], lastSeenAt, awayDuration };
  }

  const items: SinceAwayItem[] = [];

  // Fresh key events
  const freshEvents = (data.key_events ?? [])
    .filter((e) => e.freshness_status === "fresh")
    .slice(0, 3);
  for (const ev of freshEvents) {
    items.push({ type: "event", title: ev.title, detail: ev.summary_plain_english });
  }

  // What-changed items (no individual timestamps — show up to 3)
  for (const wc of (data.what_changed ?? []).slice(0, 3)) {
    if (!items.some((i) => i.title === wc)) {
      items.push({ type: "driver", title: wc });
    }
  }

  // Themes building momentum
  const buildingThemes = (data.themes ?? [])
    .filter((t) => t.event_signal === "strengthening" || t.state === "strengthening")
    .slice(0, 2);
  for (const t of buildingThemes) {
    if (!items.some((i) => i.title.toLowerCase().includes(t.theme.toLowerCase()))) {
      items.push({
        type: "theme",
        title: `${t.theme.replace(/_/g, " ")} theme is building momentum`,
        detail: t.from_events?.[0],
      });
    }
  }

  return {
    hasChanges: items.length > 0,
    items: items.slice(0, 5),
    lastSeenAt,
    awayDuration,
  };
}

export function updateLastSeenAt(): void {
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(LAST_SEEN_KEY, new Date().toISOString());
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export interface CustomerBriefingState {
  data: MarketNowPayload | null;
  loading: boolean;
  error: string | null;
  isRefreshing: boolean;
  story: CustomerStory | null;
  causeCards: MarketCauseCard[];
  clock: MarketClockState;
  freshnessState: FreshnessState;
  freshnessLabel: string;
  sinceAway: SinceAwaySummary;
  ttgThemes: TtgTheme[];
  activeForces: CustomerMarketForce[];
  watchingForces: CustomerMarketForce[];
  dormantForces: CustomerMarketForce[];
  connectionTree: CustomerConnectionNode[];
  refresh: () => Promise<void>;
}

export function useCustomerBriefing(): CustomerBriefingState {
  const [data, setData] = useState<MarketNowPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [clock, setClock] = useState<MarketClockState>(computeMarketClock);
  const [ttgThemes, setTtgThemes] = useState<TtgTheme[]>([]);
  const [macroContext, setMacroContext] = useState<MacroEventContext | null>(null);
  const sinceAwayRef = useRef<SinceAwaySummary>({
    hasChanges: false,
    items: [],
    lastSeenAt: null,
    awayDuration: null,
  });
  const sinceAwayComputedRef = useRef(false);

  // Update clock every minute
  useEffect(() => {
    const t = setInterval(() => setClock(computeMarketClock()), 60_000);
    return () => clearInterval(t);
  }, []);

  const load = useCallback(async (manual: boolean) => {
    if (manual) setIsRefreshing(true);
    try {
      const payload = await fetchMarketNow();
      // Compute since-away ONCE per session, before updating lastSeenAt
      if (!sinceAwayComputedRef.current) {
        sinceAwayRef.current = buildSinceAwaySummary(payload);
        sinceAwayComputedRef.current = true;
        updateLastSeenAt();
      }
      setData(payload);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load market intelligence.");
    } finally {
      setLoading(false);
      if (manual) setIsRefreshing(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    load(false);
    const t = setInterval(() => load(false), 5 * 60_000);
    return () => clearInterval(t);
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // TTG themes fetch — separate from the main data interval; once on mount
  useEffect(() => {
    fetchTtgThemes()
      .then(setTtgThemes)
      .catch(() => setTtgThemes([]));
    fetchMacroContext()
      .then(setMacroContext)
      .catch(() => setMacroContext(null));
  }, []);

  const refresh = useCallback((): Promise<void> => load(true), [load]);

  const story = data ? buildCustomerStory(data) : null;
  const causeCards = data ? buildMarketCauseCards(data) : [];
  const freshnessState = computeFreshnessState(data, loading, clock.session);
  const freshnessLabel = FRESHNESS_LABELS[freshnessState];

  const forcesResult = data ? buildCustomerForces(data, macroContext) : { active: [], watching: [], dormant: [] };
  const connectionTree = data ? buildConnectionTree(data, ttgThemes) : [];

  /* eslint-disable react-hooks/refs */
  return {
    data,
    loading,
    error,
    isRefreshing,
    story,
    causeCards,
    clock,
    freshnessState,
    freshnessLabel,
    sinceAway: sinceAwayRef.current,
    ttgThemes,
    activeForces: forcesResult.active,
    watchingForces: forcesResult.watching,
    dormantForces: forcesResult.dormant,
    connectionTree,
    refresh,
  };
  /* eslint-enable react-hooks/refs */
}
