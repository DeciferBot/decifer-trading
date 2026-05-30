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
              {/* Expansion ratios */}
              <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
                <ExpansionPill label="Calls" value={data.summary.call_expansion} active={data.summary.unusual_calls} />
                <ExpansionPill label="Puts" value={data.summary.put_expansion} active={data.summary.unusual_puts} />
              </div>
              {/* Volume breakdown */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
                <Stat label="Call volume" value={data.summary.call_volume != null ? fmtContracts(data.summary.call_volume) : "—"} />
                <Stat label="Put volume" value={data.summary.put_volume != null ? fmtContracts(data.summary.put_volume) : "—"} />
                <Stat label="Call sweeps" value={String(data.summary.call_sweep_count)} />
                <Stat label="Put sweeps" value={String(data.summary.put_sweep_count)} />
              </div>
              {/* Flags */}
              {(data.summary.flags ?? []).length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  {(data.summary.flags ?? []).map((f, i) => (
                    <div key={i} style={{ fontSize: 11, color: "#ccc", marginBottom: 3 }}>· {f}</div>
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
          <span style={{ color: "var(--muted)" }}>{fmtTime(e.ts)}</span>
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
  const primaryFlag = (row.flags ?? [])[0] ?? null;

  return (
    <div
      onClick={() => onSymbolClick(row.underlying)}
      style={{
        padding: "13px 0", cursor: "pointer",
        borderBottom: "1px solid var(--border)",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#0d0d0d")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {/* Row 1: logo + symbol + company name + side + score */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: info?.brief ? 4 : 6 }}>
        <SymbolLogo symbol={row.underlying} size={32} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontWeight: 700, fontSize: 14, fontFamily: "var(--mono)", flexShrink: 0 }}>
              {row.underlying}
            </span>
            {info?.name && (
              <span style={{ fontSize: 12, color: "var(--muted2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {info.name}
              </span>
            )}
          </div>
          {info?.brief && (
            <div style={{ fontSize: 11, color: "#666", marginTop: 2, lineHeight: 1.4, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
              {info.brief}
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4, flexShrink: 0 }}>
          <SideBadge side={row.dominant_side} />
          <ScoreBar score={row.top_score} />
        </div>
      </div>

      {/* Row 2: expansion pills */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 5, paddingLeft: 0 }}>
        <ExpansionPill label="C" value={row.call_expansion} active={row.unusual_calls} />
        <ExpansionPill label="P" value={row.put_expansion} active={row.unusual_puts} />
        {row.call_volume != null && row.put_volume != null && (
          <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--mono)" }}>
            {fmtContracts(row.call_volume)}C · {fmtContracts(row.put_volume)}P
          </span>
        )}
        {row.total_contracts > 0 && (row.call_volume == null) && (
          <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--mono)" }}>
            {fmtContracts(row.total_contracts)} contracts
          </span>
        )}
      </div>

      {/* Row 3: signal description + driver tags */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {primaryFlag && (
          <span style={{ fontSize: 11, color: "#aaa" }}>{primaryFlag}</span>
        )}
        {(row.driver_tags ?? []).slice(0, 2).map((t) => <DriverTag key={t} tag={t} />)}
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
        color: active ? "var(--orange)" : "var(--muted)",
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

// ── Main page ─────────────────────────────────────────────────────────────────

export default function OptionsPage() {
  const [tab, setTab] = useState<Tab>("leaderboard");
  const [signal, setSignal] = useState("");
  const [side, setSide] = useState("");
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[]>([]);
  const [feed, setFeed] = useState<FlowEvent[]>([]);
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
      // Fetch company info for visible symbols (fire-and-forget, non-blocking)
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
    if (feedData) { setFeed(feedData.events); setLastUpdated(feedData.ts); }
  }, [signal, side]);

  useEffect(() => { refresh(); }, [refresh]);

  // Auto-poll
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    timerRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [refresh]);

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
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", paddingTop: 16, paddingBottom: 8 }}>
                <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                  Hottest symbols — last 30 min
                </span>
                <span style={{ fontSize: 10, color: "#444" }}>
                  Score = flow intensity (0–100) · 100 = max volume expansion + strong directional skew
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
              <div style={{ paddingTop: 16, paddingBottom: 8, fontSize: 11, color: "var(--muted)", fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                Live events — newest first
              </div>
              {feed.length === 0 && (
                <div style={{ color: "var(--muted)", fontSize: 13, padding: "24px 0" }}>No events in the current filter.</div>
              )}
              {feed.map((e, i) => (
                <EventRow key={i} event={e} onSymbolClick={setSelectedSymbol} />
              ))}
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
