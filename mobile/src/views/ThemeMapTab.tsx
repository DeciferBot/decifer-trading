"use client";
// Theme Map tab — visual map of active themes with drill-down into sectors and names.
// Click a theme → expand detail panel with sectors + related names.
// Breadcrumb navigation.

import { useMemo } from "react";
import { ChevronRight, X } from "lucide-react";
import type { MarketNowPayload, ThemeItem, SectorItem, RadarItem } from "@/lib/customerApi";
import { translateTheme, themeDescription } from "@/lib/translate";

// ── State badge ───────────────────────────────────────────────────────────────

function StateBadge({ state, signal }: { state?: string; signal?: string }) {
  const s = state ?? "";
  const sig = signal ?? "";

  let style = { bg: "rgba(255,255,255,0.06)", text: "#6b7280", label: "Monitoring" };
  if (s === "activated" || s === "active" || sig === "strengthening")
    style = { bg: "rgba(16,185,129,0.12)", text: "#059669", label: "Active" };
  else if (s === "strengthening")
    style = { bg: "rgba(16,185,129,0.12)", text: "#059669", label: "Building" };
  else if (s === "crowded" || s === "watch")
    style = { bg: "rgba(245,158,11,0.12)", text: "#d97706", label: s === "crowded" ? "Crowded" : "Watch" };
  else if (sig === "weakening")
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
}: {
  theme: ThemeItem;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
      style={{
        background: isSelected ? "rgba(249,115,22,0.1)" : "#131f35",
        border: `1.5px solid ${isSelected ? "#f97316" : "rgba(255,255,255,0.08)"}`,
        boxShadow: isSelected ? "0 2px 8px rgba(249,115,22,0.15)" : "none",
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-slate-100 leading-snug">
            {translateTheme(theme.theme)}
          </p>
          {(() => {
            const sub = theme.from_events?.[0] || themeDescription(theme.theme);
            return sub ? (
              <p className="text-[10px] text-slate-400 mt-1 line-clamp-2">{sub}</p>
            ) : null;
          })()}
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
  onClose,
  onNameSelect,
}: {
  theme: ThemeItem;
  sectors: SectorItem[];
  names: RadarItem[];
  onClose: () => void;
  onNameSelect: (name: RadarItem) => void;
}) {
  const desc = themeDescription(theme.theme);

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{
        border: "1.5px solid #f97316",
        boxShadow: "0 4px 20px rgba(249,115,22,0.12)",
        background: "#131f35",
      }}
    >
      {/* Header */}
      <div
        className="px-4 pt-4 pb-3 flex items-start justify-between gap-2"
        style={{ borderBottom: "1px solid rgba(249,115,22,0.2)" }}
      >
        <div className="flex-1 min-w-0">
          <p
            className="text-[9px] font-bold uppercase tracking-wider mb-1"
            style={{ color: "#f97316" }}
          >
            Theme Detail
          </p>
          <h3 className="text-sm font-bold text-slate-100 leading-snug">
            {translateTheme(theme.theme)}
          </h3>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-full transition-colors"
          style={{ background: "rgba(255,255,255,0.08)" }}
        >
          <X size={13} className="text-slate-400" />
        </button>
      </div>

      <div className="p-4 space-y-4">
        {/* Why it matters */}
        <div>
          <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
            Why This Matters
          </p>
          <p className="text-xs text-slate-300 leading-relaxed">{desc}</p>
        </div>

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
                  <span
                    key={i}
                    className="text-[10px] font-semibold px-2.5 py-1 rounded-full"
                    style={{ background: colors.bg, color: colors.text }}
                  >
                    {s.name.replace(/_/g, " ")}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* Related names on the intelligence map */}
        {names.length > 0 && (
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-2">
              Connected Names on the Map
            </p>
            <div className="space-y-1.5">
              {names.map((n, i) => (
                <button
                  key={i}
                  onClick={() => onNameSelect(n)}
                  className="w-full text-left rounded-xl px-3.5 py-3 flex items-start justify-between gap-2 transition-all active:scale-[0.98]"
                  style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)" }}
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
            <p className="text-[9px] text-slate-500 mt-2">
              Tap a name to learn more. Not a recommendation.
            </p>
          </div>
        )}

        {names.length === 0 && sectors.length === 0 && (
          <p className="text-xs text-slate-500 text-center py-2">
            Monitoring this theme — more detail pending.
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
}

export default function ThemeMapTab({ data, selectedTheme, onThemeSelect, onNameSelect }: Props) {
  const themes: ThemeItem[] = data.themes?.length
    ? data.themes
    : (data.active_themes ?? []).map(t => ({ theme: t, state: "active" }));

  const sectors = data.sectors ?? [];
  const radar   = data.radar ?? [];

  const sorted = useMemo(() => {
    const order: Record<string, number> = {
      activated: 0, active: 0, strengthening: 1,
      crowded: 2, watch: 3, headwind: 4, dormant: 5,
    };
    return [...themes].sort(
      (a, b) => (order[a.state ?? "dormant"] ?? 5) - (order[b.state ?? "dormant"] ?? 5),
    );
  }, [themes]);

  const selectedThemeObj = useMemo(
    () => themes.find(t => t.theme === selectedTheme) ?? null,
    [themes, selectedTheme],
  );

  const relatedSectors = useMemo(() => {
    if (!selectedTheme) return [];
    return sectors.filter(s => s.mood === "tailwind" || s.mood === "headwind").slice(0, 8);
  }, [selectedTheme, sectors]);

  const relatedNames = useMemo(() => {
    if (!selectedTheme) return [];
    return radar.filter(r => r.theme_link === selectedTheme);
  }, [selectedTheme, radar]);

  const grouped = useMemo(() => ({
    active:    sorted.filter(t => ["activated", "active", "strengthening"].includes(t.state ?? "")),
    watching:  sorted.filter(t => ["crowded", "watch"].includes(t.state ?? "")),
    headwinds: sorted.filter(t => t.state === "headwind"),
    quiet:     sorted.filter(t => t.state === "dormant" ||
      !["activated","active","strengthening","crowded","watch","headwind"].includes(t.state ?? "")),
  }), [sorted]);

  const SectionLabel = ({ children }: { children: React.ReactNode }) => (
    <p className="text-[10px] font-bold uppercase tracking-[0.18em] mb-2.5" style={{ color: "#f97316" }}>
      {children}
    </p>
  );

  if (themes.length === 0) {
    return (
      <div className="px-4 pt-12 flex flex-col items-center gap-3 text-center">
        <p className="text-slate-400 text-sm">No active themes right now.</p>
        <p className="text-xs text-slate-500 leading-relaxed max-w-xs">
          The intelligence pipeline is monitoring the market. Themes activate when price drivers and event evidence align — typically during market hours.
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
          onClose={() => onThemeSelect(null)}
          onNameSelect={onNameSelect}
        />
      )}

      {/* Active themes */}
      {grouped.active.length > 0 && (
        <section>
          <SectionLabel>Active Themes</SectionLabel>
          <div className="space-y-2">
            {grouped.active.map((t, i) => (
              <ThemeCard
                key={i}
                theme={t}
                isSelected={selectedTheme === t.theme}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Watching */}
      {grouped.watching.length > 0 && (
        <section>
          <SectionLabel>Watching</SectionLabel>
          <div className="space-y-2">
            {grouped.watching.map((t, i) => (
              <ThemeCard
                key={i}
                theme={t}
                isSelected={selectedTheme === t.theme}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Headwinds */}
      {grouped.headwinds.length > 0 && (
        <section>
          <SectionLabel>Headwinds</SectionLabel>
          <div className="space-y-2">
            {grouped.headwinds.map((t, i) => (
              <ThemeCard
                key={i}
                theme={t}
                isSelected={selectedTheme === t.theme}
                onClick={() => onThemeSelect(selectedTheme === t.theme ? null : t.theme)}
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
                  border: `1px solid ${selectedTheme === t.theme ? "#f97316" : "rgba(255,255,255,0.08)"}`,
                }}
              >
                <p className="text-[11px] font-semibold text-slate-300 leading-snug">
                  {translateTheme(t.theme)}
                </p>
                <p className="text-[9px] text-slate-500 mt-1">Not signalling</p>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
