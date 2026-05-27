"use client";
// Today tab — Sprint M16 redesign.
// Immersive hero header, dual clock, top movers, sector map, themed news feed.

import { useState, useEffect } from "react";
import {
  ArrowRight,
  Eye,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Shield,
  Zap,
  Layers,
  TrendingUp,
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
  buildCustomerForces,
  type TapeSnapshot,
  type CustomerMarketForce,
} from "@/lib/customerBriefingModel";
import { buildCauseGroups, type MarketCauseGroup } from "@/lib/marketCauseStory";
import type { TapeEntry } from "@/app/api/market-tape/route";
import type { MarketMoversPayload, Mover } from "@/app/api/market-movers/route";
import type { SectorEntry } from "@/app/api/sectors/route";
import type { NewsItem } from "@/app/api/market-news/route";

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
    dxy_pct:   by["UUP"]?.changePct ?? null,
    vix_level: by["VIX"]?.level     ?? null,
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
  onTap,
}: {
  force: CustomerMarketForce;
  onTap: () => void;
}) {
  const isNeg = NEGATIVE_FORCES.has(force.id);
  const color = isNeg ? "#f87171" : "#34d399";
  const icon  = FORCE_ICON[force.id] ?? force.label.slice(0, 2).toUpperCase();

  return (
    <button
      onClick={onTap}
      className="flex flex-col items-center gap-1.5 shrink-0 transition-all active:scale-95"
    >
      <div
        className="w-14 h-14 rounded-full flex items-center justify-center"
        style={{
          background: `${color}12`,
          border: `2px solid ${color}45`,
          boxShadow: `0 0 12px ${color}18`,
        }}
      >
        <span className="text-[11px] font-black tracking-tight" style={{ color }}>
          {icon}
        </span>
      </div>
      <span
        className="text-[9px] font-semibold text-center leading-tight"
        style={{ color: "#94a3b8", maxWidth: "56px" }}
      >
        {force.label}
      </span>
    </button>
  );
}

// ── Story circles strip ───────────────────────────────────────────────────────

function StoryCirclesStrip({
  data,
  onAskAbout,
}: {
  data: MarketNowPayload;
  onAskAbout?: (ctx: string) => void;
}) {
  const { active } = buildCustomerForces(data);
  if (active.length === 0) return null;

  return (
    <div
      className="py-3"
      style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
    >
      <div className="overflow-x-auto" style={{ scrollbarWidth: "none" }}>
        <div className="flex gap-5 px-5 min-w-max">
          {active.slice(0, 10).map((force, i) => (
            <StoryCircle
              key={i}
              force={force}
              onTap={() =>
                onAskAbout?.(
                  `Tell me about the ${force.label} market force and what it means for investors today`,
                )
              }
            />
          ))}
        </div>
      </div>
    </div>
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

  const spy = tapeSnapshot.spy_pct;
  const vix = tapeSnapshot.vix_level;
  const spyColor = spy == null ? "#64748b" : spy > 0 ? "#34d399" : spy < 0 ? "#f87171" : "#94a3b8";
  const spySign  = spy != null && spy > 0 ? "+" : "";

  const freshnessTimeCopy =
    freshnessState === "fresh" && data.freshness_timestamp
      ? new Date(data.freshness_timestamp).toLocaleTimeString("en-US", {
          hour: "2-digit", minute: "2-digit", timeZoneName: "short",
        })
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

        {/* ── Row 3: SPY number or regime fallback ── */}
        {spy != null ? (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
              S&amp;P 500
            </p>
            <div className="flex items-end gap-3">
              <span
                className="font-black leading-none"
                style={{ color: spyColor, fontSize: "52px" }}
              >
                {spySign}{spy.toFixed(2)}%
              </span>
              {vix != null && (
                <div className="mb-2">
                  <p className="text-[9px] uppercase text-slate-600 tracking-wide">VIX</p>
                  <p
                    className="text-[16px] font-black leading-none"
                    style={{ color: vix >= 25 ? "#f87171" : vix >= 20 ? "#fbbf24" : "#64748b" }}
                  >
                    {vix.toFixed(1)}
                  </p>
                </div>
              )}
            </div>
            <p className="text-[12px] text-slate-400 mt-1">{ms.macro_label}</p>
          </div>
        ) : (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
              Market view
            </p>
            <p
              className="font-black leading-tight"
              style={{ color: c.text, fontSize: "26px" }}
            >
              {ms.macro_label}
            </p>
          </div>
        )}
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
  onAskAbout,
  onGoToForces,
}: {
  data: MarketNowPayload;
  story: CustomerStory;
  tapeSnapshot: TapeSnapshot;
  onAskAbout?: (ctx: string) => void;
  onGoToForces?: () => void;
}) {
  const ms = buildCustomerMarketStory(data, story);
  const narrativeParagraph = buildNarrativeParagraph(data, ms, tapeSnapshot);

  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
    >
      <p className="text-[13px] text-slate-200 leading-relaxed">{narrativeParagraph}</p>

      {ms.caution && (
        <div
          className="mt-3 rounded-xl px-3 py-2.5 flex items-start gap-2"
          style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,0.18)" }}
        >
          <Shield size={11} className="text-amber-400 shrink-0 mt-0.5" />
          <p className="text-[11px] text-amber-300 leading-relaxed">{ms.caution}</p>
        </div>
      )}

      {(ms.supporting_bullets.length > 0) && (
        <ul className="mt-3 space-y-1.5">
          {ms.supporting_bullets.slice(0, 2).map((b, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="w-1 h-1 rounded-full shrink-0 mt-1.5" style={{ background: "#f97316" }} />
              <p className="text-[11px] text-slate-400 leading-relaxed">{b}</p>
            </li>
          ))}
        </ul>
      )}

      <div
        className="mt-3 pt-3 flex items-center gap-3"
        style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
      >
        {onAskAbout && (
          <button
            onClick={() => onAskAbout("Why is the market moving in this direction today?")}
            className="flex items-center gap-1 text-[10px] font-semibold text-slate-500 transition-all active:scale-95"
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

// ── Mover row ─────────────────────────────────────────────────────────────────

function MoverRow({ mover, direction }: { mover: Mover; direction: "up" | "down" }) {
  const [imgErr, setImgErr] = useState(false);
  const color = direction === "up" ? "#34d399" : "#f87171";
  const sign  = direction === "up" ? "+" : "";
  const monogram = mover.symbol.slice(0, 2);

  return (
    <div className="flex items-center gap-2 py-1.5">
      {/* Logo */}
      <div
        className="w-7 h-7 rounded-lg overflow-hidden shrink-0 flex items-center justify-center"
        style={{ background: "#1e293b" }}
      >
        {!imgErr ? (
          <img
            src={mover.logoUrl}
            alt={mover.symbol}
            className="w-full h-full object-contain p-0.5"
            onError={() => setImgErr(true)}
          />
        ) : (
          <span className="text-[9px] font-black" style={{ color: "#475569" }}>{monogram}</span>
        )}
      </div>

      {/* Symbol */}
      <span className="text-[12px] font-bold text-slate-200 flex-1 min-w-0 truncate">
        {mover.symbol}
      </span>

      {/* Change */}
      <span className="text-[13px] font-black shrink-0" style={{ color }}>
        {sign}{mover.changePct.toFixed(1)}%
      </span>
    </div>
  );
}

// ── Movers section ────────────────────────────────────────────────────────────

function MoversSection() {
  const [data, setData] = useState<MarketMoversPayload | null>(null);

  useEffect(() => {
    fetch("/api/market-movers")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {});
  }, []);

  if (!data || (data.gainers.length === 0 && data.losers.length === 0)) return null;

  const gainers = data.gainers.slice(0, 3);
  const losers  = data.losers.slice(0, 3);

  return (
    <section>
      <SectionLabel>Today&apos;s biggest moves</SectionLabel>
      <div className="grid grid-cols-2 gap-3">
        {/* Gainers */}
        <div
          className="rounded-2xl px-3 py-3"
          style={{ background: "#141b26", border: "1px solid rgba(16,185,129,0.12)" }}
        >
          <p className="text-[9px] font-bold uppercase tracking-widest text-emerald-500 mb-2">
            ↑ Gainers
          </p>
          {gainers.map((m, i) => (
            <MoverRow key={i} mover={m} direction="up" />
          ))}
        </div>
        {/* Losers */}
        <div
          className="rounded-2xl px-3 py-3"
          style={{ background: "#141b26", border: "1px solid rgba(239,68,68,0.12)" }}
        >
          <p className="text-[9px] font-bold uppercase tracking-widest text-red-400 mb-2">
            ↓ Losers
          </p>
          {losers.map((m, i) => (
            <MoverRow key={i} mover={m} direction="down" />
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Sector tile ───────────────────────────────────────────────────────────────

function SectorTile({ entry }: { entry: SectorEntry }) {
  const pct = entry.changePct;
  const isPos   = pct != null && pct > 0;
  const isNeg   = pct != null && pct < 0;
  const strong  = pct != null && Math.abs(pct) >= 1;
  const textColor  = pct == null ? "#64748b" : isPos ? "#34d399" : isNeg ? "#f87171" : "#94a3b8";
  const borderColor = pct == null
    ? "rgba(255,255,255,0.06)"
    : isPos
      ? strong ? "rgba(16,185,129,0.22)" : "rgba(16,185,129,0.12)"
      : isNeg
        ? strong ? "rgba(239,68,68,0.22)" : "rgba(239,68,68,0.12)"
        : "rgba(255,255,255,0.06)";
  const bg = pct == null
    ? "#141b26"
    : isPos
      ? strong ? "rgba(16,185,129,0.07)" : "rgba(16,185,129,0.04)"
      : isNeg
        ? strong ? "rgba(239,68,68,0.07)" : "rgba(239,68,68,0.04)"
        : "#141b26";
  const sign = isPos ? "+" : "";

  return (
    <div
      className="rounded-xl px-2.5 py-2.5"
      style={{ background: bg, border: `1px solid ${borderColor}` }}
    >
      <p className="text-[10px] font-semibold text-slate-400 leading-none mb-1.5 truncate">
        {entry.shortLabel}
      </p>
      <p className="text-[14px] font-black leading-none" style={{ color: textColor }}>
        {pct != null ? `${sign}${pct.toFixed(1)}%` : "—"}
      </p>
    </div>
  );
}

// ── Sector grid ───────────────────────────────────────────────────────────────

function SectorGrid() {
  const [sectors, setSectors] = useState<SectorEntry[]>([]);

  useEffect(() => {
    fetch("/api/sectors")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.sectors) setSectors(d.sectors); })
      .catch(() => {});
  }, []);

  if (sectors.length === 0) return null;

  return (
    <section>
      <SectionLabel>Market at a glance</SectionLabel>
      <div className="grid grid-cols-3 gap-2">
        {sectors.map(s => (
          <SectorTile key={s.sym} entry={s} />
        ))}
      </div>
    </section>
  );
}

// ── Theme chip colours ────────────────────────────────────────────────────────

const THEME_CHIP: Record<string, { bg: string; color: string }> = {
  "AI Infrastructure":  { bg: "rgba(139,92,246,0.12)", color: "#a78bfa" },
  Tech:                 { bg: "rgba(59,130,246,0.12)",  color: "#60a5fa" },
  Defence:              { bg: "rgba(148,163,184,0.10)", color: "#94a3b8" },
  Energy:               { bg: "rgba(249,115,22,0.12)",  color: "#fb923c" },
  Gold:                 { bg: "rgba(234,179,8,0.12)",   color: "#facc15" },
  Healthcare:           { bg: "rgba(16,185,129,0.10)",  color: "#34d399" },
  "EV & Autos":         { bg: "rgba(132,204,22,0.10)",  color: "#a3e635" },
  Autos:                { bg: "rgba(132,204,22,0.10)",  color: "#a3e635" },
  Financials:           { bg: "rgba(14,165,233,0.10)",  color: "#38bdf8" },
  "Digital Assets":     { bg: "rgba(99,102,241,0.12)",  color: "#818cf8" },
};

function ThemeChip({ label }: { label: string }) {
  const style = THEME_CHIP[label] ?? { bg: "rgba(255,255,255,0.06)", color: "#64748b" };
  return (
    <span
      className="text-[9px] font-bold px-1.5 py-0.5 rounded-full shrink-0"
      style={{ background: style.bg, color: style.color }}
    >
      {label}
    </span>
  );
}

// ── News section ──────────────────────────────────────────────────────────────

function NewsSection({ onAskAbout }: { onAskAbout?: (ctx: string) => void }) {
  const [items, setItems] = useState<NewsItem[]>([]);

  useEffect(() => {
    fetch("/api/market-news")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.news) setItems(d.news.slice(0, 8)); })
      .catch(() => {});
  }, []);

  if (items.length === 0) return null;

  const fmtAge = (mins: number) =>
    mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`;

  return (
    <section>
      <SectionLabel>What&apos;s in the news</SectionLabel>
      <div className="space-y-2">
        {items.map((item, i) => (
          <button
            key={i}
            onClick={() =>
              onAskAbout?.(`Tell me about this news story and how it affects markets: "${item.title}"`)
            }
            className="w-full rounded-xl px-3.5 py-3 text-left transition-all active:scale-[0.99]"
            style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
          >
            {item.themeLabel && (
              <div className="mb-1.5">
                <ThemeChip label={item.themeLabel} />
              </div>
            )}
            <p className="text-[13px] font-semibold text-slate-100 leading-snug line-clamp-2">
              {item.title}
            </p>
            <div className="flex items-center justify-between mt-1.5">
              <div className="flex items-center gap-1.5">
                {item.logoUrl && (
                  <img
                    src={item.logoUrl}
                    alt=""
                    className="w-3.5 h-3.5 rounded object-contain"
                    style={{ background: "#1e293b" }}
                    onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                  />
                )}
                <p className="text-[10px] text-slate-500">
                  {item.source}
                  <span className="mx-1">·</span>
                  {fmtAge(item.minutesAgo)} ago
                </p>
              </div>
              <span className="text-[10px] text-slate-600">Ask →</span>
            </div>
          </button>
        ))}
      </div>

      {onAskAbout && (
        <button
          onClick={() => onAskAbout("What are the most important news stories driving markets today?")}
          className="mt-2 flex items-center gap-1 text-[10px] font-semibold text-slate-500 transition-all active:scale-95"
        >
          Ask Decifer about the news
          <ArrowRight size={9} />
        </button>
      )}
    </section>
  );
}

// ── Event card ────────────────────────────────────────────────────────────────

function EventCard({ ev }: { ev: KeyEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded-xl cursor-pointer"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}
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
                className="text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0 mt-0.5"
                style={{ background: "rgba(239,68,68,0.12)", color: "#f87171" }}
              >
                High impact
              </span>
            )}
          </div>
        </div>
        {open
          ? <ChevronUp size={14} className="text-slate-500 shrink-0 mt-0.5" />
          : <ChevronDown size={14} className="text-slate-500 shrink-0 mt-0.5" />
        }
      </div>
      {open && (
        <div className="px-3.5 pb-3.5 pt-3 space-y-2.5" style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}>
          {ev.summary_plain_english && (
            <p className="text-xs text-slate-300 leading-relaxed">{ev.summary_plain_english}</p>
          )}
          {((ev.likely_positive_exposures?.length ?? 0) > 0 ||
            (ev.likely_negative_exposures?.length ?? 0) > 0) && (
            <div className="flex flex-wrap gap-1.5">
              {(ev.likely_positive_exposures ?? []).map((s, i) => (
                <span key={i} className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(16,185,129,0.1)", color: "#34d399" }}>
                  {s}
                </span>
              ))}
              {(ev.likely_negative_exposures ?? []).map((s, i) => (
                <span key={i} className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(239,68,68,0.1)", color: "#f87171" }}>
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
      <p className="text-[12px] text-slate-400 leading-relaxed">{card.market_impact}</p>
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
      {onAskAbout && (
        <button
          onClick={() => onAskAbout(`Why is ${card.cause_label.toLowerCase()} affecting markets?`)}
          className="mt-2.5 flex items-center gap-1 text-[10px] font-semibold text-slate-500 transition-all active:scale-95"
        >
          Ask Decifer why
          <ArrowRight size={9} />
        </button>
      )}
    </div>
  );
}

// ── Where Decifer is looking ──────────────────────────────────────────────────

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
      <div className="rounded-2xl p-4" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
        {stories.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {stories.map((s, i) => (
              <span key={i} className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}>
                {s}
              </span>
            ))}
          </div>
        )}
        {names.length > 0 && (
          <div className="space-y-2.5">
            {names.map((n, i) => (
              <div key={i} className="flex items-start gap-2.5">
                <span className="text-[11px] font-bold text-slate-200 shrink-0 w-11">{n.symbol}</span>
                <p className="text-[11px] text-slate-400 leading-relaxed line-clamp-2">{n.reason}</p>
              </div>
            ))}
          </div>
        )}
        {onAskAbout && (stories.length > 0 || names.length > 0) && (
          <button
            onClick={() => onAskAbout(`Which names are connected to ${stories[0] ?? "these themes"} today?`)}
            className="mt-3 flex items-center gap-1 text-[10px] font-semibold text-slate-500 transition-all active:scale-95"
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
  clock,
  sinceAway,
  freshnessState,
  freshnessLabel,
  isRefreshing,
  onRefresh,
  onThemeSelect,
  onAskAbout,
  onGoToForces,
}: Props) {
  const keyEvents = data.key_events ?? [];
  const apiWatch  = data.watch_next?.length ? data.watch_next : (data.what_to_watch ?? []);
  const watchNext = apiWatch.length > 0 ? apiWatch : buildWhatCouldChange(data);
  const groups    = buildCauseGroups(data);

  const [tape, setTape] = useState<TapeEntry[]>([]);
  useEffect(() => {
    fetch("/api/market-tape")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.tape) setTape(d.tape); })
      .catch(() => {});
  }, []);

  const tapeSnapshot = deriveTapeSnapshot(tape);

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
      <StoryCirclesStrip data={data} onAskAbout={onAskAbout} />

      {/* ── PADDED CONTENT ────────────────────────────────────────────────── */}
      <div className="px-4 pb-8 space-y-5 pt-5">

        {/* ── A: Human market narrative ─────────────────────────────────── */}
        {story && (
          <MarketNarrative
            data={data}
            story={story}
            tapeSnapshot={tapeSnapshot}
            onAskAbout={onAskAbout}
            onGoToForces={onGoToForces}
          />
        )}

        {/* ── B: Since you were away ────────────────────────────────────── */}
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
                  <div key={i} className="rounded-xl px-4 py-3 flex items-start gap-3"
                    style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
                    <span className="w-1.5 h-1.5 rounded-full shrink-0 mt-1.5"
                      style={{
                        background: item.type === "event" ? "#f59e0b" : item.type === "theme" ? "#3b82f6" : "#10b981",
                      }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] text-slate-200 leading-snug">{item.title}</p>
                      {item.detail && (
                        <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed line-clamp-2">{item.detail}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-2xl p-4" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
                <p className="text-sm text-slate-400">Market story looks the same since you were away.</p>
                <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">Scroll down for the full briefing.</p>
              </div>
            )}
          </section>
        )}

        {/* ── C: Top movers ────────────────────────────────────────────── */}
        <MoversSection />

        {/* ── D: Sector map ────────────────────────────────────────────── */}
        <SectorGrid />

        {/* ── E: News feed ─────────────────────────────────────────────── */}
        <NewsSection onAskAbout={onAskAbout} />

        {/* ── F: Key events from intelligence layer ────────────────────── */}
        {keyEvents.length > 0 && (
          <section>
            <SectionLabel>Events behind today&apos;s moves</SectionLabel>
            <div className="space-y-2">
              {keyEvents.slice(0, 5).map((ev, i) => (
                <EventCard key={i} ev={ev} />
              ))}
            </div>
            {onAskAbout && (
              <button
                onClick={() => onAskAbout("What real-world events are driving markets today?")}
                className="mt-2 flex items-center gap-1 text-[10px] font-semibold text-slate-500 transition-all active:scale-95"
              >
                Ask Decifer about these events
                <ArrowRight size={9} />
              </button>
            )}
          </section>
        )}

        {/* ── G: What is moving markets ─────────────────────────────────── */}
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

        {/* ── H: Where Decifer is looking ───────────────────────────────── */}
        <WhereLookingSection data={data} onAskAbout={onAskAbout} />

        {/* ── I: What could change the picture ─────────────────────────── */}
        {watchNext.length > 0 && (
          <section>
            <SectionLabel>What could change the picture</SectionLabel>
            <div className="rounded-2xl p-4" style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.07)" }}>
              <ul className="space-y-2.5">
                {watchNext.map((item, i) => (
                  <li key={i} className="flex items-start gap-2.5">
                    <Eye size={11} className="text-slate-500 shrink-0 mt-1" />
                    <p className="text-xs text-slate-300 leading-relaxed">{item}</p>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}

        {/* ── Disclaimer ─────────────────────────────────────────────────── */}
        <div className="rounded-xl p-4 text-center"
          style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.04)" }}>
          <p className="text-[11px] text-slate-500 leading-relaxed">
            Market intelligence only. Not financial advice. No trade execution.
          </p>
          {data.data_entitlement_note && (
            <p className="text-[10px] text-slate-500 mt-1">{data.data_entitlement_note}</p>
          )}
        </div>

      </div>
    </div>
  );
}
