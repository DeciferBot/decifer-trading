"use client";

import { useState, useMemo } from "react";
import { ChevronRight, Zap, TrendingUp, Circle } from "lucide-react";
import type { IntelligenceGraphData } from "@/lib/intelligence-types";

interface Props {
  data: IntelligenceGraphData;
  onSymbolSelect?: (symbol: string) => void;
}

interface DriverNode { id: string; label: string; description: string; active: boolean; }
interface ThemeNode  { id: string; label: string; driverIds: string[]; }
interface SymbolNode { id: string; label: string; confidence: number; exposureType: string; riskNote?: string | null; themeId: string; driverId: string; inPlay: boolean; }

export default function IntelligenceDrillDown({ data, onSymbolSelect }: Props) {
  const [activeDriver, setActiveDriver] = useState<string | null>(null);
  const [activeTheme,  setActiveTheme]  = useState<string | null>(null);

  // ── Parse nodes ─────────────────────────────────────────────────────────
  const drivers = useMemo<DriverNode[]>(() => {
    const activeSet = new Set(data.active_driver_ids);
    return data.nodes
      .filter(n => n.type === "driver")
      .map(n => ({ id: n.id, label: n.label, description: n.description ?? "", active: activeSet.has(n.id) }))
      .sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0));
  }, [data]);

  // themes connected to selected driver
  const themes = useMemo<ThemeNode[]>(() => {
    if (!activeDriver) return [];
    const themeIds = new Set(
      data.edges
        .filter(e => e.type === "activates" && e.source === activeDriver)
        .map(e => e.target as string)
    );
    return data.nodes
      .filter(n => n.type === "theme" && themeIds.has(n.id))
      .map(n => ({
        id: n.id,
        label: n.label,
        driverIds: data.edges.filter(e => e.type === "activates" && e.target === n.id).map(e => e.source as string),
      }));
  }, [data, activeDriver]);

  // symbols connected to selected theme
  const symbols = useMemo<SymbolNode[]>(() => {
    if (!activeTheme) return [];
    const playSet = new Set(data.active_candidate_symbols);
    return data.edges
      .filter(e => e.type === "exposes" && e.source === activeTheme)
      .map(e => {
        const sym = data.nodes.find(n => n.id === (e.target as string));
        if (!sym) return null;
        return {
          id: sym.id,
          label: sym.label ?? sym.id,
          confidence: (sym.confidence ?? 0.7),
          exposureType: (e as { exposure_type?: string }).exposure_type ?? sym.exposure_type ?? "direct_beneficiary",
          riskNote: sym.risk_note,
          themeId: activeTheme,
          driverId: activeDriver ?? "",
          inPlay: playSet.has(sym.id),
        };
      })
      .filter(Boolean)
      .sort((a, b) => {
        if (a!.inPlay !== b!.inPlay) return a!.inPlay ? -1 : 1;
        return b!.confidence - a!.confidence;
      }) as SymbolNode[];
  }, [data, activeTheme, activeDriver]);

  // symbol count per theme (for display)
  const symbolCount = useMemo(() => {
    const map: Record<string, number> = {};
    data.edges.filter(e => e.type === "exposes").forEach(e => {
      const t = e.source as string;
      map[t] = (map[t] ?? 0) + 1;
    });
    return map;
  }, [data]);

  const inPlayCount = new Set(data.active_candidate_symbols).size;
  const activeCount = data.active_driver_ids.length;

  return (
    <div className="flex h-full overflow-hidden" style={{ fontFamily: "system-ui, sans-serif" }}>

      {/* ── Column 1: Drivers ─────────────────────────────────────────────── */}
      <div className="flex flex-col border-r border-white/8 overflow-hidden" style={{ width: 260, flexShrink: 0 }}>
        <div className="px-4 py-3 border-b border-white/8 flex-shrink-0">
          <div className="text-xs text-gray-500 uppercase tracking-widest mb-0.5">Market drivers</div>
          <div className="text-xs text-amber-400">{activeCount} active today</div>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {/* Active drivers */}
          {drivers.filter(d => d.active).map(d => (
            <button
              key={d.id}
              onClick={() => { setActiveDriver(d.id); setActiveTheme(null); }}
              className={`w-full text-left px-4 py-3 border-b border-white/5 transition-all group ${
                activeDriver === d.id ? "bg-amber-500/10 border-l-2 border-l-amber-500" : "hover:bg-white/5"
              }`}
            >
              <div className="flex items-center gap-2.5 mb-1">
                <div className="w-2.5 h-2.5 rounded-full bg-amber-400 flex-shrink-0" style={{ boxShadow: "0 0 6px #f59e0b" }} />
                <span className="text-xs font-semibold text-amber-200 leading-tight">{d.label}</span>
                <ChevronRight size={11} className={`ml-auto text-gray-600 flex-shrink-0 transition-transform ${activeDriver === d.id ? "rotate-90 text-amber-400" : ""}`} />
              </div>
              {d.description && (
                <p className="text-[10px] text-gray-600 leading-relaxed ml-5 line-clamp-2">{d.description}</p>
              )}
            </button>
          ))}

          {/* Divider */}
          {drivers.some(d => !d.active) && (
            <div className="px-4 py-2 mt-1">
              <div className="text-[10px] text-gray-700 uppercase tracking-widest">Inactive</div>
            </div>
          )}

          {/* Inactive drivers */}
          {drivers.filter(d => !d.active).map(d => (
            <button
              key={d.id}
              onClick={() => { setActiveDriver(d.id); setActiveTheme(null); }}
              className="w-full text-left px-4 py-2.5 border-b border-white/5 hover:bg-white/5 transition-all"
            >
              <div className="flex items-center gap-2.5">
                <div className="w-2 h-2 rounded-full bg-gray-700 flex-shrink-0" />
                <span className="text-xs text-gray-600">{d.label}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Column 2: Themes ──────────────────────────────────────────────── */}
      <div className="flex flex-col border-r border-white/8 overflow-hidden" style={{ width: 240, flexShrink: 0 }}>
        <div className="px-4 py-3 border-b border-white/8 flex-shrink-0">
          <div className="text-xs text-gray-500 uppercase tracking-widest mb-0.5">Themes activated</div>
          {activeDriver
            ? <div className="text-xs text-indigo-400">{themes.length} theme{themes.length !== 1 ? "s" : ""}</div>
            : <div className="text-xs text-gray-700">← select a driver</div>}
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {themes.length === 0 && activeDriver && (
            <div className="px-4 py-6 text-xs text-gray-700 text-center">No themes linked to this driver</div>
          )}
          {themes.map(t => {
            const count = symbolCount[t.id] ?? 0;
            return (
              <button
                key={t.id}
                onClick={() => setActiveTheme(t.id)}
                className={`w-full text-left px-4 py-3 border-b border-white/5 transition-all ${
                  activeTheme === t.id ? "bg-indigo-500/10 border-l-2 border-l-indigo-500" : "hover:bg-white/5"
                }`}
              >
                <div className="flex items-center gap-2.5 mb-1">
                  <div className="w-2 h-2 rounded-full bg-indigo-400 flex-shrink-0" />
                  <span className={`text-xs font-medium leading-tight ${activeTheme === t.id ? "text-indigo-200" : "text-gray-300"}`}>{t.label}</span>
                  <ChevronRight size={11} className={`ml-auto text-gray-600 flex-shrink-0 transition-transform ${activeTheme === t.id ? "rotate-90 text-indigo-400" : ""}`} />
                </div>
                <div className="ml-4.5 text-[10px] text-gray-600">{count} symbol{count !== 1 ? "s" : ""}</div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Column 3: Symbols ─────────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">
        <div className="px-5 py-3 border-b border-white/8 flex-shrink-0 flex items-center justify-between">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-widest mb-0.5">Symbols in play</div>
            {activeTheme
              ? <div className="text-xs text-emerald-400">{symbols.filter(s => s.inPlay).length} in universe · {symbols.length} total</div>
              : activeDriver
                ? <div className="text-xs text-gray-700">← select a theme</div>
                : <div className="text-xs text-gray-700">{inPlayCount} symbols currently in universe</div>}
          </div>
          {activeTheme && (
            <div className="flex items-center gap-3 text-[10px] text-gray-600">
              <div className="flex items-center gap-1"><div className="w-2 h-2 rounded-full bg-emerald-400" /><span>In universe</span></div>
              <div className="flex items-center gap-1"><div className="w-2 h-2 rounded-full bg-gray-600" /><span>Not in universe</span></div>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {/* Default: show all in-play symbols when nothing selected */}
          {!activeTheme && (
            <div className="p-5">
              {!activeDriver && (
                <div>
                  <div className="text-xs text-gray-500 mb-3">All symbols currently in the live opportunity universe:</div>
                  <div className="flex flex-wrap gap-2">
                    {data.active_candidate_symbols.map(sym => (
                      <button
                        key={sym}
                        onClick={() => onSymbolSelect?.(sym)}
                        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/8 text-xs font-mono text-emerald-300 hover:bg-emerald-500/15 transition-all"
                      >
                        <div className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                        {sym}
                      </button>
                    ))}
                    {data.active_candidate_symbols.length === 0 && (
                      <div className="text-xs text-gray-700">No symbols in live universe — check bot status</div>
                    )}
                  </div>
                </div>
              )}
              {activeDriver && (
                <div className="text-xs text-gray-700 text-center py-8">Select a theme to see its symbols</div>
              )}
            </div>
          )}

          {/* Symbol rows */}
          {activeTheme && symbols.map(s => (
            <button
              key={s.id}
              onClick={() => onSymbolSelect?.(s.id)}
              className={`w-full text-left flex items-center gap-4 px-5 py-3 border-b border-white/5 transition-all ${
                s.inPlay ? "hover:bg-emerald-500/5" : "hover:bg-white/3"
              }`}
            >
              {/* In-play indicator */}
              <div className={`w-2 h-2 rounded-full flex-shrink-0 ${s.inPlay ? "bg-emerald-400" : "bg-gray-700"}`}
                style={s.inPlay ? { boxShadow: "0 0 5px #10b981" } : {}} />

              {/* Ticker + company */}
              <div className="flex-shrink-0" style={{ width: 60 }}>
                <div className="font-mono font-bold text-sm text-white">{s.id}</div>
                <div className="text-[10px] text-gray-600 truncate">{s.label}</div>
              </div>

              {/* Confidence bar */}
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] text-gray-500 capitalize">{s.exposureType.replace(/_/g, " ")}</span>
                  <span className="text-[10px] font-mono text-gray-400">{Math.round(s.confidence * 100)}%</span>
                </div>
                <div className="h-1 rounded-full bg-white/8">
                  <div className="h-full rounded-full transition-all"
                    style={{ width: `${s.confidence * 100}%`, background: s.inPlay ? "#10b981" : "#374151" }} />
                </div>
              </div>

              {/* IN PLAY badge */}
              {s.inPlay && (
                <div className="flex items-center gap-1 px-2 py-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 flex-shrink-0">
                  <Zap size={9} className="text-emerald-400" />
                  <span className="text-[10px] text-emerald-400 font-medium">IN PLAY</span>
                </div>
              )}
            </button>
          ))}

          {activeTheme && symbols.length === 0 && (
            <div className="px-5 py-8 text-xs text-gray-700 text-center">No symbols mapped to this theme</div>
          )}
        </div>
      </div>
    </div>
  );
}
