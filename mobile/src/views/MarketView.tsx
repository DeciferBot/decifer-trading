"use client";

import { useEffect, useState, useCallback } from "react";
import { Globe, AlertCircle, Eye, ArrowRight, CheckCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchMarketNow,
  type MarketNowPayload,
  type ThemeItem,
} from "@/lib/customerApi";
import { translateTheme } from "@/lib/translate";

// ── Freshness helpers ─────────────────────────────────────────────────────────

function freshnessInfo(payload: MarketNowPayload): {
  label: string;
  colorClass: string;
  dotClass: string;
  borderClass: string;
} {
  const ts = payload.freshness_timestamp;
  const conf = (payload.confidence_label ?? "").toLowerCase();

  if (!ts || conf.includes("insufficient")) {
    return {
      label: "Degraded",
      colorClass: "text-rose-400",
      dotClass: "bg-rose-400",
      borderClass: "border-rose-500/30",
    };
  }

  const ageMin = (Date.now() - new Date(ts).getTime()) / 60_000;

  if (isNaN(ageMin) || ageMin > 120 || conf.includes("degraded")) {
    return {
      label: "Degraded",
      colorClass: "text-rose-400",
      dotClass: "bg-rose-400",
      borderClass: "border-rose-500/30",
    };
  }
  if (ageMin > 30) {
    return {
      label: "Stale",
      colorClass: "text-amber-400",
      dotClass: "bg-amber-400",
      borderClass: "border-amber-500/30",
    };
  }
  return {
    label: "Fresh",
    colorClass: "text-emerald-400",
    dotClass: "bg-emerald-400 animate-pulse",
    borderClass: "border-emerald-500/30",
  };
}

function formatTs(iso: string | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

// ── Color helpers ─────────────────────────────────────────────────────────────

function moodBorderColor(mood: string): string {
  const lower = mood.toLowerCase();
  if (
    lower.includes("risk-on") ||
    lower.includes("de-escalat") ||
    lower.includes("easing")
  )
    return "border-emerald-500/30";
  if (
    lower.includes("risk-off") ||
    lower.includes("stress") ||
    lower.includes("panic")
  )
    return "border-rose-500/30";
  if (
    lower.includes("mixed") ||
    lower.includes("caution") ||
    lower.includes("conflict")
  )
    return "border-amber-500/30";
  return "border-[#1e2a3a]";
}

function moodTextColor(mood: string): string {
  const lower = mood.toLowerCase();
  if (
    lower.includes("risk-on") ||
    lower.includes("de-escalat") ||
    lower.includes("easing")
  )
    return "text-emerald-300";
  if (
    lower.includes("risk-off") ||
    lower.includes("stress") ||
    lower.includes("panic")
  )
    return "text-rose-300";
  if (
    lower.includes("mixed") ||
    lower.includes("caution") ||
    lower.includes("conflict")
  )
    return "text-amber-300";
  return "text-slate-200";
}

function sectorMoodBadge(mood: string | undefined): string {
  if (mood === "tailwind") return "bg-emerald-500/10 text-emerald-400";
  if (mood === "headwind") return "bg-rose-500/10 text-rose-400";
  return "bg-amber-500/10 text-amber-400";
}

function themeStateBadge(
  state: string | undefined,
  signal: string | undefined,
): { label: string; style: string } {
  const s = state ?? "";
  if (
    signal === "strengthening" ||
    s === "activated" ||
    s === "strengthening" ||
    s === "active"
  )
    return { label: "Active", style: "bg-emerald-500/10 text-emerald-400" };
  if (signal === "weakening" || s === "watch")
    return { label: "Watch", style: "bg-amber-500/10 text-amber-400" };
  if (s === "crowded")
    return { label: "Crowded", style: "bg-amber-500/10 text-amber-400" };
  if (s === "dormant")
    return { label: "Quiet", style: "bg-slate-600/30 text-slate-500" };
  return { label: s || "Active", style: "bg-slate-600/30 text-slate-400" };
}

// ── Payload shape adapters ────────────────────────────────────────────────────
// Support both old pre-M11A DO payload and new M11A payload transparently.

function resolveThemes(payload: MarketNowPayload): ThemeItem[] {
  // M11A: themes is a list of objects with {theme, state, event_signal, ...}
  if (payload.themes && payload.themes.length > 0) return payload.themes;
  // Old shape: active_themes is a list of strings
  return (payload.active_themes ?? []).map((t) => ({ theme: t, state: "active" }));
}

function resolveWatchNext(payload: MarketNowPayload): string[] {
  if (payload.watch_next && payload.watch_next.length > 0)
    return payload.watch_next;
  return payload.what_to_watch ?? [];
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MarketView() {
  const [data, setData] = useState<MarketNowPayload | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const payload = await fetchMarketNow();
      setData(payload);
      setFetchError(null);
    } catch (e) {
      setFetchError(
        e instanceof Error ? e.message : "Unable to load market intelligence.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ── Loading skeleton ──────────────────────────────────────────────────────

  if (loading)
    return (
      <div className="px-5 pt-6 space-y-4">
        <Skeleton className="h-10 w-44 bg-[#161e2e]" />
        <Skeleton className="h-28 rounded-2xl bg-[#161e2e]" />
        <Skeleton className="h-20 rounded-2xl bg-[#161e2e]" />
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 rounded-xl bg-[#161e2e]" />
        ))}
      </div>
    );

  // ── Error state ───────────────────────────────────────────────────────────

  if (fetchError && !data)
    return (
      <div className="px-5 pt-6">
        <div className="rounded-2xl bg-[#101622] border border-rose-500/20 p-6 flex flex-col items-center gap-3 text-center">
          <AlertCircle size={24} className="text-rose-400" />
          <p className="text-sm text-slate-300 font-medium">
            Market intelligence unavailable
          </p>
          <p className="text-xs text-slate-500">{fetchError}</p>
          <button
            onClick={load}
            className="text-xs text-blue-400 font-semibold hover:text-blue-300 mt-1"
          >
            Try again
          </button>
        </div>
      </div>
    );

  const payload = data!;
  const freshness = freshnessInfo(payload);
  const mood =
    payload.market_mood || payload.plain_english_summary || "";
  const whatChanged = payload.what_changed ?? [];
  const keyEvents = payload.key_events ?? [];
  const knownConflicts = payload.known_conflicts ?? [];
  const sectors = payload.sectors ?? [];
  const themes = resolveThemes(payload);
  const radar = payload.radar ?? [];
  const watchNext = resolveWatchNext(payload);
  const sourceNotes = payload.source_notes ?? [];
  const sectionFreshness = payload.section_freshness ?? {};

  return (
    <div className="px-5 pt-6 pb-8 space-y-5">

      {/* ── 1. Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] font-bold tracking-[0.22em] text-slate-600 uppercase mb-0.5">
            Decifer
          </p>
          <h1 className="text-xl font-bold text-white">Market Intelligence</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Updated {formatTs(payload.freshness_timestamp)}
          </p>
        </div>
        <div
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-[11px] font-semibold border ${freshness.colorClass} ${freshness.borderClass} shrink-0 mt-1`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${freshness.dotClass}`} />
          {freshness.label}
        </div>
      </div>

      {/* ── 2. Market mood ────────────────────────────────────────────────── */}
      {mood && (
        <div
          className={`rounded-2xl bg-[#101622] border ${moodBorderColor(mood)} p-4`}
        >
          <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-2">
            Market mood
          </p>
          <p
            className={`text-sm font-semibold leading-relaxed ${moodTextColor(mood)}`}
          >
            {mood}
          </p>
        </div>
      )}

      {/* ── 3. What changed ───────────────────────────────────────────────── */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
        <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-2">
          What changed
        </p>
        {whatChanged.length > 0 ? (
          <ul className="space-y-1.5">
            {whatChanged.map((item, i) => (
              <li key={i} className="flex items-start gap-2">
                <ArrowRight
                  size={12}
                  className="text-blue-400 shrink-0 mt-0.5"
                />
                <p className="text-sm text-slate-300 leading-relaxed">{item}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">No fresh change detected yet.</p>
        )}
      </div>

      {/* ── 4. Key events ─────────────────────────────────────────────────── */}
      {keyEvents.length > 0 && (
        <div>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">
            Key events
          </p>
          <div className="space-y-2">
            {keyEvents.slice(0, 5).map((ev, i) => (
              <div
                key={i}
                className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4"
              >
                <div className="flex items-start justify-between gap-2 mb-1.5">
                  <p className="text-sm font-semibold text-white leading-snug flex-1">
                    {ev.title}
                  </p>
                  {ev.materiality === "high" && (
                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0 bg-rose-500/10 text-rose-400">
                      High impact
                    </span>
                  )}
                </div>
                {ev.summary_plain_english && (
                  <p className="text-xs text-slate-400 leading-relaxed mb-2">
                    {ev.summary_plain_english}
                  </p>
                )}
                {((ev.likely_positive_exposures?.length ?? 0) > 0 ||
                  (ev.likely_negative_exposures?.length ?? 0) > 0) && (
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {(ev.likely_positive_exposures ?? []).map((s, j) => (
                      <span
                        key={`p${j}`}
                        className="text-[10px] font-medium px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400"
                      >
                        {s}
                      </span>
                    ))}
                    {(ev.likely_negative_exposures ?? []).map((s, j) => (
                      <span
                        key={`n${j}`}
                        className="text-[10px] font-medium px-2 py-0.5 rounded bg-rose-500/10 text-rose-400"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 5. Conflicting signals ────────────────────────────────────────── */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
        <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-2">
          Conflicting signals
        </p>
        {knownConflicts.length > 0 ? (
          <ul className="space-y-2">
            {knownConflicts.map((conflict, i) => (
              <li key={i} className="flex items-start gap-2">
                <AlertCircle
                  size={12}
                  className="text-amber-400 shrink-0 mt-0.5"
                />
                <p className="text-xs text-slate-300 leading-relaxed">
                  {conflict}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">No major conflict detected.</p>
        )}
      </div>

      {/* ── 6. Sectors in focus ───────────────────────────────────────────── */}
      {sectors.length > 0 && (
        <div>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">
            Sectors in focus
          </p>
          <div className="grid grid-cols-2 gap-2">
            {sectors.slice(0, 6).map((sector, i) => (
              <div
                key={i}
                className="rounded-xl bg-[#101622] border border-[#1e2a3a] p-3"
              >
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs font-semibold text-white capitalize truncate mr-1">
                    {sector.name.replace(/_/g, " ")}
                  </p>
                  <span
                    className={`text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0 ${sectorMoodBadge(sector.mood)}`}
                  >
                    {sector.mood === "tailwind"
                      ? "↑"
                      : sector.mood === "headwind"
                        ? "↓"
                        : "↕"}
                  </span>
                </div>
                {sector.reasons?.[0] && (
                  <p className="text-[10px] text-slate-500 line-clamp-2">
                    {sector.reasons[0]}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 7. Themes in focus ────────────────────────────────────────────── */}
      {themes.length > 0 ? (
        <div>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">
            Themes in focus
          </p>
          <div className="space-y-2">
            {themes.slice(0, 8).map((t, i) => {
              const badge = themeStateBadge(t.state, t.event_signal);
              return (
                <div
                  key={i}
                  className="rounded-xl bg-[#101622] border border-[#1e2a3a] p-3 flex items-start justify-between gap-2"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white">
                      {translateTheme(t.theme)}
                    </p>
                    {t.from_events?.[0] && (
                      <p className="text-[10px] text-slate-500 mt-0.5 line-clamp-1">
                        {t.from_events[0]}
                      </p>
                    )}
                  </div>
                  <span
                    className={`text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0 mt-0.5 ${badge.style}`}
                  >
                    {badge.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-8 flex flex-col items-center gap-3">
          <Globe size={24} className="text-slate-600" />
          <p className="text-slate-500 text-sm text-center">
            No themes identified right now
          </p>
        </div>
      )}

      {/* ── 8. Names on the radar ─────────────────────────────────────────── */}
      {radar.length > 0 && (
        <div>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">
            Names on the radar
          </p>
          <div className="space-y-2">
            {radar.slice(0, 6).map((item, i) => (
              <div
                key={i}
                className="rounded-xl bg-[#101622] border border-[#1e2a3a] p-3"
              >
                <div className="flex items-start gap-3">
                  <span className="text-base font-black text-white shrink-0 w-14">
                    {item.symbol}
                  </span>
                  <div className="flex-1 min-w-0">
                    {item.reason_to_watch && (
                      <p className="text-xs text-slate-400 leading-relaxed line-clamp-2">
                        {item.reason_to_watch}
                      </p>
                    )}
                    {item.theme_link && (
                      <p className="text-[10px] text-blue-400/60 mt-0.5">
                        {translateTheme(item.theme_link)}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-slate-600 mt-2 text-center">
            Not a buy or sell recommendation.
          </p>
        </div>
      )}

      {/* ── 9. Watch next ─────────────────────────────────────────────────── */}
      {watchNext.length > 0 && (
        <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
          <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-2">
            Watch next
          </p>
          <ul className="space-y-1.5">
            {watchNext.map((item, i) => (
              <li key={i} className="flex items-start gap-2">
                <Eye size={11} className="text-slate-500 shrink-0 mt-0.5" />
                <p className="text-xs text-slate-400 leading-relaxed">{item}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── 10. Source notes & freshness ──────────────────────────────────── */}
      {(sourceNotes.length > 0 ||
        Object.keys(sectionFreshness).length > 0) && (
        <div className="rounded-xl bg-[#0a1018] border border-[#1e2a3a] p-4 space-y-2">
          <p className="text-[10px] font-bold text-slate-700 uppercase tracking-wider">
            Data freshness
          </p>
          {Object.keys(sectionFreshness).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(sectionFreshness).map(([section, entry]) => (
                <span
                  key={section}
                  className={`text-[9px] font-medium px-2 py-0.5 rounded ${
                    entry.status === "fresh"
                      ? "bg-emerald-500/10 text-emerald-500"
                      : entry.status === "stale"
                        ? "bg-amber-500/10 text-amber-500"
                        : "bg-slate-600/20 text-slate-600"
                  }`}
                >
                  {section.replace(/_/g, " ")}: {entry.status}
                </span>
              ))}
            </div>
          )}
          {sourceNotes.map((note, i) => (
            <p key={i} className="text-[10px] text-slate-600">
              {note}
            </p>
          ))}
        </div>
      )}

      {/* ── 11. Disclaimer ────────────────────────────────────────────────── */}
      <div className="rounded-xl bg-[#0a1018] border border-[#1e2a3a] p-4 text-center space-y-1">
        <div className="flex items-center justify-center gap-1.5 mb-1">
          <CheckCircle size={12} className="text-slate-600" />
          <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide">
            About this data
          </p>
        </div>
        <p className="text-[11px] text-slate-500 leading-relaxed">
          Market intelligence only. Not financial advice. No trade execution.
        </p>
        {payload.data_entitlement_note && (
          <p className="text-[10px] text-slate-600">
            {payload.data_entitlement_note}
          </p>
        )}
      </div>
    </div>
  );
}
