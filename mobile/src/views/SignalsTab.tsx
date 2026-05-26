"use client";
// Signals tab — fresh evidence patterns across structural market themes.
// Shows what is building, fading, or quiet in the current market cycle.
// No buy/sell/hold language. No execution or trading logic.

import { useState } from "react";
import { ChevronDown, ChevronUp, ArrowRight } from "lucide-react";
import type { MarketNowPayload, ThemeItem } from "@/lib/customerApi";
import {
  translateTheme, themeDescription, themeInvalidation, resolveSignalStatus,
} from "@/lib/translate";
import { getCrosswalkByMarketNow } from "@/lib/themeCrosswalk";

type Filter = "all" | "in_focus" | "building" | "fading" | "quiet";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all",      label: "All"      },
  { id: "in_focus", label: "In Focus" },
  { id: "building", label: "Building" },
  { id: "fading",   label: "Fading"   },
  { id: "quiet",    label: "Quiet"    },
];

function matchesFilter(theme: ThemeItem, filter: Filter): boolean {
  if (filter === "all") return true;
  const s   = theme.state ?? "";
  const sig = theme.event_signal ?? "";
  if (filter === "in_focus") return s === "activated" || s === "active" || sig === "strengthening";
  if (filter === "building") return s === "strengthening";
  if (filter === "fading")   return s === "weakening" || s === "headwind" || sig === "weakening";
  if (filter === "quiet")    return s === "dormant";
  return true;
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
  const status      = resolveSignalStatus(theme.state, theme.event_signal);
  const desc        = themeDescription(theme.theme);
  const inval       = themeInvalidation(theme.theme);
  const isDormant   = theme.state === "dormant";
  const hasEvidence = (theme.from_events?.length ?? 0) > 0;
  const crosswalk   = getCrosswalkByMarketNow(theme.theme);

  // One-liner shown in collapsed view: first event or first sentence of description
  const oneLiner = theme.from_events?.[0] ?? desc.split(".")[0];

  return (
    <div
      className="rounded-2xl overflow-hidden transition-shadow"
      style={{
        background: "#131f35",
        border: "1px solid rgba(255,255,255,0.07)",
        boxShadow: expanded ? "0 2px 12px rgba(0,0,0,0.3)" : "none",
      }}
    >
      {/* ── Collapsed header ── */}
      <button
        onClick={() => setExpanded(o => !o)}
        className="w-full px-4 py-3.5 text-left flex items-start justify-between gap-2"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{ background: status.dotColor }}
            />
            <p className="text-[13px] font-semibold text-slate-100 truncate">
              {translateTheme(theme.theme)}
            </p>
          </div>
          <p className="text-[10px] ml-3.5 mb-0.5" style={{ color: status.color }}>
            {status.label}
          </p>
          {oneLiner && (
            <p className="text-[10px] text-slate-500 ml-3.5 line-clamp-1 leading-relaxed">
              {oneLiner}
            </p>
          )}
        </div>
        <div className="shrink-0 mt-0.5">
          {expanded
            ? <ChevronUp size={14} className="text-slate-400" />
            : <ChevronDown size={14} className="text-slate-400" />}
        </div>
      </button>

      {/* ── Expanded detail ── */}
      {expanded && (
        <div
          className="px-4 pb-4 space-y-3.5 pt-3"
          style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}
        >

          {/* Why This Matters — always first */}
          <div>
            <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
              Why This Matters
            </p>
            <p className="text-xs text-slate-300 leading-relaxed">{desc}</p>
          </div>

          {/* Fresh Event Evidence */}
          {hasEvidence ? (
            <div>
              <p className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                Fresh Event Evidence
              </p>
              <ul className="space-y-1.5">
                {(theme.from_events ?? []).map((ev, i) => (
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
          ) : (
            <p className="text-[9px] text-slate-600">No fresh event evidence this cycle.</p>
          )}

          {/* Connected Structural Theme */}
          {crosswalk ? (
            <div
              className="rounded-xl px-3.5 py-3"
              style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.15)" }}
            >
              <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#f97316" }}>
                Connected Structural Theme
              </p>
              <p className="text-[11px] font-semibold text-slate-200 mb-1">
                {crosswalk.ttgPrimaryLabel}
              </p>
              <p className="text-[10px] text-slate-500 leading-relaxed">
                {crosswalk.relationship}
              </p>
            </div>
          ) : !isDormant && (
            <p className="text-[9px] text-slate-600">
              Fresh signal — structural theme connection not yet established.
            </p>
          )}

          {/* Risk to Monitor / What Would Bring This Back */}
          {isDormant ? (
            <div
              className="rounded-xl px-3.5 py-3"
              style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.15)" }}
            >
              <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#f97316" }}>
                What Would Bring This Signal Back
              </p>
              <p className="text-xs text-slate-400 leading-relaxed">
                A reversal of the conditions that would weaken it:{" "}
                {inval.toLowerCase().replace(/\.$/, "")}.
              </p>
            </div>
          ) : (
            <div
              className="rounded-xl px-3.5 py-3"
              style={{ background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.12)" }}
            >
              <p className="text-[9px] font-bold uppercase tracking-wider mb-1.5" style={{ color: "#ef4444" }}>
                Risk to Monitor
              </p>
              <p className="text-xs text-slate-400 leading-relaxed">{inval}</p>
            </div>
          )}

          {/* CTA — context-aware label when structural theme is known */}
          <button
            onClick={() => onThemeSelect(theme.theme)}
            className="flex items-center gap-1.5 text-[10px] font-semibold px-3 py-1.5 rounded-full transition-all active:scale-95"
            style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
          >
            {crosswalk
              ? `View ${crosswalk.ttgPrimaryLabel} in Theme Map`
              : "View in Theme Map"
            }
            <ArrowRight size={10} />
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

  const counts: Record<Filter, number> = {
    all:      themes.length,
    in_focus: themes.filter(t => matchesFilter(t, "in_focus")).length,
    building: themes.filter(t => matchesFilter(t, "building")).length,
    fading:   themes.filter(t => matchesFilter(t, "fading")).length,
    quiet:    themes.filter(t => matchesFilter(t, "quiet")).length,
  };

  const SectionLabel = ({ children }: { children: React.ReactNode }) => (
    <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-3" style={{ color: "#f97316" }}>
      {children}
    </p>
  );

  return (
    <div className="px-4 pt-2 pb-8 space-y-5">

      {/* ── Intro card ─────────────────────────────────────────────────────── */}
      <div
        className="rounded-2xl px-4 py-3.5"
        style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.12)" }}
      >
        <p className="text-[12px] text-slate-300 leading-relaxed">
          Fresh evidence patterns across structural market themes — what is building, fading, or quiet today.
          Market intelligence only. Not financial advice.
        </p>
      </div>

      {/* ── Filter chips ────────────────────────────────────────────────────── */}
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
              {f.label}{f.id !== "all" && counts[f.id] > 0 ? ` (${counts[f.id]})` : ""}
            </button>
          ) : null
        ))}
      </div>

      {/* ── Conflicting evidence ────────────────────────────────────────────── */}
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

      {/* ── Signal cards ────────────────────────────────────────────────────── */}
      <section>
        {filter !== "all" && (
          <SectionLabel>{FILTERS.find(f => f.id === filter)?.label} Signals</SectionLabel>
        )}
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
              {filter === "all"
                ? "Signals are quiet right now."
                : `No ${FILTERS.find(f => f.id === filter)?.label.toLowerCase()} signals right now.`}
            </p>
            <p className="text-xs text-slate-500 leading-relaxed">
              {filter === "all"
                ? "Structural themes remain available in the Theme Map. Fresh evidence will appear here when market conditions strengthen."
                : `Switch to "All" to see all ${themes.length} monitored themes.`}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
