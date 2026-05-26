"use client";
// Universe tab — names on the Decifer intelligence map.
// Grouped by theme. Tap a name to see why it's on the map.
// No trading language.

import { useMemo } from "react";
import { ChevronRight } from "lucide-react";
import type { MarketNowPayload, RadarItem } from "@/lib/customerApi";
import { translateTheme } from "@/lib/translate";

// ── Name card ─────────────────────────────────────────────────────────────────

function NameCard({ item, onSelect }: { item: RadarItem; onSelect: (item: RadarItem) => void }) {
  return (
    <button
      onClick={() => onSelect(item)}
      className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
      style={{
        background: "#131f35",
        border: "1px solid rgba(255,255,255,0.08)",
      }}
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

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  onNameSelect: (name: RadarItem) => void;
  onThemeSelect: (themeId: string) => void;
}

export default function UniverseTab({ data, onNameSelect, onThemeSelect }: Props) {
  const radar = data.radar ?? [];

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

  const SectionLabel = ({
    children,
    themeId,
  }: {
    children: React.ReactNode;
    themeId?: string;
  }) => (
    <div className="flex items-center justify-between mb-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.18em]" style={{ color: "#f97316" }}>
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

  if (radar.length === 0) {
    return (
      <div className="px-4 pt-12 flex flex-col items-center gap-3 text-center">
        <p className="text-slate-400 text-sm">No names on the intelligence map right now.</p>
        <p className="text-xs text-slate-500 leading-relaxed max-w-xs">
          Names appear when active themes identify connected companies. Check the Theme Map tab as markets open.
        </p>
      </div>
    );
  }

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">
      <p className="text-[11px] text-slate-500">
        Names connected to active themes that Decifer is monitoring. Not a recommendation.
      </p>

      {/* Grouped by theme */}
      {Array.from(grouped.entries()).map(([themeId, items]) => (
        <section key={themeId}>
          <SectionLabel themeId={themeId}>{translateTheme(themeId)}</SectionLabel>
          <div className="space-y-2">
            {items.map((item, i) => (
              <NameCard key={i} item={item} onSelect={onNameSelect} />
            ))}
          </div>
        </section>
      ))}

      {/* Ungrouped */}
      {ungrouped.length > 0 && (
        <section>
          <SectionLabel>On the Radar</SectionLabel>
          <div className="space-y-2">
            {ungrouped.map((item, i) => (
              <NameCard key={i} item={item} onSelect={onNameSelect} />
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
