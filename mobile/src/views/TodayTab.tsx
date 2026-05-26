"use client";
// Today tab — M13B refactor.
// Leads with Market Story Hero (regime + macro + bullets + caution + watch next),
// then: since-away | event context | market forces | worth watching.
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
  Zap,
  Shield,
} from "lucide-react";
import type { MarketNowPayload, KeyEvent } from "@/lib/customerApi";
import type { CustomerStory } from "@/lib/customerStory";
import type { MarketCauseCard } from "@/lib/marketCauseStory";
import type {
  MarketClockState,
  FreshnessState,
  SinceAwaySummary,
} from "@/lib/useCustomerBriefing";
import { buildCustomerMarketStory } from "@/lib/customerBriefingModel";

// ── Helpers ───────────────────────────────────────────────────────────────────

function regimeColors(state: string) {
  if (state === "risk-on")  return { border: "#10b981", text: "#34d399", bg: "rgba(16,185,129,0.06)", badge: "rgba(16,185,129,0.15)" };
  if (state === "risk-off") return { border: "#ef4444", text: "#f87171", bg: "rgba(239,68,68,0.06)",  badge: "rgba(239,68,68,0.15)"  };
  if (state === "mixed")    return { border: "#f59e0b", text: "#fbbf24", bg: "rgba(245,158,11,0.06)", badge: "rgba(245,158,11,0.15)"  };
  return { border: "#334155", text: "#94a3b8", bg: "rgba(255,255,255,0.03)", badge: "rgba(255,255,255,0.08)" };
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

// ── Market Story Hero ─────────────────────────────────────────────────────────

interface MarketStoryHeroProps {
  data: MarketNowPayload;
  story: CustomerStory;
  isRefreshing: boolean;
  freshnessState: FreshnessState;
  freshnessLabel: string;
  onRefresh: () => Promise<void>;
  onAskAbout?: (ctx: string) => void;
  onGoToForces?: () => void;
}

function MarketStoryHero({
  data,
  story,
  isRefreshing,
  freshnessState,
  freshnessLabel,
  onRefresh,
  onAskAbout,
  onGoToForces,
}: MarketStoryHeroProps) {
  const ms = buildCustomerMarketStory(data, story);
  const c  = regimeColors(ms.regime.state);

  const freshnessTimeCopy =
    freshnessState === "fresh" && data.freshness_timestamp
      ? `Fresh as of ${new Date(data.freshness_timestamp).toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          timeZoneName: "short",
        })}`
      : freshnessLabel;

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{
        background: c.bg,
        border: `1.5px solid ${c.border}30`,
      }}
    >
      {/* Regime strip */}
      <div
        className="px-4 pt-4 pb-3 flex items-center justify-between gap-2"
        style={{ borderBottom: `1px solid ${c.border}20` }}
      >
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] font-bold px-2.5 py-1 rounded-full"
            style={{ background: c.badge, color: c.text }}
          >
            {ms.regime.label}
          </span>
          {ms.has_live_events && (
            <span
              className="text-[9px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1"
              style={{ background: "rgba(249,115,22,0.12)", color: "#fb923c" }}
            >
              <Zap size={8} />
              Live events
            </span>
          )}
        </div>
        <button
          onClick={onRefresh}
          disabled={isRefreshing}
          className="text-[10px] font-semibold flex items-center gap-1 transition-all active:scale-95"
          style={{ color: "#475569" }}
          aria-label="Refresh"
        >
          <RefreshCw size={9} className={isRefreshing ? "animate-spin" : ""} />
          {isRefreshing ? "Updating..." : freshnessTimeCopy}
        </button>
      </div>

      {/* Macro label */}
      <div className="px-4 pt-3 pb-1">
        <p className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: c.text }}>
          {ms.macro_label}
        </p>
      </div>

      {/* Headline */}
      <div className="px-4 pb-3">
        <p className="text-[15px] font-bold text-slate-100 leading-snug">
          {ms.headline}
        </p>
      </div>

      {/* Summary */}
      <div className="px-4 pb-3">
        <p className="text-[12px] text-slate-300 leading-relaxed">
          {ms.summary}
        </p>
      </div>

      {/* Supporting bullets */}
      {ms.supporting_bullets.length > 0 && (
        <div className="px-4 pb-3 space-y-1.5">
          {ms.supporting_bullets.map((bullet, i) => (
            <div key={i} className="flex items-start gap-2.5">
              <span
                className="w-1 h-1 rounded-full shrink-0 mt-1.5"
                style={{ background: c.text }}
              />
              <p className="text-[11px] text-slate-400 leading-relaxed">{bullet}</p>
            </div>
          ))}
        </div>
      )}

      {/* Caution */}
      {ms.caution && (
        <div
          className="mx-4 mb-3 rounded-xl px-3 py-2.5 flex items-start gap-2"
          style={{
            background: "rgba(245,158,11,0.07)",
            border: "1px solid rgba(245,158,11,0.18)",
          }}
        >
          <Shield size={11} className="text-amber-400 shrink-0 mt-0.5" />
          <p className="text-[11px] text-amber-300 leading-relaxed">{ms.caution}</p>
        </div>
      )}

      {/* Watch next */}
      {ms.watch_next && (
        <div className="px-4 pb-3 flex items-start gap-2">
          <Eye size={11} className="text-slate-500 shrink-0 mt-0.5" />
          <p className="text-[11px] text-slate-400 leading-relaxed">
            <span className="text-slate-500 font-semibold">Worth watching: </span>
            {ms.watch_next}
          </p>
        </div>
      )}

      {/* CTAs */}
      <div
        className="px-4 pt-2.5 pb-4 flex items-center gap-3 flex-wrap"
        style={{ borderTop: `1px solid ${c.border}15` }}
      >
        {onAskAbout && (
          <button
            onClick={() => onAskAbout("Why is the market moving in this direction today?")}
            className="flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
            style={{ color: "#94a3b8" }}
          >
            Ask why
            <ArrowRight size={9} />
          </button>
        )}
        {onGoToForces && (
          <button
            onClick={onGoToForces}
            className="flex items-center gap-1.5 text-[10px] font-semibold px-2.5 py-1 rounded-full transition-all active:scale-95"
            style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
          >
            <Zap size={9} />
            See forces
          </button>
        )}
      </div>
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
  onGoToForces?: () => void;
}

export default function TodayTab({
  data,
  story,
  causeCards,
  sinceAway,
  freshnessState,
  freshnessLabel,
  isRefreshing,
  onRefresh,
  onThemeSelect,
  onAskAbout,
  onGoToForces,
}: Props) {
  const keyEvents  = data.key_events ?? [];
  const watchNext  = data.watch_next?.length ? data.watch_next : (data.what_to_watch ?? []);

  return (
    <div className="px-4 pb-8 space-y-5 pt-3">

      {/* ── A: Market Story Hero ──────────────────────────────────────────── */}
      {story && (
        <section>
          <MarketStoryHero
            data={data}
            story={story}
            isRefreshing={isRefreshing}
            freshnessState={freshnessState}
            freshnessLabel={freshnessLabel}
            onRefresh={onRefresh}
            onAskAbout={onAskAbout}
            onGoToForces={onGoToForces}
          />
        </section>
      )}

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
              <p className="text-sm text-slate-400">No major new changes detected since your last visit.</p>
              <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">The latest market briefing is below.</p>
            </Card>
          )}
        </section>
      )}

      {/* ── C: Real-world event context ───────────────────────────────────── */}
      {keyEvents.length > 0 && (
        <section>
          <SectionLabel>Real-world events behind the move</SectionLabel>
          <div className="space-y-2">
            {keyEvents.slice(0, 5).map((ev, i) => (
              <EventCard key={i} ev={ev} />
            ))}
          </div>
          {onAskAbout && keyEvents.length > 0 && (
            <button
              onClick={() => onAskAbout("What real-world events are driving markets today?")}
              className="mt-2 flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
              style={{ color: "#94a3b8" }}
            >
              Ask Decifer about these events
              <ArrowRight size={9} />
            </button>
          )}
        </section>
      )}

      {/* ── D: What is moving markets ─────────────────────────────────────── */}
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
                  <TrendingUp size={12} style={{ color: "#f97316", flexShrink: 0 }} />
                  <p className="text-[13px] font-bold text-slate-100 flex-1">{card.cause_label}</p>
                  <span
                    className="text-[9px] font-medium px-1.5 py-0.5 rounded shrink-0"
                    style={{ background: "rgba(255,255,255,0.05)", color: "#6b7280" }}
                  >
                    {card.evidence_basis}
                  </span>
                </div>

                <p className="text-[12px] text-slate-300 leading-relaxed mb-1">{card.what_happened}</p>
                <p className="text-[12px] text-slate-400 leading-relaxed">{card.market_impact}</p>

                {/* Connected themes */}
                {card.connected_themes.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2.5">
                    {card.connected_themes.slice(0, 3).map((t, j) => (
                      <button
                        key={j}
                        onClick={() => {
                          if (card.primary_market_now_id) onThemeSelect(card.primary_market_now_id);
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
                      onAskAbout(`Why is ${card.cause_label.toLowerCase()} moving markets?`)
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
          {onGoToForces && (
            <button
              onClick={onGoToForces}
              className="mt-3 w-full flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-[11px] font-semibold transition-all active:scale-[0.98]"
              style={{
                background: "rgba(249,115,22,0.06)",
                border: "1px solid rgba(249,115,22,0.15)",
                color: "#fb923c",
              }}
            >
              <Zap size={10} />
              See all active forces
            </button>
          )}
        </section>
      )}

      {/* ── E: Worth watching ─────────────────────────────────────────────── */}
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

      {/* ── Disclaimer ─────────────────────────────────────────────────────── */}
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
