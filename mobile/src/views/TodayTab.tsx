"use client";
// Today tab — Sprint M16 redesign.

import { useState, useEffect } from "react";
import {
  ArrowRight,
  RefreshCw,
  Shield,
  Zap,
  Layers,
  TrendingUp,
  TrendingDown,
  CalendarDays,
  BarChart2,
  ChevronDown,
} from "lucide-react";
import type { MarketNowPayload, TtgSymbolCard } from "@/lib/customerApi";
import { fetchTtgThemes, fetchTtgThemeDetail } from "@/lib/customerApi";
import type { CustomerStory } from "@/lib/customerStory";
import type {
  MarketClockState,
  FreshnessState,
  SinceAwaySummary,
  MarketSession,
} from "@/lib/useCustomerBriefing";
import {
  buildCustomerMarketStory,
  buildNarrativeParagraph,
  buildWhereLooking,
  buildCustomerForces,
  type TapeSnapshot,
  type CustomerMarketForce,
} from "@/lib/customerBriefingModel";
import { buildCauseGroups, type MarketCauseGroup } from "@/lib/marketCauseStory";
import type { TapeEntry } from "@/app/api/market-tape/route";
import type { MarketMoversPayload, Mover } from "@/app/api/market-movers/route";
import type { MorningBriefPayload, EconEvent, EarningsItem, AnalystItem } from "@/app/api/morning-brief/route";
import type { NewsItem } from "@/app/api/market-news/route";
import { fetchUniverseSymbols } from "@/lib/customerApi";

// ── Regime colour palette ─────────────────────────────────────────────────────

function regimeColors(state: string) {
  if (state === "risk-on")
    return {
      border: "#10b981", text: "#34d399", badge: "rgba(16,185,129,0.18)",
      heroGradient: "linear-gradient(165deg, #0c2820 0%, #0d1b2a 60%, #080d15 100%)",
    };
  if (state === "risk-off")
    return {
      border: "#ef4444", text: "#f87171", badge: "rgba(239,68,68,0.18)",
      heroGradient: "linear-gradient(165deg, #200c0c 0%, #1a0d18 60%, #080d15 100%)",
    };
  if (state === "mixed")
    return {
      border: "#f59e0b", text: "#fbbf24", badge: "rgba(245,158,11,0.18)",
      heroGradient: "linear-gradient(165deg, #1f1508 0%, #1a180d 60%, #080d15 100%)",
    };
  return {
    border: "#334155", text: "#64748b", badge: "rgba(255,255,255,0.08)",
    heroGradient: "linear-gradient(165deg, #0d1520 0%, #0a1018 60%, #080d15 100%)",
  };
}

// ── Shared primitives ─────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-3" style={{ color: "#f97316" }}>
      {children}
    </p>
  );
}

function AskDeciferButton({ label }: { label: string }) {
  return (
    <span className="flex items-center gap-1 text-[10px] font-semibold text-slate-700 select-none cursor-not-allowed">
      {label}
      <ArrowRight size={9} />
    </span>
  );
}

// ── Countdown to market open ──────────────────────────────────────────────────

function msUntilNextMarketOpen(): number {
  const now = new Date();
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "long",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(now).reduce<Record<string, string>>(
    (acc, p) => ({ ...acc, [p.type]: p.value }), {}
  );

  const weekday  = parts.weekday ?? "";
  const nyH      = parseInt(parts.hour === "24" ? "0" : (parts.hour ?? "0"), 10);
  const nyM      = parseInt(parts.minute ?? "0", 10);
  const nyS      = parseInt(parts.second ?? "0", 10);
  const nowNYSec = nyH * 3600 + nyM * 60 + nyS;
  const openSec  = 9 * 3600 + 30 * 60; // 9:30 AM = 34200s

  // Find how many days ahead the next market open is
  let daysAhead = 0;
  if (weekday === "Saturday" || weekday === "Sunday" || nowNYSec >= openSec) {
    const DAYS = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
    const idx = DAYS.indexOf(weekday);
    for (let i = 1; i <= 7; i++) {
      if (!["Saturday","Sunday"].includes(DAYS[(idx + i) % 7])) { daysAhead = i; break; }
    }
  }

  return Math.max(0, (openSec - nowNYSec + daysAhead * 86400) * 1000);
}

function CountdownToOpen({ session }: { session: MarketSession }) {
  const [msLeft, setMsLeft] = useState(() => msUntilNextMarketOpen());

  useEffect(() => {
    if (session === "open") return;
    const t = setInterval(() => setMsLeft(msUntilNextMarketOpen()), 1000);
    return () => clearInterval(t);
  }, [session]);

  if (session === "open") return null;

  const totalSec = Math.floor(msLeft / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const formatted = h > 0
    ? `${h}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`
    : `${m}m ${String(s).padStart(2, "0")}s`;

  const tickColor = session === "pre_market" ? "#fbbf24" : "#475569";
  const labelColor = session === "pre_market" ? "#92400e" : "#1e293b";

  return (
    <div className="flex items-center gap-2 mt-2">
      <span
        className="text-[10px] font-bold uppercase tracking-widest"
        style={{ color: labelColor }}
      >
        Opens in
      </span>
      <span
        className="text-[15px] font-black tabular-nums leading-none"
        style={{ color: tickColor }}
        suppressHydrationWarning
      >
        {formatted}
      </span>
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
    dia_pct:   by["DIA"]?.changePct ?? null,
    iwm_pct:   by["IWM"]?.changePct ?? null,
    tlt_pct:   by["TLT"]?.changePct ?? null,
    gld_pct:   by["GLD"]?.changePct ?? null,
    uso_pct:   by["USO"]?.changePct ?? null,
    dxy_pct:   by["UUP"]?.changePct   ?? null,
    es_pct:    by["ESUSD"]?.changePct ?? null,
    nq_pct:    by["NQUSD"]?.changePct ?? null,
    vix_level: by["VIX"]?.level       ?? null,
  };
}

// ── Force direction helper ────────────────────────────────────────────────────

const NEGATIVE_FORCES = new Set([
  "geopolitical_risk_rising", "futures_risk_off", "yields_rising",
  "oil_supply_shock", "smh_tactical_weakness", "reits_falling_yield",
]);

const FORCE_ICON: Record<string, string> = {
  ai_capex_growth:         "AI",
  ai_compute_demand:       "GPU",
  geopolitical_risk_rising:"GEO",
  futures_risk_on:         "ES↑",
  futures_risk_off:        "ES↓",
  yields_falling:          "10Y↓",
  yields_rising:           "10Y↑",
  risk_on_rotation:        "RISK",
  gold_safe_haven_bid:     "GLD",
  credit_stress_easing:    "HYG",
  small_cap_risk_on:       "IWM",
  oil_supply_shock:        "OIL",
  smh_tactical_weakness:   "SMH",
  reits_falling_yield:     "REIT",
};

// ── Story circle ──────────────────────────────────────────────────────────────

function StoryCircle({
  force,
  onSelect,
}: {
  force: CustomerMarketForce;
  onSelect: (f: CustomerMarketForce) => void;
}) {
  const isNeg = NEGATIVE_FORCES.has(force.id);
  const color = isNeg ? "#f87171" : "#34d399";
  const icon  = FORCE_ICON[force.id] ?? force.label.slice(0, 2).toUpperCase();

  return (
    <button
      onClick={() => onSelect(force)}
      className="flex flex-col items-center gap-1.5 shrink-0 transition-all active:scale-95"
    >
      <div
        className="w-14 h-14 rounded-full flex items-center justify-center"
        style={{
          background: `${color}15`,
          border: `2.5px solid ${color}60`,
          boxShadow: `0 0 14px ${color}20`,
        }}
      >
        <span className="text-[11px] font-black tracking-tight" style={{ color }}>
          {icon}
        </span>
      </div>
      <span
        className="text-[9px] font-semibold text-center leading-tight"
        style={{ color: "#e2e8f0", maxWidth: "56px" }}
      >
        {force.label}
      </span>
    </button>
  );
}

// ── Story circles strip ───────────────────────────────────────────────────────

function StoryCirclesStrip({
  data,
  onSelect,
}: {
  data: MarketNowPayload;
  onSelect: (f: CustomerMarketForce) => void;
}) {
  const { active } = buildCustomerForces(data);
  if (active.length === 0) return null;

  return (
    <div
      className="py-3"
      style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
    >
      <div
        className="overflow-x-auto"
        style={{ scrollbarWidth: "none", WebkitOverflowScrolling: "touch" as never,
          maskImage: "linear-gradient(to right, black 80%, transparent 100%)",
          WebkitMaskImage: "linear-gradient(to right, black 80%, transparent 100%)" }}
      >
        <div className="flex gap-5 px-5 min-w-max pr-8">
          {active.slice(0, 10).map((force, i) => (
            <StoryCircle key={i} force={force} onSelect={onSelect} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Force story sheet ─────────────────────────────────────────────────────────

function ForceStorySheet({
  force,
  onClose,
  onAskAbout,
}: {
  force: CustomerMarketForce;
  onClose: () => void;
  onAskAbout?: (ctx: string) => void;
}) {
  const isNeg  = NEGATIVE_FORCES.has(force.id);
  const color  = isNeg ? "#f87171" : "#34d399";
  const dirLabel = isNeg ? "Market headwind" : "Market tailwind";
  const icon   = FORCE_ICON[force.id] ?? force.label.slice(0, 2).toUpperCase();

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(0,0,0,0.65)" }}
        onClick={onClose}
      />
      {/* Sheet */}
      <div
        className="fixed bottom-0 left-0 right-0 z-50 rounded-t-3xl overflow-y-auto"
        style={{
          background: "#0d1520",
          border: "1px solid rgba(255,255,255,0.1)",
          maxHeight: "82vh",
        }}
      >
        {/* Handle */}
        <div className="flex justify-center pt-3 pb-2">
          <div className="w-10 h-1 rounded-full" style={{ background: "rgba(255,255,255,0.15)" }} />
        </div>

        <div className="px-5 pb-10">
          {/* Header */}
          <div className="flex items-center gap-3 mb-5">
            <div
              className="w-14 h-14 rounded-full flex items-center justify-center shrink-0"
              style={{ background: `${color}15`, border: `2.5px solid ${color}60` }}
            >
              <span className="text-[13px] font-black" style={{ color }}>{icon}</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[20px] font-black text-white leading-tight">{force.label}</p>
              <p className="text-[11px] font-semibold mt-0.5" style={{ color }}>
                {dirLabel} · Active now
              </p>
            </div>
            <button onClick={onClose} className="text-slate-500 text-[11px] px-2 py-1">
              close
            </button>
          </div>

          {/* Why it matters */}
          {force.why_it_matters && (
            <div className="mb-5">
              <p className="text-[10px] font-bold uppercase tracking-widest mb-2" style={{ color: "#f97316" }}>
                Why it matters
              </p>
              <p className="text-[14px] text-white leading-relaxed">{force.why_it_matters}</p>
            </div>
          )}

          {/* Market impact */}
          {force.market_impact && (
            <div className="mb-5">
              <p className="text-[10px] font-bold uppercase tracking-widest mb-2" style={{ color: "#f97316" }}>
                Market impact
              </p>
              <p className="text-[13px] text-slate-200 leading-relaxed">{force.market_impact}</p>
            </div>
          )}

          {/* Risk to watch */}
          {force.risk_to_monitor && (
            <div
              className="rounded-xl px-3 py-3 mb-5 flex items-start gap-2"
              style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,0.2)" }}
            >
              <Shield size={12} className="text-amber-400 shrink-0 mt-0.5" />
              <p className="text-[12px] text-amber-200 leading-relaxed">{force.risk_to_monitor}</p>
            </div>
          )}

          {/* Connected themes */}
          {force.connected_theme_labels.length > 0 && (
            <div className="mb-5">
              <p className="text-[10px] font-bold uppercase tracking-widest mb-2" style={{ color: "#f97316" }}>
                Connected themes
              </p>
              <div className="flex flex-wrap gap-1.5">
                {force.connected_theme_labels.map((lbl, i) => (
                  <span
                    key={i}
                    className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
                    style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
                  >
                    {lbl}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Ask CTA — greyed out until Ask Decifer is wired */}
          <div
            className="w-full py-3.5 rounded-2xl text-[14px] font-bold text-center cursor-not-allowed select-none"
            style={{ background: "rgba(255,255,255,0.03)", color: "#334155", border: "1px solid rgba(255,255,255,0.06)" }}
          >
            Ask Decifer about this →
          </div>
        </div>
      </div>
    </>
  );
}

// ── Hero header ───────────────────────────────────────────────────────────────

interface HeroHeaderProps {
  data: MarketNowPayload;
  story: CustomerStory;
  tapeSnapshot: TapeSnapshot;
  clock: MarketClockState;
  isRefreshing: boolean;
  freshnessLabel: string;
  freshnessState: FreshnessState;
  onRefresh: () => Promise<void>;
}

function HeroHeader({
  data,
  story,
  tapeSnapshot,
  clock,
  isRefreshing,
  freshnessLabel,
  freshnessState,
  onRefresh,
}: HeroHeaderProps) {
  const ms = buildCustomerMarketStory(data, story);
  const c  = regimeColors(ms.regime.state);

  const isMktOpen = clock.session === "open";
  const spy = tapeSnapshot.spy_pct;
  const qqq = tapeSnapshot.qqq_pct;
  const dia = tapeSnapshot.dia_pct;
  const vix = tapeSnapshot.vix_level;
  const pctColor = (v: number | null) =>
    v == null ? "#64748b" : v > 0 ? "#34d399" : v < 0 ? "#f87171" : "#94a3b8";
  const pctSign  = (v: number | null) => (v != null && v > 0 ? "+" : "");

  const freshnessTimeCopy =
    freshnessState === "fresh" && data.freshness_timestamp
      ? new Date(data.freshness_timestamp).toLocaleTimeString("en-US", {
          hour: "2-digit", minute: "2-digit", timeZone: "America/New_York",
        }) + " New York"
      : freshnessLabel;

  const sessionDot =
    clock.session === "open"        ? "#34d399" :
    clock.session === "pre_market"  ? "#fbbf24" :
    clock.session === "after_hours" ? "#94a3b8" :
                                      "#475569";

  const localEqNY = clock.localTime === clock.newYorkTime;

  return (
    <div style={{ background: c.heroGradient }}>
      <div className="px-5 pt-5 pb-6">

        {/* ── Row 1: greeting left, clocks right ── */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <p
              className="font-black leading-tight text-white"
              style={{ fontSize: "30px" }}
              suppressHydrationWarning
            >
              {clock.greeting}.
            </p>
            <p
              className="text-[12px] text-slate-400 mt-0.5"
              suppressHydrationWarning
            >
              {clock.sessionLabel}
            </p>
            <CountdownToOpen session={clock.session} />
          </div>

          {/* Clocks stacked — NY always on top */}
          <div className="text-right pt-1">
            <div className="mb-1">
              <p
                className="text-[17px] font-black text-slate-100 leading-none"
                suppressHydrationWarning
              >
                {clock.newYorkTime}
              </p>
              <p className="text-[9px] font-semibold uppercase tracking-wide text-slate-500 mt-0.5">
                New York
              </p>
            </div>
            {!localEqNY && (
              <div className="mt-2">
                <p
                  className="text-[14px] font-semibold text-slate-400 leading-none"
                  suppressHydrationWarning
                >
                  {clock.localTime}
                </p>
                <p className="text-[9px] font-semibold uppercase tracking-wide text-slate-600 mt-0.5">
                  Local
                </p>
              </div>
            )}
          </div>
        </div>

        {/* ── Divider ── */}
        <div className="mb-4" style={{ height: "1px", background: `${c.border}20` }} />

        {/* ── Row 2: regime badge + refresh ── */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: sessionDot }} />
            <span
              className="text-[10px] font-bold px-2.5 py-0.5 rounded-full"
              style={{ background: c.badge, color: c.text }}
            >
              {ms.regime.label}
            </span>
          </div>
          <button
            onClick={onRefresh}
            disabled={isRefreshing}
            className="flex items-center gap-1 text-[10px] text-slate-600 transition-all active:scale-95"
          >
            <RefreshCw size={9} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "…" : freshnessTimeCopy}
          </button>
        </div>

        {/* ── Row 3: index numbers or regime fallback ── */}
        {/* When open: show SPY. When closed/pre-market: show ES futures if available, fall back to SPY */}
        {(() => {
          const primaryPct = isMktOpen ? spy : (tapeSnapshot.es_pct ?? spy);
          const primaryLabel = isMktOpen ? "S&P 500" : (tapeSnapshot.es_pct != null ? "S&P FUTURES" : "S&P 500 (prev close)");
          const primaryColor = primaryPct == null ? "#64748b" : primaryPct > 0 ? "#34d399" : primaryPct < 0 ? "#f87171" : "#94a3b8";
          const primarySign = primaryPct != null && primaryPct > 0 ? "+" : "";

          const secPct = isMktOpen ? qqq : (tapeSnapshot.nq_pct ?? qqq);
          const secLabel = isMktOpen ? "NASDAQ" : (tapeSnapshot.nq_pct != null ? "NASDAQ FUT." : "NASDAQ");

          if (primaryPct == null) return (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">Market view</p>
              <p className="font-black leading-tight" style={{ color: c.text, fontSize: "26px" }}>{ms.macro_label}</p>
            </div>
          );

          return (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">{primaryLabel}</p>
              <p className="font-black leading-none mb-3" style={{ color: primaryColor, fontSize: "52px" }}>
                {primarySign}{primaryPct.toFixed(2)}%
              </p>
              <div className="flex items-center gap-4">
                {secPct != null && (
                  <div>
                    <p className="text-[9px] uppercase text-slate-600 tracking-wide mb-0.5">{secLabel}</p>
                    <p className="text-[15px] font-black leading-none" style={{ color: pctColor(secPct) }}>
                      {pctSign(secPct)}{secPct.toFixed(2)}%
                    </p>
                  </div>
                )}
                {isMktOpen && dia != null && (
                  <div>
                    <p className="text-[9px] uppercase text-slate-600 tracking-wide mb-0.5">Dow</p>
                    <p className="text-[15px] font-black leading-none" style={{ color: pctColor(dia) }}>
                      {pctSign(dia)}{dia.toFixed(2)}%
                    </p>
                  </div>
                )}
                {vix != null && (
                  <div>
                    <p className="text-[9px] uppercase text-slate-600 tracking-wide mb-0.5">VIX</p>
                    <p className="text-[15px] font-black leading-none"
                      style={{ color: vix >= 25 ? "#f87171" : vix >= 20 ? "#fbbf24" : "#64748b" }}>
                      {vix.toFixed(1)}
                    </p>
                  </div>
                )}
              </div>
              <p className="text-[12px] text-slate-200 mt-3">{ms.macro_label}</p>
            </div>
          );
        })()}
      </div>

      <div style={{ height: "1px", background: `${c.border}25` }} />
    </div>
  );
}

// ── Market narrative ──────────────────────────────────────────────────────────

function MarketNarrative({
  data,
  story,
  tapeSnapshot,
  clock,
  morningBrief,
  onAskAbout,
  onGoToForces,
}: {
  data: MarketNowPayload;
  story: CustomerStory;
  tapeSnapshot: TapeSnapshot;
  clock: MarketClockState;
  morningBrief: MorningBriefPayload | null;
  onAskAbout?: (ctx: string) => void;
  onGoToForces?: () => void;
}) {
  const ms = buildCustomerMarketStory(data, story);
  const isOpen = clock.session === "open";

  // Build opening sentence based on session + available data
  let opener = "";
  if (!isOpen && tapeSnapshot.es_pct != null) {
    const esSign = tapeSnapshot.es_pct >= 0 ? "+" : "";
    const nqPart = tapeSnapshot.nq_pct != null
      ? `, Nasdaq futures ${tapeSnapshot.nq_pct >= 0 ? "+" : ""}${tapeSnapshot.nq_pct.toFixed(2)}%`
      : "";
    const direction = tapeSnapshot.es_pct >= 0.1 ? "pointing to a higher open" : tapeSnapshot.es_pct <= -0.1 ? "pointing lower" : "flat overnight";
    opener = `S&P futures ${esSign}${tapeSnapshot.es_pct.toFixed(2)}%${nqPart} — ${direction}.`;
  } else {
    opener = buildNarrativeParagraph(data, ms, tapeSnapshot);
  }

  // Build specific tape insights (things that actually changed today)
  const insights: string[] = [];
  const uso = tapeSnapshot.uso_pct;
  const tlt = tapeSnapshot.tlt_pct;
  const gld = tapeSnapshot.gld_pct;
  const iwm = tapeSnapshot.iwm_pct;
  const spyTape = tapeSnapshot.spy_pct;
  const dxy = tapeSnapshot.dxy_pct;

  if (uso != null && Math.abs(uso) > 0.6) {
    if (uso < 0) insights.push(`Oil down ${Math.abs(uso).toFixed(1)}% — supply shock premium is fading.`);
    else insights.push(`Oil up ${uso.toFixed(1)}% — supply disruption fears are building.`);
  }
  if (tlt != null && Math.abs(tlt) > 0.3) {
    if (tlt > 0) insights.push(`Bonds gained ${tlt.toFixed(1)}% — rate cut expectations are firming.`);
    else insights.push(`Bonds fell ${Math.abs(tlt).toFixed(1)}% — yields are pushing higher again.`);
  }
  if (iwm != null && spyTape != null && Math.abs(iwm - spyTape) > 0.6) {
    const diff = iwm - spyTape;
    if (diff > 0) insights.push(`Small caps outperformed large caps by ${diff.toFixed(1)}% — market breadth is widening.`);
    else insights.push(`Small caps lagged large caps by ${Math.abs(diff).toFixed(1)}% — the rally is narrow.`);
  }
  if (gld != null && Math.abs(gld) > 0.5) {
    if (gld > 0) insights.push(`Gold up ${gld.toFixed(1)}% — safe-haven demand is elevated.`);
    else insights.push(`Gold fell ${Math.abs(gld).toFixed(1)}% — risk appetite is healthy.`);
  }
  if (dxy != null && Math.abs(dxy) > 0.3) {
    if (dxy > 0) insights.push(`Dollar strengthening — headwind for US multinationals and commodities.`);
    else insights.push(`Dollar weakening — tailwind for international earners and commodities.`);
  }

  const conflicts = data.known_conflicts ?? [];
  const caution = ms.caution ?? (conflicts[0] ? conflicts[0] : null);

  const topEvent = (morningBrief?.econ ?? []).find(e => e.impact === "High" && !e.actual);
  const watchText = topEvent
    ? `${econPlainLabel(topEvent.event)}${topEvent.estimate != null ? ` — forecast ${topEvent.estimate}${topEvent.unit ?? ""}` : ""}${topEvent.time && topEvent.time !== "All Day" ? ` at ${formatEconTime(topEvent.time)} ET` : ""}.`
    : null;

  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
    >
      <p className="text-[13px] text-slate-200 leading-relaxed">{opener}</p>

      {insights.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {insights.slice(0, 3).map((ins, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="w-1 h-1 rounded-full shrink-0 mt-1.5" style={{ background: "#f97316" }} />
              <p className="text-[11px] text-slate-300 leading-relaxed">{ins}</p>
            </li>
          ))}
        </ul>
      )}

      {caution && (
        <div
          className="mt-3 rounded-xl px-3 py-2.5 flex items-start gap-2"
          style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,0.18)" }}
        >
          <Shield size={11} className="text-amber-400 shrink-0 mt-0.5" />
          <p className="text-[11px] text-amber-300 leading-relaxed">{caution}</p>
        </div>
      )}

      {watchText && (
        <div
          className="mt-3 pt-3"
          style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
        >
          <p className="text-[9px] font-bold uppercase tracking-widest mb-1" style={{ color: "#64748b" }}>
            Today&apos;s watch
          </p>
          <p className="text-[11px] text-slate-300 leading-relaxed">{watchText}</p>
        </div>
      )}

      <div
        className="mt-3 pt-3 flex items-center gap-3"
        style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
      >
        <AskDeciferButton label="Ask why" />
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

// ── Mover detail sheet ────────────────────────────────────────────────────────

function MoverDetailSheet({
  mover,
  direction,
  onClose,
}: {
  mover: Mover;
  direction: "up" | "down";
  onClose: () => void;
}) {
  const [imgErr, setImgErr] = useState(false);
  const [profile, setProfile] = useState<{
    companyName?: string;
    description?: string;
    sector?: string;
    industry?: string;
    mktCap?: number;
  } | null>(null);
  const [profileTs, setProfileTs] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    fetch(`/api/name-fundamentals?symbol=${encodeURIComponent(mover.symbol)}`, { signal: ctrl.signal })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        setProfile(d?.profile ?? {});
        if (d?.ts) setProfileTs(d.ts);
      })
      .catch(() => { setProfile({}); });
    return () => ctrl.abort();
  }, [mover.symbol]);

  const color = direction === "up" ? "#34d399" : "#f87171";
  const sign  = direction === "up" ? "+" : "";

  const fmtCap = (n?: number) => {
    if (!n) return null;
    if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
    if (n >= 1e9)  return `$${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6)  return `$${(n / 1e6).toFixed(0)}M`;
    return null;
  };

  return (
    <>
      <div className="fixed inset-0 z-40" style={{ background: "rgba(0,0,0,0.65)" }} onClick={onClose} />
      <div
        className="fixed bottom-0 left-0 right-0 z-50 rounded-t-3xl overflow-y-auto"
        style={{ background: "#0d1520", border: "1px solid rgba(255,255,255,0.1)", maxHeight: "75vh" }}
      >
        <div className="flex justify-center pt-3 pb-2">
          <div className="w-10 h-1 rounded-full" style={{ background: "rgba(255,255,255,0.15)" }} />
        </div>
        <div className="px-5 pb-10">
          {/* Header */}
          <div className="flex items-center gap-3 mb-5">
            <div className="w-14 h-14 rounded-xl overflow-hidden shrink-0 flex items-center justify-center"
              style={{ background: "#1e293b" }}>
              {!imgErr ? (
                <img src={mover.logoUrl} alt={mover.symbol}
                  className="w-full h-full object-contain p-1"
                  onError={() => setImgErr(true)} />
              ) : (
                <span className="text-[13px] font-black text-slate-500">{mover.symbol.slice(0, 2)}</span>
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[22px] font-black text-white leading-none">{mover.symbol}</p>
              <p className="text-[13px] text-slate-300 mt-0.5 leading-tight truncate">{mover.name}</p>
            </div>
            <div className="text-right shrink-0">
              <p className="text-[22px] font-black leading-none" style={{ color }}>
                {sign}{mover.changePct.toFixed(1)}%
              </p>
              <p className="text-[12px] text-slate-400 mt-0.5">${mover.price.toFixed(2)}</p>
            </div>
          </div>

          {/* Context chips */}
          {(profile?.sector || fmtCap(profile?.mktCap)) && (
            <div className="flex gap-2 flex-wrap mb-5">
              {profile?.sector && (
                <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
                  style={{ background: "rgba(255,255,255,0.07)", color: "#94a3b8" }}>
                  {profile.sector}
                </span>
              )}
              {fmtCap(profile?.mktCap) && (
                <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
                  style={{ background: "rgba(255,255,255,0.07)", color: "#94a3b8" }}>
                  {fmtCap(profile?.mktCap)} mkt cap
                </span>
              )}
            </div>
          )}

          {/* Description */}
          {profile?.description && (
            <div className="mb-5">
              <p className="text-[10px] font-bold uppercase tracking-widest mb-2" style={{ color: "#f97316" }}>About</p>
              <p className="text-[13px] text-slate-200 leading-relaxed line-clamp-6">{profile.description}</p>
            </div>
          )}

          {!profile && (
            <div className="mb-5 animate-pulse">
              <div className="h-3 rounded mb-2" style={{ background: "rgba(255,255,255,0.06)", width: "40%" }} />
              <div className="h-3 rounded mb-1.5" style={{ background: "rgba(255,255,255,0.04)", width: "100%" }} />
              <div className="h-3 rounded mb-1.5" style={{ background: "rgba(255,255,255,0.04)", width: "90%" }} />
              <div className="h-3 rounded" style={{ background: "rgba(255,255,255,0.04)", width: "70%" }} />
            </div>
          )}

          {profile !== null && !profile?.sector && !profile?.description && !profile?.mktCap && (
            <p className="text-[13px] text-slate-500 mb-5">No company details available.</p>
          )}

          <p className="text-[10px] text-slate-700">
            Market data via Financial Modeling Prep · Market intelligence only
            {profileTs && (
              <> · as of {new Date(profileTs).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" })} New York</>
            )}
          </p>
        </div>
      </div>
    </>
  );
}

// ── Mover row ─────────────────────────────────────────────────────────────────

function MoverRow({
  mover,
  direction,
  onTap,
}: {
  mover: Mover;
  direction: "up" | "down";
  onTap: () => void;
}) {
  const [imgErr, setImgErr] = useState(false);
  const color    = direction === "up" ? "#34d399" : "#f87171";
  const sign     = direction === "up" ? "+" : "";
  const monogram = mover.symbol.slice(0, 2);

  return (
    <button
      onClick={onTap}
      className="w-full flex items-center gap-2 py-1.5 transition-all active:scale-[0.98]"
    >
      <div
        className="w-7 h-7 rounded-lg overflow-hidden shrink-0 flex items-center justify-center"
        style={{ background: "#1e293b" }}
      >
        {!imgErr ? (
          <img src={mover.logoUrl} alt={mover.symbol}
            className="w-full h-full object-contain p-0.5"
            onError={() => setImgErr(true)} />
        ) : (
          <span className="text-[9px] font-black" style={{ color: "#475569" }}>{monogram}</span>
        )}
      </div>

      <div className="flex-1 min-w-0 text-left">
        <p className="text-[12px] font-bold text-slate-200 truncate leading-none">{mover.symbol}</p>
        <p className="text-[10px] text-slate-500 truncate mt-0.5 leading-none">{mover.name}</p>
      </div>

      <span className="text-[13px] font-black shrink-0" style={{ color }}>
        {sign}{mover.changePct.toFixed(1)}%
      </span>
    </button>
  );
}

// ── Movers section ────────────────────────────────────────────────────────────

function MoversSection({ session }: { session?: MarketSession }) {
  const [data, setData] = useState<MarketMoversPayload | null>(null);
  const [selectedMover, setSelectedMover] = useState<{ mover: Mover; direction: "up" | "down" } | null>(null);

  useEffect(() => {
    fetch("/api/market-movers")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {});
  }, []);

  if (!data || (data.gainers.length === 0 && data.losers.length === 0)) return null;

  const gainers = data.gainers.slice(0, 5);
  const losers  = data.losers.slice(0, 5);

  const freshnessLabel = data.ts
    ? (() => {
        const diffMs = Date.now() - new Date(data.ts).getTime();
        const mins = Math.floor(diffMs / 60000);
        if (mins < 1) return "Just updated";
        if (mins < 60) return `Updated ${mins}m ago`;
        return `Updated ${Math.floor(mins / 60)}h ago`;
      })()
    : null;

  const sectionTitle =
    session === "open"        ? "Today's biggest moves" :
    session === "pre_market"  ? "Pre-market movers" :
    session === "after_hours" ? "After-hours movers" :
                                "Yesterday's biggest moves";

  return (
    <section>
      <div className="flex items-baseline justify-between mb-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.15em]" style={{ color: "#f97316" }}>
          {sectionTitle}
        </p>
        {freshnessLabel && (
          <span className="text-[10px] text-slate-600">{freshnessLabel}</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl px-3 py-3"
          style={{ background: "#141b26", border: "1px solid rgba(16,185,129,0.12)" }}>
          <p className="text-[9px] font-bold uppercase tracking-widest text-emerald-500 mb-2">↑ Gainers</p>
          {gainers.map((m, i) => (
            <MoverRow key={i} mover={m} direction="up" onTap={() => setSelectedMover({ mover: m, direction: "up" })} />
          ))}
        </div>
        <div className="rounded-2xl px-3 py-3"
          style={{ background: "#141b26", border: "1px solid rgba(239,68,68,0.12)" }}>
          <p className="text-[9px] font-bold uppercase tracking-widest text-red-400 mb-2">↓ Losers</p>
          {losers.map((m, i) => (
            <MoverRow key={i} mover={m} direction="down" onTap={() => setSelectedMover({ mover: m, direction: "down" })} />
          ))}
        </div>
      </div>

      {selectedMover && (
        <MoverDetailSheet
          mover={selectedMover.mover}
          direction={selectedMover.direction}
          onClose={() => setSelectedMover(null)}
        />
      )}
    </section>
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
    <div className="rounded-2xl p-4" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
      <div className="flex items-center gap-2 mb-2.5">
        {group.is_cluster
          ? <Layers size={12} style={{ color: "#f97316", flexShrink: 0 }} />
          : <TrendingUp size={12} style={{ color: "#f97316", flexShrink: 0 }} />}
        <p className="text-[13px] font-bold text-slate-100 flex-1">{card.cause_label}</p>
        <div className="flex items-center gap-1.5 shrink-0">
          {group.is_cluster && (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
              style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}>
              {group.driver_count} drivers
            </span>
          )}
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded"
            style={{ background: "rgba(255,255,255,0.05)", color: "#94a3b8" }}>
            {card.evidence_basis}
          </span>
        </div>
      </div>
      <p className="text-[12px] text-slate-300 leading-relaxed mb-1">{card.what_happened}</p>
      <p className="text-[12px] text-slate-200 leading-relaxed">{card.market_impact}</p>
      {card.connected_themes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2.5">
          {card.connected_themes.slice(0, 3).map((t, j) => (
            <button key={j}
              onClick={() => { if (card.primary_market_now_id) onThemeSelect(card.primary_market_now_id); }}
              className="text-[10px] font-semibold px-2 py-0.5 rounded-full transition-all active:scale-95"
              style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}>
              {t.ttgLabel}
            </button>
          ))}
          {card.connected_names_count > 0 && (
            <span className="text-[10px] text-slate-400 self-center ml-1">
              {card.connected_names_count} {card.connected_names_count !== 1 ? "names" : "name"}
            </span>
          )}
        </div>
      )}
      <div className="mt-2.5">
        <AskDeciferButton label="Ask Decifer why" />
      </div>
    </div>
  );
}

// ── Where Decifer is looking ──────────────────────────────────────────────────

interface NameEntry {
  symbol: string;
  reason: string;
  theme_label: string;
  exposure_type?: string;
}

function exposureColor(chip?: string): string {
  if (chip === "Direct") return "#34d399";
  if (chip === "Supply chain") return "#60a5fa";
  if (chip === "ETF") return "#94a3b8";
  return "#94a3b8";
}

// ── Economic event → active driver annotation ─────────────────────────────────

const ECON_DRIVER_KEYWORDS: Array<{ keywords: string[]; drivers: string[]; label: string }> = [
  { keywords: ["oil", "crude", "petroleum", "opec", "gasoline", "distillate", "refin", "cushing"], drivers: ["oil_supply_shock"], label: "Oil Supply" },
  { keywords: ["cpi", "consumer price", "inflation", "pce", "personal consumption", "price index", "core price"], drivers: ["yields_rising", "yields_falling"], label: "Inflation" },
  { keywords: ["fed", "fomc", "interest rate", "fed funds", "monetary policy", "beige book", "powell"], drivers: ["yields_rising", "yields_falling"], label: "Fed Policy" },
  { keywords: ["jobs", "employment", "unemployment", "nonfarm", "payroll", "adp", "labor", "jobless", "jolt", "jolts"], drivers: ["risk_on_rotation", "small_cap_risk_on"], label: "Jobs" },
  { keywords: ["gdp", "gross domestic product", "economic growth", "economic output"], drivers: ["risk_on_rotation"], label: "Growth" },
  { keywords: ["consumer confidence", "consumer sentiment", "retail sales", "consumer spending"], drivers: ["risk_on_rotation"], label: "Consumer" },
  { keywords: ["housing", "existing home", "new home", "building permit", "construction", "mortgage"], drivers: ["reits", "reits_falling_yield"], label: "Housing" },
  { keywords: ["ism", "manufacturing pmi", "factory orders", "industrial production"], drivers: ["risk_on_rotation"], label: "Manufacturing" },
];

function annotateEconEvent(eventName: string, activeDrivers: string[]): string | null {
  const lower = eventName.toLowerCase();
  for (const { keywords, drivers, label } of ECON_DRIVER_KEYWORDS) {
    if (keywords.some(k => lower.includes(k)) && drivers.some(d => activeDrivers.includes(d))) {
      return label;
    }
  }
  return null;
}

function formatEconTime(t: string): string {
  if (!t || t === "All Day") return "All Day";
  // "08:30:00" → "8:30 AM"
  const [h, m] = t.split(":").map(Number);
  if (isNaN(h)) return t;
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${h12}:${String(m).padStart(2, "0")} ${period}`;
}

function formatEarningsDate(dateStr: string): string {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const tomorrow = (() => { const d = new Date(); d.setDate(d.getDate() + 1); return d.toLocaleDateString("en-CA", { timeZone: "America/New_York" }); })();
  if (dateStr === today) return "Today";
  if (dateStr === tomorrow) return "Tomorrow";
  const d = new Date(dateStr + "T12:00:00Z");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function earningsTimeLabel(t: string): string {
  if (t === "bmo") return "Pre-market";
  if (t === "amc") return "After close";
  if (t === "dmh") return "During session";
  return "";
}

// ── Economic event plain-language labels ──────────────────────────────────────

const ECON_PLAIN_LABELS: Array<{ keywords: string[]; label: string }> = [
  { keywords: ["cftc s&p", "cftc s&p 500", "cftc spx"], label: "CFTC S&P 500 Positioning" },
  { keywords: ["cftc nasdaq", "cftc ndx", "cftc nasdaq 100"], label: "CFTC Nasdaq 100 Positioning" },
  { keywords: ["cftc crude oil", "cftc wti"], label: "CFTC Crude Oil Positioning" },
  { keywords: ["cftc gold", "cftc xau"], label: "CFTC Gold Positioning" },
  { keywords: ["cftc", "speculative net position", "commitment of traders"], label: "CFTC Futures Positioning" },
  { keywords: ["wholesale inventories"], label: "Wholesale Inventories" },
  { keywords: ["nonfarm payroll", "non-farm payroll", "nfp"], label: "Jobs Report — Non-Farm Payrolls" },
  { keywords: ["initial jobless claim"], label: "Weekly Jobless Claims (New Filings)" },
  { keywords: ["continuing jobless claim"], label: "Ongoing Unemployment Claims" },
  { keywords: ["jolts", "job openings and labor"], label: "Job Openings Report (JOLTS)" },
  { keywords: ["adp employment", "adp nonfarm"], label: "ADP Private Sector Jobs" },
  { keywords: ["unemployment rate"], label: "Unemployment Rate" },
  { keywords: ["core cpi", "cpi excl"], label: "Core Inflation — Excl. Food & Energy" },
  { keywords: ["cpi yoy", "consumer price index yoy"], label: "Consumer Inflation — Year on Year" },
  { keywords: ["cpi mom", "consumer price index mom"], label: "Consumer Inflation — Month on Month" },
  { keywords: ["core pce", "pce price index excl", "personal consumption expenditures excl"], label: "Core PCE Inflation — Fed's Preferred Gauge" },
  { keywords: ["pce price index yoy", "pce yoy"], label: "PCE Inflation — Year on Year" },
  { keywords: ["pce price index mom", "pce mom"], label: "PCE Inflation — Month on Month" },
  { keywords: ["personal income"], label: "Personal Income" },
  { keywords: ["personal spending", "personal consumption expenditures"], label: "Consumer Spending" },
  { keywords: ["fomc minutes", "fed minutes", "fomc meeting minutes"], label: "Fed Meeting Minutes (FOMC)" },
  { keywords: ["fomc", "federal open market committee", "fed rate", "interest rate decision"], label: "Fed Interest Rate Decision" },
  { keywords: ["fed chair", "powell speech", "yellen"], label: "Fed Chair Speech" },
  { keywords: ["beige book"], label: "Fed Beige Book — Regional Conditions" },
  { keywords: ["gdp annualized", "gdp qoq", "gdp growth", "gross domestic product"], label: "Economic Growth (GDP)" },
  { keywords: ["retail sales mom"], label: "Retail Sales — Month on Month" },
  { keywords: ["retail sales"], label: "Retail Sales" },
  { keywords: ["consumer confidence"], label: "Consumer Confidence Index" },
  { keywords: ["consumer sentiment", "michigan"], label: "Consumer Sentiment (Univ. of Michigan)" },
  { keywords: ["ism manufacturing", "manufacturing pmi", "pmi manufacturing"], label: "Manufacturing Activity (ISM)" },
  { keywords: ["ism services", "services pmi", "pmi services", "ism non-manufacturing", "ism non manufacturing"], label: "Services Sector Activity (ISM)" },
  { keywords: ["industrial production"], label: "Industrial Production" },
  { keywords: ["building permit"], label: "Building Permits" },
  { keywords: ["housing start"], label: "Housing Starts" },
  { keywords: ["existing home sale"], label: "Existing Home Sales" },
  { keywords: ["new home sale"], label: "New Home Sales" },
  { keywords: ["durable goods"], label: "Durable Goods Orders" },
  { keywords: ["crude oil inventories", "eia crude"], label: "Crude Oil Inventories (EIA)" },
  { keywords: ["natural gas inventories", "eia natural gas"], label: "Natural Gas Inventories (EIA)" },
  { keywords: ["trade balance", "current account"], label: "Trade Balance" },
  { keywords: ["ppi yoy", "producer price index yoy"], label: "Producer Prices — Year on Year" },
  { keywords: ["ppi mom", "producer price index mom"], label: "Producer Prices — Month on Month" },
  { keywords: ["producer price"], label: "Producer Price Index (PPI)" },
  { keywords: ["treasury auction", "note auction", "bond auction", "bill auction"], label: "Treasury Auction" },
  { keywords: ["empire state", "philly fed", "kansas city fed", "richmond fed", "dallas fed"], label: "Regional Manufacturing Survey" },
  { keywords: ["chicago pmi", "chicago business barometer"], label: "Chicago Business Activity" },
  { keywords: ["flash pmi", "composite pmi"], label: "Composite Business Activity (PMI)" },
];

function econPlainLabel(eventName: string): string {
  const lower = eventName.toLowerCase();
  for (const { keywords, label } of ECON_PLAIN_LABELS) {
    if (keywords.some(k => lower.includes(k))) return label;
  }
  return eventName;
}

interface EconContext {
  what: string;       // what this measures (1 sentence)
  watch: string;      // what direction means what for markets (1 sentence)
}

const ECON_CONTEXT: Array<{ keywords: string[]; ctx: EconContext }> = [
  {
    keywords: ["cftc s&p", "cftc s&p 500", "cftc spx"],
    ctx: { what: "Tracks how hedge funds and large speculators are positioned in S&P 500 futures.", watch: "Rising net longs = professional money leaning bullish on equities. Extreme positioning (very long or short) often precedes reversals." },
  },
  {
    keywords: ["cftc nasdaq", "cftc ndx", "cftc nasdaq 100"],
    ctx: { what: "Tracks speculative futures positioning in Nasdaq 100 — a direct gauge of sentiment on tech and growth stocks.", watch: "Crowded long positioning can amplify a selloff when it unwinds; net shorts building signals institutional caution on tech." },
  },
  {
    keywords: ["cftc crude oil", "cftc wti"],
    ctx: { what: "Shows how large traders are positioned in crude oil futures — net longs vs net shorts.", watch: "Rising speculative longs push oil prices up; a fast unwind can cause sharp oil drops which ripple into energy stocks." },
  },
  {
    keywords: ["cftc gold", "cftc xau"],
    ctx: { what: "Tracks speculative positioning in gold futures — a proxy for safe-haven and inflation hedging demand.", watch: "Net longs building signals rising fear or inflation expectations; sharp reduction often precedes a gold pullback." },
  },
  {
    keywords: ["cftc", "speculative net position", "commitment of traders"],
    ctx: { what: "Weekly snapshot of how large speculators (hedge funds, banks) are positioned in futures markets.", watch: "Extreme crowding in either direction is a contrarian signal — markets tend to reverse when everyone is already positioned the same way." },
  },
  {
    keywords: ["wholesale inventories"],
    ctx: { what: "Measures the monthly change in goods stockpiled by wholesalers — a supply chain health indicator.", watch: "Rising inventories with weak demand = goods sitting unsold, bearish for manufacturing. Rising inventories with strong demand = healthy restocking." },
  },
  {
    keywords: ["nonfarm payroll", "non-farm payroll", "nfp"],
    ctx: { what: "The most watched jobs report — measures how many new jobs the US economy added last month.", watch: "Strong jobs = Fed stays higher for longer (bad for bonds, mixed for stocks). Weak jobs = rate cut odds rise (good for bonds, growth stocks)." },
  },
  {
    keywords: ["initial jobless claim"],
    ctx: { what: "Weekly count of people filing for unemployment benefits for the first time — the freshest jobs data available.", watch: "Rising claims = labour market softening, rate cut expectations increase. Falling claims = economy still tight, Fed cautious." },
  },
  {
    keywords: ["continuing jobless claim"],
    ctx: { what: "Total number of people currently receiving unemployment benefits — shows how long people stay jobless.", watch: "Rising trend signals hiring has dried up and the unemployed are struggling to find new work — a leading recession indicator." },
  },
  {
    keywords: ["jolts", "job openings and labor"],
    ctx: { what: "Measures the number of unfilled job positions — a proxy for labour demand strength.", watch: "Falling openings = companies pulling back on hiring, Fed can cut sooner. Still-elevated openings = tight labour keeps inflation sticky." },
  },
  {
    keywords: ["core cpi", "cpi excl"],
    ctx: { what: "Measures inflation stripping out volatile food and energy — the Fed's preferred measure of underlying price pressure.", watch: "Above 3% keeps rate cut hopes on ice. Falling toward 2.5% or below is the clearest green light for Fed cuts." },
  },
  {
    keywords: ["cpi yoy", "consumer price index yoy"],
    ctx: { what: "Year-on-year change in what consumers pay for a basket of goods — the headline inflation number.", watch: "Above expectations = bond yields rise, growth stocks sell off. Below expectations = rate cut bets build, markets rally." },
  },
  {
    keywords: ["cpi mom", "consumer price index mom"],
    ctx: { what: "Month-on-month change in consumer prices — shows whether inflation is re-accelerating.", watch: "Even a small monthly overshoot (e.g. 0.4% vs 0.3% expected) moves bond markets because it compounds over the year." },
  },
  {
    keywords: ["core pce", "pce price index excl"],
    ctx: { what: "The Fed's single most-watched inflation gauge — strips out food and energy and adjusts for consumer substitution.", watch: "This is what determines the Fed's next move. Any print above 2.6% delays cuts; a print near 2.2% accelerates them." },
  },
  {
    keywords: ["pce price index", "pce yoy", "pce mom"],
    ctx: { what: "Broad measure of what consumers are spending and how prices are changing — feeds directly into Fed models.", watch: "Stronger spending = economy holding up; hotter prices = Fed cautious. Both together = stagflation risk." },
  },
  {
    keywords: ["fomc minutes", "fed minutes"],
    ctx: { what: "The detailed record of the Fed's last interest rate meeting — reveals what they were actually debating.", watch: "Hawkish language (more cuts delayed, inflation concern) = bond yields rise. Dovish language (worried about growth) = cut expectations move up." },
  },
  {
    keywords: ["fomc", "fed rate", "interest rate decision"],
    ctx: { what: "The Fed's decision on the overnight lending rate — the single most powerful lever in financial markets.", watch: "Surprise cut = equities and bonds rally. Surprise hold or hike = rates and dollar strengthen, growth stocks sell off." },
  },
  {
    keywords: ["powell speech", "fed chair", "fed governor", "fed bowman", "fed waller", "fed williams", "fed speech"],
    ctx: { what: "A speech from a Federal Reserve official — often used to signal upcoming policy shifts.", watch: "Listen for language around 'patient', 'data dependent', or 'still restrictive'. Any hint at timing of cuts or hikes moves bond markets immediately." },
  },
  {
    keywords: ["beige book"],
    ctx: { what: "The Fed's on-the-ground survey of economic conditions across 12 US districts — anecdotal but broad.", watch: "Phrases like 'slight decline' or 'modest growth' matter — a shift to weaker language across multiple districts is an early recession signal." },
  },
  {
    keywords: ["gdp annualized", "gdp qoq", "gdp growth", "gross domestic product"],
    ctx: { what: "The broadest measure of how fast the US economy grew last quarter — the score on the board.", watch: "Above 2.5% = economy running hot, Fed cautious. Below 1% = slowdown fears build, rate cut bets rise. Negative = recession alarm." },
  },
  {
    keywords: ["retail sales mom"],
    ctx: { what: "Monthly change in what consumers spent at stores and online — 70% of the US economy is consumer spending.", watch: "Strong retail = economy resilient, Fed less likely to cut. Weak retail = consumers pulling back, recession risk rising." },
  },
  {
    keywords: ["consumer confidence"],
    ctx: { what: "A survey of how optimistic Americans feel about the economy and their own finances.", watch: "Falling confidence = people are worried and will spend less — a leading indicator for weaker retail and GDP data ahead." },
  },
  {
    keywords: ["consumer sentiment", "michigan"],
    ctx: { what: "University of Michigan survey of consumer attitudes — also captures inflation expectations, which the Fed watches closely.", watch: "High inflation expectations in this survey are a direct Fed concern — it can cause them to stay hawkish even when data softens." },
  },
  {
    keywords: ["ism manufacturing", "manufacturing pmi", "pmi manufacturing"],
    ctx: { what: "Monthly survey of purchasing managers at factories — above 50 = expansion, below 50 = contraction.", watch: "A sustained read below 50 signals the manufacturing sector is in recession. New orders sub-index is the most forward-looking component." },
  },
  {
    keywords: ["ism services", "services pmi", "ism non-manufacturing"],
    ctx: { what: "Tracks activity in service industries (restaurants, healthcare, finance) which make up 80% of the US economy.", watch: "Services staying above 50 has kept the US out of recession even as manufacturing contracted. A drop below 50 here is serious." },
  },
  {
    keywords: ["durable goods"],
    ctx: { what: "Monthly orders for long-lasting manufactured items — planes, machines, appliances. A proxy for business investment.", watch: "Ex-aircraft orders matter most — rising business investment signals confidence. A drop signals companies tightening capex." },
  },
  {
    keywords: ["crude oil inventories", "eia crude"],
    ctx: { what: "Weekly US oil stockpile data — a direct measure of supply vs demand in the energy market.", watch: "Larger-than-expected build = supply glut, oil prices fall. Bigger-than-expected draw = tight supply, oil prices rise." },
  },
  {
    keywords: ["trade balance", "current account"],
    ctx: { what: "The difference between what the US exports and imports — a wider deficit means more foreign goods being bought.", watch: "A widening deficit can pressure the dollar. In tariff environments, this number becomes a political flashpoint." },
  },
  {
    keywords: ["ppi yoy", "ppi mom", "producer price"],
    ctx: { what: "Measures price changes at the factory gate — what producers charge before goods reach consumers.", watch: "PPI leads CPI by 1-3 months. A hot PPI print warns that consumer inflation may re-accelerate even if CPI looks tame today." },
  },
  {
    keywords: ["chicago pmi", "chicago business barometer", "chicago business activity"],
    ctx: { what: "A gauge of business activity in the Chicago region — considered an early signal for the national ISM Manufacturing report.", watch: "Below 45 adds to manufacturing recession concerns. Watch for the new orders component — it leads the headline by 1-2 months." },
  },
  {
    keywords: ["empire state", "philly fed", "kansas city fed", "richmond fed", "dallas fed"],
    ctx: { what: "Regional Federal Reserve survey of local manufacturing and business conditions.", watch: "On its own, limited impact. But a cluster of weak regional surveys (multiple below zero) is a reliable leading indicator for the national ISM." },
  },
  {
    keywords: ["building permit"],
    ctx: { what: "Monthly count of new building permits issued — a forward-looking gauge of housing construction activity.", watch: "Rising permits = housing market recovering, construction jobs incoming. Falling permits = developers see weak demand ahead." },
  },
  {
    keywords: ["housing start"],
    ctx: { what: "Measures how many new homes started construction last month — a real-time read on housing supply.", watch: "Strong starts with high rates = builders are betting on rate cuts. Weak starts = affordability crisis deepening, fewer homes in the pipeline." },
  },
  {
    keywords: ["existing home sale"],
    ctx: { what: "Volume of previously-owned homes sold — represents ~90% of the housing market.", watch: "Weak sales despite demand signals a supply lock-in effect (owners won't sell to avoid losing a low mortgage rate) — a structural issue for affordability." },
  },
];

function econWhatItMeans(eventName: string): EconContext | null {
  const lower = eventName.toLowerCase();
  for (const { keywords, ctx } of ECON_CONTEXT) {
    if (keywords.some(k => lower.includes(k))) return ctx;
  }
  return null;
}

function econBeatMiss(ev: EconEvent): { label: string; color: string } | null {
  if (ev.actual == null || ev.estimate == null) return null;
  const diff = ev.actual - ev.estimate;
  if (Math.abs(diff) < 0.00001) return null;
  return diff > 0
    ? { label: "↑ Above est.", color: "#34d399" }
    : { label: "↓ Below est.", color: "#f87171" };
}

// ── Where Decifer is looking ──────────────────────────────────────────────────

function WhereLookingSection({
  data,
  ttgNames,
  onAskAbout,
}: {
  data: MarketNowPayload;
  ttgNames: NameEntry[] | null;
  onAskAbout?: (ctx: string) => void;
}) {
  // Radar fallback when TTG is unavailable or returned nothing
  const { stories: radarStories, names: radarNames, empty: radarEmpty } = buildWhereLooking(data);
  const useTtg = ttgNames !== null && ttgNames.length > 0;
  const names: NameEntry[] = useTtg ? ttgNames : radarNames;

  // Derive theme chips from actual displayed names, not all driver themes
  const themeChips = [...new Set(names.map(n => n.theme_label))];
  const chipList = useTtg ? themeChips : radarStories.slice(0, 5);

  if (!useTtg && radarEmpty) return null;
  if (useTtg && names.length === 0) return null;

  return (
    <section>
      <SectionLabel>Where Decifer is looking</SectionLabel>
      <div className="rounded-2xl p-4" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
        {chipList.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-4">
            {chipList.map((s, i) => (
              <span key={i} className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}>
                {s}
              </span>
            ))}
          </div>
        )}
        <div className="space-y-4">
          {names.map((n, i) => (
            <div key={i} className="flex items-start gap-3">
              <span className="text-[12px] font-black text-white shrink-0 w-11 pt-0.5">{n.symbol}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-1">
                  {n.exposure_type && (
                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
                      style={{ background: "rgba(255,255,255,0.05)", color: exposureColor(n.exposure_type) }}>
                      {n.exposure_type}
                    </span>
                  )}
                  <span className="text-[9px] text-slate-600 truncate">{n.theme_label}</span>
                </div>
                <p className="text-[11px] text-slate-300 leading-relaxed line-clamp-2">{n.reason}</p>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4">
          <AskDeciferButton label="Ask Decifer about these names" />
        </div>
      </div>
    </section>
  );
}

// ── News section ──────────────────────────────────────────────────────────────

const POSITIVE_WORDS = ["beat", "surges", "surge", "rally", "rallies", "gain", "gains", "rises", "jumps", "record", "upgrade", "upgraded", "strong", "bullish", "outperform", "tops", "exceed", "exceeds", "boosts"];
const NEGATIVE_WORDS = ["miss", "misses", "falls", "drops", "drop", "cuts", "cut", "weak", "loss", "losses", "downgrade", "downgraded", "bearish", "concern", "warning", "warns", "slump", "slumps", "disappoints", "trails", "lowers"];

function newsItemSentiment(title: string): "positive" | "negative" | "neutral" {
  const lower = title.toLowerCase();
  const pos = POSITIVE_WORDS.some(w => lower.includes(w));
  const neg = NEGATIVE_WORDS.some(w => lower.includes(w));
  if (pos && !neg) return "positive";
  if (neg && !pos) return "negative";
  return "neutral";
}

function formatNewsAge(minutesAgo: number): string {
  if (minutesAgo < 60) return `${minutesAgo}m`;
  const h = Math.floor(minutesAgo / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function NewsSection({ ttgSymbolMap }: { ttgSymbolMap: Map<string, { theme_label: string }> }) {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const symbols = [...ttgSymbolMap.keys()].slice(0, 50).join(",");
    const url = symbols ? `/api/market-news?symbols=${symbols}` : "/api/market-news";
    fetch(url)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.news) setNews(d.news); })
      .catch(() => {});
  }, [ttgSymbolMap]);

  if (news.length === 0) return null;

  // Sort: TTG-connected symbols first, then by recency
  const sorted = [...news].sort((a, b) => {
    const aInTtg = a.symbol ? ttgSymbolMap.has(a.symbol) : false;
    const bInTtg = b.symbol ? ttgSymbolMap.has(b.symbol) : false;
    if (aInTtg && !bInTtg) return -1;
    if (!aInTtg && bInTtg) return 1;
    return a.minutesAgo - b.minutesAgo;
  });

  const displayed = expanded ? sorted : sorted.slice(0, 6);

  // Sentiment counts
  const posCount = news.filter(n => newsItemSentiment(n.title) === "positive").length;
  const negCount = news.filter(n => newsItemSentiment(n.title) === "negative").length;
  const neutCount = news.length - posCount - negCount;

  return (
    <section>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.15em]" style={{ color: "#f97316" }}>
          Market news
        </p>
        <div className="flex items-center gap-2">
          {posCount > 0 && (
            <span className="text-[10px] font-semibold" style={{ color: "#34d399" }}>
              {posCount}▲
            </span>
          )}
          {negCount > 0 && (
            <span className="text-[10px] font-semibold" style={{ color: "#f87171" }}>
              {negCount}▼
            </span>
          )}
          {neutCount > 0 && (
            <span className="text-[10px] font-semibold text-slate-600">
              {neutCount}–
            </span>
          )}
        </div>
      </div>

      {/* News list */}
      <div className="rounded-2xl overflow-hidden" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
        {displayed.map((item, i) => {
          const sentiment = newsItemSentiment(item.title);
          const sentColor = sentiment === "positive" ? "#34d399" : sentiment === "negative" ? "#f87171" : "#475569";
          const sentIcon = sentiment === "positive" ? "▲" : sentiment === "negative" ? "▼" : "–";
          const themeInfo = item.symbol ? ttgSymbolMap.get(item.symbol) : null;
          const isLast = i === displayed.length - 1;

          return (
            <div
              key={i}
              className="px-4 py-3"
              style={{ borderBottom: !isLast || !expanded ? "1px solid rgba(255,255,255,0.04)" : "none" }}
            >
              <div className="flex items-start gap-2.5">
                {/* Sentiment indicator */}
                <span
                  className="text-[11px] font-black shrink-0 mt-0.5 w-4 text-center"
                  style={{ color: sentColor }}
                >
                  {sentIcon}
                </span>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  {/* Meta row: age + ticker + theme */}
                  <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                    <span className="text-[10px]" style={{ color: "#475569" }}>
                      {formatNewsAge(item.minutesAgo)}
                    </span>
                    {item.symbol && (
                      <span
                        className="text-[9px] font-black px-1.5 py-0.5 rounded"
                        style={{ background: "rgba(255,255,255,0.06)", color: "#e2e8f0" }}
                      >
                        {item.symbol}
                      </span>
                    )}
                    {themeInfo && (
                      <span
                        className="text-[9px] font-semibold px-1.5 py-0.5 rounded"
                        style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
                      >
                        {themeInfo.theme_label}
                      </span>
                    )}
                  </div>
                  {/* Headline */}
                  <p className="text-[12px] text-slate-200 leading-snug">{item.title}</p>
                  {/* Source */}
                  <p className="text-[10px] text-slate-600 mt-0.5">{item.source}</p>
                </div>
              </div>
            </div>
          );
        })}

        {/* Show more / less toggle */}
        {news.length > 6 && (
          <button
            onClick={() => setExpanded(e => !e)}
            className="w-full px-4 py-3 flex items-center justify-center gap-1 text-[11px] font-semibold transition-all active:scale-[0.98]"
            style={{ color: "#64748b", borderTop: "1px solid rgba(255,255,255,0.04)" }}
          >
            <ChevronDown
              size={12}
              style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s" }}
            />
            {expanded ? "Show less" : `Show ${news.length - 6} more`}
          </button>
        )}
      </div>
    </section>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

// ── Today's Agenda ────────────────────────────────────────────────────────────

function TodayAgendaSection({
  data,
  brief,
  ttgSymbolMap,
}: {
  data: MarketNowPayload;
  brief: MorningBriefPayload | null;
  ttgSymbolMap: Map<string, { theme_label: string }>;
}) {
  const activeDrivers = data.key_drivers ?? [];

  const econEvents = (brief?.econ ?? []).filter(e => e.impact === "High" || e.impact === "Medium");
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const earningsThisWeek = (brief?.earnings ?? []).filter(e => ttgSymbolMap.has(e.symbol));
  const todayEarnings = earningsThisWeek.filter(e => e.date === today);
  const upcomingEarnings = earningsThisWeek.filter(e => e.date !== today).slice(0, 5);

  if (econEvents.length === 0 && earningsThisWeek.length === 0) return null;

  return (
    <section>
      <SectionLabel>Today&apos;s agenda</SectionLabel>
      <div className="rounded-2xl overflow-hidden" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>

        {/* Economic events */}
        {econEvents.length > 0 && (
          <div className="px-4 pt-4 pb-3">
            <div className="flex items-center gap-1.5 mb-3">
              <CalendarDays size={11} style={{ color: "#f97316" }} />
              <p className="text-[9px] font-bold uppercase tracking-widest" style={{ color: "#f97316" }}>Economic releases</p>
            </div>
            <div className="space-y-3">
              {econEvents.slice(0, 8).map((ev, i) => {
                const driver = annotateEconEvent(ev.event, activeDrivers);
                const isHigh = ev.impact === "High";
                const hasTime = ev.time && ev.time !== "All Day";
                const beatMiss = econBeatMiss(ev);
                const plainLabel = econPlainLabel(ev.event);
                const context = econWhatItMeans(ev.event);
                return (
                  <div key={i} className="flex flex-col gap-1" style={{
                    paddingBottom: i < Math.min(econEvents.length, 8) - 1 ? "12px" : "0",
                    borderBottom: i < Math.min(econEvents.length, 8) - 1 ? "1px solid rgba(255,255,255,0.04)" : "none",
                  }}>
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[12px] font-semibold text-slate-100 leading-snug flex-1">{plainLabel}</p>
                      <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
                        {beatMiss && (
                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
                            style={{ background: `${beatMiss.color}18`, color: beatMiss.color }}>
                            {beatMiss.label}
                          </span>
                        )}
                        {driver && (
                          <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded"
                            style={{ background: "rgba(249,115,22,0.12)", color: "#fb923c" }}>
                            {driver}
                          </span>
                        )}
                        {isHigh && !driver && (
                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
                            style={{ background: "rgba(251,191,36,0.1)", color: "#fbbf24" }}>
                            High Impact
                          </span>
                        )}
                      </div>
                    </div>
                    {/* Plain-English commentary */}
                    {context && (
                      <div className="mt-0.5 space-y-1">
                        <p className="text-[11px] leading-relaxed" style={{ color: "#94a3b8" }}>{context.what}</p>
                        <p className="text-[11px] leading-relaxed" style={{ color: "#64748b" }}>
                          <span style={{ color: "#f97316", fontWeight: 600 }}>Watch: </span>{context.watch}
                        </p>
                      </div>
                    )}
                    <div className="flex items-center gap-2 flex-wrap mt-0.5">
                      {hasTime && (
                        <span className="text-[10px]" style={{ color: isHigh ? "#fbbf24" : "#64748b" }}>
                          {formatEconTime(ev.time)} ET
                        </span>
                      )}
                      {ev.actual != null && (
                        <span className="text-[10px] text-slate-400">
                          Released: <span className="text-slate-200">{ev.actual}{ev.unit ? ` ${ev.unit}` : ""}</span>
                          {ev.estimate != null && <span className="text-slate-600"> · Forecast was {ev.estimate}{ev.unit ? ` ${ev.unit}` : ""}</span>}
                        </span>
                      )}
                      {ev.actual == null && ev.estimate != null && (
                        <span className="text-[10px] text-slate-500">Forecast: {ev.estimate}{ev.unit ? ` ${ev.unit}` : ""}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Divider between sections */}
        {econEvents.length > 0 && earningsThisWeek.length > 0 && (
          <div style={{ height: "1px", background: "rgba(255,255,255,0.05)", margin: "0 16px" }} />
        )}

        {/* Earnings */}
        {earningsThisWeek.length > 0 && (
          <div className="px-4 pt-3 pb-4">
            <div className="flex items-center gap-1.5 mb-3">
              <BarChart2 size={11} style={{ color: "#f97316" }} />
              <p className="text-[9px] font-bold uppercase tracking-widest" style={{ color: "#f97316" }}>Earnings — your themes</p>
            </div>
            <div className="space-y-2.5">
              {[...todayEarnings, ...upcomingEarnings].map((e, i) => {
                const info = ttgSymbolMap.get(e.symbol);
                const timeLabel = earningsTimeLabel(e.time);
                const dateLabel = formatEarningsDate(e.date);
                const isToday = e.date === today;
                return (
                  <div key={i} className="flex items-start gap-2.5">
                    <span className="text-[12px] font-black text-white shrink-0 w-12 pt-0.5">{e.symbol}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px] font-semibold" style={{ color: isToday ? "#fbbf24" : "#64748b" }}>
                          {dateLabel}
                        </span>
                        {timeLabel && (
                          <span className="text-[9px] text-slate-600">{timeLabel}</span>
                        )}
                        {info && (
                          <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded"
                            style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}>
                            {info.theme_label}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-400 mt-0.5 truncate">{e.name}</p>
                      {e.epsEst != null && (
                        <p className="text-[10px] text-slate-500 mt-0.5">EPS est: ${e.epsEst.toFixed(2)}</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// ── Analyst Moves on Your Names ───────────────────────────────────────────────

function AnalystMovesSection({
  brief,
  ttgSymbolMap,
}: {
  brief: MorningBriefPayload | null;
  ttgSymbolMap: Map<string, { theme_label: string }>;
}) {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const sevenDaysAgo = (() => { const d = new Date(); d.setDate(d.getDate() - 7); return d.toLocaleDateString("en-CA", { timeZone: "America/New_York" }); })();

  const moves = (brief?.analyst ?? [])
    .filter(a => {
      if (!ttgSymbolMap.has(a.symbol)) return false;
      const dateStr = a.publishedDate?.slice(0, 10);
      if (!dateStr || dateStr < sevenDaysAgo || dateStr > today) return false;
      const act = a.action.toLowerCase();
      return act.includes("upgrade") || act.includes("downgrade") || act === "initiated" || act === "initiation" || act.includes("target raised") || act.includes("target lowered") || act.includes("raise") || act.includes("lower");
    })
    .slice(0, 8);

  if (moves.length === 0) return null;

  function actionDisplay(action: string): { icon: "up" | "down" | "new" | "target"; label: string; color: string } {
    const a = action.toLowerCase();
    if (a.includes("upgrade")) return { icon: "up", label: "Upgraded", color: "#34d399" };
    if (a.includes("downgrade")) return { icon: "down", label: "Downgraded", color: "#f87171" };
    if (a === "initiated" || a === "initiation" || a.includes("initiat")) return { icon: "new", label: "Initiated", color: "#60a5fa" };
    if (a.includes("raise") || a.includes("target raised")) return { icon: "target", label: "Target ↑", color: "#34d399" };
    if (a.includes("lower") || a.includes("target lowered")) return { icon: "target", label: "Target ↓", color: "#f87171" };
    return { icon: "target", label: action, color: "#94a3b8" };
  }

  return (
    <section>
      <SectionLabel>Analyst moves on your names</SectionLabel>
      <div className="rounded-2xl p-4" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
        <div className="space-y-3">
          {moves.map((m, i) => {
            const { icon, label, color } = actionDisplay(m.action);
            const info = ttgSymbolMap.get(m.symbol);
            return (
              <div key={i} className="flex items-start gap-2.5">
                <div className="shrink-0 mt-0.5 w-4 flex justify-center">
                  {icon === "up" && <TrendingUp size={13} style={{ color }} />}
                  {icon === "down" && <TrendingDown size={13} style={{ color }} />}
                  {(icon === "new" || icon === "target") && <span className="text-[11px] font-bold" style={{ color }}>→</span>}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[12px] font-black text-white">{m.symbol}</span>
                    <span className="text-[10px] font-semibold" style={{ color }}>{label}</span>
                    {m.fromGrade && m.toGrade && m.fromGrade !== m.toGrade && (
                      <span className="text-[9px] text-slate-500">{m.fromGrade} → {m.toGrade}</span>
                    )}
                    {!m.fromGrade && m.toGrade && (
                      <span className="text-[9px] text-slate-500">{m.toGrade}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <p className="text-[10px] text-slate-500">{m.gradingCompany}</p>
                    {info && (
                      <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded"
                        style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}>
                        {info.theme_label}
                      </span>
                    )}
                  </div>
                </div>
                {m.priceWhenPosted != null && (
                  <span className="text-[10px] text-slate-500 shrink-0">${m.priceWhenPosted.toFixed(0)}</span>
                )}
              </div>
            );
          })}
        </div>
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
  clock,
  freshnessState,
  freshnessLabel,
  isRefreshing,
  onRefresh,
  onThemeSelect,
  onAskAbout,
  onGoToForces,
}: Props) {
  const groups = buildCauseGroups(data);

  const [selectedForce, setSelectedForce] = useState<CustomerMarketForce | null>(null);

  // Market tape
  const [tape, setTape] = useState<TapeEntry[]>([]);
  useEffect(() => {
    fetch("/api/market-tape")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.tape) setTape(d.tape); })
      .catch(() => {});
  }, []);
  const tapeSnapshot = deriveTapeSnapshot(tape);

  // Morning brief — economic calendar, earnings, analyst moves
  const [morningBrief, setMorningBrief] = useState<MorningBriefPayload | null>(null);
  useEffect(() => {
    fetch("/api/morning-brief")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setMorningBrief(d); })
      .catch(() => {});
  }, []);

  // TTG data — names for WhereLooking + symbolMap for Agenda & Analyst sections
  interface TtgData { names: NameEntry[]; symbolMap: Map<string, { theme_label: string }>; }
  const [ttgData, setTtgData] = useState<TtgData | null>(null);
  useEffect(() => {
    let cancelled = false;
    async function load() {
      // Fetch TTG themes + operational roster in parallel — roster extends
      // earnings/analyst coverage from 10 TTG packs to all 23 operational themes.
      const [themesResult, rosterResult] = await Promise.allSettled([
        fetchTtgThemes(),
        fetchUniverseSymbols(),
      ]);

      const themes = themesResult.status === "fulfilled" ? themesResult.value : [];
      const roster = rosterResult.status === "fulfilled" ? rosterResult.value : [];

      // Seed the symbolMap from the operational roster first (broadest coverage).
      // TTG entries will overwrite with richer customer-facing labels where they exist.
      const symbolMap = new Map<string, { theme_label: string }>();
      for (const u of roster) {
        symbolMap.set(u.symbol, { theme_label: u.theme_label });
      }

      if (themes.length === 0) {
        if (!cancelled) setTtgData({ names: [], symbolMap });
        return;
      }

      try {
        // Fetch ALL TTG themes for a complete symbolMap (used by Agenda + Analyst filters).
        // Active themes are used separately for name selection (WhereLooking).
        const activeThemeIds = new Set(
          themes.filter((t: { driver_active: boolean }) => t.driver_active)
                .map((t: { theme_id: string }) => t.theme_id)
        );
        const allDetails = await Promise.allSettled(
          themes.map((t: { theme_id: string }) => fetchTtgThemeDetail(t.theme_id))
        );
        const candidates: Array<NameEntry & { _conf: number }> = [];
        for (const result of allDetails) {
          if (result.status !== "fulfilled" || !result.value) continue;
          const detail = result.value;
          // TTG active symbols overwrite roster entries (richer labels)
          for (const s of detail.symbols) {
            if (s.status === "active") symbolMap.set(s.symbol, { theme_label: detail.label });
          }
          // Only driver-active themes contribute candidates for WhereLooking
          if (!activeThemeIds.has(detail.theme_id)) continue;
          const eligible = detail.symbols
            .filter((s: TtgSymbolCard) => s.status === "active" && s.driver_active)
            .sort((a: TtgSymbolCard, b: TtgSymbolCard) => (b.confidence ?? 0) - (a.confidence ?? 0))
            .slice(0, 3);
          for (const s of eligible) {
            if (candidates.find(c => c.symbol === s.symbol)) continue;
            const chip = s.exposure_type === "direct_beneficiary" ? "Direct"
              : s.exposure_type === "supply_chain_beneficiary" ? "Supply chain"
              : s.exposure_type === "etf_proxy" ? "ETF"
              : undefined;
            candidates.push({
              symbol: s.symbol,
              reason: s.reason_to_care,
              theme_label: detail.label,
              exposure_type: chip,
              _conf: s.confidence ?? 0,
            });
          }
        }
        // Global sort by confidence — highest conviction first
        const names: NameEntry[] = candidates
          .sort((a, b) => b._conf - a._conf)
          .slice(0, 5)
          .map(({ _conf: _, ...n }) => n);
        if (!cancelled) setTtgData({ names, symbolMap });
      } catch {
        if (!cancelled) setTtgData({ names: [], symbolMap });
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <div>

      {/* ── HERO (full-bleed, no horizontal padding) ──────────────────────── */}
      {story && (
        <HeroHeader
          data={data}
          story={story}
          tapeSnapshot={tapeSnapshot}
          clock={clock}
          isRefreshing={isRefreshing}
          freshnessLabel={freshnessLabel}
          freshnessState={freshnessState}
          onRefresh={onRefresh}
        />
      )}

      {/* ── STORY CIRCLES (full-bleed, below hero) ───────────────────────── */}
      <StoryCirclesStrip data={data} onSelect={setSelectedForce} />

      {/* ── PADDED CONTENT ────────────────────────────────────────────────── */}
      <div className="px-4 pb-8 space-y-5 pt-5">

        {/* ── A: Human market narrative ─────────────────────────────────── */}
        {story && (
          <MarketNarrative
            data={data}
            story={story}
            tapeSnapshot={tapeSnapshot}
            clock={clock}
            morningBrief={morningBrief}
            onAskAbout={onAskAbout}
            onGoToForces={onGoToForces}
          />
        )}

        {/* ── B: Today's agenda — economic releases + earnings on your themes ── */}
        <TodayAgendaSection
          data={data}
          brief={morningBrief}
          ttgSymbolMap={ttgData?.symbolMap ?? new Map()}
        />

        {/* ── C: What is moving markets ─────────────────────────────────── */}
        {groups.length > 0 && (
          <section>
            <SectionLabel>What is moving markets</SectionLabel>
            <div className="space-y-3">
              {groups.map((group, i) => (
                <CauseGroupCard key={i} group={group} onThemeSelect={onThemeSelect} onAskAbout={onAskAbout} />
              ))}
            </div>
            {onGoToForces && (
              <button
                onClick={onGoToForces}
                className="mt-3 w-full flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-[11px] font-semibold transition-all active:scale-[0.98]"
                style={{ background: "rgba(249,115,22,0.06)", border: "1px solid rgba(249,115,22,0.15)", color: "#fb923c" }}
              >
                <Zap size={10} />
                See all active forces
              </button>
            )}
          </section>
        )}

        {/* ── D: Top movers ────────────────────────────────────────────── */}
        <MoversSection session={clock.session} />

        {/* ── E: Analyst moves on your themes ──────────────────────────── */}
        <AnalystMovesSection
          brief={morningBrief}
          ttgSymbolMap={ttgData?.symbolMap ?? new Map()}
        />

        {/* ── F: Where Decifer is looking ───────────────────────────────── */}
        <WhereLookingSection
          data={data}
          ttgNames={ttgData?.names ?? null}
          onAskAbout={onAskAbout}
        />

        {/* ── G: Market news ────────────────────────────────────────────── */}
        <NewsSection ttgSymbolMap={ttgData?.symbolMap ?? new Map()} />

        {/* ── Disclaimer ─────────────────────────────────────────────────── */}
        <div className="rounded-xl p-4 text-center"
          style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
          <p className="text-[11px] text-slate-400 leading-relaxed">
            {data.data_entitlement_note ?? "Market intelligence only. Not financial advice. No trade execution."}
          </p>
          <p className="text-[10px] text-slate-600 mt-1">
            v{process.env.NEXT_PUBLIC_APP_VERSION ?? "dev"}
          </p>
        </div>

      </div>

      {/* ── Force story sheet overlay ──────────────────────────────────────── */}
      {selectedForce && (
        <ForceStorySheet
          force={selectedForce}
          onClose={() => setSelectedForce(null)}
          onAskAbout={onAskAbout}
        />
      )}
    </div>
  );
}
