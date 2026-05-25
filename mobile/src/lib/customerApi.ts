// Customer-facing Intelligence API client.
// Reads exclusively from NEXT_PUBLIC_INTELLIGENCE_API_URL.
// Must NOT reference private bot routes, broker state, or execution data.

const DEFAULT_INTELLIGENCE_API_BASE = "https://intelligence.decifertrading.com";

// Use || not ?? so that an empty-string env var also falls back to the default.
function getIntelligenceApiBase(): string {
  const raw = process.env.NEXT_PUBLIC_INTELLIGENCE_API_URL?.trim();
  return raw || DEFAULT_INTELLIGENCE_API_BASE;
}

// M11A key event shape
export interface KeyEvent {
  event_id?: string;
  event_family?: string;
  event_type?: string;
  status?: string;
  title: string;
  summary_plain_english?: string;
  likely_positive_exposures?: string[];
  likely_negative_exposures?: string[];
  freshness_status?: string;
  materiality?: string;
}

// M11A sector shape
export interface SectorItem {
  name: string;
  mood?: string;
  reasons?: string[];
  from_events?: string[];
}

// M11A theme shape (differs from old active_themes: string[])
export interface ThemeItem {
  theme: string;
  state?: string;
  event_signal?: string;
  from_events?: string[];
}

// M11A radar shape
export interface RadarItem {
  symbol: string;
  reason_to_watch: string;
  theme_link?: string | null;
  confirmation_signal?: string;
  invalidation_signal?: string;
}

export interface FreshnessEntry {
  status: string;
  age_hours?: number | null;
}

// Unified payload type — covers both pre-M11A (old DO) and M11A shapes.
// All M11A fields are optional so the old DO payload degrades gracefully.
export interface MarketNowPayload {
  // Core fields (present in both old and M11A payloads)
  market_regime_label?: string;
  plain_english_summary?: string;
  key_drivers?: string[];
  active_themes?: string[];
  opportunity_explanations?: Array<{ theme: string; explanation: string }>;
  risk_notes?: string[];
  what_to_watch?: string[];
  freshness_timestamp?: string;
  confidence_label?: string;
  source_category_labels?: string[];
  data_entitlement_note?: string;
  // M11A Market Map sections (absent on old DO until M11A is deployed)
  market_mood?: string;
  what_changed?: string[];
  key_events?: KeyEvent[];
  sectors?: SectorItem[];
  themes?: ThemeItem[];
  radar?: RadarItem[];
  watch_next?: string[];
  known_conflicts?: string[];
  section_freshness?: Record<string, FreshnessEntry>;
  source_notes?: string[];
}

export async function fetchMarketNow(): Promise<MarketNowPayload> {
  const base = getIntelligenceApiBase().replace(/\/$/, "");
  // Plain fetch — no cache: "no-store" to avoid triggering CORS preflight.
  // cache: "no-store" adds Cache-Control + Pragma request headers which are not
  // CORS-safelisted. Response-side Cache-Control: no-store is set by the API.
  const res = await fetch(`${base}/api/market-now`);
  if (!res.ok) throw new Error(`/api/market-now → ${res.status}`);
  return res.json();
}
