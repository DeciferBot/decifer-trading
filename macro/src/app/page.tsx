"use client";

import { useEffect, useState, useCallback } from "react";

// ── Types ────────────────────────────────────────────────────────────────────

interface ActiveDriver {
  id: string;
  label: string;
  evidence: Record<string, number | string>;
}

interface BlockedCondition {
  id: string;
  label: string;
  evidence: Record<string, number | string>;
}

interface Futures {
  es_5d_ret: number;
  nq_5d_ret: number;
  advisory_drivers: string[];
}

interface ActivatedTheme {
  theme_id: string;
  state: string;
  direction: string;
  confidence: number;
  activated_by: string[];
  risk_flags: string[];
}

interface DriversPayload {
  api_version: string;
  ts: string;
  stale: boolean;
  stale_reason: string | null;
  data_ts: string;
  mode: string;
  active_drivers: ActiveDriver[];
  active_driver_ids: string[];
  blocked_conditions: BlockedCondition[];
  futures: Futures | null;
  activated_themes: ActivatedTheme[];
  activated_theme_count: number;
  sensor_count: number;
  disclaimer: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtEvidenceKey(key: string): string {
  return key
    .replace(/_5d_ret$/, " 5d")
    .replace(/_ret$/, "")
    .replace(/_/g, " ")
    .toUpperCase();
}

function fmtEvidenceVal(val: number | string): string {
  if (typeof val === "number") {
    const pct = (val * 100).toFixed(1);
    return `${val >= 0 ? "+" : ""}${pct}%`;
  }
  return String(val);
}

function fmtTs(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleString("en-US", {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
      hour12: false, timeZone: "America/New_York",
    }) + " ET";
  } catch {
    return ts;
  }
}

function fmtRet(val: number): string {
  const pct = (val * 100).toFixed(2);
  return `${val >= 0 ? "+" : ""}${pct}%`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <span className="text-2xs font-mono font-medium tracking-[0.18em] text-[#6B6358] uppercase">
        {children}
      </span>
      <div className="flex-1 h-px bg-[#1E1E1E]" />
    </div>
  );
}

function DriverCard({ driver, idx }: { driver: ActiveDriver; idx: number }) {
  const entries = Object.entries(driver.evidence);
  return (
    <div
      className="card-grid-item border border-[#1E1E1E] bg-[#111111] p-4 flex flex-col gap-3 hover:border-[#2A2A2A] transition-colors"
      style={{ animationDelay: `${idx * 40}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-mono font-medium text-[#E8E0D0] leading-snug">
          {driver.label}
        </span>
        <span className="shrink-0 text-2xs font-mono font-medium tracking-widest text-[#22C55E] border border-[#14532D] px-1.5 py-0.5 bg-[#0A1F0F]">
          ACTIVE
        </span>
      </div>
      {entries.length > 0 && (
        <div className="flex flex-col gap-1">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between">
              <span className="text-2xs font-mono text-[#6B6358] tracking-wider">
                {fmtEvidenceKey(k)}
              </span>
              <span
                className={`text-xs font-mono font-medium tabular-nums ${
                  typeof v === "number" && v >= 0 ? "text-[#22C55E]" : "text-[#EF4444]"
                }`}
              >
                {fmtEvidenceVal(v as number | string)}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-auto pt-2 border-t border-[#1E1E1E]">
        <span className="text-2xs font-mono text-[#3D3830] tracking-widest">
          {driver.id}
        </span>
      </div>
    </div>
  );
}

function ThemeRow({ theme }: { theme: ActivatedTheme }) {
  const isHeadwind = theme.state === "headwind" || theme.direction === "headwind";
  const confidencePct = Math.round(theme.confidence * 100);

  return (
    <div className="flex items-center gap-4 py-3 border-b border-[#1A1A1A] last:border-0 hover:bg-[#0E0E0E] transition-colors px-2 -mx-2 animate-fade-in">
      {/* state dot */}
      <div
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          isHeadwind ? "bg-[#F59E0B]" : "bg-[#22C55E]"
        }`}
      />

      {/* theme label */}
      <span className="text-sm font-mono font-medium text-[#E8E0D0] w-48 shrink-0 truncate">
        {theme.theme_id.replace(/_/g, " ")}
      </span>

      {/* state badge */}
      <span
        className={`text-2xs font-mono font-medium tracking-widest px-1.5 py-0.5 border w-20 text-center shrink-0 ${
          isHeadwind
            ? "text-[#F59E0B] border-[#78350F] bg-[#1A0F00]"
            : "text-[#22C55E] border-[#14532D] bg-[#0A1F0F]"
        }`}
      >
        {theme.state.toUpperCase()}
      </span>

      {/* confidence bar */}
      <div className="flex items-center gap-2 flex-1">
        <div className="flex-1 h-1 bg-[#1E1E1E] rounded-full overflow-hidden max-w-32">
          <div
            className={`h-full rounded-full ${isHeadwind ? "bg-[#F59E0B]" : "bg-[#22C55E]"}`}
            style={{ width: `${confidencePct}%`, opacity: 0.7 }}
          />
        </div>
        <span className="text-2xs font-mono tabular-nums text-[#6B6358] w-8 text-right">
          {confidencePct}%
        </span>
      </div>

      {/* direction chip */}
      <span className="text-2xs font-mono text-[#6B6358] tracking-wider w-16 shrink-0 text-right hidden sm:block">
        {theme.direction.toUpperCase()}
      </span>

      {/* risk flags */}
      <div className="flex gap-1 shrink-0 hidden md:flex">
        {theme.risk_flags.map((f) => (
          <span
            key={f}
            className="text-2xs font-mono text-[#F97316] border border-[#7C3910] px-1.5 py-0.5 bg-[#150A00]"
          >
            {f}
          </span>
        ))}
      </div>
    </div>
  );
}

function BlockedRow({ condition }: { condition: BlockedCondition }) {
  const entries = Object.entries(condition.evidence);
  return (
    <div className="flex items-center gap-4 py-3 border-b border-[#1A1A1A] last:border-0 animate-fade-in">
      <div className="w-1.5 h-1.5 rounded-full shrink-0 bg-[#EF4444]" />
      <span className="text-sm font-mono font-medium text-[#EF4444] flex-1 truncate">
        {condition.label}
      </span>
      {entries.length > 0 && (
        <div className="flex gap-3">
          {entries.map(([k, v]) => (
            <span key={k} className="text-2xs font-mono text-[#6B6358]">
              {fmtEvidenceKey(k)}{" "}
              <span className="text-[#EF4444]">{fmtEvidenceVal(v as number | string)}</span>
            </span>
          ))}
        </div>
      )}
      <span className="text-2xs font-mono tracking-widest text-[#EF4444] border border-[#7F1D1D] px-1.5 py-0.5 bg-[#1A0505] shrink-0">
        BLOCKED
      </span>
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function MacroPage() {
  const [data, setData] = useState<DriversPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);
  const [refreshIn, setRefreshIn] = useState(300);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/drivers", { cache: "no-store" });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? `HTTP ${res.status}`);
      setData(json as DriversPayload);
      setError(null);
      setLastFetch(new Date());
      setRefreshIn(300);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    const tick = setInterval(() => {
      setRefreshIn((prev) => (prev <= 1 ? 300 : prev - 1));
    }, 1000);
    return () => clearInterval(tick);
  }, []);

  const isLive = data?.mode === "live_market_data";

  return (
    <div className="min-h-screen bg-[#0A0A0A] text-[#E8E0D0] font-mono">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <header className="border-b border-[#1E1E1E] px-6 py-4 sticky top-0 bg-[#0A0A0A] z-10">
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <span className="text-[#F97316] text-xl font-mono font-medium tracking-tight">
              DECIFER
            </span>
            <span className="text-[#2A2A2A] text-xl">/</span>
            <span className="text-[#E8E0D0] text-xl font-mono font-light tracking-widest">
              MACRO DRIVERS
            </span>
          </div>

          <div className="flex items-center gap-4">
            {/* mode badge */}
            {data && (
              <div className="flex items-center gap-2">
                <div
                  className={`w-1.5 h-1.5 rounded-full ${
                    isLive ? "bg-[#22C55E] animate-pulse-dot" : "bg-[#6B6358]"
                  }`}
                />
                <span
                  className={`text-xs font-mono tracking-widest ${
                    isLive ? "text-[#22C55E]" : "text-[#6B6358]"
                  }`}
                >
                  {isLive ? "LIVE" : data.mode.replace(/_/g, " ").toUpperCase()}
                </span>
              </div>
            )}

            {/* refresh countdown */}
            <span className="text-2xs font-mono text-[#3D3830] tabular-nums hidden sm:block">
              ↺ {refreshIn}s
            </span>

            {/* manual refresh */}
            <button
              onClick={() => { setLoading(true); fetchData(); }}
              className="text-2xs font-mono text-[#6B6358] hover:text-[#F97316] transition-colors tracking-widest border border-[#1E1E1E] px-2 py-1 hover:border-[#7C3910]"
            >
              REFRESH
            </button>
          </div>
        </div>

        {/* data timestamp sub-row */}
        {data && (
          <div className="max-w-5xl mx-auto mt-2 flex items-center gap-4">
            <span className="text-2xs font-mono text-[#3D3830]">
              DATA <span className="text-[#6B6358]">{fmtTs(data.data_ts)}</span>
            </span>
            <span className="text-[#1E1E1E]">·</span>
            <span className="text-2xs font-mono text-[#3D3830]">
              FEED <span className="text-[#6B6358]">{fmtTs(data.ts)}</span>
            </span>
            {lastFetch && (
              <>
                <span className="text-[#1E1E1E]">·</span>
                <span className="text-2xs font-mono text-[#3D3830]">
                  FETCHED <span className="text-[#6B6358]">{fmtTs(lastFetch.toISOString())}</span>
                </span>
              </>
            )}
          </div>
        )}
      </header>

      {/* ── Stale warning ──────────────────────────────────────────── */}
      {data?.stale && (
        <div className="border-b border-[#78350F] bg-[#1A0F00] px-6 py-2">
          <div className="max-w-5xl mx-auto flex items-center gap-3">
            <span className="text-[#F59E0B] text-xs font-mono">⚠</span>
            <span className="text-[#F59E0B] text-xs font-mono tracking-wide">
              Data is 30+ min old — pipeline may be sleeping.
              {data.stale_reason && (
                <span className="text-[#78350F] ml-2">{data.stale_reason}</span>
              )}
            </span>
          </div>
        </div>
      )}

      {/* ── Loading / Error ─────────────────────────────────────────── */}
      {loading && !data && (
        <div className="max-w-5xl mx-auto px-6 py-16 flex items-center gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-[#F97316] animate-pulse-dot" />
          <span className="text-sm font-mono text-[#6B6358] tracking-widest">LOADING DRIVER STATE…</span>
        </div>
      )}
      {error && (
        <div className="max-w-5xl mx-auto px-6 py-6">
          <div className="border border-[#7F1D1D] bg-[#1A0505] p-4 text-[#EF4444] text-sm font-mono">
            ERROR: {error}
          </div>
        </div>
      )}

      {/* ── Body ────────────────────────────────────────────────────── */}
      {data && (
        <main className="max-w-5xl mx-auto px-6 py-8 flex flex-col gap-10">

          {/* ACTIVE DRIVERS */}
          <section>
            <SectionLabel>
              Active Drivers — {data.active_drivers.length} / {data.sensor_count} sensors
            </SectionLabel>
            {data.active_drivers.length === 0 ? (
              <p className="text-sm font-mono text-[#3D3830]">No active drivers.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {data.active_drivers.map((d, i) => (
                  <DriverCard key={d.id} driver={d} idx={i} />
                ))}
              </div>
            )}
          </section>

          {/* FUTURES ADVISORY */}
          {data.futures && (
            <section>
              <SectionLabel>Futures Advisory</SectionLabel>
              <div className="border border-[#1E1E1E] bg-[#111111] p-4 flex flex-wrap items-center gap-6">
                <div className="flex items-center gap-3">
                  <span className="text-2xs font-mono text-[#6B6358] tracking-widest">ES 5D</span>
                  <span
                    className={`text-lg font-mono font-medium tabular-nums ${
                      data.futures.es_5d_ret >= 0 ? "text-[#22C55E]" : "text-[#EF4444]"
                    }`}
                  >
                    {fmtRet(data.futures.es_5d_ret)}
                  </span>
                </div>
                <div className="w-px h-6 bg-[#1E1E1E]" />
                <div className="flex items-center gap-3">
                  <span className="text-2xs font-mono text-[#6B6358] tracking-widest">NQ 5D</span>
                  <span
                    className={`text-lg font-mono font-medium tabular-nums ${
                      data.futures.nq_5d_ret >= 0 ? "text-[#22C55E]" : "text-[#EF4444]"
                    }`}
                  >
                    {fmtRet(data.futures.nq_5d_ret)}
                  </span>
                </div>
                {data.futures.advisory_drivers.length > 0 && (
                  <>
                    <div className="w-px h-6 bg-[#1E1E1E]" />
                    <div className="flex flex-wrap gap-2">
                      {data.futures.advisory_drivers.map((d) => (
                        <span
                          key={d}
                          className="text-2xs font-mono font-medium tracking-widest text-[#22C55E] border border-[#14532D] px-2 py-0.5 bg-[#0A1F0F]"
                        >
                          {d.replace(/_/g, " ").toUpperCase()}
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </section>
          )}

          {/* ACTIVATED THEMES */}
          <section>
            <SectionLabel>
              Activated Themes — {data.activated_theme_count}
            </SectionLabel>
            {data.activated_themes.length === 0 ? (
              <p className="text-sm font-mono text-[#3D3830]">No activated themes.</p>
            ) : (
              <div className="border border-[#1E1E1E] bg-[#111111] px-4 py-1">
                {data.activated_themes.map((t) => (
                  <ThemeRow key={t.theme_id} theme={t} />
                ))}
              </div>
            )}
          </section>

          {/* BLOCKED CONDITIONS */}
          {data.blocked_conditions.length > 0 && (
            <section>
              <SectionLabel>Blocked Conditions</SectionLabel>
              <div className="border border-[#7F1D1D] bg-[#0D0505] px-4 py-1">
                {data.blocked_conditions.map((c) => (
                  <BlockedRow key={c.id} condition={c} />
                ))}
              </div>
            </section>
          )}

          {/* FOOTER */}
          <footer className="border-t border-[#1E1E1E] pt-6 flex flex-col gap-2">
            <div className="flex items-center gap-4 flex-wrap">
              <span className="text-2xs font-mono text-[#3D3830]">
                SENSORS <span className="text-[#6B6358]">{data.sensor_count}</span>
              </span>
              <span className="text-[#1E1E1E]">·</span>
              <span className="text-2xs font-mono text-[#3D3830]">
                API v<span className="text-[#6B6358]">{data.api_version}</span>
              </span>
              <span className="text-[#1E1E1E]">·</span>
              <span className="text-2xs font-mono text-[#3D3830]">
                AUTO-REFRESH <span className="text-[#6B6358]">5 MIN</span>
              </span>
            </div>
            {data.disclaimer && (
              <p className="text-2xs font-mono text-[#3D3830] max-w-2xl leading-relaxed">
                {data.disclaimer}
              </p>
            )}
          </footer>
        </main>
      )}
    </div>
  );
}
