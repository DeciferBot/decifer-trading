"use client";
// Theme Map tab — visual map of active themes with drill-down into detail.
// Groups: Active Now | Building | Weakening | Not Currently Signalling
// Theme detail includes: why it matters, drivers, connected sectors/names, what would weaken it.

import { useMemo, useState, useEffect } from "react";
import { ChevronRight, X, ArrowRight } from "lucide-react";
import type {
  MarketNowPayload, ThemeItem, SectorItem, RadarItem, UniverseItem,
  TtgSymbolCard,
} from "@/lib/customerApi";
import { fetchTtgThemeDetail } from "@/lib/customerApi";
import { translateTheme, themeDescription, themeInvalidation } from "@/lib/translate";
import { getTtgIdForMarketNow, getCrosswalkByMarketNow, type CrosswalkEntry } from "@/lib/themeCrosswalk";

// ── State badge ───────────────────────────────────────────────────────────────

function StateBadge({ state, signal }: { state?: string; signal?: string }) {
  const s = state ?? "";
  const sig = signal ?? "";

  let style = { bg: "rgba(255,255,255,0.06)", text: "#6b7280", label: "Monitoring" };
  if (s === "activated" || s === "active" || sig === "strengthening")
    style = { bg: "rgba(16,185,129,0.12)", text: "#059669", label: "Active" };
  else if (s === "strengthening")
    style = { bg: "rgba(59,130,246,0.12)", text: "#3b82f6", label: "Building" };
  else if (s === "crowded" || s === "watch")
    style = { bg: "rgba(245,158,11,0.12)", text: "#d97706", label: s === "crowded" ? "Crowded" : "Watch" };
  else if (sig === "weakening" || s === "weakening")
    style = { bg: "rgba(245,158,11,0.12)", text: "#d97706", label: "Weakening" };
  else if (s === "headwind")
    style = { bg: "rgba(239,68,68,0.12)", text: "#dc2626", label: "Headwind" };
  else if (s === "dormant")
    style = { bg: "rgba(255,255,255,0.04)", text: "#475569", label: "Quiet" };

  return (
    <span
      className="text-[9px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: style.bg, color: style.text }}
    >
      {style.label}
    </span>
  );
}

// ── Theme card ─────────────────────────────────────────────────────────────────

function ThemeCard({
  theme,
  isSelected,
  onClick,
  connectedSectors,
  connectedNames,
}: {
  theme: ThemeItem;
  isSelected: boolean;
  onClick: () => void;
  connectedSectors: number;
  connectedNames: number;
}) {
  const sub = theme.from_events?.[0] || themeDescription(theme.theme);

  return (
    <button
      onClick={onClick}
      className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
      style={{
        background: isSelected ? "rgba(249,115,22,0.1)" : "#131f35",
        border: `1.5px solid ${isSelected ? "#f97316" : "rgba(255,255,255,0.07)"}`,
        boxShadow: isSelected ? "0 2px 12px rgba(249,115,22,0.12)" : "none",
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-slate-100 leading-snug">
            {translateTheme(theme.theme)}
          </p>
          {sub && (
            <p className="text-[10px] text-slate-400 mt-1 line-clamp-2 leading-relaxed">{sub}</p>
          )}
          {/* Connected counts */}
          {(connectedSectors > 0 || connectedNames > 0) && (
            <div className="flex gap-2.5 mt-2">
              {connectedSectors > 0 && (
                <span className="text-[9px] text-slate-500">
                  {connectedSectors} sector{connectedSectors !== 1 ? "s" : ""}
                </span>
              )}
              {connectedNames > 0 && (
                <span className="text-[9px] text-slate-500">
                  {connectedNames} name{connectedNames !== 1 ? "s" : ""}
                </span>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 mt-0.5">
          <StateBadge state={theme.state} signal={theme.event_signal} />
          <ChevronRight
            size={14}
            style={{ color: isSelected ? "#f97316" : "#475569" }}
          />
        </div>
      </div>
    </button>
  );
}

// ── Theme detail panel ─────────────────────────────────────────────────────────

function ThemeDetail({
  theme,
  sectors,
  names,
  universeNames,
  ttgSymbols,
  crosswalkEntry,
  onClose,
  onNameSelect,
  onGoToUniverse,
}: {
  theme: ThemeItem;
  sectors: SectorItem[];
  names: RadarItem[];
  universeNames: UniverseItem[];
  ttgSymbols: TtgSymbolCard[];
  crosswalkEntry: CrosswalkEntry | null;
  onClose: () => void;
  onNameSelect: (name: RadarItem) => void;
  onGoToUniverse?: () => void;
}) {
  const desc = themeDescription(theme.theme);
  const invalidation = themeInvalidation(theme.theme);
  const hasEventEvidence = (theme.from_events?.length ?? 0) > 0;

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{
        border: "1.5px solid #f97316",
        boxShadow: "0 4px 20px rgba(249,115,22,0.1)",
        background: "#131f35",
      }}
    >
      {/* Header */}
      <div
        className="px-4 pt-4 pb-3 flex items-start justify-between gap-2"
        style={{ borderBottom: "1px solid rgba(249,115,22,0.15)" }}
      >
        <div className="flex-1 min-w-0">
          <p className="text-[9px] font-bold uppercase tracking-wider mb-1" style={{ color: "#f97316" }}>
            Theme Detail
          </p>
          <h3 className="text-sm font-bold text-slate-100 leading-snug">
            {translateTheme(theme.theme)}
          </h3>
          <div className="mt-1.5">
            <StateBadge state={theme.state} signal={theme.event_signal} />
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-full transition-colors shrink-0"
          style={{ background: "rgba(255,255,255,0.08)" }}
        >
          <X size={13} className="text-slate-400" />
        </button>
      </div>

      <div className="p-4 space-y-4">

        {/* Event evidence (if present) */}
        {hasEventEvidence && (
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
              Latest Evidence
            </p>
            <ul className="space-y-1.5">
              {(theme.from_events ?? []).map((ev, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="w-1.5 h-1.5 rounded-full mt-1 shrink-0" style={{ background: "#f97316" }} />
                  <p className="text-xs text-slate-300 leading-relaxed">{ev}</p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Why this matters */}
        <div>
          <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
            {hasEventEvidence ? "Structural Context" : "Why This Matters"}
          </p>
          <p className="text-xs text-slate-300 leading-relaxed">{desc}</p>
          {!hasEventEvidence && (
            <p className="text-[9px] text-slate-600 mt-1.5">Structural context — no fresh event evidence this cycle.</p>
          )}
        </div>

        {/* Structural theme crosswalk */}
        {crosswalkEntry && (
          <div
            className="rounded-xl px-3.5 py-3"
            style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.15)" }}
          >
            <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#f97316" }}>
              Structural Theme Connection
            </p>
            <div className="flex items-center gap-1.5 flex-wrap mb-1.5">
              <span className="text-[11px] text-slate-300">{crosswalkEntry.marketNowLabel}</span>
              <ArrowRight size={10} className="text-slate-600 shrink-0" />
              <span
                className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
                style={{ background: "rgba(249,115,22,0.12)", color: "#fb923c" }}
              >
                {crosswalkEntry.ttgPrimaryLabel}
              </span>
            </div>
            <p className="text-[10px] text-slate-500 leading-relaxed">{crosswalkEntry.relationship}</p>
            {onGoToUniverse && ttgSymbols.length > 0 && (
              <button
                onClick={onGoToUniverse}
                className="mt-2.5 text-[10px] font-semibold flex items-center gap-1"
                style={{ color: "#fb923c" }}
              >
                See {ttgSymbols.length} connected name{ttgSymbols.length !== 1 ? "s" : ""} in Universe
                <ArrowRight size={10} />
              </button>
            )}
          </div>
        )}

        {/* Connected sectors */}
        {sectors.length > 0 && (
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-2">
              Connected Sectors
            </p>
            <div className="flex flex-wrap gap-1.5">
              {sectors.map((s, i) => {
                const colors =
                  s.mood === "tailwind"
                    ? { bg: "rgba(16,185,129,0.1)", text: "#34d399" }
                    : s.mood === "headwind"
                      ? { bg: "rgba(239,68,68,0.1)", text: "#f87171" }
                      : { bg: "rgba(249,115,22,0.1)", text: "#fb923c" };
                return (
                  <span key={i} className="text-[10px] font-semibold px-2.5 py-1 rounded-full"
                    style={{ background: colors.bg, color: colors.text }}>
                    {s.name.replace(/_/g, " ")}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* Live radar names (from event tape) */}
        {names.length > 0 && (
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-2">
              Live Radar — Connected Names
            </p>
            <div className="space-y-1.5">
              {names.map((n, i) => (
                <button
                  key={i}
                  onClick={() => onNameSelect(n)}
                  className="w-full text-left rounded-xl px-3.5 py-3 flex items-start justify-between gap-2 transition-all active:scale-[0.98]"
                  style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.07)" }}
                >
                  <div className="min-w-0 flex-1">
                    <span className="text-[13px] font-black text-slate-100">{n.symbol}</span>
                    {n.reason_to_watch && (
                      <p className="text-[10px] text-slate-400 mt-0.5 line-clamp-2">
                        {n.reason_to_watch}
                      </p>
                    )}
                  </div>
                  <ChevronRight size={12} className="text-slate-500 shrink-0 mt-0.5" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* TTG evidence-gated names (preferred over universe snapshot) */}
        {names.length === 0 && ttgSymbols.length > 0 && (
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-2">
              Theme-Connected Names
            </p>
            <p className="text-[9px] text-slate-600 mb-2">Structurally verified connections.</p>
            <div className="space-y-1.5">
              {ttgSymbols.slice(0, 6).map((s, i) => (
                <div
                  key={i}
                  className="rounded-xl px-3.5 py-2.5"
                  style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)" }}
                >
                  <div className="flex items-baseline gap-2">
                    <p className="text-[13px] font-black text-slate-100">{s.symbol}</p>
                    {s.label && <p className="text-[10px] text-slate-500 truncate">{s.label}</p>}
                  </div>
                  <p className="text-[10px] text-slate-400 mt-0.5 line-clamp-2 leading-relaxed">
                    {s.reason_to_care}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Universe snapshot names (fallback when no live radar and no TTG match) */}
        {names.length === 0 && ttgSymbols.length === 0 && universeNames.length > 0 && (
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-2">
              Theme-Connected Names
            </p>
            <p className="text-[9px] text-slate-600 mb-2">Structural context — no fresh event evidence this cycle.</p>
            <div className="space-y-1.5">
              {universeNames.slice(0, 5).map((n, i) => (
                <div
                  key={i}
                  className="rounded-xl px-3.5 py-2.5"
                  style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)" }}
                >
                  <p className="text-[13px] font-black text-slate-100">{n.symbol}</p>
                  {n.company_name && (
                    <p className="text-[10px] text-slate-500">{n.company_name}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* What would weaken this theme */}
        <div
          className="rounded-xl px-3.5 py-3"
          style={{ background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.15)" }}
        >
          <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#ef4444" }}>
            What Would Weaken This Theme
          </p>
          <p className="text-xs text-slate-400 leading-relaxed">{invalidation}</p>
        </div>

        {names.length === 0 && ttgSymbols.length === 0 && universeNames.length === 0 && sectors.length === 0 && (
          <p className="text-xs text-slate-500 text-center py-1">
            Monitoring this theme — connected names will appear as conditions develop.
          </p>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  selectedTheme: string | null;
  onThemeSelect: (themeId: string | null) => void;
  onNameSelect: (name: RadarItem) => void;
  onGoToUniverseTheme?: (ttgThemeId: string) => void;
}

export default function ThemeMapTab({ data, selectedTheme, onThemeSelect, onNameSelect, onGoToUniverseTheme }: Props) {
  const [ttgSymbols, setTtgSymbols] = useState<TtgSymbolCard[]>([]);

  // Fetch TTG data when user drills into a theme detail.
  // Use crosswalk to map market_now ID → TTG structural ID before fetching,
  // since most market_now IDs don't match TTG IDs directly.
  useEffect(() => {
    if (!selectedTheme) { setTtgSymbols([]); return; }
    const ttgId = getTtgIdForMarketNow(selectedTheme) ?? selectedTheme;
    fetchTtgThemeDetail(ttgId)
      .then(d => setTtgSymbols(d?.symbols ?? []))
      .catch(() => setTtgSymbols([]));
  }, [selectedTheme]);

  const themes: ThemeItem[] = data.themes?.length
    ? data.themes
    : (data.active_themes ?? []).map(t => ({ theme: t, state: "active" }));

  const sectors        = data.sectors ?? [];
  const radar          = data.radar ?? [];
  const universeSnap   = data.universe_snapshot ?? [];

  const sorted = useMemo(() => {
    const order: Record<string, number> = {
      activated: 0, active: 0, strengthening: 1, crowded: 2, watch: 3,
      weakening: 4, headwind: 5, dormant: 6,
    };
    return [...themes].sort(
      (a, b) => (order[a.state ?? "dormant"] ?? 6) - (order[b.state ?? "dormant"] ?? 6),
    );
  }, [themes]);

  const selectedThemeObj = useMemo(
    () => themes.find(t => t.theme === selectedTheme) ?? null,
    [themes, selectedTheme],
  );

  // Connected sectors for selected theme (use all sectors as approximation)
  const relatedSectors = useMemo(() => {
    if (!selectedTheme) return [];
    return sectors.filter(s => s.mood === "tailwind" || s.mood === "headwind").slice(0, 8);
  }, [selectedTheme, sectors]);

  const relatedNames = useMemo(() => {
    if (!selectedTheme) return [];
    return radar.filter(r => r.theme_link === selectedTheme);
  }, [selectedTheme, radar]);

  const relatedUniverseNames = useMemo(() => {
    if (!selectedTheme) return [];
    return universeSnap.filter(u => u.theme_id === selectedTheme);
  }, [selectedTheme, universeSnap]);

  // Connected counts for each theme card
  const sectorsByTheme = useMemo(() => {
    return sectors.filter(s => s.mood === "tailwind" || s.mood === "headwind").length;
  }, [sectors]);

  const namesByTheme = useMemo(() => {
    const map = new Map<string, number>();
    for (const r of radar) {
      if (r.theme_link) map.set(r.theme_link, (map.get(r.theme_link) ?? 0) + 1);
    }
    for (const u of universeSnap) {
      if (!map.has(u.theme_id)) map.set(u.theme_id, 0);
      // Only count universe names if no live radar for this theme
      const hasRadar = radar.some(r => r.theme_link === u.theme_id);
      if (!hasRadar) map.set(u.theme_id, (map.get(u.theme_id) ?? 0) + 1);
    }
    return map;
  }, [radar, universeSnap]);

  const grouped = useMemo(() => ({
    active:    sorted.filter(t => ["activated", "active"].includes(t.state ?? "")),
    building:  sorted.filter(t => t.state === "strengthening"),
    weakening: sorted.filter(t => t.state === "weakening" || t.state === "headwind"),
    quiet:     sorted.filter(t =>
      !["activated", "active", "strengthening", "weakening", "headwind"].includes(t.state ?? "")
    ),
  }), [sorted]);

  const SectionLabel = ({ children }: { children: React.ReactNode }) => (
    <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-2.5" style={{ color: "#f97316" }}>
      {children}
    </p>
  );

  if (themes.length === 0) {
    return (
      <div className="px-4 pt-12 flex flex-col items-center gap-3 text-center">
        <p className="text-slate-400 text-sm">No active themes right now.</p>
        <p className="text-xs text-slate-500 leading-relaxed max-w-xs">
          Structural themes appear when market forces and supporting evidence align —
          typically during market hours.
        </p>
      </div>
    );
  }

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">

      {/* Breadcrumb */}
      {selectedTheme && (
        <div className="flex items-center gap-1.5 text-[10px]">
          <button
            onClick={() => onThemeSelect(null)}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            Theme Map
          </button>
          <ChevronRight size={10} className="text-slate-600" />
          <span style={{ color: "#f97316" }}>{translateTheme(selectedTheme)}</span>
        </div>
      )}

      {/* Theme detail panel */}
      {selectedThemeObj && (
        <ThemeDetail
          theme={selectedThemeObj}
          sectors={relatedSectors}
          names={relatedNames}
          universeNames={relatedUniverseNames}
          ttgSymbols={ttgSymbols}
          crosswalkEntry={getCrosswalkByMarketNow(selectedThemeObj.theme)}
          onClose={() => onThemeSelect(null)}
          onNameSelect={onNameSelect}
          onGoToUniverse={
            onGoToUniverseTheme && getTtgIdForMarketNow(selectedThemeObj.theme)
              ? () => onGoToUniverseTheme(getTtgIdForMarketNow(selectedThemeObj.theme)!)
              : undefined
          }
        />
      )}

      {/* Active themes */}
      {grouped.active.length > 0 && (
        <section>
          <SectionLabel>Active Now</SectionLabel>
          <div className="space-y-2">
            {grouped.active.map((t, i) => (
              <ThemeCard
                key={i}
                theme={t}
                isSelected={selectedTheme === t.theme}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
                connectedSectors={sectors.length > 0 ? sectorsByTheme : 0}
                connectedNames={namesByTheme.get(t.theme) ?? 0}
              />
            ))}
          </div>
        </section>
      )}

      {/* Building */}
      {grouped.building.length > 0 && (
        <section>
          <SectionLabel>Building Momentum</SectionLabel>
          <div className="space-y-2">
            {grouped.building.map((t, i) => (
              <ThemeCard
                key={i}
                theme={t}
                isSelected={selectedTheme === t.theme}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
                connectedSectors={0}
                connectedNames={namesByTheme.get(t.theme) ?? 0}
              />
            ))}
          </div>
        </section>
      )}

      {/* Weakening */}
      {grouped.weakening.length > 0 && (
        <section>
          <SectionLabel>Weakening / Headwinds</SectionLabel>
          <div className="space-y-2">
            {grouped.weakening.map((t, i) => (
              <ThemeCard
                key={i}
                theme={t}
                isSelected={selectedTheme === t.theme}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
                connectedSectors={0}
                connectedNames={namesByTheme.get(t.theme) ?? 0}
              />
            ))}
          </div>
        </section>
      )}

      {/* Not Currently Signalling */}
      {grouped.quiet.length > 0 && (
        <section>
          <SectionLabel>Not Currently Signalling</SectionLabel>
          <div className="grid grid-cols-2 gap-2">
            {grouped.quiet.map((t, i) => (
              <button
                key={i}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
                className="rounded-xl p-3 text-left transition-all active:scale-[0.98]"
                style={{
                  background: selectedTheme === t.theme ? "rgba(249,115,22,0.1)" : "#131f35",
                  border: `1px solid ${selectedTheme === t.theme ? "#f97316" : "rgba(255,255,255,0.07)"}`,
                }}
              >
                <p className="text-[11px] font-semibold text-slate-300 leading-snug">
                  {translateTheme(t.theme)}
                </p>
                <p className="text-[9px] text-slate-500 mt-1">
                  {namesByTheme.get(t.theme) ? `${namesByTheme.get(t.theme)} names` : "Not signalling"}
                </p>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
