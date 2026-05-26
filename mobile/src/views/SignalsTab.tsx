"use client";
// Signals tab — evidence behind the market view.
// Filter chips: All | Active | Weakening | Conflicting | Quiet
// Signal cards expand to show evidence, why it matters, and a link to Theme Map.

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { MarketNowPayload, ThemeItem } from "@/lib/customerApi";
import { translateTheme, themeDescription } from "@/lib/translate";

type Filter = "all" | "active" | "weakening" | "conflicting" | "quiet";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all",         label: "All"         },
  { id: "active",      label: "Active"      },
  { id: "weakening",   label: "Weakening"   },
  { id: "conflicting", label: "Conflicting" },
  { id: "quiet",       label: "Quiet"       },
];

function matchesFilter(theme: ThemeItem, filter: Filter): boolean {
  if (filter === "all") return true;
  const s   = theme.state ?? "";
  const sig = theme.event_signal ?? "";
  if (filter === "active")     return s === "activated" || s === "active" || sig === "strengthening";
  if (filter === "weakening")  return s === "crowded" || s === "watch" || sig === "weakening";
  if (filter === "quiet")      return s === "dormant";
  return true;
}

function confidenceInfo(state?: string, signal?: string): { label: string; color: string } {
  const s   = state ?? "";
  const sig = signal ?? "";
  if (s === "activated" || s === "active" || sig === "strengthening")
    return { label: "Strengthening",            color: "#34d399" };
  if (s === "crowded")
    return { label: "Crowded — watch for shift", color: "#fbbf24" };
  if (sig === "weakening" || s === "watch")
    return { label: "Weakening",                color: "#fbbf24" };
  if (s === "headwind")
    return { label: "Headwind signal",          color: "#f87171" };
  if (s === "dormant")
    return { label: "Not currently signalling", color: "#64748b" };
  return { label: "Monitoring",               color: "#64748b" };
}

// ── Signal card ───────────────────────────────────────────────────────────────

function SignalCard({
  theme,
  onThemeSelect,
}: {
  theme: ThemeItem;
  onThemeSelect: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const conf = confidenceInfo(theme.state, theme.event_signal);
  const desc = themeDescription(theme.theme);

  return (
    <div
      className="rounded-2xl overflow-hidden transition-shadow"
      style={{
        background: "#131f35",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: expanded ? "0 2px 12px rgba(0,0,0,0.3)" : "none",
      }}
    >
      <button
        onClick={() => setExpanded(o => !o)}
        className="w-full px-4 py-3.5 text-left flex items-start justify-between gap-2"
      >
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-slate-100">
            {translateTheme(theme.theme)}
          </p>
          <p className="text-[10px] mt-0.5" style={{ color: conf.color }}>
            {conf.label}
          </p>
        </div>
        <div className="shrink-0 mt-0.5">
          {expanded
            ? <ChevronUp size={14} className="text-slate-400" />
            : <ChevronDown size={14} className="text-slate-400" />}
        </div>
      </button>

      {expanded && (
        <div
          className="px-4 pb-4 space-y-3.5 pt-3"
          style={{ borderTop: "1px solid rgba(255,255,255,0.08)" }}
        >

          {/* Evidence */}
          {theme.from_events && theme.from_events.length > 0 && (
            <div>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                Evidence
              </p>
              <ul className="space-y-1.5">
                {theme.from_events.map((ev, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span
                      className="w-1.5 h-1.5 rounded-full mt-1 shrink-0"
                      style={{ background: "#f97316" }}
                    />
                    <p className="text-xs text-slate-300 leading-relaxed">{ev}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Why this matters */}
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
              Why This Matters
            </p>
            <p className="text-xs text-slate-300 leading-relaxed">{desc}</p>
          </div>

          {/* Explore button */}
          <button
            onClick={() => onThemeSelect(theme.theme)}
            className="text-[10px] font-semibold px-3 py-1.5 rounded-full transition-all active:scale-95"
            style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
          >
            Explore in Theme Map →
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  onThemeSelect: (themeId: string) => void;
}

export default function SignalsTab({ data, onThemeSelect }: Props) {
  const [filter, setFilter] = useState<Filter>("all");

  const themes: ThemeItem[] = data.themes?.length
    ? data.themes
    : (data.active_themes ?? []).map(t => ({ theme: t, state: "active" }));

  const conflicts = data.known_conflicts ?? [];
  const filtered  = themes.filter(t => matchesFilter(t, filter));

  const SectionLabel = ({ children }: { children: React.ReactNode }) => (
    <p className="text-[10px] font-bold uppercase tracking-[0.18em] mb-3" style={{ color: "#f97316" }}>
      {children}
    </p>
  );

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">

      {/* Filter chips */}
      <section>
        <SectionLabel>Filter Signals</SectionLabel>
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map(f => (
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
              {f.label}
            </button>
          ))}
        </div>
      </section>

      {/* Conflicting evidence */}
      {(filter === "all" || filter === "conflicting") && conflicts.length > 0 && (
        <section>
          <SectionLabel>Conflicting Evidence</SectionLabel>
          <div className="space-y-2">
            {conflicts.map((c, i) => (
              <div
                key={i}
                className="rounded-xl px-3.5 py-3"
                style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.25)" }}
              >
                <p className="text-xs text-amber-300 leading-relaxed">{c}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Signal cards */}
      {filter !== "conflicting" && (
        <section>
          {filter !== "all" && <SectionLabel>{filter.charAt(0).toUpperCase() + filter.slice(1)} Signals</SectionLabel>}
          {filtered.length > 0 ? (
            <div className="space-y-2">
              {filtered.map((t, i) => (
                <SignalCard key={i} theme={t} onThemeSelect={onThemeSelect} />
              ))}
            </div>
          ) : (
            <div
              className="rounded-xl px-6 py-10 text-center space-y-2"
              style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
            >
              <p className="text-sm text-slate-400">
                {filter === "all"
                  ? "No signals right now."
                  : `No ${filter} signals right now.`}
              </p>
              {filter === "all" && (
                <p className="text-xs text-slate-500 leading-relaxed">
                  The intelligence pipeline is monitoring the market. Signals appear as themes activate — typically during market hours.
                </p>
              )}
            </div>
          )}
        </section>
      )}

      {filter === "conflicting" && conflicts.length === 0 && (
        <div
          className="rounded-xl p-8 text-center"
          style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
        >
          <p className="text-sm text-slate-500">No conflicting signals detected.</p>
        </div>
      )}
    </div>
  );
}
