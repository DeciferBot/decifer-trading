"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import { Activity, Filter, Search, X } from "lucide-react";
import type { GraphData, EnrichedNode, GraphEdge, Cluster } from "@/lib/types";
import { computeBrightness } from "@/lib/types";
import NodePanel from "@/components/NodePanel";
import MobileMap from "@/components/MobileMap";

const MarketGraph = dynamic(() => import("@/components/MarketGraph"), { ssr: false });

const EDGE_TYPE_FILTERS = [
  { key: "all",             label: "All" },
  { key: "supply_chain_up", label: "Supply chain" },
  { key: "customer",        label: "Customer" },
  { key: "competition",     label: "Competition" },
  { key: "investment",      label: "Investment" },
  { key: "ecosystem",       label: "Ecosystem" },
] as const;

export default function MapPage() {
  const [graphData, setGraphData]       = useState<GraphData | null>(null);
  const [enriched, setEnriched]         = useState<EnrichedNode[]>([]);
  const [selected, setSelected]         = useState<EnrichedNode | null>(null);
  const [activeFilter, setActiveFilter] = useState<string>("all");
  const [isMobile, setIsMobile]         = useState(false);
  const [lastUpdated, setLastUpdated]   = useState<Date | null>(null);
  const [search, setSearch]             = useState("");
  const [focusId, setFocusId]           = useState<string | null>(null);
  const [showSearch, setShowSearch]     = useState(false);
  const autoSelectedRef                 = useRef(false);
  const searchRef                       = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  useEffect(() => {
    fetch("/api/graph").then(r => r.json()).then(setGraphData);
  }, []);

  const loadPrices = useCallback(async () => {
    if (!graphData) return;
    try {
      const res = await fetch("/api/prices");
      const { prices } = await res.json();
      const nodes: EnrichedNode[] = graphData.nodes.map(n => ({
        ...n,
        price: prices[n.id],
        brightness: computeBrightness(prices[n.id]),
      }));
      setEnriched(nodes);
      setLastUpdated(new Date());
    } catch {
      setEnriched(graphData.nodes.map(n => ({ ...n, brightness: 20 })));
    }
  }, [graphData]);

  useEffect(() => {
    if (!graphData) return;
    loadPrices();
    const interval = setInterval(loadPrices, 120_000);
    return () => clearInterval(interval);
  }, [graphData, loadPrices]);

  const filteredEdges: GraphEdge[] = (graphData?.edges ?? []).filter(
    e => activeFilter === "all" || e.type === activeFilter
  );

  const handleSelect = useCallback((node: EnrichedNode | null) => setSelected(node), []);

  const handleNavigate = useCallback((id: string) => {
    const node = enriched.find(n => n.id === id);
    if (node) setSelected(node);
  }, [enriched]);

  useEffect(() => {
    if (!selected) return;
    const updated = enriched.find(n => n.id === selected.id);
    if (updated) setSelected(updated);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enriched]);

  // Auto-select hottest node once on first data load
  useEffect(() => {
    if (enriched.length === 0 || autoSelectedRef.current) return;
    const timer = setTimeout(() => {
      if (autoSelectedRef.current) return;
      autoSelectedRef.current = true;
      const hottest = [...enriched].sort((a, b) => b.brightness - a.brightness)[0];
      if (hottest) { setSelected(hottest); setFocusId(hottest.id); }
    }, 2800);
    return () => clearTimeout(timer);
  }, [enriched]);

  // Search: find node, select and pan to it
  useEffect(() => {
    if (!search.trim()) { setFocusId(null); return; }
    const q = search.trim().toUpperCase();
    const match = enriched.find(n =>
      n.id.toUpperCase() === q ||
      n.id.toUpperCase().startsWith(q) ||
      n.label.toUpperCase().includes(search.trim().toUpperCase())
    );
    if (match) { setSelected(match); setFocusId(match.id); }
  }, [search, enriched]);

  // Keyboard shortcut: / to focus search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "/" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setShowSearch(true);
        setTimeout(() => searchRef.current?.focus(), 50);
      }
      if (e.key === "Escape") { setShowSearch(false); setSearch(""); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const clusters: Cluster[] = graphData?.clusters ?? [];

  const hotNodes = enriched
    .filter(n => n.brightness > 55)
    .sort((a, b) => b.brightness - a.brightness)
    .slice(0, 6);

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col" style={{ background: "#080d1a", color: "#fff" }}>
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-white/10 flex-shrink-0 gap-3">
        <div className="flex items-center gap-3 flex-shrink-0">
          <Activity size={16} className="text-indigo-400" />
          <span className="font-semibold text-white tracking-tight hidden sm:block">Decifer Market Map</span>
          {!isMobile && clusters.map(c => (
            <div key={c.id} className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full" style={{ background: c.color }} />
              <span className="text-xs text-gray-500">{c.label}</span>
            </div>
          ))}
        </div>

        {/* Search */}
        <div className="flex-1 max-w-xs">
          {showSearch ? (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/8 border border-white/15">
              <Search size={12} className="text-gray-400 flex-shrink-0" />
              <input
                ref={searchRef}
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search ticker or company…"
                className="flex-1 bg-transparent text-xs text-white placeholder-gray-600 outline-none min-w-0"
                autoComplete="off"
              />
              {search && (
                <button onClick={() => { setSearch(""); setFocusId(null); }} className="text-gray-600 hover:text-gray-300">
                  <X size={11} />
                </button>
              )}
            </div>
          ) : (
            <button
              onClick={() => { setShowSearch(true); setTimeout(() => searchRef.current?.focus(), 50); }}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-gray-500 hover:text-gray-300 hover:bg-white/5 transition-all border border-transparent hover:border-white/10"
            >
              <Search size={12} />
              <span className="hidden sm:block">Search</span>
              <span className="text-gray-700 hidden sm:block">·</span>
              <kbd className="text-gray-700 font-mono hidden sm:block">/</kbd>
            </button>
          )}
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          {lastUpdated && (
            <span className="text-xs text-gray-600 hidden lg:block">
              {lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <div className="flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-xs text-emerald-400">Live</span>
          </div>
        </div>
      </header>

      {/* Hot strip */}
      {hotNodes.length > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-white/5 overflow-x-auto flex-shrink-0">
          <span className="text-xs text-gray-600 flex-shrink-0">Hot:</span>
          {hotNodes.map(n => (
            <button
              key={n.id}
              onClick={() => setSelected(n)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs flex-shrink-0 transition-all hover:bg-white/10"
              style={{
                borderColor: (n.price?.change_pct ?? 0) >= 0 ? "#10b98140" : "#ef444440",
                background:  (n.price?.change_pct ?? 0) >= 0 ? "#10b98110" : "#ef444410",
              }}
            >
              <span className="font-mono font-semibold text-white">{n.id}</span>
              {n.price && (
                <span className={n.price.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}>
                  {n.price.change_pct >= 0 ? "+" : ""}{n.price.change_pct.toFixed(1)}%
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {isMobile ? (
          <div className="flex-1 overflow-hidden">
            {selected ? (
              <div className="h-full flex flex-col">
                <NodePanel
                  node={selected}
                  edges={filteredEdges}
                  allNodes={enriched}
                  onClose={() => setSelected(null)}
                  onNavigate={handleNavigate}
                />
              </div>
            ) : (
              <MobileMap
                nodes={enriched}
                edges={filteredEdges}
                clusters={clusters}
                onSelect={handleSelect}
              />
            )}
          </div>
        ) : (
          <>
            <div className="flex-1 relative">
              {enriched.length > 0 ? (
                <MarketGraph
                  nodes={enriched}
                  edges={filteredEdges}
                  clusters={clusters}
                  onSelect={handleSelect}
                  selected={selected}
                  focusId={focusId}
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="text-center space-y-3">
                    <div className="w-8 h-8 border-2 border-indigo-400/50 border-t-indigo-400 rounded-full animate-spin mx-auto" />
                    <div className="text-sm text-gray-500">Loading market map…</div>
                  </div>
                </div>
              )}

              {/* Edge filter */}
              <div className="absolute bottom-4 left-4 flex items-center gap-1.5 overflow-x-auto max-w-lg pb-0.5">
                <Filter size={12} className="text-gray-600" />
                {EDGE_TYPE_FILTERS.map(f => (
                  <button
                    key={f.key}
                    onClick={() => setActiveFilter(f.key)}
                    className={`px-2.5 py-1 rounded-full text-xs transition-all border whitespace-nowrap flex-shrink-0 ${
                      activeFilter === f.key
                        ? "bg-indigo-500/20 border-indigo-500/50 text-indigo-300"
                        : "bg-white/5 border-white/10 text-gray-500 hover:text-gray-300"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>

              {/* Legend */}
              <div className="absolute bottom-4 right-4 hidden lg:block">
                <div className="rounded-xl bg-black/60 backdrop-blur-sm border border-white/10 p-3 space-y-1.5">
                  {[
                    { color: "#6366f1", label: "Supply chain" },
                    { color: "#10b981", label: "Customer" },
                    { color: "#ef4444", label: "Competition" },
                    { color: "#a855f7", label: "Investment" },
                    { color: "#f59e0b", label: "Ecosystem" },
                  ].map(({ color, label }) => (
                    <div key={label} className="flex items-center gap-2">
                      <div className="w-6 h-0.5 rounded-full" style={{ background: color }} />
                      <span className="text-xs text-gray-500">{label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Side panel */}
            {selected && (
              <div className="w-80 xl:w-96 border-l border-white/10 flex-shrink-0 overflow-hidden bg-black/20">
                <NodePanel
                  node={selected}
                  edges={filteredEdges}
                  allNodes={enriched}
                  onClose={() => setSelected(null)}
                  onNavigate={handleNavigate}
                />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
