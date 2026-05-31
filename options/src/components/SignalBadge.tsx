import type { Side, SignalType } from "@/lib/types";

const SIGNAL_STYLES: Record<SignalType, { label: string; bg: string; color: string }> = {
  SWEEP: { label: "SWEEP", bg: "rgba(232,125,46,0.15)", color: "#e87d2e" },
  CLUSTER: { label: "CLUSTER", bg: "rgba(139,92,246,0.15)", color: "#8b5cf6" },
  CROSS_EXPIRY: { label: "CROSS-EXPIRY", bg: "rgba(59,130,246,0.15)", color: "#3b82f6" },
};

const SIDE_STYLES: Record<Side, { color: string }> = {
  CALL: { color: "#2ecc71" },
  PUT: { color: "#e74c3c" },
  MIXED: { color: "#888" },
};

export function SignalBadge({ type }: { type: SignalType }) {
  const s = SIGNAL_STYLES[type];
  return (
    <span style={{
      background: s.bg, color: s.color,
      fontSize: 10, fontWeight: 700, letterSpacing: "0.08em",
      padding: "2px 7px", borderRadius: 4,
      border: `1px solid ${s.color}33`,
    }}>
      {s.label}
    </span>
  );
}

export function SideBadge({ side }: { side: Side }) {
  const s = SIDE_STYLES[side];
  return (
    <span style={{ color: s.color, fontWeight: 700, fontSize: 12, fontFamily: "var(--mono)" }}>
      {side}
    </span>
  );
}

export function ScoreBar({ score, side }: { score: number; side?: Side }) {
  const color = side === "CALL" ? "#2ecc71" : side === "PUT" ? "#e74c3c" : "#e87d2e";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 48, height: 3, background: "#222", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${score}%`, height: "100%", background: color, borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 11, color, fontWeight: 600, fontFamily: "var(--mono)" }}>{score}</span>
    </div>
  );
}

import labelRegistry from "@/data/label_registry.json";
const _driverLabels = labelRegistry.drivers as Record<string, string>;

export function DriverTag({ tag }: { tag: string }) {
  const label = _driverLabels[tag] ?? tag.replace(/_/g, " ");
  return (
    <span style={{
      background: "#181818", border: "1px solid #2a2a2a",
      borderRadius: 12, padding: "2px 8px",
      fontSize: 10, color: "#888", whiteSpace: "nowrap",
    }}>
      {label}
    </span>
  );
}
