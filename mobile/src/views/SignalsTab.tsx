"use client";
// Signals tab — evidence behind the market view.
// Filter chips: All | Active | Building | Weakening | Quiet
// Signal cards expand to show evidence, why it matters, and what would reactivate.

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { MarketNowPayload, ThemeItem } from "@/lib/customerApi";
import { translateTheme, themeDescription, themeInvalidation } from "@/lib/translate";

type Filter = "all" | "active" | "building" | "weakening" | "quiet";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all",      label: "All"      },
  { id: "active",   label: "Active"   },
  { id: "building", label: "Building" },
  { id: "weakening",label: "Weakening"},
  { id: "quiet",    label: "Quiet"    },
];

function matchesFilter(theme: ThemeItem, filter: Filter): boolean {
  if (filter === "all") return true;
  const s   = theme.state ?? "";
  const sig = theme.event_signal ?? "";
  if (filter === "active")    return s === "activated" || s === "active" || sig === "strengthening";
  if (filter === "building")  return s === "strengthening";
  if (filter === "weakening") return s === "weakening" || s === "headwind" || sig === "weakening";
  if (filter === "quiet")     return s === "dormant";
  return true;
}

function confidenceInfo(state?: string, signal?: string): { label: string; color: string; dotColor: string } {
  const s   = state ?? "";
  const sig = signal ?? "";
  if (s === "activated" || s === "active" || sig === "strengthening")
    return { label: "Active",              color: "#34d399", dotColor: "#10b981" };
  if (s === "strengthening")
    return { label: "Building momentum",  color: "#60a5fa", dotColor: "#3b82f6" };
  if (s === "crowded")
    return { label: "Crowded — risk of reversal", color: "#fbbf24", dotColor: "#f59e0b" };
  if (sig === "weakening" || s === "weakening")
    return { label: "Weakening",          color: "#fbbf24", dotColor: "#f59e0b" };
  if (s === "headwind")
    return { label: "Headwind signal",   color: "#f87171", dotColor: "#ef4444" };
  if (s === "dormant")
    return { label: "Not currently signalling", color: "#475569", dotColor: "#334155" };
  return   { label: "Monitoring",        color: "#64748b", dotColor: "#475569" };
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
  const conf  = confidenceInfo(theme.state, theme.event_signal);
  const desc  = themeDescription(theme.theme);
  const inval = themeInvalidation(theme.theme);
  const isDormant = theme.state === "dormant";
  const hasEvidence = (theme.from_events?.length ?? 0) > 0;

  return (
    <div
      className="rounded-2xl overflow-hidden transition-shadow"
      style={{
        background: "#131f35",
        border: "1px solid rgba(255,255,255,0.07)",
        boxShadow: expanded ? "0 2px 12px rgba(0,0,0,0.3)" : "none",
      }}
    >
      <button
        onClick={() => setExpanded(o => !o)}
        className="w-full px-4 py-3.5 text-left flex items-start justify-between gap-2"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{ background: conf.dotColor }}
            />
            <p className="text-[13px] font-semibold text-slate-100">
              {translateTheme(theme.theme)}
            </p>
          </div>
          <p className="text-[10px] ml-3.5" style={{ color: conf.color }}>
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
          style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}
        >
          {/* Event evidence */}
          {hasEvidence && (
            <div>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                Evidence
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
              {hasEvidence ? "Structural Context" : "Why This Matters"}
            </p>
            <p className="text-xs text-slate-300 leading-relaxed">{desc}</p>
            {!hasEvidence && (
              <p className="text-[9px] text-slate-600 mt-1.5">Structural context — no fresh event evidence this cycle.</p>
            )}
          </div>

          {/* What would reactivate (dormant) or weaken (active) */}
          {isDormant ? (
            <div
              className="rounded-xl px-3.5 py-3"
              style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.15)" }}
            >
              <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#f97316" }}>
                What Would Bring This Signal Back
              </p>
              <p className="text-xs text-slate-400 leading-relaxed">
                A reversal of the conditions that would weaken it: {inval.toLowerCase().replace(/\.$/, "")}.
              </p>
            </div>
          ) : (
            <div
              className="rounded-xl px-3.5 py-3"
              style={{ background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.12)" }}
            >
              <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#ef4444" }}>
                What Would Weaken This Signal
              </p>
              <p className="text-xs text-slate-400 leading-relaxed">{inval}</p>
            </div>
          )}

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

  // Count per category for filter chips
  const counts: Record<Filter, number> = {
    all:       themes.length,
    active:    themes.filter(t => matchesFilter(t, "active")).length,
    building:  themes.filter(t => matchesFilter(t, "building")).length,
    weakening: themes.filter(t => matchesFilter(t, "weakening")).length,
    quiet:     themes.filter(t => matchesFilter(t, "quiet")).length,
  };

  const SectionLabel = ({ children }: { children: React.ReactNode }) => (
    <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-3" style={{ color: "#f97316" }}>
      {children}
    </p>
  );

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">

      {/* Filter chips */}
      <section>
        <SectionLabel>Filter</SectionLabel>
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map(f => (
            counts[f.id] > 0 || f.id === "all" ? (
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
                {f.label} {counts[f.id] > 0 && f.id !== "all" ? `(${counts[f.id]})` : ""}
              </button>
            ) : null
          ))}
        </div>
      </section>

      {/* Conflicting evidence */}
      {conflicts.length > 0 && (
        <section>
          <SectionLabel>Conflicting Evidence</SectionLabel>
          <div className="space-y-2">
            {conflicts.map((c, i) => (
              <div
                key={i}
                className="rounded-xl px-3.5 py-3"
                style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}
              >
                <p className="text-xs text-amber-300 leading-relaxed">{c}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Signal cards */}
      <section>
        {filter !== "all" && <SectionLabel>{FILTERS.find(f => f.id === filter)?.label} Signals</SectionLabel>}
        {filtered.length > 0 ? (
          <div className="space-y-2">
            {filtered.map((t, i) => (
              <SignalCard key={i} theme={t} onThemeSelect={onThemeSelect} />
            ))}
          </div>
        ) : (
          <div
            className="rounded-xl px-6 py-8 text-center space-y-2"
            style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
          >
            <p className="text-sm text-slate-400">
              No {filter === "all" ? "" : filter + " "}signals right now.
            </p>
            <p className="text-xs text-slate-500 leading-relaxed">
              {filter === "all"
                ? "Structural signals appear as themes strengthen — typically during market hours."
                : `Switch to \"All\" to see all ${themes.length} monitored themes.`}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
