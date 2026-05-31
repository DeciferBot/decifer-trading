"use client";

import { useEffect, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import { Activity, Brain, Map, X, TrendingUp, TrendingDown, Minus, Link2 } from "lucide-react";
import type { GraphData, EnrichedNode, GraphEdge } from "@/lib/types";
import { computeBrightness, EDGE_COLORS, EDGE_LABELS } from "@/lib/types";
import type { IntelligenceNode, IntelligenceEdge, IntelligenceGraphData } from "@/lib/intelligence-types";
import BrainPanel from "@/components/BrainPanel";

const MarketGraph       = dynamic(() => import("@/components/MarketGraph"),       { ssr: false });
const IntelligenceGraph = dynamic(() => import("@/components/IntelligenceGraph"), { ssr: false });

type MapMode = "ai" | "space" | "brain";

const MAP_OPTIONS: { key: MapMode; label: string; icon?: string; desc: string }[] = [
  { key: "ai",    label: "AI Ecosystem",    desc: "51 companies across compute, software, power and infrastructure" },
  { key: "space", label: "Space & Defence", desc: "18 companies across launch, earth observation, defence and comms" },
  { key: "brain", label: "Intelligence",    desc: "Live driver → theme → symbol intelligence graph" },
];

export default function MapPage() {
  const [mapMode, setMapMode]   = useState<MapMode>("ai");

  // ── Market data ──────────────────────────────────────────────────────────
  const [graphData, setGraphData]   = useState<GraphData | null>(null);
  const [enriched, setEnriched]     = useState<EnrichedNode[]>([]);
  const [selected, setSelected]     = useState<EnrichedNode | null>(null);

  // ── Brain data ────────────────────────────────────────────────────────────
  const [brainData, setBrainData]         = useState<IntelligenceGraphData | null>(null);
  const [brainSelected, setBrainSelected] = useState<IntelligenceNode | null>(null);

  // ── Load graph + prices ───────────────────────────────────────────────────
  useEffect(() => { fetch("/api/graph").then(r => r.json()).then(setGraphData); }, []);

  const loadPrices = useCallback(async () => {
    if (!graphData) return;
    try {
      const { prices } = await fetch("/api/prices").then(r => r.json());
      setEnriched(graphData.nodes.map(n => ({
        ...n, price: prices[n.id], brightness: computeBrightness(prices[n.id]),
      })));
    } catch {
      setEnriched(graphData.nodes.map(n => ({ ...n, brightness: 20 })));
    }
  }, [graphData]);

  useEffect(() => {
    if (!graphData) return;
    loadPrices();
    const t = setInterval(loadPrices, 120_000);
    return () => clearInterval(t);
  }, [graphData, loadPrices]);

  // ── Load brain ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (mapMode === "brain" && !brainData) {
      fetch("/api/intelligence").then(r => r.ok ? r.json() : null).then(d => d && setBrainData(d));
    }
  }, [mapMode, brainData]);

  // ── Derived: filtered nodes for current market map ────────────────────────
  const clusterFilter = mapMode === "brain" ? null : mapMode;

  // Default: tier-0 only. When a node is selected: that node + all directly connected nodes.
  const visibleNodes: EnrichedNode[] = (() => {
    const byCluster = clusterFilter
      ? enriched.filter(n => n.cluster === clusterFilter)
      : enriched;
    if (!selected) return byCluster.filter(n => n.tier === 0);
    const connectedIds = new Set<string>();
    connectedIds.add(selected.id);
    enriched.forEach(n => {
      const linked = (graphData?.edges ?? []).some(
        e => (e.source === selected.id && e.target === n.id) ||
             (e.target === selected.id && e.source === n.id)
      );
      if (linked) connectedIds.add(n.id);
    });
    return byCluster.filter(n => connectedIds.has(n.id));
  })();

  const visibleEdges: GraphEdge[] = (graphData?.edges ?? []).filter(e => {
    const ids = new Set(visibleNodes.map(n => n.id));
    return ids.has(e.source as string) && ids.has(e.target as string);
  });

  // Clear selected when map mode changes
  useEffect(() => { setSelected(null); setBrainSelected(null); }, [mapMode]);

  const handleBrainNavigate = useCallback((id: string) => {
    const node = brainData?.nodes.find(n => n.id === id);
    if (node) setBrainSelected(node as IntelligenceNode);
  }, [brainData]);

  const activeDriverIds = new Set(brainData?.active_driver_ids ?? []);
  const blockedIds      = new Set(brainData?.blocked_condition_ids ?? []);
  const hotSymbols      = new Set(brainData?.active_candidate_symbols ?? []);

  // Price change info for selected node
  const pct   = selected?.price?.change_pct ?? 0;
  const price = selected?.price?.price;

  // Connections for selected node (full graph edges, not just visible)
  const connections = selected
    ? (graphData?.edges ?? [])
        .filter(e => e.source === selected.id || e.target === selected.id)
        .map(e => {
          const otherId = e.source === selected.id ? e.target : e.source;
          const other = enriched.find(n => n.id === otherId);
          return { edge: e, other, isOutgoing: e.source === selected.id };
        })
        .filter(c => c.other)
        .sort((a, b) => b.edge.strength - a.edge.strength)
    : [];

  return (
    <div className="h-screen w-screen overflow-hidden flex" style={{ background: "#080d1a", color: "#fff" }}>

      {/* ── Main graph area ─────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Header */}
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-white/8 flex-shrink-0">
          <Activity size={15} className="text-indigo-400 flex-shrink-0" />
          <span className="font-semibold text-white tracking-tight text-sm">Decifer</span>
          <div className="flex-1" />
          {mapMode !== "brain" && selected && (
            <span className="text-xs text-gray-500">
              Showing {visibleNodes.length} connected — click background to reset
            </span>
          )}
          {mapMode !== "brain" && !selected && (
            <span className="text-xs text-gray-600">
              Showing tier-0 anchors · click any node to drill in
            </span>
          )}
          <div className="flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-xs text-emerald-400">Live</span>
          </div>
        </header>

        {/* Graph */}
        <div className="flex-1 relative overflow-hidden">
          {mapMode !== "brain" ? (
            enriched.length > 0 ? (
              <MarketGraph
                nodes={visibleNodes}
                edges={visibleEdges}
                clusters={graphData?.clusters ?? []}
                onSelect={setSelected}
                selected={selected}
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-8 h-8 border-2 border-indigo-400/50 border-t-indigo-400 rounded-full animate-spin" />
              </div>
            )
          ) : (
            brainData ? (
              <IntelligenceGraph
                nodes={brainData.nodes as IntelligenceNode[]}
                edges={brainData.edges as IntelligenceEdge[]}
                activeDriverIds={activeDriverIds}
                blockedIds={blockedIds}
                hotSymbols={hotSymbols}
                onSelect={n => setBrainSelected(n)}
                selected={brainSelected}
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-8 h-8 border-2 border-amber-400/50 border-t-amber-400 rounded-full animate-spin" />
              </div>
            )
          )}

          {/* Edge type legend (market mode only) */}
          {mapMode !== "brain" && (
            <div className="absolute bottom-4 left-4 rounded-xl bg-black/50 backdrop-blur border border-white/8 p-3 space-y-1.5">
              {[
                { color: EDGE_COLORS.supply_chain_up, label: "Supply chain" },
                { color: EDGE_COLORS.customer,        label: "Customer" },
                { color: EDGE_COLORS.competition,     label: "Competition" },
                { color: EDGE_COLORS.investment,      label: "Investment" },
                { color: EDGE_COLORS.ecosystem,       label: "Ecosystem" },
              ].map(({ color, label }) => (
                <div key={label} className="flex items-center gap-2">
                  <div className="w-6 h-0.5 rounded-full" style={{ background: color }} />
                  <span className="text-xs text-gray-500">{label}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Right sidebar ──────────────────────────────────────────────────── */}
      <div style={{ width: 224, flexShrink: 0, background: "#050810", borderLeft: "1px solid rgba(255,255,255,0.08)", display: "flex", flexDirection: "column" }}>

        {/* Map selector */}
        <div className="p-3 border-b border-white/8">
          <div className="text-xs text-gray-600 uppercase tracking-widest mb-2.5">Choose map</div>
          <div className="space-y-1">
            {MAP_OPTIONS.map(opt => (
              <button
                key={opt.key}
                onClick={() => setMapMode(opt.key)}
                className={`w-full text-left px-3 py-2.5 rounded-lg transition-all text-xs ${
                  mapMode === opt.key
                    ? opt.key === "brain"
                      ? "bg-amber-500/15 border border-amber-500/30 text-amber-300"
                      : "bg-indigo-500/15 border border-indigo-500/30 text-indigo-300"
                    : "text-gray-500 hover:text-gray-300 hover:bg-white/5 border border-transparent"
                }`}
              >
                <div className="flex items-center gap-2 mb-0.5">
                  {opt.key === "brain"
                    ? <Brain size={10} />
                    : <Map size={10} />}
                  <span className="font-medium">{opt.label}</span>
                </div>
                <div className="text-gray-600 leading-snug text-[10px]">{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Node detail (market mode) */}
        {mapMode !== "brain" && selected && (
          <div className="flex-1 overflow-y-auto">
            <div className="p-3 border-b border-white/8">
              <div className="flex items-start justify-between mb-1">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-white text-base">{selected.id}</span>
                    {price !== undefined && <span className="text-xs text-gray-400">${price.toFixed(2)}</span>}
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">{selected.label}</div>
                  <div className="text-xs text-gray-700 capitalize">{selected.subcluster?.replace(/_/g, " ")} · Tier {selected.tier}</div>
                </div>
                <button onClick={() => setSelected(null)} className="text-gray-600 hover:text-gray-300 mt-0.5">
                  <X size={13} />
                </button>
              </div>
              {pct !== 0 && (
                <div className={`flex items-center gap-1 text-xs font-semibold mt-1 ${pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {pct > 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                  {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
                </div>
              )}
            </div>

            {/* Description */}
            {selected.description && (
              <div className="px-3 pt-3 pb-2">
                <div className="text-xs text-gray-500 leading-relaxed">{selected.description}</div>
              </div>
            )}

            {/* Connections */}
            {connections.length > 0 && (
              <div className="px-3 pb-4">
                <div className="text-xs text-gray-600 uppercase tracking-widest mb-2">
                  Connections ({connections.length})
                </div>
                <div className="space-y-1">
                  {connections.map(({ edge, other, isOutgoing }) => (
                    <button
                      key={`${edge.source}-${edge.target}`}
                      onClick={() => other && setSelected(other)}
                      className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/5 transition-colors"
                    >
                      <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: EDGE_COLORS[edge.type] }} />
                      <span className="text-xs font-mono text-white font-semibold flex-shrink-0">{other?.id}</span>
                      <span className="text-[10px] px-1 py-0.5 rounded flex-shrink-0"
                        style={{ color: EDGE_COLORS[edge.type], background: EDGE_COLORS[edge.type] + "22" }}>
                        {EDGE_LABELS[edge.type]}
                      </span>
                      {!isOutgoing && <span className="text-[10px] text-gray-700 ml-auto">←</span>}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Brain node detail */}
        {mapMode === "brain" && brainSelected && (
          <div className="flex-1 overflow-y-auto">
            <BrainPanel
              node={brainSelected}
              activeDriverIds={activeDriverIds}
              blockedIds={blockedIds}
              hotSymbols={hotSymbols}
              allNodes={(brainData?.nodes ?? []) as IntelligenceNode[]}
              onClose={() => setBrainSelected(null)}
              onNavigate={handleBrainNavigate}
            />
          </div>
        )}

        {/* Brain driver summary when nothing selected */}
        {mapMode === "brain" && !brainSelected && brainData && (
          <div className="p-3 space-y-2">
            <div className="text-xs text-gray-600 uppercase tracking-widest">Active drivers</div>
            {brainData.active_driver_ids.map(id => {
              const node = brainData.nodes.find(n => n.id === id);
              return (
                <div key={id} className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse flex-shrink-0" />
                  <span className="text-xs text-amber-200/80">{node?.label ?? id}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Empty state */}
        {mapMode !== "brain" && !selected && (
          <div className="p-3 mt-2">
            <div className="text-xs text-gray-700 leading-relaxed">
              Click any node to see its full connection map and drill into tier-2 relationships.
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="p-3 border-t border-white/8 mt-auto">
          <div className="text-[10px] text-gray-700">map.decifertrading.com</div>
        </div>
      </div>
    </div>
  );
}
