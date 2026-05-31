import { NextResponse } from "next/server";
import intelligenceGraph from "@/data/intelligence_graph.json";

export const revalidate = 60;

const INTELLIGENCE_API_URL = process.env.INTELLIGENCE_API_URL ?? "";

interface DriverState {
  active_drivers: string[];
  blocked_conditions: string[];
  evidence: Record<string, string | number>;
  generated_at?: string;
}

async function fetchLiveDriverState(): Promise<DriverState | null> {
  if (!INTELLIGENCE_API_URL) return null;
  try {
    const res = await fetch(`${INTELLIGENCE_API_URL}/api/market-now`, {
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return {
      active_drivers: data.active_themes ?? [],
      blocked_conditions: data.blocked_conditions ?? [],
      evidence: data.key_drivers ?? {},
      generated_at: data.generated_at,
    };
  } catch {
    return null;
  }
}

async function fetchActiveCandidates(): Promise<string[]> {
  if (!INTELLIGENCE_API_URL) return [];
  try {
    const res = await fetch(`${INTELLIGENCE_API_URL}/api/intelligence/universe`, {
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    const candidates: Array<{ symbol: string }> = data.theme_graph_universe ?? data.candidates ?? data.universe ?? [];
    return candidates.map((c) => c.symbol);
  } catch {
    return [];
  }
}

// Fallback active drivers — embedded in bundle, updated at deploy time
const FALLBACK_ACTIVE_DRIVERS = [
  "ai_capex_growth",
  "ai_compute_demand",
  "yields_falling",
  "oil_supply_shock",
  "geopolitical_risk_rising",
  "risk_on_rotation",
  "futures_risk_on",
];

export async function GET() {
  const [liveState, activeCandidates] = await Promise.all([
    fetchLiveDriverState(),
    fetchActiveCandidates(),
  ]);

  const activeDriverIds = liveState?.active_drivers ?? FALLBACK_ACTIVE_DRIVERS;
  const blockedConditions = liveState?.blocked_conditions ?? [];
  const evidence = liveState?.evidence ?? {};

  return NextResponse.json({
    nodes: intelligenceGraph.nodes,
    edges: intelligenceGraph.edges,
    active_driver_ids: activeDriverIds,
    blocked_condition_ids: blockedConditions,
    active_candidate_symbols: activeCandidates,
    evidence,
    live: !!liveState,
    generated_at: intelligenceGraph.generated_at,
  });
}
