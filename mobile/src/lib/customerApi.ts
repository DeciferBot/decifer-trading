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

// M11C universe snapshot item — customer-safe projection of active opportunity universe
export interface UniverseItem {
  symbol: string;
  company_name?: string | null;
  theme_id: string;
  why_connected: string;
  transmission: "tailwind" | "headwind" | "none" | string;
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
  // M11C — customer-safe universe snapshot (theme-connected names)
  universe_snapshot?: UniverseItem[];
}

// ── Theme Transmission Graph (TTG) — M12A addendum ────────────────────────────
// Evidence-gated structural intelligence. Suppressed symbols (needs_review /
// proposed) never reach these endpoints — the evidence gate lives in Python.

export interface TtgTheme {
  theme_id: string;
  label: string;
  plain_english_description: string;
  status: string;           // "active" | "reference" | "proposed"
  driver_ids: string[];
  driver_active: boolean;
  risk_note: string | null;
}

export interface TtgSymbolCard {
  symbol: string;
  label: string;
  theme_id: string;
  theme_label: string;
  bucket_id: string;
  bucket_label: string;
  exposure_type: string;    // "direct_beneficiary" | "supply_chain_beneficiary" | etc.
  confidence: number | null;
  reason_to_care: string;
  reason_path: string[];
  evidence_basis_label: string;
  route_hint: string;       // "In focus" | "On the radar" | "ETF route" | "Monitor only"
  status: string;           // "active" | "monitor_only"
  risk_note: string | null;
  driver_active: boolean;
  theme_risk_note?: string | null;
}

export interface TtgThemeDetail extends TtgTheme {
  symbols: TtgSymbolCard[];
  symbol_count: number;
}

export async function fetchTtgThemes(): Promise<TtgTheme[]> {
  const base = getIntelligenceApiBase().replace(/\/$/, "");
  const res = await fetch(`${base}/api/intelligence/themes`);
  if (!res.ok) throw new Error(`/api/intelligence/themes → ${res.status}`);
  const data = await res.json();
  return data.theme_graph_themes ?? [];
}

export async function fetchTtgThemeDetail(themeId: string): Promise<TtgThemeDetail | null> {
  const base = getIntelligenceApiBase().replace(/\/$/, "");
  const res = await fetch(`${base}/api/intelligence/themes/${encodeURIComponent(themeId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`/api/intelligence/themes/${themeId} → ${res.status}`);
  const data = await res.json();
  const meta: TtgTheme | undefined = data.theme_graph_themes?.[0];
  if (!meta) return null;
  return { ...meta, symbols: data.symbols ?? [], symbol_count: data.symbol_count ?? 0 };
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
