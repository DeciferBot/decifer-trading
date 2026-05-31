export type NodeType = "driver" | "theme" | "symbol";

export interface IntelligenceNode {
  id: string;
  label: string;
  type: NodeType;
  tier: number;
  description?: string;
  status?: string;
  // symbol-specific
  confidence?: number;
  exposure_type?: string;
  driver_ids?: string[];
  theme_ids?: string[];
  risk_note?: string | null;
  reason_to_care?: string;
  // runtime enrichment
  isActive?: boolean;       // driver: currently firing; theme: has active driver; symbol: active driver + high conf
  isHot?: boolean;          // symbol: in active_opportunity_universe
  isBlocked?: boolean;      // driver: in blocked_conditions
  x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null;
}

export interface IntelligenceEdge {
  source: string;
  target: string;
  type: "activates" | "exposes";
  strength?: number;
  driver_id?: string;
  exposure_type?: string;
  bucket_id?: string;
}

export interface IntelligenceGraphData {
  nodes: IntelligenceNode[];
  edges: IntelligenceEdge[];
  active_driver_ids: string[];
  blocked_condition_ids: string[];
  active_candidate_symbols: string[];
  evidence: Record<string, string | number>;
  live: boolean;
  generated_at: string;
}

// Colour palette per node type / state
export const DRIVER_COLOR_ACTIVE  = "#f59e0b"; // amber
export const DRIVER_COLOR_INACTIVE = "#374151"; // dim grey
export const DRIVER_COLOR_BLOCKED  = "#ef4444"; // red (blocked/conflicted)
export const THEME_COLORS: Record<string, string> = {
  ai_energy_nuclear:           "#6366f1",
  glp1_metabolic_health:       "#ec4899",
  defence_rearmament:          "#f59e0b",
  cybersecurity:               "#10b981",
  reshoring_industrial_capex:  "#84cc16",
  housing_rate_sensitivity:    "#06b6d4",
  water_infrastructure:        "#3b82f6",
  critical_minerals_copper:    "#d97706",
  gold_real_assets:            "#fbbf24",
  digital_assets_infrastructure:"#a855f7",
};
export const THEME_COLOR_DEFAULT = "#6366f1";

export const SYMBOL_COLOR_HOT    = "#10b981"; // emerald — in universe + active driver
export const SYMBOL_COLOR_ACTIVE = "#818cf8"; // indigo — active driver but not in universe
export const SYMBOL_COLOR_IDLE   = "#334155"; // slate — no active driver
