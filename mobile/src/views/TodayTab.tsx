"use client";
// Today tab — top-level market briefing.
// Market Mood | What Changed | Conflicting Signals | Key Events | Watch Next | Data Freshness

import { useState } from "react";
import { ArrowRight, AlertCircle, Eye, ChevronDown, ChevronUp } from "lucide-react";
import type { MarketNowPayload, ThemeItem, KeyEvent } from "@/lib/customerApi";
import { translateTheme } from "@/lib/translate";

// ── Helpers ───────────────────────────────────────────────────────────────────

function moodScheme(mood: string): { border: string; tag: string; text: string; bg: string } {
  const lower = mood.toLowerCase();
  if (lower.includes("risk-on") || lower.includes("de-escalat") || lower.includes("easing"))
    return { border: "#10b981", tag: "Risk-On",        text: "#34d399", bg: "rgba(16,185,129,0.1)"   };
  if (lower.includes("risk-off") || lower.includes("stress") || lower.includes("panic"))
    return { border: "#ef4444", tag: "Risk-Off",       text: "#f87171", bg: "rgba(239,68,68,0.1)"    };
  if (lower.includes("mixed") || lower.includes("caution") || lower.includes("conflict"))
    return { border: "#f59e0b", tag: "Mixed Signals",  text: "#fbbf24", bg: "rgba(245,158,11,0.1)"   };
  return   { border: "#334155", tag: "Monitoring",     text: "#94a3b8", bg: "rgba(255,255,255,0.04)" };
}

function resolveThemes(payload: MarketNowPayload): ThemeItem[] {
  if (payload.themes?.length) return payload.themes;
  return (payload.active_themes ?? []).map(t => ({ theme: t, state: "active" }));
}

function resolveWatchNext(payload: MarketNowPayload): string[] {
  return payload.watch_next?.length ? payload.watch_next : (payload.what_to_watch ?? []);
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-bold uppercase tracking-[0.18em] mb-3" style={{ color: "#f97316" }}>
      {children}
    </p>
  );
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.08)", ...style }}
    >
      {children}
    </div>
  );
}

function EventCard({ ev }: { ev: KeyEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded-xl cursor-pointer transition-all"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.08)" }}
      onClick={() => setOpen(o => !o)}
    >
      <div className="p-3.5 flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-start gap-2">
            <p className="text-[13px] font-semibold text-slate-100 leading-snug flex-1">
              {ev.title}
            </p>
            {ev.materiality === "high" && (
              <span
                className="text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0 mt-0.5"
                style={{ background: "rgba(239,68,68,0.12)", color: "#f87171" }}
              >
                High impact
              </span>
            )}
          </div>
        </div>
        {open
          ? <ChevronUp size={14} className="text-slate-400 shrink-0 mt-0.5" />
          : <ChevronDown size={14} className="text-slate-400 shrink-0 mt-0.5" />}
      </div>
      {open && (
        <div
          className="px-3.5 pb-3.5 space-y-2.5 pt-3"
          style={{ borderTop: "1px solid rgba(255,255,255,0.08)" }}
        >
          {ev.summary_plain_english && (
            <p className="text-xs text-slate-300 leading-relaxed">{ev.summary_plain_english}</p>
          )}
          {((ev.likely_positive_exposures?.length ?? 0) > 0 ||
            (ev.likely_negative_exposures?.length ?? 0) > 0) && (
            <div className="flex flex-wrap gap-1.5">
              {(ev.likely_positive_exposures ?? []).map((s, i) => (
                <span
                  key={i}
                  className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(16,185,129,0.1)", color: "#34d399" }}
                >
                  {s}
                </span>
              ))}
              {(ev.likely_negative_exposures ?? []).map((s, i) => (
                <span
                  key={i}
                  className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(239,68,68,0.1)", color: "#f87171" }}
                >
                  {s}
                </span>
              ))}
            </div>
          )}
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

export default function TodayTab({ data, onThemeSelect }: Props) {
  const mood             = data.market_mood || data.plain_english_summary || "";
  const whatChanged      = data.what_changed ?? [];
  const keyEvents        = data.key_events ?? [];
  const knownConflicts   = data.known_conflicts ?? [];
  const themes           = resolveThemes(data);
  const watchNext        = resolveWatchNext(data);
  const sectionFreshness = data.section_freshness ?? {};
  const sourceNotes      = data.source_notes ?? [];

  const ms = mood ? moodScheme(mood) : null;
  const activeThemes = themes.filter(t =>
    t.state === "activated" || t.state === "active" || t.state === "strengthening" ||
    t.event_signal === "strengthening"
  );

  return (
    <div className="px-4 pb-8 space-y-5 pt-2">

      {/* ── Market Mood ─────────────────────────────────────────────────── */}
      {mood && ms && (
        <section>
          <SectionLabel>Market Mood</SectionLabel>
          <div
            className="rounded-2xl p-4"
            style={{
              background: ms.bg,
              border: `1.5px solid ${ms.border}44`,
            }}
          >
            <div className="flex items-center gap-2 mb-2">
              <div className="w-2 h-2 rounded-full" style={{ background: ms.border }} />
              <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: ms.border }}>
                {ms.tag}
              </span>
            </div>
            <p className="text-sm font-semibold leading-relaxed" style={{ color: ms.text }}>
              {mood}
            </p>
          </div>
        </section>
      )}

      {/* ── What Changed ────────────────────────────────────────────────── */}
      <section>
        <SectionLabel>What Changed</SectionLabel>
        <Card>
          {whatChanged.length > 0 ? (
            <ul className="space-y-2.5">
              {whatChanged.map((item, i) => (
                <li key={i} className="flex items-start gap-2.5">
                  <ArrowRight size={12} className="shrink-0 mt-0.5" style={{ color: "#f97316" }} />
                  <p className="text-[13px] text-slate-200 leading-relaxed">{item}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500 text-center py-2">No significant changes detected yet.</p>
          )}
        </Card>
      </section>

      {/* ── Conflicting Signals ──────────────────────────────────────────── */}
      {knownConflicts.length > 0 && (
        <section>
          <SectionLabel>Conflicting Signals</SectionLabel>
          <div className="space-y-2">
            {knownConflicts.map((conflict, i) => (
              <div
                key={i}
                className="rounded-xl p-3.5 flex items-start gap-2.5"
                style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.25)" }}
              >
                <AlertCircle size={13} className="text-amber-400 shrink-0 mt-0.5" />
                <p className="text-xs text-amber-300 leading-relaxed">{conflict}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Key Events ──────────────────────────────────────────────────── */}
      {keyEvents.length > 0 && (
        <section>
          <SectionLabel>Key Events</SectionLabel>
          <div className="space-y-2">
            {keyEvents.slice(0, 5).map((ev, i) => (
              <EventCard key={i} ev={ev} />
            ))}
          </div>
        </section>
      )}

      {/* ── Active Themes (quick access) ─────────────────────────────────── */}
      {activeThemes.length > 0 && (
        <section>
          <SectionLabel>Active Themes</SectionLabel>
          <div className="flex flex-wrap gap-2">
            {activeThemes.slice(0, 8).map((t, i) => (
              <button
                key={i}
                onClick={() => onThemeSelect(t.theme)}
                className="px-3 py-1.5 rounded-full text-[11px] font-semibold transition-all active:scale-95"
                style={{
                  background: "rgba(249,115,22,0.12)",
                  color: "#fb923c",
                  border: "1px solid rgba(249,115,22,0.3)",
                }}
              >
                {translateTheme(t.theme)}
              </button>
            ))}
          </div>
          <p className="text-[10px] text-slate-500 mt-2">
            Tap a theme to explore it in the Theme Map.
          </p>
        </section>
      )}

      {/* ── Watch Next ──────────────────────────────────────────────────── */}
      {watchNext.length > 0 && (
        <section>
          <SectionLabel>Watch Next</SectionLabel>
          <Card>
            <ul className="space-y-2">
              {watchNext.map((item, i) => (
                <li key={i} className="flex items-start gap-2.5">
                  <Eye size={11} className="text-slate-400 shrink-0 mt-0.5" />
                  <p className="text-xs text-slate-300 leading-relaxed">{item}</p>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      )}

      {/* ── Data Freshness ───────────────────────────────────────────────── */}
      {(Object.keys(sectionFreshness).length > 0 || sourceNotes.length > 0) && (
        <section>
          <SectionLabel>Data Freshness</SectionLabel>
          <div
            className="rounded-xl p-3.5"
            style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.07)" }}
          >
            {Object.keys(sectionFreshness).length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {Object.entries(sectionFreshness)
                  .filter(([, entry]) => entry.status !== "unknown")
                  .map(([section, entry]) => {
                    const c =
                      entry.status === "fresh"
                        ? { bg: "rgba(16,185,129,0.1)",   text: "#10b981" }
                        : entry.status === "stale" || entry.status === "delayed"
                          ? { bg: "rgba(245,158,11,0.1)", text: "#f59e0b" }
                          : { bg: "rgba(255,255,255,0.05)", text: "#6b7280" };
                    return (
                      <span
                        key={section}
                        className="text-[9px] font-medium px-2 py-0.5 rounded"
                        style={{ background: c.bg, color: c.text }}
                      >
                        {section.replace(/_/g, " ")}: {entry.status}
                      </span>
                    );
                  })}
              </div>
            )}
            {sourceNotes.map((note, i) => (
              <p key={i} className="text-[10px] text-slate-500">{note}</p>
            ))}
          </div>
        </section>
      )}

      {/* ── Disclaimer ───────────────────────────────────────────────────── */}
      <section>
        <div
          className="rounded-xl p-4 text-center"
          style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
        >
          <p className="text-[11px] text-slate-500 leading-relaxed">
            Market intelligence only. Not financial advice. No trade execution.
          </p>
          {data.data_entitlement_note && (
            <p className="text-[10px] text-slate-600 mt-1">{data.data_entitlement_note}</p>
          )}
        </div>
      </section>
    </div>
  );
}
