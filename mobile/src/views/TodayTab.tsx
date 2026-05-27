"use client";
// Today tab — M13D refactor.
// Leads with Market Story Hero, then Market Tape strip, then grouped cause
// stories (de-duplicated), event context, and worth-watching items.

import { useState, useEffect } from "react";
import {
  ArrowRight,
  AlertCircle,
  Eye,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  TrendingUp,
  Zap,
  Shield,
  Layers,
} from "lucide-react";
import type { MarketNowPayload, KeyEvent } from "@/lib/customerApi";
import type { CustomerStory } from "@/lib/customerStory";
import type {
  MarketClockState,
  FreshnessState,
  SinceAwaySummary,
} from "@/lib/useCustomerBriefing";
import {
  buildCustomerMarketStory,
  buildNarrativeParagraph,
  buildWhereLooking,
  buildWhatCouldChange,
  type TapeSnapshot,
} from "@/lib/customerBriefingModel";
import { buildCauseGroups, type MarketCauseGroup } from "@/lib/marketCauseStory";
import type { TapeEntry } from "@/app/api/market-tape/route";
import type { Headline } from "@/app/api/headlines/route";

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

// ── Tape → TapeSnapshot ───────────────────────────────────────────────────────

function deriveTapeSnapshot(tape: TapeEntry[]): TapeSnapshot {
  const by: Record<string, TapeEntry> = {};
  for (const t of tape) by[t.sym] = t;
  return {
    spy_pct:   by["SPY"]?.changePct ?? null,
    qqq_pct:   by["QQQ"]?.changePct ?? null,
    iwm_pct:   by["IWM"]?.changePct ?? null,
    tlt_pct:   by["TLT"]?.changePct ?? null,
    gld_pct:   by["GLD"]?.changePct ?? null,
    uso_pct:   by["USO"]?.changePct ?? null,
    dxy_pct:   by["UUP"]?.changePct ?? null, // UUP = US dollar proxy
    vix_level: by["VIX"]?.level     ?? null,
  };
}

// ── Market Tape ───────────────────────────────────────────────────────────────

function TapeItem({ entry }: { entry: TapeEntry }) {
  if (entry.type === "vol") {
    const level = entry.level;
    const vixColor =
      level == null   ? "#64748b" :
      level >= 25     ? "#f87171" :
      level >= 20     ? "#fbbf24" :
                        "#34d399";
    return (
      <div
        className="flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl shrink-0"
        style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
      >
        <span className="text-[11px] font-black" style={{ color: vixColor }}>
          {level != null ? level.toFixed(1) : "—"}
        </span>
        <span className="text-[10px] font-medium text-slate-400">VIX</span>
      </div>
    );
  }

  const pct = entry.changePct;
  const isPos = pct != null && pct > 0;
  const isNeg = pct != null && pct < 0;
  const color = isPos ? "#34d399" : isNeg ? "#f87171" : "#64748b";

  return (
    <div
      className="flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl shrink-0"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
    >
      <span className="text-[11px] font-black" style={{ color }}>
        {pct != null ? `${isPos ? "+" : ""}${pct.toFixed(1)}%` : "—"}
      </span>
      <span className="text-[10px] font-medium text-slate-400">{entry.label}</span>
    </div>
  );
}

function MarketTapeStrip({ tape }: { tape: TapeEntry[] }) {
  if (tape.length === 0) return null;
  return (
    <div className="overflow-x-auto" style={{ scrollbarWidth: "none" }}>
      <div className="flex gap-2 min-w-max">
        {tape.map(t => <TapeItem key={t.sym} entry={t} />)}
      </div>
    </div>
  );
}

// ── Event card ────────────────────────────────────────────────────────────────

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
                className="text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0 mt-0.5"
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
  tapeSnapshot: TapeSnapshot;
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
  tapeSnapshot,
  isRefreshing,
  freshnessState,
  freshnessLabel,
  onRefresh,
  onAskAbout,
  onGoToForces,
}: MarketStoryHeroProps) {
  const ms = buildCustomerMarketStory(data, story);
  const c  = regimeColors(ms.regime.state);

  const narrativeParagraph = buildNarrativeParagraph(data, ms, tapeSnapshot);

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
              className="text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1"
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
          style={{ color: "#94a3b8" }}
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

      {/* Narrative paragraph — replaces mechanical headline + summary */}
      <div className="px-4 pb-3">
        <p className="text-[13px] text-slate-200 leading-relaxed">
          {narrativeParagraph}
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

// ── Cause group card ──────────────────────────────────────────────────────────

function CauseGroupCard({
  group,
  onThemeSelect,
  onAskAbout,
}: {
  group: MarketCauseGroup;
  onThemeSelect: (id: string) => void;
  onAskAbout?: (ctx: string) => void;
}) {
  const card = group.display_card;
  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2.5">
        {group.is_cluster ? (
          <Layers size={12} style={{ color: "#f97316", flexShrink: 0 }} />
        ) : (
          <TrendingUp size={12} style={{ color: "#f97316", flexShrink: 0 }} />
        )}
        <p className="text-[13px] font-bold text-slate-100 flex-1">{card.cause_label}</p>
        <div className="flex items-center gap-1.5 shrink-0">
          {group.is_cluster && (
            <span
              className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
              style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
            >
              {group.driver_count} drivers
            </span>
          )}
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded"
            style={{ background: "rgba(255,255,255,0.05)", color: "#94a3b8" }}
          >
            {card.evidence_basis}
          </span>
        </div>
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
            <span className="text-[10px] text-slate-400 self-center ml-1">
              {card.connected_names_count}{" "}
              {card.connected_names_count !== 1 ? "names" : "name"}
            </span>
          )}
        </div>
      )}

      {/* Ask Decifer CTA */}
      {onAskAbout && (
        <button
          onClick={() => onAskAbout(`Why is ${card.cause_label.toLowerCase()} affecting markets?`)}
          className="mt-2.5 flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
          style={{ color: "#94a3b8" }}
        >
          Ask Decifer why
          <ArrowRight size={9} />
        </button>
      )}
    </div>
  );
}

// ── Where Decifer Is Looking ──────────────────────────────────────────────────

function WhereLookingSection({
  data,
  onAskAbout,
}: {
  data: MarketNowPayload;
  onAskAbout?: (ctx: string) => void;
}) {
  const { stories, names, empty } = buildWhereLooking(data);
  if (empty) return null;

  return (
    <section>
      <SectionLabel>Where Decifer is looking</SectionLabel>
      <div
        className="rounded-2xl p-4"
        style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
      >
        {/* Story / sector chips */}
        {stories.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {stories.map((s, i) => (
              <span
                key={i}
                className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
              >
                {s}
              </span>
            ))}
          </div>
        )}

        {/* Names list */}
        {names.length > 0 && (
          <div className="space-y-2.5">
            {names.map((n, i) => (
              <div key={i} className="flex items-start gap-2.5">
                <span
                  className="text-[11px] font-bold text-slate-200 shrink-0 w-11"
                >
                  {n.symbol}
                </span>
                <p className="text-[11px] text-slate-400 leading-relaxed line-clamp-2">{n.reason}</p>
              </div>
            ))}
          </div>
        )}

        {/* Ask CTA */}
        {onAskAbout && (stories.length > 0 || names.length > 0) && (
          <button
            onClick={() =>
              onAskAbout(
                `Which names are connected to ${stories[0] ?? "these themes"} today?`,
              )
            }
            className="mt-3 flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
            style={{ color: "#94a3b8" }}
          >
            Ask Decifer about these names
            <ArrowRight size={9} />
          </button>
        )}
      </div>
    </section>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  story: CustomerStory | null;
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
  const apiWatch   = data.watch_next?.length ? data.watch_next : (data.what_to_watch ?? []);
  const watchNext  = apiWatch.length > 0 ? apiWatch : buildWhatCouldChange(data);
  const groups     = buildCauseGroups(data);

  const [tape, setTape] = useState<TapeEntry[]>([]);
  useEffect(() => {
    fetch("/api/market-tape")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.tape) setTape(d.tape); })
      .catch(() => {});
  }, []);

  const tapeSnapshot = deriveTapeSnapshot(tape);

  // Headlines fallback: only fetch when backend key_events are absent
  const [headlines, setHeadlines] = useState<Headline[]>([]);
  useEffect(() => {
    if (keyEvents.length > 0) return;
    fetch("/api/headlines")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.headlines) setHeadlines(d.headlines.slice(0, 4)); })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyEvents.length]);

  return (
    <div className="px-4 pb-8 space-y-5 pt-3">

      {/* ── A: Market Story Hero ──────────────────────────────────────────── */}
      {story && (
        <section>
          <MarketStoryHero
            data={data}
            story={story}
            tapeSnapshot={tapeSnapshot}
            isRefreshing={isRefreshing}
            freshnessState={freshnessState}
            freshnessLabel={freshnessLabel}
            onRefresh={onRefresh}
            onAskAbout={onAskAbout}
            onGoToForces={onGoToForces}
          />
        </section>
      )}

      {/* ── B: Market tape ───────────────────────────────────────────────── */}
      {tape.length > 0 && (
        <section>
          <SectionLabel>Market snapshot</SectionLabel>
          <MarketTapeStrip tape={tape} />
        </section>
      )}

      {/* ── C: Since you were away ────────────────────────────────────────── */}
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
                      <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed line-clamp-2">
                        {item.detail}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Card>
              <p className="text-sm text-slate-400">Market story looks the same since you were away.</p>
              <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">Scroll down for the full briefing.</p>
            </Card>
          )}
        </section>
      )}

      {/* ── D: Real-world event context ───────────────────────────────────── */}
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

      {/* ── D-fallback: Recent market headlines (when no backend key_events) ── */}
      {keyEvents.length === 0 && headlines.length > 0 && (
        <section>
          <SectionLabel>Latest market headlines</SectionLabel>
          <div className="space-y-2">
            {headlines.map((h, i) => (
              <div
                key={i}
                className="rounded-xl px-3.5 py-3"
                style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
              >
                <p className="text-[13px] font-semibold text-slate-100 leading-snug">{h.title}</p>
                <p className="text-[10px] text-slate-500 mt-1">
                  {h.source}
                  {" · "}
                  {h.minutesAgo < 60
                    ? `${h.minutesAgo}m ago`
                    : `${Math.round(h.minutesAgo / 60)}h ago`}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── E: What is moving markets (grouped) ──────────────────────────── */}
      {groups.length > 0 && (
        <section>
          <SectionLabel>What is moving markets</SectionLabel>
          <div className="space-y-3">
            {groups.map((group, i) => (
              <CauseGroupCard
                key={i}
                group={group}
                onThemeSelect={onThemeSelect}
                onAskAbout={onAskAbout}
              />
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

      {/* ── F: Where Decifer is looking ───────────────────────────────────── */}
      <WhereLookingSection data={data} onAskAbout={onAskAbout} />

      {/* ── G: What could change the picture ─────────────────────────────── */}
      {watchNext.length > 0 && (
        <section>
          <SectionLabel>What could change the picture</SectionLabel>
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
        <p className="text-[11px] text-slate-500 leading-relaxed">
          Market intelligence only. Not financial advice. No trade execution.
        </p>
        {data.data_entitlement_note && (
          <p className="text-[10px] text-slate-500 mt-1">{data.data_entitlement_note}</p>
        )}
      </div>
    </div>
  );
}
