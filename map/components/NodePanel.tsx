"use client";

import { X, TrendingUp, TrendingDown, Minus, Link2 } from "lucide-react";
import type { EnrichedNode, GraphEdge } from "@/lib/types";
import { EDGE_COLORS, EDGE_LABELS } from "@/lib/types";

interface Props {
  node: EnrichedNode;
  edges: GraphEdge[];
  allNodes: EnrichedNode[];
  onClose: () => void;
  onNavigate: (id: string) => void;
}

export default function NodePanel({ node, edges, allNodes, onClose, onNavigate }: Props) {
  const pct = node.price?.change_pct ?? 0;
  const price = node.price?.price;

  const connections = edges
    .filter(e => e.source === node.id || e.target === node.id)
    .map(e => {
      const otherId = e.source === node.id ? e.target : e.source;
      const other = allNodes.find(n => n.id === otherId);
      const isOutgoing = e.source === node.id;
      return { edge: e, other, isOutgoing };
    })
    .filter(c => c.other)
    .sort((a, b) => b.edge.strength - a.edge.strength);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-white/10">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold text-white">{node.id}</span>
            {price !== undefined && (
              <span className="text-sm text-gray-400">${price.toFixed(2)}</span>
            )}
          </div>
          <div className="text-sm text-gray-400 mt-0.5">{node.label}</div>
          <div className="text-xs text-gray-600 mt-0.5 capitalize">{node.subcluster.replace(/_/g, " ")} · Tier {node.tier}</div>
        </div>
        <div className="flex items-center gap-3">
          {pct !== 0 && (
            <div className={`flex items-center gap-1 text-sm font-semibold ${pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
              {pct > 0 ? <TrendingUp size={14} /> : pct < 0 ? <TrendingDown size={14} /> : <Minus size={14} />}
              {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
            </div>
          )}
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* Signal brightness */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-gray-500 uppercase tracking-wider">Signal strength</span>
            <span className="text-xs font-mono text-gray-300">{node.brightness}/100</span>
          </div>
          <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${node.brightness}%`,
                background: node.brightness > 65 ? "#10b981" : node.brightness > 35 ? "#f59e0b" : "#6366f1",
              }}
            />
          </div>
        </div>

        {/* Description */}
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1.5">What they do</div>
          <p className="text-sm text-gray-300 leading-relaxed">{node.description}</p>
        </div>

        {/* Chain note */}
        <div className="rounded-lg bg-white/5 border border-white/10 p-3">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Chain position</div>
          <p className="text-sm text-gray-200 leading-relaxed">{node.chain_note}</p>
        </div>

        {/* Connections */}
        {connections.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Connections ({connections.length})</div>
            <div className="space-y-1.5">
              {connections.map(({ edge, other, isOutgoing }) => (
                <button
                  key={`${edge.source}-${edge.target}`}
                  onClick={() => other && onNavigate(other.id)}
                  className="w-full flex items-start gap-2.5 rounded-lg p-2.5 bg-white/5 hover:bg-white/10 transition-colors text-left group"
                >
                  <div
                    className="mt-0.5 w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ background: EDGE_COLORS[edge.type], marginTop: 4 }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-mono font-semibold text-white">{other?.id}</span>
                      <span className="text-xs font-medium px-1 py-0.5 rounded text-[10px]"
                        style={{ color: EDGE_COLORS[edge.type], background: EDGE_COLORS[edge.type] + "22" }}>
                        {EDGE_LABELS[edge.type]}
                      </span>
                      {!isOutgoing && <span className="text-xs text-gray-600">(←)</span>}
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5 truncate">{edge.label}</div>
                    {edge.lag_weeks > 0 && (
                      <div className="flex items-center gap-1 mt-1">
                        <Link2 size={9} className="text-gray-600" />
                        <span className="text-xs text-gray-600">Signal lag ~{edge.lag_weeks}w</span>
                      </div>
                    )}
                  </div>
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                    <div className="text-xs text-gray-500">→</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
