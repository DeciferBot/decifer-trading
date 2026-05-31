"use client";

import { useState } from "react";
import { ChevronRight, TrendingUp, TrendingDown } from "lucide-react";
import type { EnrichedNode, GraphEdge, Cluster } from "@/lib/types";
import { SUBCLUSTER_LABELS } from "@/lib/types";

interface Props {
  nodes: EnrichedNode[];
  edges: GraphEdge[];
  clusters: Cluster[];
  onSelect: (node: EnrichedNode) => void;
}

export default function MobileMap({ nodes, edges, clusters, onSelect }: Props) {
  const [activeCluster, setActiveCluster] = useState<string | null>(null);
  const [activeSubcluster, setActiveSubcluster] = useState<string | null>(null);

  // Cluster brightness = average of top-5 node brightnesses
  const clusterBrightness = (cid: string) => {
    const ns = nodes.filter(n => n.cluster === cid).sort((a, b) => b.brightness - a.brightness);
    if (!ns.length) return 0;
    return ns.slice(0, 5).reduce((s, n) => s + n.brightness, 0) / Math.min(5, ns.length);
  };

  // Subclusters within a cluster
  const subclusters = (cid: string) => {
    const subs = new Set(nodes.filter(n => n.cluster === cid).map(n => n.subcluster));
    return Array.from(subs).map(sub => {
      const ns = nodes.filter(n => n.cluster === cid && n.subcluster === sub)
        .sort((a, b) => b.brightness - a.brightness);
      const brightness = ns.slice(0, 3).reduce((s, n) => s + n.brightness, 0) / Math.min(3, ns.length);
      return { sub, nodes: ns, brightness };
    }).sort((a, b) => b.brightness - a.brightness);
  };

  const selectedCluster = clusters.find(c => c.id === activeCluster);
  const subList = activeCluster ? subclusters(activeCluster) : [];
  const selectedSubNodes = activeCluster && activeSubcluster
    ? nodes.filter(n => n.cluster === activeCluster && n.subcluster === activeSubcluster)
        .sort((a, b) => b.brightness - a.brightness)
    : activeCluster && !activeSubcluster
      ? nodes.filter(n => n.cluster === activeCluster).sort((a, b) => b.brightness - a.brightness)
      : [];

  const BrightnessBar = ({ value }: { value: number }) => (
    <div className="h-1 rounded-full bg-white/10 overflow-hidden w-16">
      <div
        className="h-full rounded-full"
        style={{
          width: `${value}%`,
          background: value > 65 ? "#10b981" : value > 35 ? "#f59e0b" : "#6366f1",
        }}
      />
    </div>
  );

  // Level 1 — Cluster bubbles
  if (!activeCluster) {
    return (
      <div className="p-4 space-y-3">
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-4">Market clusters</div>
        {clusters.map(c => {
          const bright = clusterBrightness(c.id);
          const topNodes = nodes.filter(n => n.cluster === c.id)
            .sort((a, b) => b.brightness - a.brightness).slice(0, 3);
          return (
            <button
              key={c.id}
              onClick={() => setActiveCluster(c.id)}
              className="w-full rounded-xl p-4 border text-left transition-all active:scale-98"
              style={{ borderColor: c.color + "40", background: c.color + "0d" }}
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full" style={{ background: c.color }} />
                  <span className="font-semibold text-white">{c.label}</span>
                </div>
                <div className="flex items-center gap-2">
                  <BrightnessBar value={bright} />
                  <ChevronRight size={14} className="text-gray-600" />
                </div>
              </div>
              <div className="flex gap-2 flex-wrap">
                {topNodes.map(n => (
                  <div key={n.id} className="flex items-center gap-1.5 rounded-md bg-white/5 px-2 py-1">
                    <span className="text-xs font-mono font-semibold text-white">{n.id}</span>
                    {n.price && (
                      <span className={`text-xs ${n.price.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {n.price.change_pct >= 0 ? "+" : ""}{n.price.change_pct.toFixed(1)}%
                      </span>
                    )}
                  </div>
                ))}
              </div>
              <div className="text-xs text-gray-600 mt-2">
                {nodes.filter(n => n.cluster === c.id).length} companies tracked
              </div>
            </button>
          );
        })}
      </div>
    );
  }

  // Level 2 — Subclusters within a cluster
  if (activeCluster && !activeSubcluster) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center gap-2 p-4 border-b border-white/10">
          <button onClick={() => setActiveCluster(null)} className="text-gray-500 hover:text-white text-sm">
            Clusters
          </button>
          <ChevronRight size={12} className="text-gray-600" />
          <span className="text-sm text-white font-medium" style={{ color: selectedCluster?.color }}>
            {selectedCluster?.label}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-3">Sub-sectors</div>
          {subList.map(({ sub, nodes: sns, brightness }) => (
            <button
              key={sub}
              onClick={() => setActiveSubcluster(sub)}
              className="w-full rounded-lg p-3.5 border border-white/10 bg-white/5 hover:bg-white/10 text-left transition-all flex items-center justify-between"
            >
              <div>
                <div className="text-sm font-medium text-white">{SUBCLUSTER_LABELS[sub] ?? sub}</div>
                <div className="flex gap-1.5 mt-1.5 flex-wrap">
                  {sns.slice(0, 4).map(n => (
                    <span key={n.id} className="text-xs font-mono text-gray-400">{n.id}</span>
                  ))}
                  {sns.length > 4 && <span className="text-xs text-gray-600">+{sns.length - 4}</span>}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <BrightnessBar value={brightness} />
                <ChevronRight size={14} className="text-gray-600" />
              </div>
            </button>
          ))}
          <div className="pt-1">
            <button
              onClick={() => setActiveSubcluster("__all__")}
              className="w-full rounded-lg p-3 border border-white/10 bg-white/5 hover:bg-white/10 text-left text-sm text-gray-400 transition-all"
            >
              Show all {nodes.filter(n => n.cluster === activeCluster).length} names →
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Level 3 — Names list
  const displayNodes = activeSubcluster === "__all__"
    ? nodes.filter(n => n.cluster === activeCluster).sort((a, b) => b.brightness - a.brightness)
    : selectedSubNodes;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 p-4 border-b border-white/10 flex-wrap">
        <button onClick={() => setActiveCluster(null)} className="text-gray-500 hover:text-white text-sm">
          Clusters
        </button>
        <ChevronRight size={12} className="text-gray-600" />
        <button onClick={() => setActiveSubcluster(null)} className="text-sm" style={{ color: selectedCluster?.color }}>
          {selectedCluster?.label}
        </button>
        {activeSubcluster !== "__all__" && (
          <>
            <ChevronRight size={12} className="text-gray-600" />
            <span className="text-sm text-white">{SUBCLUSTER_LABELS[activeSubcluster ?? ""] ?? activeSubcluster}</span>
          </>
        )}
      </div>
      <div className="flex-1 overflow-y-auto divide-y divide-white/5">
        {displayNodes.map(n => (
          <button
            key={n.id}
            onClick={() => onSelect(n)}
            className="w-full flex items-center gap-3 px-4 py-3.5 hover:bg-white/5 transition-colors text-left"
          >
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 text-xs font-bold font-mono"
              style={{
                background: (n.price?.change_pct ?? 0) > 1 ? "#10b98120" :
                             (n.price?.change_pct ?? 0) < -1 ? "#ef444420" : "#ffffff10",
                color: (n.price?.change_pct ?? 0) > 1 ? "#10b981" :
                       (n.price?.change_pct ?? 0) < -1 ? "#ef4444" : "#fff",
              }}
            >
              {n.id.slice(0, 4)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-white">{n.id}</span>
                <span className="text-xs text-gray-500 truncate">{n.label}</span>
              </div>
              <div className="text-xs text-gray-600 mt-0.5 truncate">{n.chain_note}</div>
            </div>
            <div className="flex flex-col items-end gap-1 flex-shrink-0">
              {n.price && (
                <div className={`flex items-center gap-0.5 text-xs font-semibold ${n.price.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {n.price.change_pct >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                  {n.price.change_pct >= 0 ? "+" : ""}{n.price.change_pct.toFixed(2)}%
                </div>
              )}
              <div className="h-1 w-12 rounded-full bg-white/10 overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${n.brightness}%`,
                    background: n.brightness > 65 ? "#10b981" : n.brightness > 35 ? "#f59e0b" : "#6366f1",
                  }}
                />
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
