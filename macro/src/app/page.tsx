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

interface StoryHero {
  headline: string;
  subline: string | null;
  tension: string | null;
  regime: string;
  regimeBg: string;
  regimeText: string;
  regimeBorder: string;
}

// ── Story generation ─────────────────────────────────────────────────────────

// Maps driver IDs to what is actually happening in the world — plain English, no jargon.
const DRIVER_PLAIN: Record<string, string> = {
  ai_capex_growth:          "spending on AI infrastructure — data centres, power, and the chips that run them",
  ai_compute_demand:        "surging demand for the computing power that runs AI models",
  yields_falling:           "falling interest rates",
  oil_supply_shock:         "a sharp drop in oil prices",
  geopolitical_risk_rising: "elevated geopolitical tension",
  credit_stress_rising:     "widening credit spreads",
  risk_on_rotation:         "investors moving money into riskier assets",
  futures_risk_on:          "futures markets pointing higher",
  futures_risk_off:         "futures markets pointing lower",
  small_cap_risk_on:        "small-cap stocks joining the rally",
  gold_safe_haven_bid:      "investors buying gold as a safe haven",
  credit_stress_easing:     "credit markets stabilising",
};

// Blocked condition IDs to plain friction language
const BLOCKED_PLAIN: Record<string, string> = {
  credit_stress_rising: "the bond market is flashing caution — credit spreads are widening even as stocks climb",
  geopolitical_risk_rising: "geopolitical risk is elevated, which could interrupt the move",
};

function driverPlain(driver: ActiveDriver): string {
  return DRIVER_PLAIN[driver.id] ?? driver.label.toLowerCase();
}

function buildStoryHero(data: DriversPayload): StoryHero {
  const { active_drivers, blocked_conditions, futures } = data;
  const hasFuturesRiskOn = futures?.advisory_drivers?.includes("futures_risk_on");
  const hasFuturesRiskOff = futures?.advisory_drivers?.includes("futures_risk_off");
  const hasBlocked = blocked_conditions.length > 0;
  const driverCount = active_drivers.length;

  // Regime
  let regime: string;
  let regimeBg: string;
  let regimeText: string;
  let regimeBorder: string;

  if (hasBlocked && driverCount > 0) {
    regime = "MIXED SIGNALS";
    regimeBg = "var(--accent-amber-bg)";
    regimeText = "var(--accent-amber)";
    regimeBorder = "var(--accent-amber-border)";
  } else if (driverCount === 0) {
    regime = "QUIET";
    regimeBg = "#F5F5F5";
    regimeText = "#6B7280";
    regimeBorder = "#E5E7EB";
  } else if (hasFuturesRiskOff) {
    regime = "RISK OFF";
    regimeBg = "var(--accent-red-bg)";
    regimeText = "var(--accent-red)";
    regimeBorder = "var(--accent-red-border)";
  } else {
    regime = "RISK ON";
    regimeBg = "var(--accent-green-bg)";
    regimeText = "var(--accent-green)";
    regimeBorder = "var(--accent-green-border)";
  }

  // Headline — describe what is happening, not what our system calls it
  let headline = "";
  if (driverCount === 0) {
    headline = "No clear macro force is in play right now. Markets look quiet.";
  } else if (hasFuturesRiskOn && futures) {
    const nqPct = (futures.nq_5d_ret * 100).toFixed(1);
    // Lead with the phenomenon, not the driver name
    const aiDrivers = active_drivers.filter((d) =>
      ["ai_capex_growth", "ai_compute_demand"].includes(d.id)
    );
    const hasOilDrop = active_drivers.some((d) => d.id === "oil_supply_shock");
    const hasYieldsFalling = active_drivers.some((d) => d.id === "yields_falling");

    if (aiDrivers.length >= 2) {
      headline = `Tech is the story this week. The Nasdaq is up ${nqPct}% over five days, driven by heavy spending on AI — the chips, data centres, and energy that make it run.`;
    } else if (aiDrivers.length === 1) {
      headline = `Markets are leaning into growth. The Nasdaq is up ${nqPct}% this week, with ${driverPlain(aiDrivers[0])} the clearest force behind the move.`;
    } else if (hasOilDrop && hasYieldsFalling) {
      headline = `Oil is falling and so are interest rates — a combination that tends to lift stocks broadly. The Nasdaq is up ${nqPct}% over five days.`;
    } else {
      const top = driverPlain(active_drivers[0]);
      headline = `Markets are moving higher. The Nasdaq is up ${nqPct}% over five days, with ${top} as the clearest driver.`;
    }
  } else if (hasFuturesRiskOff && futures) {
    const nqPct = Math.abs(futures.nq_5d_ret * 100).toFixed(1);
    headline = `Markets are pulling back. The Nasdaq is down ${nqPct}% over five days. ${driverCount > 0 ? `The pressure is coming from ${driverPlain(active_drivers[0])}.` : ""}`;
  } else {
    const top = driverPlain(active_drivers[0]);
    const second = active_drivers[1] ? ` alongside ${driverPlain(active_drivers[1])}` : "";
    headline = `The dominant force right now is ${top}${second}.`;
  }

  // Subline — add context about breadth or what this means
  let subline: string | null = null;
  const hasOilDrop = active_drivers.some((d) => d.id === "oil_supply_shock");
  const hasYieldsFalling = active_drivers.some((d) => d.id === "yields_falling");
  const hasRiskOn = active_drivers.some((d) => d.id === "risk_on_rotation");

  if (hasFuturesRiskOn) {
    if (hasOilDrop && !hasYieldsFalling) {
      subline = "Oil dropping this sharply also takes pressure off inflation, which gives the rally more room to run.";
    } else if (hasYieldsFalling && !hasOilDrop) {
      subline = "Falling rates make growth stocks cheaper to own, which helps explain the tech-led move.";
    } else if (hasOilDrop && hasYieldsFalling) {
      subline = "Both forces tend to benefit growth stocks, which helps explain why tech is leading.";
    } else if (hasRiskOn && driverCount >= 3) {
      subline = "The move is broad. Investors are rotating into equities across the board, not just in one pocket of the market.";
    }
  }

  // Tension — explain what the friction actually means, not what we call it
  let tension: string | null = null;
  if (hasBlocked) {
    const blockedId = blocked_conditions[0]?.id ?? "";
    const plain = BLOCKED_PLAIN[blockedId];
    if (plain && hasFuturesRiskOn) {
      tension = `Worth watching: ${plain}. When stocks and credit diverge like this, one of them usually catches up to the other.`;
    } else if (plain) {
      tension = `One caution: ${plain}.`;
    } else {
      tension = `One caution flag is active. The rally may be running into some friction.`;
    }
  }

  return { headline, subline, tension, regime, regimeBg, regimeText, regimeBorder };
}

// ── Conviction ───────────────────────────────────────────────────────────────

type Conviction = "high" | "medium" | "low" | "watchlist";

// All colors reference CSS variables so dark/light toggle is automatic
const CONVICTION_STYLE: Record<Conviction, {
  bg: string; border: string; leftAccent: string;
  badge: string; badgeColor: string; badgeBg: string; badgeBorder: string;
}> = {
  high: {
    bg: "var(--cv-high-bg)",
    border: "var(--cv-high-border)",
    leftAccent: "var(--cv-high-accent)",
    badge: "HIGH CONVICTION",
    badgeColor: "var(--cv-high-badge-color)",
    badgeBg: "var(--cv-high-badge-bg)",
    badgeBorder: "var(--cv-high-badge-border)",
  },
  medium: {
    bg: "var(--cv-medium-bg)",
    border: "var(--cv-medium-border)",
    leftAccent: "var(--cv-medium-accent)",
    badge: "ACTIVE",
    badgeColor: "var(--cv-medium-badge-color)",
    badgeBg: "var(--cv-medium-badge-bg)",
    badgeBorder: "var(--cv-medium-badge-border)",
  },
  low: {
    bg: "var(--cv-low-bg)",
    border: "var(--cv-low-border)",
    leftAccent: "var(--cv-low-accent)",
    badge: "ACTIVE",
    badgeColor: "var(--cv-low-badge-color)",
    badgeBg: "var(--cv-low-badge-bg)",
    badgeBorder: "var(--cv-low-badge-border)",
  },
  watchlist: {
    bg: "var(--cv-watch-bg)",
    border: "var(--cv-watch-border)",
    leftAccent: "var(--cv-watch-accent)",
    badge: "BUILDING",
    badgeColor: "var(--cv-watch-badge-color)",
    badgeBg: "var(--cv-watch-badge-bg)",
    badgeBorder: "var(--cv-watch-badge-border)",
  },
};

function driverConviction(driver: ActiveDriver): Conviction {
  const vals = Object.values(driver.evidence).filter((v) => typeof v === "number") as number[];
  if (vals.length === 0) return "low";
  const avgAbs = vals.reduce((s, v) => s + Math.abs(v), 0) / vals.length;
  if (avgAbs >= 0.05) return "high";
  if (avgAbs >= 0.02) return "medium";
  return "low";
}

function themeConviction(theme: ActivatedTheme): Conviction {
  if (theme.state === "crowded") return "watchlist";
  if (theme.confidence >= 0.70) return "high";
  if (theme.confidence >= 0.40) return "medium";
  return "low";
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function parseStaleReason(reason: string | null): string {
  if (!reason) return "";
  const match = reason.match(/data_(\d+)min_old/);
  if (match) {
    const mins = parseInt(match[1]);
    if (mins < 60) return `last updated ${mins} minutes ago`;
    if (mins < 1440) return `last updated ${Math.round(mins / 60)} hours ago`;
    return `last updated ${Math.round(mins / 1440)} days ago`;
  }
  return reason;
}

function fmtEvidenceKey(key: string): string {
  return key
    .replace(/_5d_ret$/, " 5D")
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
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
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

function SectionLabel({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <span
        style={{
          fontFamily: "'DM Sans', system-ui, sans-serif",
          fontSize: "11px",
          fontWeight: 600,
          letterSpacing: "0.12em",
          color: "var(--text-muted)",
          textTransform: "uppercase",
        }}
      >
        {children}
      </span>
      {count !== undefined && (
        <span
          style={{
            fontFamily: "'DM Mono', monospace",
            fontSize: "11px",
            color: "var(--text-muted)",
            background: "var(--border-light)",
            padding: "1px 6px",
            borderRadius: "10px",
          }}
        >
          {count}
        </span>
      )}
      <div style={{ flex: 1, height: "1px", background: "var(--border)" }} />
    </div>
  );
}

function DriverCard({ driver, idx }: { driver: ActiveDriver; idx: number }) {
  const entries = Object.entries(driver.evidence);
  const conviction = driverConviction(driver);
  const style = CONVICTION_STYLE[conviction];
  const description = DRIVER_PLAIN[driver.id] ?? driver.label.toLowerCase();

  return (
    <div
      className="card-grid-item"
      style={{
        background: style.bg,
        border: `1px solid ${style.border}`,
        borderLeft: `3px solid ${style.leftAccent}`,
        borderRadius: "10px",
        padding: "16px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        animationDelay: `${60 + idx * 40}ms`,
        boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
        transition: "box-shadow 0.15s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.boxShadow = "0 4px 14px rgba(0,0,0,0.08)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.boxShadow = "0 1px 4px rgba(0,0,0,0.04)";
      }}
    >
      {/* Conviction badge */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
        <span
          style={{
            fontSize: "10px",
            fontFamily: "'DM Mono', monospace",
            fontWeight: 600,
            letterSpacing: "0.08em",
            color: style.badgeColor,
            background: style.badgeBg,
            border: `1px solid ${style.badgeBorder}`,
            padding: "2px 8px",
            borderRadius: "4px",
          }}
        >
          {style.badge}
        </span>
      </div>

      {/* Human description — leads */}
      <p
        style={{
          fontFamily: "'DM Sans', system-ui, sans-serif",
          fontSize: "14px",
          fontWeight: 400,
          color: "var(--text-primary)",
          lineHeight: 1.5,
          margin: 0,
          textTransform: "capitalize",
        }}
      >
        {description.charAt(0).toUpperCase() + description.slice(1)}.
      </p>

      {/* Internal label pill — for traceability */}
      <div>
        <span
          style={{
            display: "inline-block",
            fontFamily: "'DM Mono', monospace",
            fontSize: "10px",
            color: "var(--text-muted)",
            background: "transparent",
            border: "1px solid var(--border)",
            padding: "2px 8px",
            borderRadius: "20px",
            letterSpacing: "0.04em",
          }}
        >
          {driver.id}
        </span>
      </div>

      {/* Evidence values */}
      {entries.length > 0 && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "5px",
            paddingTop: "8px",
            borderTop: "1px solid var(--border)",
          }}
        >
          {entries.map(([k, v]) => (
            <div key={k} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span
                style={{
                  fontFamily: "'DM Mono', monospace",
                  fontSize: "11px",
                  color: "var(--text-muted)",
                  letterSpacing: "0.04em",
                }}
              >
                {fmtEvidenceKey(k)}
              </span>
              <span
                style={{
                  fontFamily: "'DM Mono', monospace",
                  fontSize: "13px",
                  fontWeight: 500,
                  fontVariantNumeric: "tabular-nums",
                  color: typeof v === "number" && v >= 0 ? "var(--accent-green)" : "var(--accent-red)",
                }}
              >
                {fmtEvidenceVal(v as number | string)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ThemeRow({ theme, idx }: { theme: ActivatedTheme; idx: number }) {
  const isHeadwind = theme.state === "headwind" || theme.direction === "headwind";
  const conviction = themeConviction(theme);
  const cvStyle = CONVICTION_STYLE[conviction];
  const confidencePct = Math.round(theme.confidence * 100);
  const label = theme.theme_id.replace(/_/g, " ");

  const accentColor = isHeadwind ? "var(--accent-amber)" : cvStyle.leftAccent;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "16px",
        padding: "13px 20px",
        borderBottom: "1px solid var(--border)",
        borderLeft: `3px solid ${accentColor}`,
        background: "var(--surface)",
        animation: `fade-up 0.3s ease-out ${80 + idx * 35}ms forwards`,
        opacity: 0,
        transition: "background 0.1s ease",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--surface-warm)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--surface)"; }}
    >
      {/* Theme name — white, readable */}
      <span
        style={{
          fontFamily: "'DM Sans', system-ui, sans-serif",
          fontSize: "14px",
          fontWeight: 500,
          color: "var(--text-primary)",
          minWidth: "190px",
          flexShrink: 0,
          textTransform: "capitalize",
        }}
      >
        {label}
      </span>

      {/* State badge — accent color, no tinted background */}
      <span
        style={{
          fontSize: "10px",
          fontFamily: "'DM Mono', monospace",
          fontWeight: 600,
          letterSpacing: "0.1em",
          color: accentColor,
          border: `1px solid ${accentColor}`,
          background: "transparent",
          padding: "2px 8px",
          borderRadius: "4px",
          flexShrink: 0,
          whiteSpace: "nowrap",
          opacity: 0.9,
        }}
      >
        {isHeadwind ? "HEADWIND" : "ACTIVE"}
      </span>

      {/* Confidence bar */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: 1, minWidth: 0 }}>
        <div
          style={{
            flex: 1,
            maxWidth: "100px",
            height: "2px",
            background: "var(--border)",
            borderRadius: "2px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              borderRadius: "2px",
              width: `${confidencePct}%`,
              background: accentColor,
            }}
          />
        </div>
        <span
          style={{
            fontFamily: "'DM Mono', monospace",
            fontSize: "11px",
            color: "var(--text-secondary)",
            fontVariantNumeric: "tabular-nums",
            flexShrink: 0,
          }}
        >
          {confidencePct}%
        </span>
      </div>

      {/* Risk flags — subtle, no hardcoded light colours */}
      <div style={{ display: "flex", gap: "6px", flexShrink: 0, flexWrap: "wrap" }}>
        {theme.risk_flags.slice(0, 3).map((f) => (
          <span
            key={f}
            style={{
              fontSize: "10px",
              fontFamily: "'DM Mono', monospace",
              color: "var(--text-secondary)",
              border: "1px solid var(--border)",
              background: "transparent",
              padding: "1px 7px",
              borderRadius: "3px",
              letterSpacing: "0.03em",
            }}
          >
            {f.replace(/_/g, " ")}
          </span>
        ))}
        {theme.risk_flags.length > 3 && (
          <span style={{ fontSize: "10px", fontFamily: "'DM Mono', monospace", color: "var(--text-muted)" }}>
            +{theme.risk_flags.length - 3}
          </span>
        )}
      </div>
    </div>
  );
}

function BlockedRow({ condition }: { condition: BlockedCondition }) {
  const entries = Object.entries(condition.evidence);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "16px",
        padding: "13px 20px",
        borderBottom: "1px solid var(--border)",
        borderLeft: "3px solid var(--accent-red)",
        background: "var(--surface)",
      }}
    >
      <div style={{ width: "6px", height: "6px", borderRadius: "50%", flexShrink: 0, background: "var(--accent-red)" }} />
      <span
        style={{
          fontFamily: "'DM Sans', system-ui, sans-serif",
          fontSize: "14px",
          fontWeight: 500,
          color: "var(--accent-red)",
          flex: 1,
        }}
      >
        {condition.label}
      </span>
      {entries.length > 0 && (
        <div style={{ display: "flex", gap: "16px" }}>
          {entries.map(([k, v]) => (
            <span
              key={k}
              style={{
                fontFamily: "'DM Mono', monospace",
                fontSize: "12px",
                color: "var(--text-secondary)",
              }}
            >
              {fmtEvidenceKey(k)}{" "}
              <span style={{ color: typeof v === "number" && v >= 0 ? "var(--accent-green)" : "var(--accent-red)", fontWeight: 500 }}>
                {fmtEvidenceVal(v as number | string)}
              </span>
            </span>
          ))}
        </div>
      )}
      <span
        style={{
          fontSize: "10px",
          fontFamily: "'DM Mono', monospace",
          fontWeight: 500,
          letterSpacing: "0.08em",
          color: "var(--accent-red)",
          background: "var(--accent-red-bg)",
          border: "1px solid var(--accent-red-border)",
          padding: "2px 8px",
          borderRadius: "4px",
          flexShrink: 0,
        }}
      >
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
  const [isLight, setIsLight] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("decifer-theme");
    if (stored === "light") {
      setIsLight(true);
      document.documentElement.classList.add("light");
    }
  }, []);

  const toggleTheme = () => {
    const next = !isLight;
    setIsLight(next);
    if (next) {
      document.documentElement.classList.add("light");
      localStorage.setItem("decifer-theme", "light");
    } else {
      document.documentElement.classList.remove("light");
      localStorage.setItem("decifer-theme", "dark");
    }
  };

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
  const story = data ? buildStoryHero(data) : null;

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)" }}>

      {/* ── Header ─────────────────────────────────────────────────── */}
      <header
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0 24px",
          background: "var(--surface)",
          position: "sticky",
          top: 0,
          zIndex: 10,
          boxShadow: "0 1px 0 var(--border)",
        }}
      >
        <div
          style={{
            maxWidth: "1024px",
            margin: "0 auto",
            height: "56px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "16px",
          }}
        >
          {/* Brand */}
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span
              style={{
                fontFamily: "'DM Sans', system-ui, sans-serif",
                fontSize: "16px",
                fontWeight: 700,
                color: "var(--accent-orange)",
                letterSpacing: "-0.01em",
              }}
            >
              DECIFER
            </span>
            <span style={{ color: "var(--border)", fontSize: "18px", fontWeight: 300 }}>/</span>
            <span
              style={{
                fontFamily: "'DM Sans', system-ui, sans-serif",
                fontSize: "14px",
                fontWeight: 500,
                color: "var(--text-secondary)",
                letterSpacing: "0.06em",
                textTransform: "uppercase",
              }}
            >
              Macro Drivers
            </span>
          </div>

          {/* Right controls */}
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            {data && (
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <div
                  className={isLive ? "animate-pulse-dot" : ""}
                  style={{
                    width: "7px",
                    height: "7px",
                    borderRadius: "50%",
                    background: isLive ? "var(--accent-green)" : "var(--text-muted)",
                  }}
                />
                <span
                  style={{
                    fontFamily: "'DM Mono', monospace",
                    fontSize: "11px",
                    color: isLive ? "var(--accent-green)" : "var(--text-muted)",
                    letterSpacing: "0.08em",
                  }}
                >
                  {isLive ? "LIVE" : data.mode.replace(/_/g, " ").toUpperCase()}
                </span>
              </div>
            )}

            {data && lastFetch && (
              <span
                style={{
                  fontFamily: "'DM Mono', monospace",
                  fontSize: "11px",
                  color: "var(--text-muted)",
                }}
              >
                {fmtTs(lastFetch.toISOString())}
              </span>
            )}

            <span
              style={{
                fontFamily: "'DM Mono', monospace",
                fontSize: "11px",
                color: "var(--text-muted)",
              }}
            >
              ↺ {refreshIn}s
            </span>

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              title={isLight ? "Switch to dark mode" : "Switch to light mode"}
              style={{
                fontFamily: "'DM Mono', monospace",
                fontSize: "14px",
                color: "var(--text-secondary)",
                background: "var(--bg)",
                border: "1px solid var(--border)",
                padding: "4px 10px",
                borderRadius: "6px",
                cursor: "pointer",
                transition: "all 0.15s ease",
                lineHeight: 1,
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent-orange)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
              }}
            >
              {isLight ? "☾" : "☀"}
            </button>

            <button
              onClick={() => { setLoading(true); fetchData(); }}
              style={{
                fontFamily: "'DM Mono', monospace",
                fontSize: "11px",
                color: "var(--text-secondary)",
                background: "var(--bg)",
                border: "1px solid var(--border)",
                padding: "5px 12px",
                borderRadius: "6px",
                cursor: "pointer",
                letterSpacing: "0.06em",
                transition: "all 0.15s ease",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent-orange)";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--accent-orange)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--text-secondary)";
              }}
            >
              REFRESH
            </button>
          </div>
        </div>

        {/* Timestamps sub-row */}
        {data && (
          <div
            style={{
              maxWidth: "1024px",
              margin: "0 auto",
              paddingBottom: "10px",
              display: "flex",
              alignItems: "center",
              gap: "16px",
            }}
          >
            {[
              ["DATA", data.data_ts],
              ["FEED", data.ts],
            ].map(([label, ts], i) => (
              <span
                key={i}
                style={{
                  fontFamily: "'DM Mono', monospace",
                  fontSize: "10px",
                  color: "var(--text-muted)",
                }}
              >
                {label}{" "}
                <span style={{ color: "var(--text-secondary)" }}>{fmtTs(ts)}</span>
              </span>
            ))}
          </div>
        )}
      </header>

      {/* ── Stale banner ────────────────────────────────────────────── */}
      {data?.stale && (
        <div
          style={{
            background: "var(--accent-amber-bg)",
            borderBottom: "1px solid var(--accent-amber-border)",
            padding: "10px 24px",
          }}
        >
          <div style={{ maxWidth: "1024px", margin: "0 auto", display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ color: "var(--accent-amber)", fontSize: "13px" }}>⚠</span>
            <span
              style={{
                fontFamily: "'DM Sans', system-ui, sans-serif",
                fontSize: "13px",
                color: "var(--accent-amber)",
              }}
            >
              Data is 30+ minutes old — the pipeline may be sleeping.
              {data.stale_reason && (
                <span style={{ color: "var(--accent-amber)", marginLeft: "6px" }}>
                  {parseStaleReason(data.stale_reason)}
                </span>
              )}
            </span>
          </div>
        </div>
      )}

      {/* ── Loading ─────────────────────────────────────────────────── */}
      {loading && !data && (
        <div style={{ maxWidth: "1024px", margin: "0 auto", padding: "64px 24px", display: "flex", alignItems: "center", gap: "10px" }}>
          <div
            className="animate-pulse-dot"
            style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-orange)" }}
          />
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: "13px", color: "var(--text-muted)" }}>
            Loading driver state…
          </span>
        </div>
      )}

      {/* ── Error ───────────────────────────────────────────────────── */}
      {error && (
        <div style={{ maxWidth: "1024px", margin: "24px auto", padding: "0 24px" }}>
          <div
            style={{
              background: "var(--accent-red-bg)",
              border: "1px solid var(--accent-red-border)",
              borderRadius: "8px",
              padding: "14px 16px",
              fontFamily: "'DM Mono', monospace",
              fontSize: "13px",
              color: "var(--accent-red)",
            }}
          >
            Error: {error}
          </div>
        </div>
      )}

      {/* ── Body ────────────────────────────────────────────────────── */}
      {data && story && (
        <main style={{ maxWidth: "1024px", margin: "0 auto", padding: "32px 24px", display: "flex", flexDirection: "column", gap: "40px" }}>

          {/* ── Story Hero ─────────────────────────────────────────── */}
          <section
            className="story-animate"
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "12px",
              padding: "28px 32px",
              boxShadow: "0 2px 8px rgba(0,0,0,0.05)",
            }}
          >
            {/* Regime badge */}
            <div style={{ marginBottom: "14px" }}>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  fontSize: "11px",
                  fontFamily: "'DM Mono', monospace",
                  fontWeight: 600,
                  letterSpacing: "0.1em",
                  color: story.regimeText,
                  background: story.regimeBg,
                  border: `1px solid ${story.regimeBorder}`,
                  padding: "4px 12px",
                  borderRadius: "20px",
                }}
              >
                <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: story.regimeText, display: "inline-block" }} />
                {story.regime}
              </span>
            </div>

            {/* Headline */}
            <p
              style={{
                fontFamily: "'Instrument Serif', Georgia, serif",
                fontSize: "22px",
                lineHeight: 1.4,
                color: "var(--text-primary)",
                margin: "0 0 10px 0",
                fontWeight: 400,
              }}
            >
              {story.headline}
            </p>

            {/* Subline */}
            {story.subline && (
              <p
                style={{
                  fontFamily: "'DM Sans', system-ui, sans-serif",
                  fontSize: "15px",
                  lineHeight: 1.55,
                  color: "var(--text-secondary)",
                  margin: "0 0 0 0",
                }}
              >
                {story.subline}
              </p>
            )}

            {/* Tension strip */}
            {story.tension && (
              <div
                style={{
                  marginTop: "16px",
                  paddingTop: "16px",
                  borderTop: "1px solid var(--border-light)",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "10px",
                }}
              >
                <span style={{ fontSize: "14px", flexShrink: 0, marginTop: "1px" }}>⚡</span>
                <p
                  style={{
                    fontFamily: "'DM Sans', system-ui, sans-serif",
                    fontSize: "14px",
                    lineHeight: 1.5,
                    color: "var(--accent-amber)",
                    margin: 0,
                  }}
                >
                  {story.tension}
                </p>
              </div>
            )}

            {/* Sensor count footnote */}
            <p
              style={{
                fontFamily: "'DM Mono', monospace",
                fontSize: "11px",
                color: "var(--text-muted)",
                margin: "16px 0 0 0",
              }}
            >
              {data.active_drivers.length} active drivers · {data.sensor_count} sensors monitored · {data.activated_theme_count} themes activated
            </p>
          </section>

          {/* ── Futures Advisory ───────────────────────────────────── */}
          {data.futures && (
            <section>
              <SectionLabel>Futures Advisory</SectionLabel>
              <div
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "10px",
                  padding: "20px 24px",
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  gap: "24px",
                  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                }}
              >
                {[
                  { label: "S&P 500 (ES) — 5 day", val: data.futures.es_5d_ret },
                  { label: "Nasdaq 100 (NQ) — 5 day", val: data.futures.nq_5d_ret },
                ].map(({ label, val }, i) => (
                  <div key={i} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <span
                      style={{
                        fontFamily: "'DM Sans', system-ui, sans-serif",
                        fontSize: "12px",
                        color: "var(--text-muted)",
                      }}
                    >
                      {label}
                    </span>
                    <span
                      style={{
                        fontFamily: "'DM Mono', monospace",
                        fontSize: "24px",
                        fontWeight: 500,
                        fontVariantNumeric: "tabular-nums",
                        color: val >= 0 ? "var(--accent-green)" : "var(--accent-red)",
                        letterSpacing: "-0.02em",
                      }}
                    >
                      {fmtRet(val)}
                    </span>
                  </div>
                ))}

                {data.futures.advisory_drivers.length > 0 && (
                  <div style={{ marginLeft: "auto", display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {data.futures.advisory_drivers.map((d) => (
                      <span
                        key={d}
                        style={{
                          fontSize: "11px",
                          fontFamily: "'DM Mono', monospace",
                          fontWeight: 500,
                          letterSpacing: "0.07em",
                          color: "var(--accent-green)",
                          background: "var(--accent-green-bg)",
                          border: "1px solid var(--accent-green-border)",
                          padding: "4px 10px",
                          borderRadius: "5px",
                        }}
                      >
                        {d.replace(/_/g, " ").toUpperCase()}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* ── Active Drivers ─────────────────────────────────────── */}
          <section>
            <SectionLabel count={data.active_drivers.length}>
              Active Drivers
            </SectionLabel>
            {data.active_drivers.length === 0 ? (
              <p style={{ fontFamily: "'DM Sans', system-ui, sans-serif", fontSize: "14px", color: "var(--text-muted)" }}>
                No active drivers at this time.
              </p>
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                  gap: "12px",
                }}
              >
                {data.active_drivers.map((d, i) => (
                  <DriverCard key={d.id} driver={d} idx={i} />
                ))}
              </div>
            )}
          </section>

          {/* ── Activated Themes ───────────────────────────────────── */}
          <section>
            <SectionLabel count={data.activated_theme_count}>
              Activated Themes
            </SectionLabel>
            {data.activated_themes.length === 0 ? (
              <p style={{ fontFamily: "'DM Sans', system-ui, sans-serif", fontSize: "14px", color: "var(--text-muted)" }}>
                No themes activated.
              </p>
            ) : (
              <div
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "10px",
                  overflow: "hidden",
                  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                }}
              >
                {data.activated_themes.map((t, i) => (
                  <ThemeRow key={t.theme_id} theme={t} idx={i} />
                ))}
              </div>
            )}
          </section>

          {/* ── Blocked Conditions ─────────────────────────────────── */}
          {data.blocked_conditions.length > 0 && (
            <section>
              <SectionLabel count={data.blocked_conditions.length}>
                Blocked Conditions
              </SectionLabel>
              <div
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--accent-red-border)",
                  borderRadius: "10px",
                  overflow: "hidden",
                }}
              >
                {data.blocked_conditions.map((c) => (
                  <BlockedRow key={c.id} condition={c} />
                ))}
              </div>
            </section>
          )}

          {/* ── Footer ─────────────────────────────────────────────── */}
          <footer
            style={{
              borderTop: "1px solid var(--border)",
              paddingTop: "24px",
              display: "flex",
              flexDirection: "column",
              gap: "8px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
              {[
                ["Sensors", String(data.sensor_count)],
                ["API", `v${data.api_version}`],
                ["Auto-refresh", "5 min"],
              ].map(([label, val]) => (
                <span
                  key={label}
                  style={{
                    fontFamily: "'DM Mono', monospace",
                    fontSize: "11px",
                    color: "var(--text-muted)",
                  }}
                >
                  {label}:{" "}
                  <span style={{ color: "var(--text-secondary)" }}>{val}</span>
                </span>
              ))}
            </div>
            {data.disclaimer && (
              <p
                style={{
                  fontFamily: "'DM Sans', system-ui, sans-serif",
                  fontSize: "12px",
                  color: "var(--text-muted)",
                  maxWidth: "640px",
                  lineHeight: 1.6,
                  margin: 0,
                }}
              >
                {data.disclaimer}
              </p>
            )}
          </footer>
        </main>
      )}
    </div>
  );
}
