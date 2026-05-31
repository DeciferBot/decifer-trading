"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getFeed, getLeaderboard, getSymbol, getCompanyInfo } from "@/lib/api";
import type { FlowEvent, LeaderboardRow, SymbolResponse } from "@/lib/types";
import type { CompanyInfo } from "@/app/api/company-info/route";
import { DriverTag, ScoreBar, SideBadge, SignalBadge } from "@/components/SignalBadge";
import { Header } from "@/components/Header";
import { SymbolLogo } from "@/components/SymbolLogo";

const POLL_INTERVAL_MS = 30_000;

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return iso; }
}

function fmtContracts(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function scoreLabel(score: number, side?: string): { text: string; color: string } {
  const dirColor = side === "CALL" ? "#2ecc71" : side === "PUT" ? "#e74c3c" : null;
  if (score >= 100) return { text: "Max unusual", color: dirColor ?? "#e87d2e" };
  if (score >= 80)  return { text: "High activity", color: dirColor ?? "#e87d2e" };
  if (score >= 60)  return { text: "Elevated", color: "#f1c40f" };
  return { text: "Moderate", color: "#888" };
}

import type { Side } from "@/lib/types";

// Returns the correct dominant side based on actual expansion data, overriding stale backend field
function deriveSide(row: LeaderboardRow): Side {
  const ce = row.call_expansion ?? 0;
  const pe = row.put_expansion ?? 0;
  const uc = row.unusual_calls ?? false;
  const up = row.unusual_puts ?? false;
  if (uc && up) return ce >= pe ? "CALL" : "PUT";
  if (uc) return "CALL";
  if (up) return "PUT";
  return row.dominant_side ?? "MIXED";
}

function plainSignal(row: LeaderboardRow): string {
  const ce = row.call_expansion ?? 0;
  const pe = row.put_expansion ?? 0;
  const cv = row.call_volume ?? 0;
  const pv = row.put_volume ?? 0;
  const uc = row.unusual_calls ?? false;
  const up = row.unusual_puts ?? false;
  const callRatio = pv > 10 ? cv / pv : null;
  const putRatio  = cv > 10 ? pv / cv : null;

  if (uc && up) {
    const callDominant  = ce >= pe * 1.5;
    const putDominant   = pe >= ce * 1.5;
    const callSoftLean  = !callDominant && !putDominant && ce > pe * 1.2;
    const putSoftLean   = !callDominant && !putDominant && pe > ce * 1.2;

    if (callDominant && callRatio && callRatio >= 3) {
      return `Calls jumped ${ce.toFixed(1)}x vs puts at ${pe.toFixed(1)}x, with a ${callRatio.toFixed(1)}:1 ratio. Puts are likely hedging. The directional bet is up.`;
    }
    if (callDominant) {
      return `Both sides active, but calls are leading at ${ce.toFixed(1)}x vs puts at ${pe.toFixed(1)}x. Leans bullish with some protection mixed in.`;
    }
    if (putDominant && putRatio && putRatio >= 3) {
      return `Puts are overwhelming calls: ${pe.toFixed(1)}x vs ${ce.toFixed(1)}x, a ${putRatio.toFixed(1)}:1 ratio. Someone is making a serious downside bet or protecting a big position.`;
    }
    if (putDominant) {
      return `Both sides active, puts leading at ${pe.toFixed(1)}x vs calls at ${ce.toFixed(1)}x. Leans bearish or defensive.`;
    }
    if (callSoftLean) {
      return `Both sides spiked, with calls slightly ahead (${ce.toFixed(1)}x vs puts at ${pe.toFixed(1)}x). Likely event positioning with a mild bullish lean.`;
    }
    if (putSoftLean) {
      return `Both sides spiked, with puts slightly ahead (${pe.toFixed(1)}x vs calls at ${ce.toFixed(1)}x). Likely event positioning with a mild bearish lean.`;
    }
    return `Both calls (${ce.toFixed(1)}x) and puts (${pe.toFixed(1)}x) spiked together. When both sides move like this, a known catalyst is usually the driver: earnings, an FDA decision, or a big macro print.`;
  }

  if (uc) {
    if (ce >= 4) {
      const ratioNote = callRatio && callRatio >= 5 ? ` Only ${fmtContracts(pv)} puts traded vs ${fmtContracts(cv)} calls. Very one-sided.` : "";
      return `Heavy call buying at ${ce.toFixed(1)}x yesterday's volume.${ratioNote} A large bet this goes up.`;
    }
    if (ce >= 2) {
      return `Calls picked up to ${ce.toFixed(1)}x normal volume. Traders are positioning for upside.`;
    }
    return `Call activity ticked up to ${ce.toFixed(1)}x normal. Mild bullish interest.`;
  }

  if (up) {
    if (pe >= 4) {
      return `Heavy put buying at ${pe.toFixed(1)}x yesterday's volume. Either a downside bet or someone protecting a large position.`;
    }
    if (pe >= 2) {
      return `Puts picked up to ${pe.toFixed(1)}x normal volume. Traders are positioning for downside or hedging.`;
    }
    return `Put activity ticked up to ${pe.toFixed(1)}x normal. Mild bearish or defensive interest.`;
  }

  return "Volume picked up above normal but below the unusual threshold.";
}

function simpleBrief(info: CompanyInfo | undefined): string {
  if (!info?.brief) return "";
  const name = info.name ?? "";
  // ETFs and funds already explain themselves in the name — brief adds no signal
  if (/\b(ETF|Fund|Trust|Index)\b/i.test(name)) return "";
  const b = info.brief;
  let stripped = b.startsWith(name) ? b.slice(name.length).replace(/^\s*[,.]?\s*/, "") : b;
  stripped = stripped.charAt(0).toUpperCase() + stripped.slice(1);
  // Word-safe truncation at 85 chars
  if (stripped.length <= 85) return stripped;
  const cut = stripped.slice(0, 85).lastIndexOf(" ");
  return stripped.slice(0, cut > 0 ? cut : 85) + "…";
}

function volumeLabel(callVol: number | null | undefined, putVol: number | null | undefined): { text: string; color: string } | null {
  const total = (callVol ?? 0) + (putVol ?? 0);
  if (total === 0) return null;
  if (total >= 50_000) return { text: "Institutional size", color: "#3b82f6" };
  if (total >= 10_000) return { text: "Large flow", color: "#8b5cf6" };
  if (total >= 2_000)  return { text: "Notable", color: "#888" };
  return null; // light volume not worth labeling
}

// ── Market Pulse narrative ────────────────────────────────────────────────────

interface Pulse {
  headline: string;
  detail: string;
  callCount: number;
  putCount: number;
  eventCount: number;
  topSymbols: string[];
}

function buildMarketPulse(rows: LeaderboardRow[], companyMap: Record<string, CompanyInfo>): Pulse | null {
  if (rows.length === 0) return null;

  const callOnly = rows.filter(r => r.unusual_calls && !r.unusual_puts);
  const putOnly  = rows.filter(r => r.unusual_puts && !r.unusual_calls);
  const both     = rows.filter(r => r.unusual_calls && r.unusual_puts);

  // Determine session tone
  const callPct = callOnly.length / rows.length;
  const putPct  = putOnly.length / rows.length;
  const bothPct = both.length / rows.length;

  let tone: string;
  if (callPct > 0.35) tone = "skewed bullish. Calls dominating across the board";
  else if (putPct > 0.35) tone = "skewed bearish. Puts dominating, defensive positioning";
  else if (bothPct > 0.25) tone = "event-driven. Both sides moving together points to known catalysts ahead";
  else tone = "mixed. No clear directional consensus";

  // Top 3 names with short labels
  const top3 = rows.slice(0, 3).map(r => {
    const name = companyMap[r.underlying]?.name;
    const shortName = name
      ? name.replace(/,?\s*(Inc|Corp|Ltd|Co)\.?(\s|$)/gi, " ").replace(/\s+/g, " ").trim().replace(/[,.]$/, "").split(" ").slice(0, 2).join(" ")
      : null;
    return shortName ? `${r.underlying} (${shortName})` : r.underlying;
  });

  // Classify call dominance in top symbol
  const topRow = rows[0];
  const topCe = topRow.call_expansion ?? 0;
  const topPe = topRow.put_expansion ?? 0;
  let leadingNote = "";
  if (topCe >= 3 && topCe > topPe * 1.5) {
    leadingNote = `${topRow.underlying} leads with ${topCe.toFixed(1)}x call expansion. A strong directional bet.`;
  } else if (topPe >= 3 && topPe > topCe * 1.5) {
    leadingNote = `${topRow.underlying} leads with ${topPe.toFixed(1)}x put expansion. Heavy downside positioning.`;
  } else if (topCe >= 2 && topPe >= 2) {
    leadingNote = `${topRow.underlying} has both sides moving (calls ${topCe.toFixed(1)}x, puts ${topPe.toFixed(1)}x). Event risk is the likely driver.`;
  }

  const headline = `${rows.length} symbols with unusual activity. Session is ${tone}.`;

  // Flag if the top symbol's direction contradicts the session tone
  const sessionBullish = callPct > putPct;
  const topIsBullish = (topRow.call_expansion ?? 0) > (topRow.put_expansion ?? 0);
  const contradiction = (sessionBullish && !topIsBullish) || (!sessionBullish && topIsBullish)
    ? ` Note: the biggest flow (${topRow.underlying}) is going the other way.`
    : "";

  const detail = `Leading: ${top3.join(", ")}. ${leadingNote}${contradiction}`;

  return {
    headline,
    detail,
    callCount: callOnly.length,
    putCount: putOnly.length,
    eventCount: both.length,
    topSymbols: rows.slice(0, 3).map(r => r.underlying),
  };
}

function MarketPulseBanner({ pulse, ts }: { pulse: Pulse; ts: string | null }) {
  const asOf = ts
    ? new Date(ts).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) + " ET"
    : null;

  return (
    <div style={{
      background: "linear-gradient(135deg, #111 0%, #0f0f0f 100%)",
      border: "1px solid #2a2a2a",
      borderRadius: 10,
      padding: "16px 18px",
      marginBottom: 20,
      marginTop: 16,
    }}>
      {asOf && (
        <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
          As of {asOf}
        </div>
      )}
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", lineHeight: 1.4, marginBottom: 8 }}>
        {pulse.headline}
      </div>
      <div style={{ fontSize: 13, color: "var(--muted2)", lineHeight: 1.55, marginBottom: 12 }}>
        {pulse.detail}
      </div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <PulseStat label="Call plays" value={pulse.callCount} color="var(--green)" />
        <PulseStat label="Put plays" value={pulse.putCount} color="var(--red)" />
        <PulseStat label="Event-driven" value={pulse.eventCount} color="var(--yellow)" />
      </div>
    </div>
  );
}

function PulseStat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
      <span style={{ fontSize: 18, fontWeight: 700, color, fontFamily: "var(--mono)" }}>{value}</span>
      <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500 }}>{label}</span>
    </div>
  );
}

// ── Symbol Detail Panel ───────────────────────────────────────────────────────

function SymbolPanel({ ticker, onClose }: { ticker: string; onClose: () => void }) {
  const [data, setData] = useState<SymbolResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getSymbol(ticker).then((d) => { setData(d); setLoading(false); });
  }, [ticker]);

  return (
    <div style={{
      position: "fixed", right: 0, top: 0, bottom: 0, width: 380,
      background: "#0f0f0f", borderLeft: "1px solid var(--border)",
      overflowY: "auto", zIndex: 30,
    }}>
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SymbolLogo symbol={ticker} size={36} />
          <span style={{ fontWeight: 700, fontSize: 18, fontFamily: "var(--mono)" }}>{ticker}</span>
        </div>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 18 }}>✕</button>
      </div>

      {loading && <div style={{ padding: 24, color: "var(--muted)", fontSize: 13 }}>Loading...</div>}

      {!loading && !data && (
        <div style={{ padding: 24, color: "var(--muted)", fontSize: 13 }}>No flow data for {ticker}.</div>
      )}

      {!loading && data && (
        <div style={{ padding: 16 }}>
          {data.summary && (
            <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 14, marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <SideBadge side={data.summary.dominant_side} />
                <ScoreBar score={data.summary.top_score} />
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
                <ExpansionPill label="Calls" value={data.summary.call_expansion} active={data.summary.unusual_calls} />
                <ExpansionPill label="Puts" value={data.summary.put_expansion} active={data.summary.unusual_puts} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
                <Stat label="Call volume" value={data.summary.call_volume != null ? fmtContracts(data.summary.call_volume) : "—"} />
                <Stat label="Put volume" value={data.summary.put_volume != null ? fmtContracts(data.summary.put_volume) : "—"} />
                <Stat label="Call sweeps" value={String(data.summary.call_sweep_count)} />
                <Stat label="Put sweeps" value={String(data.summary.put_sweep_count)} />
              </div>
              {(data.summary.flags ?? []).length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  {(data.summary.flags ?? []).map((f, i) => (
                    <div key={i} style={{ fontSize: 11, color: "var(--muted2)", marginBottom: 3 }}>· {f}</div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {(data.summary.driver_tags ?? []).map((t) => <DriverTag key={t} tag={t} />)}
              </div>
            </div>
          )}

          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted)", marginBottom: 10 }}>
            Recent events ({data.event_count})
          </div>
          {data.events.map((e, i) => <EventRow key={i} event={e} compact />)}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, fontFamily: "var(--mono)" }}>{value}</div>
    </div>
  );
}

// ── Event Row ─────────────────────────────────────────────────────────────────

function EventRow({ event: e, compact, onSymbolClick }: {
  event: FlowEvent;
  compact?: boolean;
  onSymbolClick?: (s: string) => void;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 10,
      padding: compact ? "10px 0" : "12px 0",
      borderBottom: "1px solid var(--border)",
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
          <SymbolLogo symbol={e.underlying} size={compact ? 20 : 26} />
          {!compact && onSymbolClick ? (
            <button
              onClick={() => onSymbolClick(e.underlying)}
              style={{ background: "none", border: "none", padding: 0, fontWeight: 700, fontSize: 13, fontFamily: "var(--mono)", color: "var(--text)", textDecoration: "underline", textDecorationColor: "var(--border2)" }}
            >
              {e.underlying}
            </button>
          ) : (
            <span style={{ fontWeight: 700, fontSize: 12, fontFamily: "var(--mono)" }}>{e.underlying}</span>
          )}
          <SignalBadge type={e.signal_type} />
          <SideBadge side={e.side} />
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span>{fmtContracts(e.contracts)} contracts</span>
          {e.strike && <span>@ ${e.strike.toFixed(0)} strike</span>}
          {e.expiry && <span>exp {e.expiry}</span>}
          {e.price && <span>px ${e.price.toFixed(2)}</span>}
          <span>{fmtTime(e.ts)}</span>
        </div>
        {e.driver_tags.length > 0 && (
          <div style={{ marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {e.driver_tags.map((t) => <DriverTag key={t} tag={t} />)}
          </div>
        )}
      </div>
      <ScoreBar score={e.score} />
    </div>
  );
}

// ── Leaderboard Row ───────────────────────────────────────────────────────────

function ExpansionPill({ label, value, active }: { label: string; value: number | null | undefined; active?: boolean }) {
  if (!value) return null;
  const color = active ? (value >= 3 ? "#e74c3c" : value >= 2 ? "#e87d2e" : "#f1c40f") : "var(--muted)";
  return (
    <span style={{
      fontFamily: "var(--mono)", fontSize: 11, fontWeight: 600,
      color, background: `${color}18`, border: `1px solid ${color}33`,
      borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap",
    }}>
      {label} {value.toFixed(1)}×
    </span>
  );
}

function LeaderRow({ row, info, onSymbolClick }: {
  row: LeaderboardRow;
  info?: CompanyInfo;
  onSymbolClick: (s: string) => void;
}) {
  const brief = simpleBrief(info);
  const signal = plainSignal(row);
  const side = deriveSide(row);
  const { text: scoreText, color: scoreColor } = scoreLabel(row.top_score, side);

  return (
    <div
      onClick={() => onSymbolClick(row.underlying)}
      style={{ padding: "14px 0", cursor: "pointer", borderBottom: "1px solid var(--border)" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#0d0d0d")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 6 }}>
        <SymbolLogo symbol={row.underlying} size={34} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
            <span style={{ fontWeight: 700, fontSize: 14, fontFamily: "var(--mono)", flexShrink: 0 }}>
              {row.underlying}
            </span>
            {info?.name && (
              <span style={{ fontSize: 12, color: "var(--muted2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {info.name}
              </span>
            )}
          </div>
          {/* Company context — secondary, not the story */}
          {brief && (
            <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.45, marginBottom: 6 }}>
              {brief}
            </div>
          )}
          {/* What this flow is actually saying */}
          <div style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.55, marginBottom: 8 }}>
            {signal}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <ExpansionPill label="Calls" value={row.call_expansion} active={row.unusual_calls} />
            <ExpansionPill label="Puts" value={row.put_expansion} active={row.unusual_puts} />
            {row.call_volume != null && row.put_volume != null && (
              <span style={{ fontSize: 10, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                {fmtContracts(row.call_volume)}C · {fmtContracts(row.put_volume)}P
              </span>
            )}
            {(() => {
              const vl = volumeLabel(row.call_volume, row.put_volume);
              return vl ? (
                <span style={{ fontSize: 10, fontWeight: 600, color: vl.color, letterSpacing: "0.04em" }}>
                  {vl.text}
                </span>
              ) : null;
            })()}
            {(row.driver_tags ?? []).slice(0, 1).map((t) => <DriverTag key={t} tag={t} />)}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 5, flexShrink: 0, minWidth: 80 }}>
          <SideBadge side={side} />
          <span style={{ fontSize: 11, fontWeight: 700, color: scoreColor }}>{scoreText}</span>
          <ScoreBar score={row.top_score} side={side} />
        </div>
      </div>
    </div>
  );
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

type Tab = "leaderboard" | "feed";

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <div style={{ display: "flex", borderBottom: "1px solid var(--border)", padding: "0 20px" }}>
      {(["leaderboard", "feed"] as Tab[]).map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          style={{
            background: "none", border: "none",
            padding: "12px 16px",
            fontSize: 13, fontWeight: 600,
            color: active === t ? "var(--text)" : "var(--muted)",
            borderBottom: active === t ? "2px solid var(--orange)" : "2px solid transparent",
            marginBottom: -1, textTransform: "capitalize",
          }}
        >
          {t === "leaderboard" ? "🔥 Leaderboard" : "📡 Live Feed"}
        </button>
      ))}
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

function FilterBar({
  signal, setSignal, side, setSide,
}: {
  signal: string; setSignal: (s: string) => void;
  side: string; setSide: (s: string) => void;
}) {
  const chip = (label: string, active: boolean, onClick: () => void) => (
    <button
      key={label}
      onClick={onClick}
      style={{
        background: active ? "rgba(232,125,46,0.15)" : "var(--surface2)",
        border: `1px solid ${active ? "rgba(232,125,46,0.4)" : "var(--border)"}`,
        color: active ? "var(--orange)" : "var(--muted2)",
        borderRadius: 20, padding: "3px 12px",
        fontSize: 11, fontWeight: 600, letterSpacing: "0.05em",
      }}
    >
      {label}
    </button>
  );

  return (
    <div style={{ display: "flex", gap: 6, padding: "10px 20px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
      {chip("ALL", !signal, () => setSignal(""))}
      {chip("SWEEP", signal === "SWEEP", () => setSignal(signal === "SWEEP" ? "" : "SWEEP"))}
      {chip("CLUSTER", signal === "CLUSTER", () => setSignal(signal === "CLUSTER" ? "" : "CLUSTER"))}
      {chip("CROSS-EXPIRY", signal === "CROSS_EXPIRY", () => setSignal(signal === "CROSS_EXPIRY" ? "" : "CROSS_EXPIRY"))}
      <span style={{ width: 1, background: "var(--border)", margin: "0 4px" }} />
      {chip("CALLS", side === "CALL", () => setSide(side === "CALL" ? "" : "CALL"))}
      {chip("PUTS", side === "PUT", () => setSide(side === "PUT" ? "" : "PUT"))}
    </div>
  );
}

// ── Live feed offline state ───────────────────────────────────────────────────

function FeedOfflineCard() {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: 10,
      padding: "28px 24px",
      marginTop: 20,
      textAlign: "center",
    }}>
      <div style={{ fontSize: 28, marginBottom: 12 }}>📡</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>
        Live stream offline
      </div>
      <div style={{ fontSize: 13, color: "var(--muted2)", lineHeight: 1.6, maxWidth: 360, margin: "0 auto", marginBottom: 16 }}>
        The real-time event stream runs during market hours only. Individual option sweeps and clusters are captured as they happen and appear here while the market is open.
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>
        Outside market hours, use the <strong style={{ color: "var(--muted2)" }}>Leaderboard</strong> tab. It shows the end-of-session summary with all unusual activity ranked by magnitude.
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function OptionsPage() {
  const [tab, setTab] = useState<Tab>("leaderboard");
  const [signal, setSignal] = useState("");
  const [side, setSide] = useState("");
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[]>([]);
  const [feed, setFeed] = useState<FlowEvent[]>([]);
  const [feedAvailable, setFeedAvailable] = useState<boolean | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [companyMap, setCompanyMap] = useState<Record<string, CompanyInfo>>({});

  const refresh = useCallback(async () => {
    const [lbData, feedData] = await Promise.all([
      getLeaderboard({ limit: 50 }),
      getFeed({ limit: 100, signal: signal || undefined, side: side || undefined }),
    ]);

    if (!lbData && !feedData) { setUnavailable(true); return; }
    setUnavailable(false);

    if (lbData) {
      setLeaderboard(lbData.leaderboard);
      setLastUpdated(lbData.ts);
      const syms = lbData.leaderboard.map((r) => r.underlying);
      getCompanyInfo(syms).then((d) => {
        if (!d?.results) return;
        setCompanyMap((prev) => {
          const next = { ...prev };
          for (const c of d.results) next[c.symbol] = c;
          return next;
        });
      });
    }

    if (feedData) {
      setFeed(feedData.events);
      setLastUpdated(feedData.ts);
      setFeedAvailable(true);
    } else {
      // leaderboard loaded but feed endpoint returned null — stream offline
      setFeedAvailable(false);
    }
  }, [signal, side]);

  useEffect(() => { refresh(); }, [refresh]);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    timerRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [refresh]);

  const pulse = buildMarketPulse(leaderboard, companyMap);

  return (
    <div style={{ minHeight: "100vh" }}>
      <Header lastUpdated={lastUpdated} />

      <div style={{ maxWidth: selectedSymbol ? "calc(100% - 380px)" : "900px", margin: "0 auto" }}>
        <TabBar active={tab} onChange={setTab} />
        {tab === "feed" && (
          <FilterBar signal={signal} setSignal={setSignal} side={side} setSide={setSide} />
        )}

        <div style={{ padding: "0 20px 48px" }}>
          {unavailable && (
            <div style={{ textAlign: "center", padding: "60px 0", color: "var(--muted)", fontSize: 14 }}>
              <div style={{ marginBottom: 8 }}>Stream not yet active</div>
              <div style={{ fontSize: 12 }}>Start the options-flow-monitor container on the DO droplet.</div>
            </div>
          )}

          {!unavailable && tab === "leaderboard" && (
            <>
              {pulse && <MarketPulseBanner pulse={pulse} ts={lastUpdated} />}

              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", paddingTop: 4, paddingBottom: 8 }}>
                <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                  {leaderboard.length} symbols ranked by unusual flow
                </span>
                <span style={{ fontSize: 10, color: "var(--muted)" }}>
                  Score = how unusual today&apos;s activity is vs. yesterday
                </span>
              </div>

              {leaderboard.length === 0 && (
                <div style={{ color: "var(--muted)", fontSize: 13, padding: "24px 0" }}>No unusual flow detected yet.</div>
              )}
              {leaderboard.map((row) => (
                <LeaderRow key={row.underlying} row={row} info={companyMap[row.underlying]} onSymbolClick={setSelectedSymbol} />
              ))}
            </>
          )}

          {!unavailable && tab === "feed" && (
            <>
              {feedAvailable === false && <FeedOfflineCard />}

              {feedAvailable === true && (
                <>
                  <div style={{ paddingTop: 16, paddingBottom: 8, fontSize: 11, color: "var(--muted)", fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                    Live events — newest first
                  </div>
                  {feed.length === 0 && (
                    <div style={{ color: "var(--muted)", fontSize: 13, padding: "24px 0" }}>No events match this filter.</div>
                  )}
                  {feed.map((e, i) => (
                    <EventRow key={i} event={e} onSymbolClick={setSelectedSymbol} />
                  ))}
                </>
              )}
            </>
          )}
        </div>
      </div>

      {selectedSymbol && (
        <SymbolPanel ticker={selectedSymbol} onClose={() => setSelectedSymbol(null)} />
      )}
    </div>
  );
}
