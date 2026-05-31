export interface GraphNode {
  id: string;
  label: string;
  cluster: string;
  tier: number;
  subcluster: string;
  description: string;
  chain_note: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: "supply_chain_up" | "customer" | "competition" | "investment" | "ecosystem";
  strength: number;
  label: string;
  lag_weeks: number;
}

export interface Cluster {
  id: string;
  label: string;
  color: string;
}

export interface GraphData {
  clusters: Cluster[];
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface PriceData {
  price: number;
  change_pct: number;
  volume: number;
}

export interface EnrichedNode extends GraphNode {
  brightness: number; // 0-100
  price?: PriceData;
}

export const EDGE_COLORS: Record<GraphEdge["type"], string> = {
  supply_chain_up: "#6366f1",
  customer:        "#10b981",
  competition:     "#ef4444",
  investment:      "#a855f7",
  ecosystem:       "#f59e0b",
};

export const EDGE_LABELS: Record<GraphEdge["type"], string> = {
  supply_chain_up: "Supplies",
  customer:        "Customer",
  competition:     "Competes",
  investment:      "Invests in",
  ecosystem:       "Ecosystem",
};

export const SUBCLUSTER_LABELS: Record<string, string> = {
  compute:        "Compute",
  software:       "Software",
  foundry:        "Foundry & Equipment",
  memory:         "Memory",
  networking:     "Networking",
  systems:        "Systems",
  power:          "Power",
  infrastructure: "Infrastructure",
  photonics:      "Photonics",
  launch:         "Launch",
  earth_obs:      "Earth Observation",
  defence:        "Defence",
  comms:          "Communications",
  components:     "Components",
  materials:      "Materials",
};

export function computeBrightness(price?: PriceData): number {
  if (!price) return 20;
  const abs = Math.abs(price.change_pct);
  // 0% move = 20 brightness, 5%+ move = 100
  const fromPrice = Math.min(100, 20 + abs * 16);
  return Math.round(fromPrice);
}
