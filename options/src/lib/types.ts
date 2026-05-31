export type SignalType = "SWEEP" | "CLUSTER" | "CROSS_EXPIRY";
export type Side = "CALL" | "PUT" | "MIXED";

export interface FlowEvent {
  ts: string;
  underlying: string;
  signal_type: SignalType;
  side: Side;
  contracts: number;
  strike: number | null;
  expiry: string | null;
  price: number | null;
  ask_at_print: number | null;
  is_sweep: boolean;
  sweep_count: number;
  cluster_count: number;
  expiry_count: number;
  driver_tags: string[];
  score: number;
}

export interface LeaderboardRow {
  underlying: string;
  call_sweep_count: number;
  put_sweep_count: number;
  cluster_count: number;
  cross_expiry_count: number;
  top_score: number;
  dominant_side: Side;
  driver_tags: string[];
  last_event_ts: string;
  total_contracts: number;
  // Scanner fields (populated by options_flow_scanner)
  flags?: string[];
  call_volume?: number;
  put_volume?: number;
  call_expansion?: number | null;
  put_expansion?: number | null;
  unusual_calls?: boolean;
  unusual_puts?: boolean;
  anomaly_score?: number;
}

export interface FeedResponse {
  status: string;
  ts: string;
  total: number;
  returned: number;
  events: FlowEvent[];
}

export interface LeaderboardResponse {
  status: string;
  ts: string;
  total: number;
  returned: number;
  leaderboard: LeaderboardRow[];
  source?: "live" | "friday_close";
  friday_close_ts?: string;
}

export interface SymbolResponse {
  status: string;
  ts: string;
  underlying: string;
  event_count: number;
  summary: LeaderboardRow | null;
  events: FlowEvent[];
}
