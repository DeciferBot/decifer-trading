"use client";

import { X, Zap, Target, TrendingUp } from "lucide-react";
import type { IntelligenceNode } from "@/lib/intelligence-types";
import {
  THEME_COLORS, THEME_COLOR_DEFAULT,
  DRIVER_COLOR_ACTIVE, DRIVER_COLOR_BLOCKED,
} from "@/lib/intelligence-types";

interface Props {
  node: IntelligenceNode;
  activeDriverIds: Set<string>;
  blockedIds: Set<string>;
  hotSymbols: Set<string>;
  allNodes: IntelligenceNode[];
  onClose: () => void;
  onNavigate: (id: string) => void;
}

export default function BrainPanel({ node, activeDriverIds, blockedIds, hotSymbols, allNodes, onClose, onNavigate }: Props) {
  const isActiveDriver = node.type === "driver" && activeDriverIds.has(node.id);
  const isBlocked = node.type === "driver" && blockedIds.has(node.id);
  const isHot = node.type === "symbol" && hotSymbols.has(node.id) && (node.driver_ids ?? []).some(d => activeDriverIds.has(d));
  const isActive = node.type === "symbol" && (node.driver_ids ?? []).some(d => activeDriverIds.has(d));

  // For drivers: find connected themes + symbols
  const connectedThemes = node.type === "driver"
    ? allNodes.filter(n => n.type === "theme" && n.driver_ids?.includes(node.id))
    : [];
  const downstreamSymbols = node.type === "driver"
    ? allNodes.filter(n => n.type === "symbol" && n.driver_ids?.includes(node.id))
        .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
        .slice(0, 12)
    : [];

  // For themes: symbols in this theme
  const themeSymbols = node.type === "theme"
    ? allNodes.filter(n => n.type === "symbol" && n.theme_ids?.includes(node.id))
        .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
    : [];

  // For symbols: upstream drivers + themes
  const upstreamDrivers = node.type === "symbol"
    ? allNodes.filter(n => n.type === "driver" && node.driver_ids?.includes(n.id))
    : [];
  const upstreamThemes = node.type === "symbol"
    ? allNodes.filter(n => n.type === "theme" && node.theme_ids?.includes(n.id))
    : [];

  const accentColor =
    node.type === "driver"  ? (isBlocked ? DRIVER_COLOR_BLOCKED : isActiveDriver ? DRIVER_COLOR_ACTIVE : "#6b7280")
    : node.type === "theme" ? (THEME_COLORS[node.id] ?? THEME_COLOR_DEFAULT)
    : isHot ? "#10b981" : isActive ? "#818cf8" : "#6b7280";

  return (
    <div className="flex flex-col h-full text-sm">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-white/10 flex-shrink-0">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: accentColor }} />
            <span className="text-xs text-gray-500 uppercase tracking-wider">
              {node.type === "driver" ? "Market Driver" : node.type === "theme" ? "Theme" : "Ticker"}
            </span>
            {isActiveDriver && <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30">Active</span>}
            {isBlocked    && <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/30">Blocked</span>}
            {isHot        && <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">High conviction</span>}
            {isActive && !isHot && <span className="text-xs px-1.5 py-0.5 rounded-full bg-indigo-500/15 text-indigo-400 border border-indigo-500/30">In scope</span>}
          </div>
          <div className="font-semibold text-white leading-tight">{node.label}</div>
          {node.type === "symbol" && <div className="text-xs text-gray-500 mt-0.5 font-mono">{node.id}</div>}
        </div>
        <button onClick={onClose} className="text-gray-600 hover:text-gray-300 ml-3 flex-shrink-0 mt-0.5">
          <X size={15} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* Description */}
        {node.description && (
          <p className="text-xs text-gray-400 leading-relaxed">{node.description}</p>
        )}
        {node.reason_to_care && (
          <p className="text-xs text-gray-400 leading-relaxed">{node.reason_to_care}</p>
        )}

        {/* Confidence bar (symbols) */}
        {node.type === "symbol" && node.confidence != null && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-gray-500">Conviction</span>
              <span className="text-xs text-gray-300 font-mono">{Math.round(node.confidence * 100)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/10">
              <div className="h-full rounded-full transition-all" style={{ width: `${node.confidence * 100}%`, background: accentColor }} />
            </div>
          </div>
        )}

        {/* Exposure type (symbols) */}
        {node.type === "symbol" && node.exposure_type && (
          <div className="flex items-center gap-2">
            <Target size={12} className="text-gray-600 flex-shrink-0" />
            <span className="text-xs text-gray-400 capitalize">{node.exposure_type.replace(/_/g, " ")}</span>
          </div>
        )}

        {/* Risk note */}
        {node.risk_note && (
          <div className="rounded-lg bg-amber-500/8 border border-amber-500/20 px-3 py-2.5">
            <div className="text-xs text-amber-400/80 leading-relaxed">{node.risk_note}</div>
          </div>
        )}

        {/* Driver: downstream themes */}
        {connectedThemes.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Zap size={11} className="text-gray-500" />
              <span className="text-xs text-gray-500 uppercase tracking-wider">Activates themes</span>
            </div>
            <div className="space-y-1.5">
              {connectedThemes.map(t => (
                <button key={t.id} onClick={() => onNavigate(t.id)}
                  className="w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-white/5 transition-all">
                  <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: THEME_COLORS[t.id] ?? THEME_COLOR_DEFAULT }} />
                  <span className="text-xs text-gray-300">{t.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Driver: downstream symbols */}
        {downstreamSymbols.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <TrendingUp size={11} className="text-gray-500" />
              <span className="text-xs text-gray-500 uppercase tracking-wider">Downstream tickers</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {downstreamSymbols.map(s => (
                <button key={s.id} onClick={() => onNavigate(s.id)}
                  className="px-2 py-1 rounded text-xs font-mono transition-all border"
                  style={{
                    background: hotSymbols.has(s.id) ? "#10b98115" : "#818cf815",
                    borderColor: hotSymbols.has(s.id) ? "#10b98140" : "#818cf840",
                    color: hotSymbols.has(s.id) ? "#6ee7b7" : "#a5b4fc",
                  }}>
                  {s.id}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Theme: symbols */}
        {themeSymbols.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <TrendingUp size={11} className="text-gray-500" />
              <span className="text-xs text-gray-500 uppercase tracking-wider">Tickers in this theme</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {themeSymbols.map(s => (
                <button key={s.id} onClick={() => onNavigate(s.id)}
                  className="px-2 py-1 rounded text-xs font-mono transition-all border"
                  style={{
                    background: hotSymbols.has(s.id) ? "#10b98115" : "#334155",
                    borderColor: hotSymbols.has(s.id) ? "#10b98140" : "#475569",
                    color: hotSymbols.has(s.id) ? "#6ee7b7" : "#94a3b8",
                  }}>
                  {s.id}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Symbol: upstream chain */}
        {upstreamDrivers.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Driven by</div>
            <div className="space-y-1.5">
              {upstreamDrivers.map(d => (
                <button key={d.id} onClick={() => onNavigate(d.id)}
                  className="w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-white/5 transition-all">
                  <div className="w-2 h-2 rounded-full flex-shrink-0"
                    style={{ background: activeDriverIds.has(d.id) ? DRIVER_COLOR_ACTIVE : "#374151" }} />
                  <span className="text-xs" style={{ color: activeDriverIds.has(d.id) ? "#fcd34d" : "#6b7280" }}>
                    {d.label}
                  </span>
                  {activeDriverIds.has(d.id) && (
                    <span className="ml-auto text-xs text-amber-400 opacity-70">Active</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {upstreamThemes.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Via theme</div>
            <div className="space-y-1.5">
              {upstreamThemes.map(t => (
                <button key={t.id} onClick={() => onNavigate(t.id)}
                  className="w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-white/5 transition-all">
                  <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: THEME_COLORS[t.id] ?? THEME_COLOR_DEFAULT }} />
                  <span className="text-xs text-gray-300">{t.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
