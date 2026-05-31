"use client";
// Earnings Calendar — full-screen view, Sprint M17C.
// Works as an overlay (onClose prop) or standalone page (no onClose).

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  X, Calendar, TrendingUp, TrendingDown, Clock,
  ChevronRight, Star, ArrowUpRight, ArrowDownRight,
  Building2, ArrowLeft,
} from "lucide-react";
import type { EarningsEntry } from "@/app/api/earnings-calendar/route";
import type { EarningsHistoryPayload, EarningsHistoryItem } from "@/app/api/earnings-history/route";

// ── Types ─────────────────────────────────────────────────────────────────────

interface DayGroup {
  dateStr: string;
  label: string;
  shortLabel: string;
  dateDisplay: string;
  isToday: boolean;
  isTomorrow: boolean;
  beforeOpen: EarningsEntry[];
  afterClose: EarningsEntry[];
  duringSession: EarningsEntry[];
  total: number;
}

type FilterMode = "all" | "themes" | string;

interface Props {
  earnings: EarningsEntry[];
  ttgSymbolMap: Map<string, { theme_label: string }>;
  /** If provided, renders as overlay with a close button. If absent, renders standalone with back nav. */
  onClose?: () => void;
}

// ── Sector colour map ─────────────────────────────────────────────────────────

const SECTOR_COLORS: Record<string, { text: string; bg: string }> = {
  "Technology":              { text: "#60a5fa", bg: "rgba(96,165,250,0.10)" },
  "Healthcare":              { text: "#34d399", bg: "rgba(52,211,153,0.10)" },
  "Health Care":             { text: "#34d399", bg: "rgba(52,211,153,0.10)" },
  "Financials":              { text: "#fbbf24", bg: "rgba(251,191,36,0.10)" },
  "Financial Services":      { text: "#fbbf24", bg: "rgba(251,191,36,0.10)" },
  "Energy":                  { text: "#f97316", bg: "rgba(249,115,22,0.10)" },
  "Consumer Discretionary":  { text: "#a78bfa", bg: "rgba(167,139,250,0.10)" },
  "Consumer Staples":        { text: "#fb7185", bg: "rgba(251,113,133,0.10)" },
  "Industrials":             { text: "#94a3b8", bg: "rgba(148,163,184,0.10)" },
  "Communication Services":  { text: "#38bdf8", bg: "rgba(56,189,248,0.10)" },
  "Materials":               { text: "#4ade80", bg: "rgba(74,222,128,0.10)" },
  "Real Estate":             { text: "#f472b6", bg: "rgba(244,114,182,0.10)" },
  "Utilities":               { text: "#c084fc", bg: "rgba(192,132,252,0.10)" },
};

function sectorColor(sector: string) {
  return SECTOR_COLORS[sector] ?? { text: "#94a3b8", bg: "rgba(100,116,139,0.08)" };
}

// ── Date helpers ──────────────────────────────────────────────────────────────

function nyToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function addDays(dateStr: string, n: number): string {
  const d = new Date(`${dateStr}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function parseDateUTC(dateStr: string): Date {
  return new Date(`${dateStr}T12:00:00Z`);
}

function getMondayOfWeek(dateStr: string): string {
  const d = parseDateUTC(dateStr);
  const dow = d.getUTCDay();
  if (dow === 0) { d.setUTCDate(d.getUTCDate() + 1); return d.toISOString().slice(0, 10); }
  if (dow === 6) { d.setUTCDate(d.getUTCDate() + 2); return d.toISOString().slice(0, 10); }
  d.setUTCDate(d.getUTCDate() + (1 - dow));
  return d.toISOString().slice(0, 10);
}

function formatDateDisplay(dateStr: string): string {
  return parseDateUTC(dateStr).toLocaleDateString("en-US", {
    month: "short", day: "numeric", timeZone: "UTC",
  });
}

const DAY_NAMES = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const DAY_FULL  = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];

function buildWeekGroups(
  earnings: EarningsEntry[],
  weekMonday: string,
  today: string,
  filter: FilterMode,
  ttgMap: Map<string, { theme_label: string }>
): DayGroup[] {
  const tomorrow = addDays(today, 1);
  const byDate = new Map<string, EarningsEntry[]>();

  for (const e of earnings) {
    // Sector filter: "all" shows everything, any other string filters by sector
    if (filter !== "all" && e.sector !== filter) continue;
    if (!byDate.has(e.date)) byDate.set(e.date, []);
    byDate.get(e.date)!.push(e);
  }

  return Array.from({ length: 5 }, (_, i) => {
    const dateStr = addDays(weekMonday, i);
    const d = parseDateUTC(dateStr);
    const dow = d.getUTCDay();
    const items = byDate.get(dateStr) ?? [];
    items.sort((a, b) => {
      const at = ttgMap.has(a.symbol) ? 0 : 1;
      const bt = ttgMap.has(b.symbol) ? 0 : 1;
      return at !== bt ? at - bt : a.symbol.localeCompare(b.symbol);
    });
    return {
      dateStr,
      label: DAY_FULL[dow],
      shortLabel: DAY_NAMES[dow],
      dateDisplay: formatDateDisplay(dateStr),
      isToday: dateStr === today,
      isTomorrow: dateStr === tomorrow,
      beforeOpen:    items.filter(e => e.time === "bmo"),
      afterClose:    items.filter(e => e.time === "amc"),
      duringSession: items.filter(e => e.time !== "bmo" && e.time !== "amc"),
      total: items.length,
    };
  });
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtEps(v: number | null, sign = true): string {
  if (v === null) return "—";
  const s = sign ? (v >= 0 ? "+" : "-") : (v < 0 ? "-" : "");
  return `${s}$${Math.abs(v).toFixed(2)}`;
}

function fmtRev(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

function fmtMktCap(v: number | null): string {
  if (!v) return "";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`;
  return "";
}

function beatMissChip(actual: number | null, est: number | null) {
  if (actual === null || est === null) return null;
  if (actual > est + 0.005)  return { color: "#34d399", bg: "rgba(52,211,153,0.12)", label: "Beat" };
  if (actual < est - 0.005)  return { color: "#f87171", bg: "rgba(248,113,113,0.12)", label: "Missed" };
  return { color: "#94a3b8", bg: "rgba(148,163,184,0.10)", label: "In line" };
}

// ── Company card ──────────────────────────────────────────────────────────────

const SLOT_ACCENT = {
  bmo: { color: "#34d399", bg: "rgba(52,211,153,0.07)" },
  amc: { color: "#60a5fa", bg: "rgba(96,165,250,0.07)" },
  dmh: { color: "#a78bfa", bg: "rgba(167,139,250,0.07)" },
} as const;

// Company logo — FMP serves logos at a predictable URL, no extra API call needed.
// Hide the img if the logo 404s (e.g. obscure small-caps).
function CompanyLogo({ symbol }: { symbol: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    // Fallback: coloured letter avatar
    return (
      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 font-black text-[13px]"
        style={{ background: "rgba(255,255,255,0.07)", color: "#94a3b8" }}
      >
        {symbol[0]}
      </div>
    );
  }
  return (
    <img
      src={`https://financialmodelingprep.com/image-stock/${symbol}.png`}
      alt={symbol}
      width={36}
      height={36}
      className="w-9 h-9 rounded-xl object-contain shrink-0"
      style={{ background: "rgba(255,255,255,0.06)" }}
      onError={() => setFailed(true)}
    />
  );
}

function CompanyCard({
  item, themeInfo, slotKey, onClick,
}: {
  item: EarningsEntry;
  themeInfo?: { theme_label: string };
  slotKey: string;
  onClick: () => void;
}) {
  const hasTheme = !!themeInfo;
  const hasSector = !!item.sector;
  const sc = sectorColor(item.sector);
  const epsPos = item.epsEst !== null && item.epsEst >= 0;

  return (
    <button
      onClick={onClick}
      className="w-full text-left rounded-2xl p-3 transition-all active:scale-[0.97] flex flex-col gap-2"
      style={{
        background: hasTheme ? "rgba(249,115,22,0.06)" : "rgba(255,255,255,0.035)",
        border: hasTheme
          ? "1px solid rgba(249,115,22,0.22)"
          : "1px solid rgba(255,255,255,0.07)",
      }}
    >
      {/* Row 1: Logo + Ticker + EPS */}
      <div className="flex items-center gap-2">
        <CompanyLogo symbol={item.symbol} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-1">
            <span className="font-black leading-none" style={{ color: "#ffffff", fontSize: "16px", letterSpacing: "-0.02em" }}>
              {item.symbol}
            </span>
            {item.epsEst !== null && (
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md shrink-0" style={{
                background: epsPos ? "rgba(52,211,153,0.12)" : "rgba(248,113,113,0.12)",
                color: epsPos ? "#34d399" : "#f87171",
                border: `1px solid ${epsPos ? "rgba(52,211,153,0.18)" : "rgba(248,113,113,0.18)"}`,
              }}>
                {fmtEps(item.epsEst)} est
              </span>
            )}
          </div>
          {/* Company name */}
          <p className="text-[10px] leading-snug truncate font-medium mt-0.5" style={{ color: "#cbd5e1" }} title={item.name}>
            {item.name || "—"}
          </p>
        </div>
      </div>

      {/* Row 2: Rev + sector + theme pills */}
      <div className="flex items-center gap-1 flex-wrap">
        {item.revEst !== null && (
          <span className="text-[9px] font-medium" style={{ color: "#94a3b8" }}>
            Rev {fmtRev(item.revEst)}
          </span>
        )}
        {hasSector && (
          <span className="text-[8px] font-semibold px-1.5 py-0.5 rounded-full ml-auto" style={{ background: sc.bg, color: sc.text }}>
            {item.sector}
          </span>
        )}
        {hasTheme && !hasSector && (
          <span className="text-[8px] font-semibold px-1.5 py-0.5 rounded-full ml-auto"
            style={{ background: "rgba(249,115,22,0.11)", color: "#fb923c" }}>
            {themeInfo!.theme_label}
          </span>
        )}
      </div>

      {/* Theme pill row (when both sector and theme present) */}
      {hasTheme && hasSector && (
        <span className="text-[8px] font-semibold px-1.5 py-0.5 rounded-full self-start"
          style={{ background: "rgba(249,115,22,0.11)", color: "#fb923c" }}>
          {themeInfo!.theme_label}
        </span>
      )}
    </button>
  );
}

// ── Slot section ──────────────────────────────────────────────────────────────

function SlotSection({ label, slotKey, icon, items, ttgMap, onSelect }: {
  label: string; slotKey: string; icon: React.ReactNode;
  items: EarningsEntry[]; ttgMap: Map<string, { theme_label: string }>;
  onSelect: (e: EarningsEntry) => void;
}) {
  if (items.length === 0) return null;
  const accent = SLOT_ACCENT[slotKey as keyof typeof SLOT_ACCENT] ?? SLOT_ACCENT.dmh;
  return (
    <div>
      <div className="flex items-center gap-2 mb-2.5">
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full"
          style={{ background: accent.bg, border: `1px solid ${accent.color}22` }}>
          <span style={{ color: accent.color }}>{icon}</span>
          <p className="text-[9px] font-bold uppercase tracking-[0.12em]" style={{ color: accent.color }}>{label}</p>
        </div>
        <span className="text-[10px] font-semibold" style={{ color: "#94a3b8" }}>{items.length}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {items.map(item => (
          <CompanyCard
            key={item.symbol + item.date}
            item={item}
            themeInfo={ttgMap.get(item.symbol)}
            slotKey={slotKey}
            onClick={() => onSelect(item)}
          />
        ))}
      </div>
    </div>
  );
}

// ── Day section ───────────────────────────────────────────────────────────────

function DaySection({ group, ttgMap, isActive, onSelect }: {
  group: DayGroup; ttgMap: Map<string, { theme_label: string }>;
  isActive: boolean; onSelect: (e: EarningsEntry) => void;
}) {
  return (
    <section id={`ec-day-${group.dateStr}`}>
      <div className="sticky top-0 z-10 px-4 py-3 flex items-center gap-3"
        style={{ background: "rgba(8,13,22,0.97)", backdropFilter: "blur(16px)", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[16px] font-bold" style={{ color: isActive ? "#f97316" : "#e2e8f0" }}>
              {group.label}
            </span>
            {group.isToday && (
              <span className="text-[8px] font-black px-1.5 py-0.5 rounded-full uppercase"
                style={{ background: "#f97316", color: "#fff" }}>Today</span>
            )}
            {group.isTomorrow && (
              <span className="text-[8px] font-bold px-1.5 py-0.5 rounded-full uppercase"
                style={{ background: "rgba(148,163,184,0.1)", color: "#94a3b8" }}>Tomorrow</span>
            )}
          </div>
          <p className="text-[11px]" style={{ color: "#94a3b8" }}>{group.dateDisplay}</p>
        </div>
        {group.total > 0 && (
          <span className="ml-auto text-[11px] font-semibold px-2 py-0.5 rounded-full"
            style={{ background: "rgba(255,255,255,0.05)", color: "#94a3b8" }}>
            {group.total} reporting
          </span>
        )}
      </div>
      <div className="px-4 pt-4 pb-6 space-y-5">
        {group.total === 0
          ? <p className="text-[12px] py-2" style={{ color: "#64748b" }}>No earnings scheduled.</p>
          : <>
              <SlotSection label="Before market open" slotKey="bmo" icon={<TrendingUp size={9} />} items={group.beforeOpen} ttgMap={ttgMap} onSelect={onSelect} />
              <SlotSection label="After market close"  slotKey="amc" icon={<TrendingDown size={9} />} items={group.afterClose} ttgMap={ttgMap} onSelect={onSelect} />
              <SlotSection label="During session"      slotKey="dmh" icon={<Clock size={9} />} items={group.duringSession} ttgMap={ttgMap} onSelect={onSelect} />
            </>
        }
      </div>
      <div style={{ height: "1px", background: "rgba(255,255,255,0.04)" }} />
    </section>
  );
}

// ── Company detail sheet ──────────────────────────────────────────────────────

function DetailSheet({ item, themeInfo, onClose }: {
  item: EarningsEntry; themeInfo?: { theme_label: string }; onClose: () => void;
}) {
  const [data, setData] = useState<EarningsHistoryPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/earnings-history?symbol=${encodeURIComponent(item.symbol)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled && d) setData(d); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [item.symbol]);

  const profile = data?.profile;
  const price   = data?.price;
  const history = data?.history ?? [];
  const sc = sectorColor(item.sector || profile?.sector || "");

  const priceUp = (price?.changePct ?? 0) >= 0;

  function truncateDesc(text: string, max = 280): string {
    if (!text || text.length <= max) return text ?? "";
    const cut = text.slice(0, max);
    const last = cut.lastIndexOf(".");
    return last > 100 ? cut.slice(0, last + 1) : cut + "…";
  }

  return (
    <div className="fixed inset-0 z-[60] flex flex-col justify-end"
      style={{ background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)" }}
      onClick={onClose}>
      <div className="rounded-t-3xl flex flex-col overflow-hidden"
        style={{ background: "#0f1623", border: "1px solid rgba(255,255,255,0.08)", maxHeight: "90vh" }}
        onClick={e => e.stopPropagation()}>

        {/* Pull handle */}
        <div className="flex justify-center pt-3 pb-1 shrink-0">
          <div className="w-10 h-1 rounded-full" style={{ background: "rgba(255,255,255,0.12)" }} />
        </div>

        {/* Header */}
        <div className="px-5 pt-2 pb-4 shrink-0" style={{ borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              {/* Ticker + theme chip */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[28px] font-black tracking-tight" style={{ color: "#f1f5f9", letterSpacing: "-0.04em" }}>
                  {item.symbol}
                </span>
                {themeInfo && (
                  <span className="text-[9px] font-semibold px-2 py-0.5 rounded-full"
                    style={{ background: "rgba(249,115,22,0.13)", color: "#fb923c", border: "1px solid rgba(249,115,22,0.22)" }}>
                    ★ {themeInfo.theme_label}
                  </span>
                )}
              </div>
              {/* Company name */}
              <p className="text-[14px] leading-snug mt-0.5" style={{ color: "#94a3b8" }}>
                {loading ? "Loading…" : (profile?.name || item.name || item.symbol)}
              </p>
              {/* Sector + industry */}
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                {(item.sector || profile?.sector) && (
                  <span className="text-[9px] font-semibold px-2 py-0.5 rounded-full" style={{ background: sc.bg, color: sc.text }}>
                    {item.sector || profile?.sector}
                  </span>
                )}
                {profile?.industry && profile.industry !== (item.sector || profile?.sector) && (
                  <span className="text-[10px]" style={{ color: "#94a3b8" }}>{profile.industry}</span>
                )}
                {profile?.mktCap ? (
                  <span className="text-[10px]" style={{ color: "#94a3b8" }}>{fmtMktCap(profile.mktCap)} mkt cap</span>
                ) : null}
              </div>
            </div>
            <button onClick={onClose}
              className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-1"
              style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <X size={13} style={{ color: "#94a3b8" }} />
            </button>
          </div>

          {/* Price row */}
          {!loading && price?.price != null && (
            <div className="flex items-center gap-3 mt-3">
              <span className="text-[24px] font-bold" style={{ color: "#f1f5f9" }}>
                ${price.price.toFixed(2)}
              </span>
              {price.changePct != null && (
                <div className="flex items-center gap-1">
                  {priceUp
                    ? <ArrowUpRight size={14} style={{ color: "#34d399" }} />
                    : <ArrowDownRight size={14} style={{ color: "#f87171" }} />}
                  <span className="text-[13px] font-semibold" style={{ color: priceUp ? "#34d399" : "#f87171" }}>
                    {priceUp ? "+" : ""}{price.changePct.toFixed(2)}%
                  </span>
                  {price.change != null && (
                    <span className="text-[11px]" style={{ color: "#94a3b8" }}>
                      ({priceUp ? "+" : ""}${Math.abs(price.change).toFixed(2)})
                    </span>
                  )}
                </div>
              )}
              <span className="text-[10px] ml-auto" style={{ color: "#94a3b8" }}>Last close</span>
            </div>
          )}
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5" style={{ scrollbarWidth: "none" }}>

          {/* Upcoming report */}
          <div className="rounded-2xl p-4"
            style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.12)" }}>
            <p className="text-[9px] font-bold uppercase tracking-widest mb-3" style={{ color: "#f97316" }}>
              Upcoming report
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[10px] mb-0.5" style={{ color: "#94a3b8" }}>EPS estimate</p>
                <p className="text-[18px] font-bold" style={{ color: item.epsEst != null && item.epsEst >= 0 ? "#34d399" : "#f87171" }}>
                  {item.epsEst != null ? fmtEps(item.epsEst) : "—"}
                </p>
              </div>
              <div>
                <p className="text-[10px] mb-0.5" style={{ color: "#94a3b8" }}>Revenue estimate</p>
                <p className="text-[18px] font-bold" style={{ color: "#e2e8f0" }}>
                  {item.revEst != null ? fmtRev(item.revEst) : "—"}
                </p>
              </div>
              <div>
                <p className="text-[10px] mb-0.5" style={{ color: "#94a3b8" }}>Date</p>
                <p className="text-[13px] font-semibold" style={{ color: "#cbd5e1" }}>
                  {parseDateUTC(item.date).toLocaleDateString("en-US", {
                    weekday: "short", month: "short", day: "numeric", timeZone: "UTC",
                  })}
                </p>
              </div>
              <div>
                <p className="text-[10px] mb-0.5" style={{ color: "#94a3b8" }}>Session</p>
                <p className="text-[13px] font-semibold" style={{ color: "#cbd5e1" }}>
                  {item.time === "bmo" ? "Before open" : item.time === "amc" ? "After close" : item.time === "dmh" ? "During session" : "—"}
                </p>
              </div>
            </div>
          </div>

          {/* Previous earnings */}
          {!loading && history.length > 0 && (
            <div>
              <p className="text-[9px] font-bold uppercase tracking-widest mb-3" style={{ color: "#94a3b8" }}>
                Previous earnings
              </p>
              <div className="space-y-2">
                {history.map((h: EarningsHistoryItem, i) => {
                  const chip = beatMissChip(h.epsActual, h.epsEstimate);
                  const label = h.date
                    ? parseDateUTC(h.date).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" })
                    : "—";
                  return (
                    <div key={i} className="flex items-center gap-3 px-3 py-2.5 rounded-xl"
                      style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.05)" }}>
                      <span className="text-[11px] font-medium w-12 shrink-0" style={{ color: "#94a3b8" }}>{label}</span>
                      <div className="flex-1 flex items-center gap-2">
                        <span className="text-[13px] font-bold" style={{ color: "#cbd5e1" }}>
                          {h.epsActual != null ? fmtEps(h.epsActual, false) : "—"}
                        </span>
                        {h.epsEstimate != null && (
                          <span className="text-[10px]" style={{ color: "#94a3b8" }}>
                            est {fmtEps(h.epsEstimate, false)}
                          </span>
                        )}
                      </div>
                      {chip && (
                        <span className="text-[8px] font-bold px-1.5 py-0.5 rounded shrink-0"
                          style={{ background: chip.bg, color: chip.color }}>{chip.label}</span>
                      )}
                      {h.revenueActual != null && (
                        <span className="text-[10px] shrink-0" style={{ color: "#94a3b8" }}>
                          {fmtRev(h.revenueActual)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {loading && (
            <div className="space-y-2">
              {[1,2,3].map(i => (
                <div key={i} className="h-11 rounded-xl animate-pulse" style={{ background: "rgba(255,255,255,0.04)" }} />
              ))}
            </div>
          )}

          {/* About */}
          {!loading && profile?.description && (
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <Building2 size={11} style={{ color: "#94a3b8" }} />
                <p className="text-[9px] font-bold uppercase tracking-widest" style={{ color: "#94a3b8" }}>About</p>
              </div>
              <p className="text-[12px] leading-relaxed" style={{ color: "#94a3b8" }}>
                {truncateDesc(profile.description)}
              </p>
            </div>
          )}

          {/* Decifer theme match */}
          {themeInfo && (
            <div className="rounded-xl p-3"
              style={{ background: "rgba(249,115,22,0.05)", border: "1px solid rgba(249,115,22,0.1)" }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Star size={10} style={{ color: "#f97316" }} />
                <p className="text-[9px] font-bold uppercase tracking-widest" style={{ color: "#f97316" }}>
                  Decifer theme match
                </p>
              </div>
              <p className="text-[11px] leading-relaxed" style={{ color: "#94a3b8" }}>
                {item.symbol} is on the{" "}
                <span style={{ color: "#fb923c", fontWeight: 600 }}>{themeInfo.theme_label}</span> theme.
                This report could be a catalyst for the theme.
              </p>
            </div>
          )}
          <div className="h-4" />
        </div>
      </div>
    </div>
  );
}

// ── Main calendar ─────────────────────────────────────────────────────────────

export default function EarningsCalendarView({ earnings, ttgSymbolMap, onClose }: Props) {
  const router = useRouter();
  const today = nyToday();
  const weekMonday = getMondayOfWeek(today);

  const [filter, setFilter] = useState<FilterMode>("all");
  const [selected, setSelected] = useState<EarningsEntry | null>(null);

  // Sectors present this week, sorted by count descending
  const sectorLabels = Array.from(
    earnings.reduce((acc, e) => {
      if (e.sector) acc.set(e.sector, (acc.get(e.sector) ?? 0) + 1);
      return acc;
    }, new Map<string, number>())
  )
    .sort((a, b) => b[1] - a[1])
    .map(([s]) => s);

  const groups = buildWeekGroups(earnings, weekMonday, today, filter, ttgSymbolMap);
  const themeCount = earnings.filter(e => ttgSymbolMap.has(e.symbol)).length;

  const todayIdx = groups.findIndex(g => g.isToday);
  const activeIdx = todayIdx >= 0 ? todayIdx : 0;

  function scrollToDay(dateStr: string) {
    document.getElementById(`ec-day-${dateStr}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const handleBack = useCallback(() => {
    if (onClose) onClose();
    else router.back();
  }, [onClose, router]);

  return (
    <>
      <div className="fixed inset-0 z-50 flex flex-col" style={{ background: "#080d15" }}>

        {/* ── Top bar ── */}
        <div className="shrink-0" style={{ background: "rgba(8,12,20,0.98)", borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="px-4 pt-4 pb-2 flex items-center gap-3">
            <button onClick={handleBack}
              className="flex items-center justify-center w-8 h-8 rounded-full transition-all active:scale-90"
              style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)" }}>
              {onClose ? <X size={14} style={{ color: "#94a3b8" }} /> : <ArrowLeft size={14} style={{ color: "#94a3b8" }} />}
            </button>
            <div className="flex items-center gap-2.5 flex-1">
              <div className="flex items-center justify-center w-8 h-8 rounded-xl"
                style={{ background: "rgba(249,115,22,0.12)", border: "1px solid rgba(249,115,22,0.2)" }}>
                <Calendar size={15} style={{ color: "#f97316" }} />
              </div>
              <div>
                <p className="text-[17px] font-bold leading-none" style={{ color: "#f1f5f9" }}>Earnings Calendar</p>
                <p className="text-[10px] mt-0.5" style={{ color: "#94a3b8" }}>Week of {formatDateDisplay(weekMonday)}</p>
              </div>
            </div>
          </div>

          {/* Summary */}
          <div className="px-4 pb-3 flex items-center gap-2 flex-wrap">
            <span className="text-[10px] font-semibold px-2.5 py-1 rounded-full"
              style={{ background: "rgba(255,255,255,0.05)", color: "#94a3b8" }}>
              {earnings.length} US companies
            </span>
            {themeCount > 0 && (
              <span className="text-[10px] font-semibold px-2.5 py-1 rounded-full"
                style={{ background: "rgba(249,115,22,0.09)", color: "#fb923c", border: "1px solid rgba(249,115,22,0.18)" }}>
                {themeCount} on your themes
              </span>
            )}
          </div>

          {/* Day pills */}
          <div className="px-4 pb-3 flex gap-2 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
            {groups.map((g, i) => {
              const active = i === activeIdx;
              return (
                <button key={g.dateStr} onClick={() => scrollToDay(g.dateStr)}
                  className="shrink-0 flex flex-col items-center px-3.5 py-2 rounded-2xl transition-all active:scale-95"
                  style={{
                    background: active ? "rgba(249,115,22,0.13)" : "rgba(255,255,255,0.04)",
                    border: active ? "1px solid rgba(249,115,22,0.28)" : "1px solid rgba(255,255,255,0.06)",
                    minWidth: "52px",
                  }}>
                  <span className="text-[11px] font-bold" style={{ color: active ? "#f97316" : "#94a3b8" }}>{g.shortLabel}</span>
                  <span className="text-[10px]" style={{ color: active ? "#fb923c" : "#94a3b8" }}>{g.dateDisplay.split(" ")[1]}</span>
                  {g.total > 0 && <div className="w-1 h-1 rounded-full mt-0.5" style={{ background: active ? "#f97316" : "#94a3b8" }} />}
                </button>
              );
            })}
          </div>

          {/* Sector filter bar — wraps onto multiple lines */}
          <div className="px-4 pb-3 flex flex-wrap gap-2">
            {(["all", ...sectorLabels] as FilterMode[]).map(f => {
              const active = filter === f;
              const label = f === "all" ? "All sectors" : f;
              const sc = f !== "all" ? sectorColor(f) : null;
              return (
                <button key={f} onClick={() => setFilter(f)}
                  className="text-[10px] font-semibold px-3 py-1.5 rounded-full transition-all active:scale-95"
                  style={{
                    background: active ? (sc?.bg ?? "rgba(249,115,22,0.15)") : "rgba(255,255,255,0.04)",
                    color: active ? (sc?.text ?? "#f97316") : "#cbd5e1",
                    border: active
                      ? `1px solid ${sc?.text ?? "#f97316"}44`
                      : "1px solid rgba(255,255,255,0.1)",
                    fontWeight: active ? 700 : 600,
                  }}>
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Scrollable days ── */}
        <div className="flex-1 overflow-y-auto" style={{ scrollbarWidth: "none" }}>
          {groups.map((g, i) => (
            <DaySection key={g.dateStr} group={g} ttgMap={ttgSymbolMap}
              isActive={i === activeIdx} onSelect={setSelected} />
          ))}
          <div className="px-4 py-6 text-center">
            <p className="text-[10px]" style={{ color: "#64748b" }}>
              Data via Financial Modeling Prep. Estimates may differ. Not financial advice.
            </p>
          </div>
        </div>
      </div>

      {selected && (
        <DetailSheet
          item={selected}
          themeInfo={ttgSymbolMap.get(selected.symbol)}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
