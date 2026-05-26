"use client";
// Today tab — customer market intelligence briefing.
// Briefing (story headline + summary) | Market Pulse | Dominant Drivers |
// What Changed | Conflicting Signals | Key Events | Active Themes | Watch Next | Freshness

import { useState } from "react";
import { ArrowRight, AlertCircle, Eye, ChevronDown, ChevronUp, TrendingUp, Map, List } from "lucide-react";
import type { MarketNowPayload, ThemeItem, KeyEvent } from "@/lib/customerApi";
import { translateTheme, themeDescription } from "@/lib/translate";
import { buildCustomerStory } from "@/lib/customerStory";
import { getTtgIdForMarketNow } from "@/lib/themeCrosswalk";

// ── Helpers ───────────────────────────────────────────────────────────────────

function moodScheme(mood: string): { border: string; tag: string; text: string; bg: string } {
  const lower = mood.toLowerCase();
  if (lower.includes("risk-on") || lower.includes("de-escalat") || lower.includes("easing"))
    return { border: "#10b981", tag: "Risk-On",        text: "#34d399", bg: "rgba(16,185,129,0.08)"  };
  if (lower.includes("risk-off") || lower.includes("stress") || lower.includes("panic"))
    return { border: "#ef4444", tag: "Risk-Off",       text: "#f87171", bg: "rgba(239,68,68,0.08)"   };
  if (lower.includes("mixed") || lower.includes("caution") || lower.includes("conflict"))
    return { border: "#f59e0b", tag: "Mixed Signals",  text: "#fbbf24", bg: "rgba(245,158,11,0.08)"  };
  return   { border: "#334155", tag: "Monitoring",     text: "#94a3b8", bg: "rgba(255,255,255,0.03)" };
}

function resolveThemes(payload: MarketNowPayload): ThemeItem[] {
  if (payload.themes?.length) return payload.themes;
  return (payload.active_themes ?? []).map(t => ({ theme: t, state: "active" }));
}

function resolveWatchNext(payload: MarketNowPayload): string[] {
  return payload.watch_next?.length ? payload.watch_next : (payload.what_to_watch ?? []);
}

function themeStateSort(t: ThemeItem): number {
  const order: Record<string, number> = {
    activated: 0, active: 0, strengthening: 1, crowded: 2, watch: 3,
  };
  return order[t.state ?? ""] ?? 9;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-3" style={{ color: "#f97316" }}>
      {children}
    </p>
  );
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.07)", ...style }}
    >
      {children}
    </div>
  );
}

function StatePip({ state }: { state?: string }) {
  const s = state ?? "";
  const color =
    s === "activated" || s === "active"   ? "#10b981" :
    s === "strengthening"                 ? "#3b82f6" :
    s === "crowded" || s === "watch"      ? "#f59e0b" :
    s === "headwind"                      ? "#ef4444" :
    s === "weakening"                     ? "#f87171" :
                                            "#475569";
  return (
    <span
      className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
      style={{ background: color, marginTop: "3px" }}
    />
  );
}

function EventCard({ ev }: { ev: KeyEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded-xl cursor-pointer transition-all"
      style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.07)" }}
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
          ? <ChevronUp size={14} className="text-slate-500 shrink-0 mt-0.5" />
          : <ChevronDown size={14} className="text-slate-500 shrink-0 mt-0.5" />}
      </div>
      {open && (
        <div
          className="px-3.5 pb-3.5 space-y-2.5 pt-3"
          style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}
        >
          {ev.summary_plain_english && (
            <p className="text-xs text-slate-300 leading-relaxed">{ev.summary_plain_english}</p>
          )}
          {((ev.likely_positive_exposures?.length ?? 0) > 0 ||
            (ev.likely_negative_exposures?.length ?? 0) > 0) && (
            <div className="flex flex-wrap gap-1.5">
              {(ev.likely_positive_exposures ?? []).map((s, i) => (
                <span key={i} className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(16,185,129,0.1)", color: "#34d399" }}>{s}</span>
              ))}
              {(ev.likely_negative_exposures ?? []).map((s, i) => (
                <span key={i} className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(239,68,68,0.1)", color: "#f87171" }}>{s}</span>
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
  onGoToUniverse?: () => void;
  onGoToThemeMap?: () => void;
}

export default function TodayTab({ data, onThemeSelect, onGoToUniverse, onGoToThemeMap }: Props) {
  const story = buildCustomerStory(data);
  const mood           = data.market_mood || data.plain_english_summary || "";
  const whatChanged    = data.what_changed ?? [];
  const keyEvents      = data.key_events ?? [];
  const knownConflicts = data.known_conflicts ?? [];
  const keyDrivers     = data.key_drivers ?? [];
  const themes         = resolveThemes(data);
  const watchNext      = resolveWatchNext(data);
  const sourceNotes    = data.source_notes ?? [];
  const sectionFreshness = data.section_freshness ?? {};

  const ms = mood ? moodScheme(mood) : null;

  const activeThemes = [...themes]
    .filter(t => ["activated", "active", "strengthening"].includes(t.state ?? "") || t.event_signal === "strengthening")
    .sort((a, b) => themeStateSort(a) - themeStateSort(b));

  const themeCounts = {
    active:      themes.filter(t => ["activated", "active"].includes(t.state ?? "")).length,
    building:    themes.filter(t => t.state === "strengthening").length,
    weakening:   themes.filter(t => t.state === "weakening" || t.state === "headwind").length,
    dormant:     themes.filter(t => t.state === "dormant").length,
  };

  const storyMoodColor =
    story.market_state === "risk-on"  ? { border: "#10b981", tag: "Risk-On",       text: "#34d399", bg: "rgba(16,185,129,0.08)" } :
    story.market_state === "risk-off" ? { border: "#ef4444", tag: "Risk-Off",      text: "#f87171", bg: "rgba(239,68,68,0.08)" } :
    story.market_state === "mixed"    ? { border: "#f59e0b", tag: "Mixed Signals", text: "#fbbf24", bg: "rgba(245,158,11,0.08)" } :
                                        { border: "#334155", tag: "Monitoring",    text: "#94a3b8", bg: "rgba(255,255,255,0.03)" };

  return (
    <div className="px-4 pb-8 space-y-5 pt-2">

      {/* ── Intelligence Briefing ─────────────────────────────────────── */}
      <section>
        <div
          className="rounded-2xl p-4"
          style={{ background: storyMoodColor.bg, border: `1.5px solid ${storyMoodColor.border}40` }}
        >
          <div className="flex items-center gap-2 mb-3">
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: storyMoodColor.border, boxShadow: `0 0 6px ${storyMoodColor.border}88` }}
            />
            <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: storyMoodColor.border }}>
              {storyMoodColor.tag}
            </span>
            <span className="text-[9px] text-slate-600 ml-auto">{story.freshness_label}</span>
          </div>

          <h2 className="text-[15px] font-bold leading-snug mb-2" style={{ color: storyMoodColor.text }}>
            {story.headline}
          </h2>
          <p className="text-[12px] text-slate-300 leading-relaxed">{story.summary}</p>

          {/* Theme count summary */}
          {(story.active_theme_count + story.building_theme_count + story.weakening_theme_count) > 0 && (
            <div className="flex gap-4 mt-3.5 pt-3.5" style={{ borderTop: `1px solid ${storyMoodColor.border}25` }}>
              {story.active_theme_count > 0 && (
                <div className="text-center">
                  <p className="text-base font-black" style={{ color: "#10b981" }}>{story.active_theme_count}</p>
                  <p className="text-[9px] text-slate-500">Active</p>
                </div>
              )}
              {story.building_theme_count > 0 && (
                <div className="text-center">
                  <p className="text-base font-black" style={{ color: "#3b82f6" }}>{story.building_theme_count}</p>
                  <p className="text-[9px] text-slate-500">Building</p>
                </div>
              )}
              {story.weakening_theme_count > 0 && (
                <div className="text-center">
                  <p className="text-base font-black" style={{ color: "#f87171" }}>{story.weakening_theme_count}</p>
                  <p className="text-[9px] text-slate-500">Weakening</p>
                </div>
              )}
            </div>
          )}

          {/* CTAs */}
          <div className="flex gap-2 mt-3.5 flex-wrap">
            {onGoToThemeMap && (
              <button
                onClick={onGoToThemeMap}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-semibold transition-all active:scale-95"
                style={{ background: "rgba(249,115,22,0.15)", color: "#fb923c" }}
              >
                <Map size={10} />
                Explore Map
              </button>
            )}
            {onGoToUniverse && story.mapped_structural.length > 0 && (
              <button
                onClick={onGoToUniverse}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-semibold transition-all active:scale-95"
                style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
              >
                <List size={10} />
                See Connected Names
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ── Market Pulse ────────────────────────────────────────────────── */}
      {mood && ms && (
        <section>
          <SectionLabel>Market Pulse</SectionLabel>
          <div
            className="rounded-2xl p-4"
            style={{ background: ms.bg, border: `1.5px solid ${ms.border}40` }}
          >
            <div className="flex items-center gap-2 mb-2.5">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: ms.border, boxShadow: `0 0 6px ${ms.border}88` }}
              />
              <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: ms.border }}>
                {ms.tag}
              </span>
              <span className="text-[9px] text-slate-600 ml-auto">
                {data.freshness_timestamp
                  ? new Date(data.freshness_timestamp).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })
                  : ""}
              </span>
            </div>
            <p className="text-sm font-semibold leading-relaxed" style={{ color: ms.text }}>
              {mood}
            </p>
            {/* Theme count summary */}
            <div className="flex gap-3 mt-3 pt-3" style={{ borderTop: `1px solid ${ms.border}25` }}>
              {themeCounts.active > 0 && (
                <div className="text-center">
                  <p className="text-base font-black" style={{ color: "#10b981" }}>{themeCounts.active}</p>
                  <p className="text-[9px] text-slate-500">Active</p>
                </div>
              )}
              {themeCounts.building > 0 && (
                <div className="text-center">
                  <p className="text-base font-black" style={{ color: "#3b82f6" }}>{themeCounts.building}</p>
                  <p className="text-[9px] text-slate-500">Building</p>
                </div>
              )}
              {themeCounts.weakening > 0 && (
                <div className="text-center">
                  <p className="text-base font-black" style={{ color: "#f87171" }}>{themeCounts.weakening}</p>
                  <p className="text-[9px] text-slate-500">Weakening</p>
                </div>
              )}
              {themeCounts.dormant > 0 && (
                <div className="text-center">
                  <p className="text-base font-black text-slate-500">{themeCounts.dormant}</p>
                  <p className="text-[9px] text-slate-600">Quiet</p>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Dominant Drivers ────────────────────────────────────────────── */}
      {story.primary_drivers.length > 0 && (
        <section>
          <SectionLabel>Dominant Drivers</SectionLabel>
          <div className="space-y-2">
            {story.primary_drivers.map((driver, i) => (
              <div
                key={i}
                className="rounded-xl px-4 py-3"
                style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.07)" }}
              >
                <div className="flex items-start gap-3">
                  <TrendingUp size={13} style={{ color: "#f97316", marginTop: "2px", flexShrink: 0 }} />
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-semibold text-slate-100">{driver.label}</p>
                    <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed">
                      {driver.explanation}
                    </p>
                    {driver.linked_ttg_id && (
                      <button
                        onClick={() => {
                          const firstMarketNow = driver.linked_market_now_ids[0];
                          if (firstMarketNow) onThemeSelect(firstMarketNow);
                        }}
                        className="mt-2 flex items-center gap-1 text-[10px] font-semibold"
                        style={{ color: "#fb923c" }}
                      >
                        <span
                          className="px-1.5 py-0.5 rounded"
                          style={{ background: "rgba(249,115,22,0.1)" }}
                        >
                          {driver.linked_ttg_label}
                        </span>
                        <ArrowRight size={9} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
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
                  <ArrowRight size={12} className="shrink-0 mt-1" style={{ color: "#f97316" }} />
                  <p className="text-[13px] text-slate-200 leading-relaxed">{item}</p>
                </li>
              ))}
            </ul>
          ) : (
            <div className="space-y-1.5">
              <p className="text-sm text-slate-400">No significant intraday changes detected.</p>
              {keyDrivers.length > 0 && (
                <p className="text-xs text-slate-500 leading-relaxed">
                  The market is continuing to trade on existing macro drivers.
                  Fresh headlines will appear here as they arrive.
                </p>
              )}
            </div>
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
                style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}
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
            {keyEvents.slice(0, 6).map((ev, i) => (
              <EventCard key={i} ev={ev} />
            ))}
          </div>
        </section>
      )}

      {/* ── Active Themes ────────────────────────────────────────────────── */}
      {activeThemes.length > 0 && (
        <section>
          <SectionLabel>Active Themes</SectionLabel>
          <div className="space-y-2">
            {activeThemes.slice(0, 7).map((t, i) => {
              const isBuilding = t.state === "strengthening";
              const desc = t.from_events?.[0] || themeDescription(t.theme);
              return (
                <button
                  key={i}
                  onClick={() => onThemeSelect(t.theme)}
                  className="w-full rounded-xl px-4 py-3 text-left flex items-start gap-3 transition-all active:scale-[0.98]"
                  style={{ background: "#131f35", border: "1px solid rgba(255,255,255,0.07)" }}
                >
                  <StatePip state={t.state} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <p className="text-[13px] font-semibold text-slate-100 flex-1 min-w-0 truncate">
                        {translateTheme(t.theme)}
                      </p>
                      {isBuilding && (
                        <span className="text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0"
                          style={{ background: "rgba(59,130,246,0.12)", color: "#60a5fa" }}>
                          Building
                        </span>
                      )}
                    </div>
                    {desc && (
                      <p className="text-[11px] text-slate-400 leading-relaxed line-clamp-2">{desc}</p>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
          <p className="text-[10px] text-slate-500 mt-2 px-1">
            Tap a theme to explore in the Theme Map.
          </p>
        </section>
      )}

      {/* ── Watch Next ──────────────────────────────────────────────────── */}
      {watchNext.length > 0 && (
        <section>
          <SectionLabel>Watch Next</SectionLabel>
          <Card>
            <ul className="space-y-2.5">
              {watchNext.map((item, i) => (
                <li key={i} className="flex items-start gap-2.5">
                  <Eye size={11} className="text-slate-500 shrink-0 mt-1" />
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
          <SectionLabel>Intelligence Freshness</SectionLabel>
          <div
            className="rounded-xl p-3.5"
            style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
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
                      <span key={section} className="text-[9px] font-medium px-2 py-0.5 rounded"
                        style={{ background: c.bg, color: c.text }}>
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
      <div
        className="rounded-xl p-4 text-center"
        style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)" }}
      >
        <p className="text-[11px] text-slate-500 leading-relaxed">
          Market intelligence only. Not financial advice. No trade execution.
        </p>
        {data.data_entitlement_note && (
          <p className="text-[10px] text-slate-600 mt-1">{data.data_entitlement_note}</p>
        )}
      </div>
    </div>
  );
}
