"use client";
// Universe tab — names on the Decifer intelligence map.
// Primary: live radar items from event tape.
// Fallback: last validated universe snapshot (grouped by theme).
// Filter chips by transmission direction.

import { useMemo, useState } from "react";
import { ChevronRight, Clock } from "lucide-react";
import type { MarketNowPayload, RadarItem, UniverseItem } from "@/lib/customerApi";
import { translateTheme } from "@/lib/translate";

type Filter = "all" | "tailwind" | "headwind";

// ── Live radar card (from event tape) ─────────────────────────────────────────

function RadarCard({ item, onSelect }: { item: RadarItem; onSelect: (item: RadarItem) => void }) {
  return (
    <button
      onClick={() => onSelect(item)}
      className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.08)" }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 mb-1">
            <span className="text-base font-black text-slate-100">{item.symbol}</span>
          </div>
          {item.reason_to_watch && (
            <p className="text-xs text-slate-400 leading-relaxed line-clamp-2">
              {item.reason_to_watch}
            </p>
          )}
        </div>
        <ChevronRight size={14} className="text-slate-500 shrink-0 mt-1" />
      </div>
    </button>
  );
}

// ── Universe snapshot card (from last validated universe) ─────────────────────

function UniverseCard({ item }: { item: UniverseItem }) {
  const isHeadwind = item.transmission === "headwind";
  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.07)" }}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <span className="text-base font-black text-slate-100">{item.symbol}</span>
          {item.company_name && (
            <span className="text-[11px] text-slate-500 ml-2">{item.company_name}</span>
          )}
        </div>
        {isHeadwind ? (
          <span className="text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0"
            style={{ background: "rgba(239,68,68,0.1)", color: "#f87171" }}>
            Headwind watch
          </span>
        ) : (
          <span className="text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0"
            style={{ background: "rgba(16,185,129,0.1)", color: "#34d399" }}>
            Tailwind
          </span>
        )}
      </div>
      <p className="text-[11px] text-slate-400 leading-relaxed line-clamp-3">
        {item.why_connected}
      </p>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  onNameSelect: (name: RadarItem) => void;
  onThemeSelect: (themeId: string) => void;
}

export default function UniverseTab({ data, onNameSelect, onThemeSelect }: Props) {
  const [filter, setFilter] = useState<Filter>("all");

  const radar    = data.radar ?? [];
  const snapshot = data.universe_snapshot ?? [];

  const useLiveRadar = radar.length > 0;

  // Group live radar by theme
  const { grouped, ungrouped } = useMemo(() => {
    const map = new Map<string, RadarItem[]>();
    const rest: RadarItem[] = [];
    for (const item of radar) {
      if (item.theme_link) {
        const list = map.get(item.theme_link) ?? [];
        list.push(item);
        map.set(item.theme_link, list);
      } else {
        rest.push(item);
      }
    }
    return { grouped: map, ungrouped: rest };
  }, [radar]);

  // Group snapshot by theme_id, with filter
  const snapshotByTheme = useMemo(() => {
    const filtered = filter === "all"
      ? snapshot
      : snapshot.filter(i => i.transmission === filter);
    const map = new Map<string, UniverseItem[]>();
    for (const item of filtered) {
      const list = map.get(item.theme_id) ?? [];
      list.push(item);
      map.set(item.theme_id, list);
    }
    return map;
  }, [snapshot, filter]);

  const SectionLabel = ({ children, themeId }: { children: React.ReactNode; themeId?: string }) => (
    <div className="flex items-center justify-between mb-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.15em]" style={{ color: "#f97316" }}>
        {children}
      </p>
      {themeId && (
        <button
          onClick={() => onThemeSelect(themeId)}
          className="text-[9px] font-semibold px-2 py-0.5 rounded-full"
          style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
        >
          View theme →
        </button>
      )}
    </div>
  );

  // ── Empty state: no radar, no snapshot ────────────────────────────────────
  if (!useLiveRadar && snapshot.length === 0) {
    return (
      <div className="px-4 pt-12 flex flex-col items-center gap-3 text-center">
        <p className="text-slate-400 text-sm">No intelligence data available.</p>
        <p className="text-xs text-slate-500 leading-relaxed max-w-xs">
          The intelligence pipeline is gathering data. Check back during market hours.
        </p>
      </div>
    );
  }

  // ── Live radar (event tape) ────────────────────────────────────────────────
  if (useLiveRadar) {
    return (
      <div className="px-4 pt-2 pb-8 space-y-5">
        <p className="text-[11px] text-slate-500">
          Names connected to active themes that Decifer is monitoring. Not a recommendation.
        </p>

        {Array.from(grouped.entries()).map(([themeId, items]) => (
          <section key={themeId}>
            <SectionLabel themeId={themeId}>{translateTheme(themeId)}</SectionLabel>
            <div className="space-y-2">
              {items.map((item, i) => (
                <RadarCard key={i} item={item} onSelect={onNameSelect} />
              ))}
            </div>
          </section>
        ))}

        {ungrouped.length > 0 && (
          <section>
            <SectionLabel>On the Radar</SectionLabel>
            <div className="space-y-2">
              {ungrouped.map((item, i) => (
                <RadarCard key={i} item={item} onSelect={onNameSelect} />
              ))}
            </div>
          </section>
        )}

        <p className="text-[10px] text-slate-600 text-center pt-2">
          Market intelligence only. Not financial advice. No trade execution.
        </p>
      </div>
    );
  }

  // ── Fallback: last validated universe snapshot ─────────────────────────────
  const tailwindCount  = snapshot.filter(i => i.transmission !== "headwind").length;
  const headwindCount  = snapshot.filter(i => i.transmission === "headwind").length;

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">

      {/* Fallback notice */}
      <div
        className="rounded-xl px-4 py-3 flex items-center gap-2.5"
        style={{ background: "rgba(249,115,22,0.06)", border: "1px solid rgba(249,115,22,0.2)" }}
      >
        <Clock size={12} style={{ color: "#f97316", flexShrink: 0 }} />
        <div>
          <p className="text-[11px] font-semibold" style={{ color: "#fb923c" }}>
            Showing last validated theme-connected universe
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">
            Live radar is quiet. {snapshot.length} names from the most recent intelligence cycle.
          </p>
        </div>
      </div>

      {/* Filter chips */}
      <div className="flex gap-2">
        {(["all", "tailwind", "headwind"] as Filter[]).map(f => {
          const count = f === "all" ? snapshot.length : f === "tailwind" ? tailwindCount : headwindCount;
          if (f !== "all" && count === 0) return null;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className="px-3 py-1.5 rounded-full text-[10px] font-semibold transition-all active:scale-95"
              style={
                filter === f
                  ? { background: "#f97316", color: "#fff" }
                  : { background: "rgba(255,255,255,0.06)", color: "#94a3b8" }
              }
            >
              {f === "all" ? `All (${count})` : f === "tailwind" ? `Tailwind (${count})` : `Headwind (${count})`}
            </button>
          );
        })}
      </div>

      {/* Grouped by theme */}
      {snapshotByTheme.size > 0 ? (
        Array.from(snapshotByTheme.entries()).map(([themeId, items]) => (
          <section key={themeId}>
            <SectionLabel themeId={themeId}>{translateTheme(themeId)}</SectionLabel>
            <div className="space-y-2">
              {items.map((item, i) => (
                <UniverseCard key={i} item={item} />
              ))}
            </div>
          </section>
        ))
      ) : (
        <div className="text-center py-8">
          <p className="text-slate-500 text-sm">No {filter} names in this cycle.</p>
        </div>
      )}

      <p className="text-[10px] text-slate-600 text-center pt-2">
        Market intelligence only. Not financial advice. No trade execution.
      </p>
    </div>
  );
}
