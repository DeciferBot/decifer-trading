"use client";
import { useEffect, useState } from "react";

export function Header({ lastUpdated }: { lastUpdated: string | null }) {
  const [now, setNow] = useState("");
  useEffect(() => {
    const fmt = () =>
      setNow(new Date().toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    fmt();
    const t = setInterval(fmt, 1000);
    return () => clearInterval(t);
  }, []);

  const age = lastUpdated
    ? Math.round((Date.now() - new Date(lastUpdated).getTime()) / 1000)
    : null;
  const ageLabel = age === null
    ? "—"
    : age < 60
      ? `${age}s ago`
      : age < 3600
        ? `${Math.round(age / 60)}m ago`
        : `Last scan: ${new Date(lastUpdated!).toLocaleString("en-US", {
            timeZone: "America/New_York",
            weekday: "short", month: "short", day: "numeric",
            hour: "2-digit", minute: "2-digit",
          })} ET`;

  return (
    <header style={{
      borderBottom: "1px solid var(--border)",
      padding: "14px 20px",
      display: "flex", alignItems: "center", justifyContent: "space-between",
      position: "sticky", top: 0, background: "var(--bg)", zIndex: 20,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ color: "var(--orange)", fontWeight: 700, fontSize: 13, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Decifer <span style={{ color: "var(--muted)", fontWeight: 400 }}>/ Options Flow</span>
        </span>
        <span style={{
          background: "#1a1a1a", border: "1px solid var(--border)",
          borderRadius: 4, padding: "2px 8px",
          fontSize: 10, color: "var(--muted)", fontFamily: "var(--mono)",
        }}>
          1,000 symbol universe · OPRA
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 11, color: "var(--muted)" }}>
        <span>Updated {ageLabel}</span>
        <span style={{ color: "var(--border2)" }}>|</span>
        <span style={{ fontFamily: "var(--mono)" }}>{now} ET</span>
      </div>
    </header>
  );
}
