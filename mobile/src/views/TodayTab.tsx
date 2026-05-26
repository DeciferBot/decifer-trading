"use client";
// Today tab — briefing home. M13A refactor.
// Welcome card | Since you were away | What is moving markets | Market mood |
// Conflicting signals | Key events | Worth watching
// Receives pre-computed story, causeCards, clock, sinceAway from CustomerApp.

import { useState } from "react";
import {
  ArrowRight,
  AlertCircle,
  Eye,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Clock,
  TrendingUp,
  Compass,
} from "lucide-react";
import type { MarketNowPayload, KeyEvent } from "@/lib/customerApi";
import type { CustomerStory } from "@/lib/customerStory";
import type { MarketCauseCard } from "@/lib/marketCauseStory";
import type {
  MarketClockState,
  FreshnessState,
  SinceAwaySummary,
} from "@/lib/useCustomerBriefing";

// ── Helpers ───────────────────────────────────────────────────────────────────

function moodScheme(mood: string) {
  const l = mood.toLowerCase();
  if (l.includes("risk-on") || l.includes("de-escalat") || l.includes("easing"))
    return { border: "#10b981", text: "#34d399", bg: "rgba(16,185,129,0.07)" };
  if (l.includes("risk-off") || l.includes("stress") || l.includes("panic"))
    return { border: "#ef4444", text: "#f87171", bg: "rgba(239,68,68,0.07)" };
  if (l.includes("mixed") || l.includes("caution") || l.includes("conflict"))
    return { border: "#f59e0b", text: "#fbbf24", bg: "rgba(245,158,11,0.07)" };
  return { border: "#334155", text: "#94a3b8", bg: "rgba(255,255,255,0.03)" };
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-[10px] font-bold uppercase tracking-[0.15em] mb-3"
      style={{ color: "#f97316" }}
    >
      {children}
    </p>
  );
}

function Card({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)", ...style }}
    >
      {children}
    </div>
  );
}

function EventCard({ ev }: { ev: KeyEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded-xl cursor-pointer"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
      onClick={() => setOpen((o) => !o)}
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
        {open ? (
          <ChevronUp size={14} className="text-slate-500 shrink-0 mt-0.5" />
        ) : (
          <ChevronDown size={14} className="text-slate-500 shrink-0 mt-0.5" />
        )}
      </div>
      {open && (
        <div
          className="px-3.5 pb-3.5 pt-3 space-y-2.5"
          style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}
        >
          {ev.summary_plain_english && (
            <p className="text-xs text-slate-300 leading-relaxed">
              {ev.summary_plain_english}
            </p>
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

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  story: CustomerStory | null;
  causeCards: MarketCauseCard[];
  clock: MarketClockState;
  sinceAway: SinceAwaySummary;
  freshnessState: FreshnessState;
  freshnessLabel: string;
  isRefreshing: boolean;
  onRefresh: () => Promise<void>;
  onThemeSelect: (themeId: string) => void;
  onAskAbout?: (context: string) => void;
  onGoToDiscover?: () => void;
  onGoToUniverse?: () => void;
}

export default function TodayTab({
  data,
  story,
  causeCards,
  clock,
  sinceAway,
  freshnessState,
  freshnessLabel,
  isRefreshing,
  onRefresh,
  onThemeSelect,
  onAskAbout,
  onGoToDiscover,
}: Props) {
  const mood = data.market_mood || data.plain_english_summary || "";
  const keyEvents = data.key_events ?? [];
  const knownConflicts = data.known_conflicts ?? [];
  const watchNext = data.watch_next?.length ? data.watch_next : (data.what_to_watch ?? []);

  const ms = mood ? moodScheme(mood) : null;

  const storyColor =
    story?.market_state === "risk-on"
      ? { border: "#10b981", text: "#34d399" }
      : story?.market_state === "risk-off"
        ? { border: "#ef4444", text: "#f87171" }
        : story?.market_state === "mixed"
          ? { border: "#f59e0b", text: "#fbbf24" }
          : { border: "#334155", text: "#94a3b8" };

  const sessionDotColor =
    clock.session === "open"
      ? "#10b981"
      : clock.session === "pre_market" || clock.session === "after_hours"
        ? "#f59e0b"
        : "#475569";

  const freshnessTimeCopy =
    freshnessState === "fresh" && data.freshness_timestamp
      ? `Fresh as of ${new Date(data.freshness_timestamp).toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          timeZoneName: "short",
        })}`
      : freshnessLabel;

  return (
    <div className="px-4 pb-8 space-y-5 pt-3">

      {/* ── A: Welcome card ───────────────────────────────────────────────── */}
      <section>
        <Card>
          {/* Session + time row */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-1.5">
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{
                  background: sessionDotColor,
                  boxShadow:
                    clock.session === "open" ? `0 0 5px ${sessionDotColor}80` : "none",
                }}
              />
              <span
                className="text-[10px] font-semibold"
                style={{ color: sessionDotColor }}
              >
                {clock.sessionLabel}
              </span>
            </div>
            <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
              <Clock size={9} />
              <span>{clock.newYorkTime} ET</span>
            </div>
          </div>

          {/* Headline */}
          {story && (
            <p
              className="text-[14px] font-bold leading-snug mb-2"
              style={{ color: storyColor.text }}
            >
              {story.headline}
            </p>
          )}
          <p className="text-[12px] text-slate-400 leading-relaxed">
            {story?.summary ?? "Gathering market intelligence..."}
          </p>

          {/* Freshness + refresh */}
          <div
            className="flex items-center justify-between mt-3 pt-3"
            style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
          >
            <span
              className="text-[10px] font-medium"
              style={{
                color:
                  freshnessState === "fresh"
                    ? "#10b981"
                    : freshnessState === "stale"
                      ? "#f87171"
                      : "#6b7280",
              }}
            >
              {freshnessTimeCopy}
            </span>
            <button
              onClick={onRefresh}
              disabled={isRefreshing}
              className="flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
              style={{ color: "#f97316" }}
            >
              <RefreshCw size={9} className={isRefreshing ? "animate-spin" : ""} />
              {isRefreshing ? "Refreshing..." : "Refresh view"}
            </button>
          </div>
        </Card>
      </section>

      {/* ── B: Since you were away ────────────────────────────────────────── */}
      {sinceAway.lastSeenAt && (
        <section>
          <SectionLabel>
            {sinceAway.awayDuration
              ? `Since you were away · ${sinceAway.awayDuration} ago`
              : "Since your last visit"}
          </SectionLabel>

          {sinceAway.hasChanges && sinceAway.items.length > 0 ? (
            <div className="space-y-2">
              {sinceAway.items.map((item, i) => (
                <div
                  key={i}
                  className="rounded-xl px-4 py-3 flex items-start gap-3"
                  style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0 mt-1.5"
                    style={{
                      background:
                        item.type === "event"
                          ? "#f59e0b"
                          : item.type === "theme"
                            ? "#3b82f6"
                            : "#10b981",
                    }}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] text-slate-200 leading-snug">{item.title}</p>
                    {item.detail && (
                      <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed line-clamp-2">
                        {item.detail}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Card>
              <p className="text-sm text-slate-400">
                No major new changes detected since your last visit.
              </p>
              <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">
                The latest market briefing is below.
              </p>
            </Card>
          )}
        </section>
      )}

      {/* ── C: What is moving markets ─────────────────────────────────────── */}
      {causeCards.length > 0 && (
        <section>
          <SectionLabel>What is moving markets</SectionLabel>
          <div className="space-y-3">
            {causeCards.map((card, i) => (
              <div
                key={i}
                className="rounded-2xl p-4"
                style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
              >
                <div className="flex items-center gap-2 mb-2.5">
                  <TrendingUp
                    size={12}
                    style={{ color: "#f97316", flexShrink: 0 }}
                  />
                  <p className="text-[13px] font-bold text-slate-100 flex-1">
                    {card.cause_label}
                  </p>
                  <span
                    className="text-[9px] font-medium px-1.5 py-0.5 rounded shrink-0"
                    style={{ background: "rgba(255,255,255,0.05)", color: "#6b7280" }}
                  >
                    {card.evidence_basis}
                  </span>
                </div>

                <p className="text-[12px] text-slate-300 leading-relaxed mb-1">
                  {card.what_happened}
                </p>
                <p className="text-[12px] text-slate-400 leading-relaxed">
                  {card.market_impact}
                </p>

                {/* Connected themes */}
                {card.connected_themes.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2.5">
                    {card.connected_themes.slice(0, 3).map((t, j) => (
                      <button
                        key={j}
                        onClick={() => {
                          if (card.primary_market_now_id)
                            onThemeSelect(card.primary_market_now_id);
                        }}
                        className="text-[10px] font-semibold px-2 py-0.5 rounded-full transition-all active:scale-95"
                        style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
                      >
                        {t.ttgLabel}
                      </button>
                    ))}
                    {card.connected_names_count > 0 && (
                      <span className="text-[10px] text-slate-600 self-center ml-1">
                        {card.connected_names_count}{" "}
                        {card.connected_names_count !== 1 ? "names" : "name"}
                      </span>
                    )}
                  </div>
                )}

                {/* Ask Decifer CTA */}
                {onAskAbout && (
                  <button
                    onClick={() =>
                      onAskAbout(
                        `Why is ${card.cause_label.toLowerCase()} moving markets?`,
                      )
                    }
                    className="mt-2.5 flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
                    style={{ color: "#94a3b8" }}
                  >
                    Ask Decifer why
                    <ArrowRight size={9} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── D: Market mood ────────────────────────────────────────────────── */}
      {mood && ms && (
        <section>
          <SectionLabel>Market mood</SectionLabel>
          <div
            className="rounded-2xl p-4"
            style={{ background: ms.bg, border: `1.5px solid ${ms.border}35` }}
          >
            <p className="text-[13px] font-semibold leading-relaxed" style={{ color: ms.text }}>
              {mood}
            </p>
            {story && (
              <div
                className="flex items-center gap-4 mt-3 pt-3"
                style={{ borderTop: `1px solid ${ms.border}20` }}
              >
                {story.active_theme_count > 0 && (
                  <div className="text-center">
                    <p className="text-base font-black" style={{ color: "#10b981" }}>
                      {story.active_theme_count}
                    </p>
                    <p className="text-[9px] text-slate-500">Active</p>
                  </div>
                )}
                {story.building_theme_count > 0 && (
                  <div className="text-center">
                    <p className="text-base font-black" style={{ color: "#3b82f6" }}>
                      {story.building_theme_count}
                    </p>
                    <p className="text-[9px] text-slate-500">Building</p>
                  </div>
                )}
                {story.weakening_theme_count > 0 && (
                  <div className="text-center">
                    <p className="text-base font-black" style={{ color: "#f87171" }}>
                      {story.weakening_theme_count}
                    </p>
                    <p className="text-[9px] text-slate-500">Weakening</p>
                  </div>
                )}
                {onGoToDiscover && (
                  <button
                    onClick={onGoToDiscover}
                    className="ml-auto flex items-center gap-1.5 text-[10px] font-semibold px-2.5 py-1 rounded-full self-center transition-all active:scale-95"
                    style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
                  >
                    <Compass size={9} />
                    Explore
                  </button>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── Conflicting signals ───────────────────────────────────────────── */}
      {knownConflicts.length > 0 && (
        <section>
          <SectionLabel>Conflicting signals</SectionLabel>
          <div className="space-y-2">
            {knownConflicts.map((conflict, i) => (
              <div
                key={i}
                className="rounded-xl p-3.5 flex items-start gap-2.5"
                style={{
                  background: "rgba(245,158,11,0.05)",
                  border: "1px solid rgba(245,158,11,0.16)",
                }}
              >
                <AlertCircle size={13} className="text-amber-400 shrink-0 mt-0.5" />
                <p className="text-xs text-amber-300 leading-relaxed">{conflict}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Key events ────────────────────────────────────────────────────── */}
      {keyEvents.length > 0 && (
        <section>
          <SectionLabel>Key events</SectionLabel>
          <div className="space-y-2">
            {keyEvents.slice(0, 6).map((ev, i) => (
              <EventCard key={i} ev={ev} />
            ))}
          </div>
        </section>
      )}

      {/* ── Worth watching ────────────────────────────────────────────────── */}
      {watchNext.length > 0 && (
        <section>
          <SectionLabel>Worth watching</SectionLabel>
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

      {/* ── Disclaimer ────────────────────────────────────────────────────── */}
      <div
        className="rounded-xl p-4 text-center"
        style={{
          background: "rgba(255,255,255,0.02)",
          border: "1px solid rgba(255,255,255,0.04)",
        }}
      >
        <p className="text-[11px] text-slate-600 leading-relaxed">
          Market intelligence only. Not financial advice. No trade execution.
        </p>
        {data.data_entitlement_note && (
          <p className="text-[10px] text-slate-700 mt-1">{data.data_entitlement_note}</p>
        )}
      </div>
    </div>
  );
}
