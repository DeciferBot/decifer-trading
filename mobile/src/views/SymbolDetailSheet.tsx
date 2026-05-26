"use client";
// Symbol deep-dive bottom sheet — shown when a symbol card is tapped in Universe.
// Displays the full transmission chain (driver → theme → subtheme → bucket → symbol),
// evidence basis, route_hint, and qualitative context.
// No buy/sell/hold/target/stop/order/position-sizing language.

import { X, ChevronRight, AlertTriangle, Info } from "lucide-react";
import type { TtgSymbolCard } from "@/lib/customerApi";

// ── Exposure type label ────────────────────────────────────────────────────────

const EXPOSURE_MAP: Record<string, { label: string; color: string; description: string }> = {
  direct_beneficiary: {
    label: "Direct Beneficiary",
    color: "#34d399",
    description: "This company directly benefits from the structural theme.",
  },
  supply_chain_beneficiary: {
    label: "Supply Chain",
    color: "#2dd4bf",
    description: "This company benefits through supply chain linkage to the theme.",
  },
  second_order_beneficiary: {
    label: "Indirect",
    color: "#60a5fa",
    description: "This company benefits indirectly through economic transmission.",
  },
  etf_basket: {
    label: "ETF Basket",
    color: "#818cf8",
    description: "This instrument provides broad exposure to the theme via an ETF structure.",
  },
  pressure_or_negative: {
    label: "Pressure Watch",
    color: "#f87171",
    description: "This company may face headwinds from the theme — monitor for deterioration.",
  },
};

function ExposureBadge({ type }: { type: string }) {
  const info = EXPOSURE_MAP[type] ?? {
    label: type.replace(/_/g, " "),
    color: "#94a3b8",
    description: "Structural theme exposure.",
  };
  return (
    <div
      className="rounded-xl px-3 py-2"
      style={{ background: `${info.color}15`, border: `1px solid ${info.color}30` }}
    >
      <p className="text-[10px] font-bold uppercase tracking-wide" style={{ color: info.color }}>
        {info.label}
      </p>
      <p className="text-[10px] text-slate-400 mt-0.5">{info.description}</p>
    </div>
  );
}

// ── Route hint description ─────────────────────────────────────────────────────

const ROUTE_DESCRIPTIONS: Record<string, { color: string; description: string }> = {
  "In Focus": {
    color: "#fb923c",
    description: "This name has strong structural evidence and is a primary reference point for this theme.",
  },
  "In focus": {
    color: "#fb923c",
    description: "This name has strong structural evidence and is a primary reference point for this theme.",
  },
  "On the Radar": {
    color: "#60a5fa",
    description: "This name has emerging evidence — the structural case is building but not yet fully established.",
  },
  "On the radar": {
    color: "#60a5fa",
    description: "This name has emerging evidence — the structural case is building but not yet fully established.",
  },
  "ETF Route": {
    color: "#818cf8",
    description: "This ETF provides thematic exposure via a basket structure rather than direct company operations.",
  },
  "ETF route": {
    color: "#818cf8",
    description: "This ETF provides thematic exposure via a basket structure rather than direct company operations.",
  },
  "Monitor Only": {
    color: "#fbbf24",
    description: "Evidence is present but below the threshold for primary reference — conditions may change.",
  },
  "Monitor only": {
    color: "#fbbf24",
    description: "Evidence is present but below the threshold for primary reference — conditions may change.",
  },
};

function getRouteInfo(hint: string) {
  return (
    ROUTE_DESCRIPTIONS[hint] ?? {
      color: "#94a3b8",
      description: "Structural intelligence reference.",
    }
  );
}

// ── Reason path display ────────────────────────────────────────────────────────

function ReasonPath({ path }: { path: string[] }) {
  if (path.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-500">
        Transmission Chain
      </p>
      <div className="flex flex-wrap items-center gap-1">
        {path.map((step, i) => (
          <div key={i} className="flex items-center gap-1">
            <span
              className="text-[11px] font-medium px-2 py-0.5 rounded-full"
              style={{ background: "rgba(255,255,255,0.06)", color: "#cbd5e1" }}
            >
              {step}
            </span>
            {i < path.length - 1 && (
              <ChevronRight size={10} className="text-slate-600 shrink-0" />
            )}
          </div>
        ))}
      </div>
      <p className="text-[10px] text-slate-600">
        How this name connects to the structural theme
      </p>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  card: TtgSymbolCard;
  onClose: () => void;
}

export default function SymbolDetailSheet({ card, onClose }: Props) {
  const isPressure = card.exposure_type === "pressure_or_negative";
  const routeInfo = getRouteInfo(card.route_hint);

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        className="fixed bottom-0 left-0 right-0 z-50 flex flex-col rounded-t-3xl overflow-hidden"
        style={{
          background: "#0d1829",
          border: "1px solid rgba(255,255,255,0.08)",
          maxHeight: "85vh",
        }}
      >
        {/* Handle */}
        <div className="flex justify-center pt-3 pb-1 shrink-0">
          <div className="w-10 h-1 rounded-full" style={{ background: "rgba(255,255,255,0.15)" }} />
        </div>

        {/* Header */}
        <div
          className="flex items-start justify-between px-5 pt-3 pb-4 shrink-0"
          style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2.5 flex-wrap">
              <span className="text-2xl font-black text-slate-100">{card.symbol}</span>
              {card.label && (
                <span className="text-sm text-slate-500 truncate max-w-[200px]">{card.label}</span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span
                className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                style={{ background: `${routeInfo.color}20`, color: routeInfo.color }}
              >
                {card.route_hint}
              </span>
              {card.driver_active && (
                <span className="text-[10px] font-bold" style={{ color: "#34d399" }}>
                  ● Driver Active
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="ml-3 p-2 rounded-full shrink-0"
            style={{ background: "rgba(255,255,255,0.06)" }}
            aria-label="Close"
          >
            <X size={16} className="text-slate-400" />
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">

          {/* Theme context */}
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-500 mb-1">
              Structural Theme
            </p>
            <p className="text-sm font-semibold text-slate-200">{card.theme_label}</p>
            {card.bucket_label && (
              <p className="text-[11px] text-slate-500 mt-0.5">{card.bucket_label}</p>
            )}
          </div>

          {/* Exposure type */}
          <ExposureBadge type={card.exposure_type} />

          {/* Reason to care */}
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-500 mb-2">
              Why This Name Is Connected
            </p>
            <p className="text-sm text-slate-300 leading-relaxed">{card.reason_to_care}</p>
          </div>

          {/* Transmission chain */}
          {card.reason_path.length > 0 && (
            <ReasonPath path={card.reason_path} />
          )}

          {/* Route context */}
          <div
            className="rounded-xl px-4 py-3 flex gap-3"
            style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
          >
            <Info size={14} className="text-slate-500 shrink-0 mt-0.5" />
            <p className="text-[11px] text-slate-400 leading-relaxed">{routeInfo.description}</p>
          </div>

          {/* Evidence basis */}
          {card.evidence_basis_label && (
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-500 mb-1">
                Evidence Basis
              </p>
              <p className="text-[11px] text-slate-400">{card.evidence_basis_label}</p>
            </div>
          )}

          {/* Pressure warning */}
          {isPressure && (
            <div
              className="rounded-xl px-4 py-3 flex gap-3"
              style={{ background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.15)" }}
            >
              <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
              <p className="text-[11px] text-red-400 leading-relaxed">
                This name is flagged as potentially facing headwinds from the structural theme —
                monitor for deteriorating conditions.
              </p>
            </div>
          )}

          {/* Symbol risk note */}
          {card.risk_note && (
            <div
              className="rounded-xl px-4 py-3"
              style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.15)" }}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-1.5" style={{ color: "#fbbf24" }}>
                Risk Note
              </p>
              <p className="text-[11px] leading-relaxed" style={{ color: "#fbbf24" }}>
                {card.risk_note}
              </p>
            </div>
          )}

          {/* Theme risk note */}
          {card.theme_risk_note && (
            <div
              className="rounded-xl px-4 py-3"
              style={{ background: "rgba(245,158,11,0.04)", border: "1px solid rgba(245,158,11,0.10)" }}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-1.5 text-amber-600">
                Theme Risk
              </p>
              <p className="text-[11px] text-amber-600 leading-relaxed">{card.theme_risk_note}</p>
            </div>
          )}

          {/* Disclaimer */}
          <div className="pt-2 pb-1">
            <p className="text-[10px] text-slate-600 text-center leading-relaxed">
              Market intelligence only. Not financial advice. Not a recommendation. No trade execution.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
