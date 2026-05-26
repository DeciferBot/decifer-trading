"use client";
// Universe tab — names on the Decifer intelligence map.
// Primary: TTG evidence-gated symbols from /api/intelligence/themes (M12A).
// Overlay: live radar items from event tape (shown above TTG when present).
// Filter chips by route_hint: All | In Focus | On the Radar | ETF Route | Monitor.
// Suppressed symbols (needs_review/proposed) are filtered by the Python evidence
// gate and never appear in TTG API responses.

import { useMemo, useState, useEffect } from "react";
import { ChevronRight, Zap } from "lucide-react";
import type {
  MarketNowPayload,
  RadarItem,
  TtgThemeDetail,
  TtgSymbolCard,
} from "@/lib/customerApi";
import { fetchTtgThemes, fetchTtgThemeDetail } from "@/lib/customerApi";

type Filter = "all" | "in_focus" | "on_radar" | "etf" | "monitor";

function routeFilter(hint: string): Filter {
  const h = hint.toLowerCase();
  if (h === "in focus") return "in_focus";
  if (h === "on the radar") return "on_radar";
  if (h.includes("etf") || h.includes("route")) return "etf";
  if (h.includes("monitor")) return "monitor";
  return "in_focus";
}

// ── Route hint chip ────────────────────────────────────────────────────────────

function RouteChip({ hint }: { hint: string }) {
  const h = hint.toLowerCase();
  let bg = "rgba(249,115,22,0.12)";
  let color = "#fb923c";
  if (h.includes("etf") || h.includes("route")) { bg = "rgba(99,102,241,0.12)"; color = "#818cf8"; }
  else if (h === "on the radar")                 { bg = "rgba(59,130,246,0.12)"; color = "#60a5fa"; }
  else if (h.includes("monitor"))                { bg = "rgba(245,158,11,0.12)"; color = "#fbbf24"; }
  return (
    <span className="text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0"
      style={{ background: bg, color }}>
      {hint}
    </span>
  );
}

// ── Exposure type label ────────────────────────────────────────────────────────

function ExposureLabel({ type }: { type: string }) {
  const map: Record<string, { label: string; color: string }> = {
    direct_beneficiary:       { label: "Direct",        color: "#34d399" },
    supply_chain_beneficiary: { label: "Supply Chain",  color: "#2dd4bf" },
    second_order_beneficiary: { label: "Indirect",      color: "#60a5fa" },
    etf_basket:               { label: "ETF",           color: "#818cf8" },
    pressure_or_negative:     { label: "Pressure Watch",color: "#f87171" },
  };
  const e = map[type] ?? { label: type.replace(/_/g, " "), color: "#94a3b8" };
  return <span className="text-[9px] font-medium" style={{ color: e.color }}>{e.label}</span>;
}

// ── TTG symbol card ────────────────────────────────────────────────────────────

function TtgCard({ card, onSelect }: { card: TtgSymbolCard; onSelect?: (card: TtgSymbolCard) => void }) {
  const isPressure = card.exposure_type === "pressure_or_negative";
  const path = card.reason_path;
  // Abbreviate long chains: first + "..." + last when > 3 items
  const pathDisplay = path.length > 3
    ? `${path[0]} → ... → ${path[path.length - 1]}`
    : path.join(" → ");

  const inner = (
    <>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-base font-black text-slate-100">{card.symbol}</span>
            {card.label && (
              <span className="text-[11px] text-slate-500 truncate max-w-[180px]">{card.label}</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <ExposureLabel type={card.exposure_type} />
            {card.driver_active && (
              <span className="text-[9px] font-bold" style={{ color: "#34d399" }}>
                ● Driver Active
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <RouteChip hint={card.route_hint} />
          {onSelect && <ChevronRight size={12} className="text-slate-600" />}
        </div>
      </div>

      <p className="text-[11px] text-slate-300 leading-relaxed line-clamp-3">
        {card.reason_to_care}
      </p>

      {path.length > 1 && (
        <p className="text-[9px] text-slate-600 mt-2 leading-relaxed truncate">
          {pathDisplay}
        </p>
      )}

      {card.risk_note && (
        <p className="text-[9px] text-amber-600 mt-1.5 leading-relaxed line-clamp-2">
          ⚠ {card.risk_note}
        </p>
      )}
    </>
  );

  if (onSelect) {
    return (
      <button
        onClick={() => onSelect(card)}
        className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
        style={{
          background: "#131f35",
          border: `1px solid ${isPressure ? "rgba(239,68,68,0.15)" : "rgba(255,255,255,0.07)"}`,
        }}
      >
        {inner}
      </button>
    );
  }

  return (
    <div
      className="rounded-2xl p-4"
      style={{
        background: "#131f35",
        border: `1px solid ${isPressure ? "rgba(239,68,68,0.15)" : "rgba(255,255,255,0.07)"}`,
      }}
    >
      {inner}
    </div>
  );
}

// ── Live radar card ────────────────────────────────────────────────────────────

function RadarCard({ item, onSelect }: { item: RadarItem; onSelect: (item: RadarItem) => void }) {
  return (
    <button
      onClick={() => onSelect(item)}
      className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.08)" }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <span className="text-base font-black text-slate-100">{item.symbol}</span>
          {item.reason_to_watch && (
            <p className="text-xs text-slate-400 leading-relaxed mt-1 line-clamp-2">
              {item.reason_to_watch}
            </p>
          )}
        </div>
        <ChevronRight size={14} className="text-slate-500 shrink-0 mt-1" />
      </div>
    </button>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  onNameSelect: (name: RadarItem) => void;
  onThemeSelect: (themeId: string) => void;
  onSymbolSelect?: (card: TtgSymbolCard) => void;
}

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all",      label: "All" },
  { id: "in_focus", label: "In Focus" },
  { id: "on_radar", label: "On the Radar" },
  { id: "etf",      label: "ETF Route" },
  { id: "monitor",  label: "Monitor" },
];

export default function UniverseTab({ data, onNameSelect, onThemeSelect, onSymbolSelect }: Props) {
  const [ttgData, setTtgData]   = useState<TtgThemeDetail[]>([]);
  const [loading, setLoading]   = useState(true);
  const [filter, setFilter]     = useState<Filter>("all");

  const radar = data.radar ?? [];

  useEffect(() => {
    fetchTtgThemes()
      .then(themes =>
        Promise.allSettled(themes.map(t => fetchTtgThemeDetail(t.theme_id)))
      )
      .then(results => {
        const loaded = results
          .filter(r => r.status === "fulfilled" && r.value !== null)
          .map(r => (r as PromiseFulfilledResult<TtgThemeDetail | null>).value as TtgThemeDetail);
        setTtgData(loaded);
      })
      .catch(() => setTtgData([]))
      .finally(() => setLoading(false));
  }, []);

  const allSymbols = useMemo(() => ttgData.flatMap(t => t.symbols), [ttgData]);

  const counts = useMemo(() => ({
    all:      allSymbols.length,
    in_focus: allSymbols.filter(s => routeFilter(s.route_hint) === "in_focus").length,
    on_radar: allSymbols.filter(s => routeFilter(s.route_hint) === "on_radar").length,
    etf:      allSymbols.filter(s => routeFilter(s.route_hint) === "etf").length,
    monitor:  allSymbols.filter(s => routeFilter(s.route_hint) === "monitor").length,
  }), [allSymbols]);

  // Group filtered symbols by TTG theme, omitting empty theme groups
  const filteredByTheme = useMemo(() => {
    return ttgData
      .map(theme => ({
        theme,
        symbols: filter === "all"
          ? theme.symbols
          : theme.symbols.filter(s => routeFilter(s.route_hint) === filter),
      }))
      .filter(({ symbols }) => symbols.length > 0);
  }, [ttgData, filter]);

  // ── Loading skeleton
  if (loading) {
    return (
      <div className="px-4 pt-8 space-y-3">
        {[1, 2, 3].map(i => (
          <div key={i} className="rounded-2xl h-24 animate-pulse"
            style={{ background: "rgba(255,255,255,0.04)" }} />
        ))}
        <p className="text-[10px] text-slate-600 text-center pt-2">
          Loading structural intelligence universe…
        </p>
      </div>
    );
  }

  // ── Error / empty state
  if (ttgData.length === 0 && radar.length === 0) {
    return (
      <div className="px-4 pt-12 flex flex-col items-center gap-3 text-center">
        <p className="text-slate-400 text-sm">No structural context available right now.</p>
        <p className="text-xs text-slate-500 leading-relaxed max-w-xs">
          Theme context remains accessible in the Theme Map. Connected names will appear here as the intelligence layer refreshes.
        </p>
      </div>
    );
  }

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">

      {/* Live radar overlay (from event tape) */}
      {radar.length > 0 && (
        <section>
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-2.5 flex items-center gap-1.5"
            style={{ color: "#f97316" }}>
            <Zap size={9} />
            Live Intelligence — On the Radar
          </p>
          <div className="space-y-2">
            {radar.map((item, i) => (
              <RadarCard key={i} item={item} onSelect={onNameSelect} />
            ))}
          </div>
        </section>
      )}

      {/* TTG structural universe */}
      {ttgData.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <p className="text-[10px] font-bold uppercase tracking-[0.15em]" style={{ color: "#f97316" }}>
              {radar.length > 0 ? "Structural Intelligence Universe" : "Intelligence Universe"}
            </p>
            <p className="text-[9px] text-slate-600">{allSymbols.length} names</p>
          </div>

          <p className="text-[11px] text-slate-500 -mt-3">
            Structurally connected names. Evidence-verified. Not a recommendation.
          </p>

          {/* Filter chips */}
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.filter(f => f.id === "all" || counts[f.id] > 0).map(f => (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className="px-3 py-1.5 rounded-full text-[10px] font-semibold transition-all active:scale-95"
                style={
                  filter === f.id
                    ? { background: "#f97316", color: "#fff" }
                    : { background: "rgba(255,255,255,0.06)", color: "#94a3b8" }
                }
              >
                {f.label}{f.id !== "all" && counts[f.id] > 0 ? ` (${counts[f.id]})` : ""}
              </button>
            ))}
          </div>

          {/* Theme sections */}
          {filteredByTheme.length > 0 ? (
            <div className="space-y-6">
              {filteredByTheme.map(({ theme, symbols }) => (
                <section key={theme.theme_id}>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-[10px] font-bold uppercase tracking-[0.15em]"
                        style={{ color: "#f97316" }}>
                        {theme.label}
                      </p>
                      {theme.driver_active && (
                        <span
                          className="text-[8px] font-bold px-1.5 py-0.5 rounded-full"
                          style={{ background: "rgba(16,185,129,0.12)", color: "#34d399" }}
                        >
                          Driver Active
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => onThemeSelect(theme.theme_id)}
                      className="text-[9px] font-semibold px-2 py-0.5 rounded-full shrink-0"
                      style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
                    >
                      Theme Map →
                    </button>
                  </div>
                  <div className="space-y-2">
                    {symbols.map((card, i) => (
                      <TtgCard key={i} card={card} onSelect={onSymbolSelect} />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <div
              className="rounded-xl px-6 py-8 text-center"
              style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
            >
              <p className="text-sm text-slate-400">No {filter.replace("_", " ")} names right now.</p>
              <p className="text-xs text-slate-500 mt-1.5">
                Switch to &ldquo;All&rdquo; to see all {allSymbols.length} names.
              </p>
            </div>
          )}
        </>
      )}

      <p className="text-[10px] text-slate-600 text-center pt-2">
        Market intelligence only. Not financial advice. No trade execution.
      </p>
    </div>
  );
}
