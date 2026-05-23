const BASE_URL = process.env.NEXT_PUBLIC_BOT_API_URL ?? "http://localhost:8080";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

export const api = { get, post };

// ── Types matching bot_dashboard.py /api/state response ───────────────────

export interface Position {
  symbol: string;
  direction: "LONG" | "SHORT";
  entry: number;
  current: number;
  qty: number;
  trade_type?: string;
  score?: number;
  open_time?: string;
  sl?: number;
  tp?: number;
  // Rich fields present when metadata is intact
  entry_thesis?: string;
  reasoning?: string;
  conviction?: number;
  signal_scores?: Record<string, number>;
  entry_regime?: string;
  pnl?: number;
  setup_type?: string;
  instrument?: string;
  metadata_status?: string;
}

export interface Regime {
  regime: string;
  vix: number;
  vix_1h_change?: number;
  vix_change_1d?: number;
  spy_price?: number;
  qqq_price?: number;
  spy_chg_1d?: number;
  qqq_chg_1d?: number;
  iwm_chg_1d?: number;
  spy_above_200d?: boolean;
  session_character?: string;
  tape_context?: { prose?: string; description?: string };
  hmm_regime?: { regime: string; confidence: number };
  hurst_regime?: { regime: string; hurst: number };
}

/** Flat dash object returned by /api/state */
export interface BotState {
  portfolio_value: number;
  daily_pnl: number;
  session: string;
  scan_count: number;
  last_scan: string | null;
  paused: boolean;
  positions: Position[];
  last_decision: LastDecision | null;
  performance?: { total_pnl?: number };
  ibkr_disconnected?: boolean;
  regime?: Regime;
  equity_history?: Array<{ date: string; value: number }>;
}

export interface LastDecision {
  symbol: string;
  company_name?: string;
  direction?: string;
  thesis?: string;
  edge_why_now?: string;
  risk?: string;
  score?: number;
  price?: number;
  price_targets?: { target_pct: number; stop_pct: number; rr_ratio: number; target_price: number; stop_price: number };
  ts?: string;
  timestamp?: string;
  allocation_pct?: number;
}

export interface HealthReport {
  stages: Array<{
    name: string;
    status: "ok" | "warn" | "error" | "unknown";
    detail: string;
  }>;
  overall: "ok" | "warn" | "error";
  ibkr_connected: boolean;
  last_updated: string;
}

export interface PMDecision {
  symbol: string;
  action_type: string;
  final_status: string;
  thesis_status?: string;
  rationale?: string;
  ts?: string;
  score_delta?: number;
  unrealised_pnl_pct?: number;
  holding_period_hours?: number;
  entry_price?: number;
  current_price?: number;
}

export interface Analytics {
  sharpe?: number;
  win_rate?: number;
  total_pnl?: number;
  max_drawdown?: number;
  equity_curve?: Array<{ date: string; equity: number }>;
  daily_pnl?: Array<{ date: string; pnl: number }>;
}
