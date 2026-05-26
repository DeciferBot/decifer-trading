"use client";
// Forces tab — M13B.
// Active and dormant market forces. Connection tree per active force.
// No broker/execution/order language. No buy/sell/hold.

import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  ArrowRight,
  Zap,
  ChevronRight,
  Search,
} from "lucide-react";
import type { MarketNowPayload } from "@/lib/customerApi";
import type {
  CustomerMarketForce,
  CustomerConnectionNode,
} from "@/lib/customerBriefingModel";

// ── Evidence basis chip ────────────────────────────────────────────────────────

function EvidenceChip({ basis }: { basis: string }) {
  let bg = "rgba(249,115,22,0.09)";
  let color = "#fb923c";
  if (basis === "Fresh evidence")  { bg = "rgba(16,185,129,0.1)";   color = "#34d399"; }
  if (basis === "Futures signal")  { bg = "rgba(99,102,241,0.1)";   color = "#818cf8"; }
  if (basis === "Quiet")           { bg = "rgba(255,255,255,0.05)"; color = "#475569"; }
  return (
    <span
      className="text-[9px] font-semibold px-2 py-0.5 rounded-full"
      style={{ background: bg, color }}
    >
      {basis}
    </span>
  );
}

// ── Connection tree for one active force ───────────────────────────────────────

function ConnectionTree({
  node,
  onThemeSelect,
  onGoToNames,
}: {
  node: CustomerConnectionNode;
  onThemeSelect: (id: string) => void;
  onGoToNames?: () => void;
}) {
  if (node.themes.length === 0) return null;
  return (
    <div
      className="rounded-xl p-3 space-y-1.5"
      style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
    >
      <p className="text-[9px] font-bold uppercase tracking-wider text-slate-600 mb-2">
        Connection path
      </p>
      {/* Force node */}
      <div className="flex items-center gap-2">
        <Zap size={10} style={{ color: "#f97316", flexShrink: 0 }} />
        <span className="text-[11px] font-semibold text-slate-300">{node.force_label}</span>
      </div>
      {/* Theme nodes */}
      {node.themes.slice(0, 4).map((theme, i) => (
        <div key={i} className="flex items-center gap-2 pl-4">
          <ChevronRight size={10} className="text-slate-700 shrink-0" />
          <button
            onClick={() => onThemeSelect(theme.theme_id)}
            className="text-[11px] font-semibold transition-all active:scale-95 text-left"
            style={{ color: theme.driver_active ? "#34d399" : "#94a3b8" }}
          >
            {theme.theme_label}
            {theme.driver_active && (
              <span className="text-[8px] ml-1.5 text-emerald-600">● Active</span>
            )}
          </button>
        </div>
      ))}
      {/* See names CTA */}
      {onGoToNames && (
        <div className="pl-4 pt-0.5">
          <button
            onClick={onGoToNames}
            className="flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
            style={{ color: "#64748b" }}
          >
            <Search size={9} />
            See connected names
          </button>
        </div>
      )}
    </div>
  );
}

// ── Active force card ─────────────────────────────────────────────────────────

function ActiveForceCard({
  force,
  treeNode,
  onThemeSelect,
  onAskAbout,
  onGoToNames,
}: {
  force: CustomerMarketForce;
  treeNode?: CustomerConnectionNode;
  onThemeSelect: (id: string) => void;
  onAskAbout?: (ctx: string) => void;
  onGoToNames?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{
        background: "#162033",
        border: "1px solid rgba(16,185,129,0.18)",
        boxShadow: "0 2px 16px rgba(0,0,0,0.25)",
      }}
    >
      {/* Header */}
      <div className="px-4 pt-4 pb-3">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: "#10b981", boxShadow: "0 0 6px rgba(16,185,129,0.5)" }}
            />
            <h3 className="text-[14px] font-bold text-slate-100 leading-snug">
              {force.label}
            </h3>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span
              className="text-[9px] font-bold px-2 py-0.5 rounded-full"
              style={{ background: "rgba(16,185,129,0.15)", color: "#34d399" }}
            >
              Active
            </span>
            <EvidenceChip basis={force.evidence_basis} />
          </div>
        </div>

        {/* Why it matters */}
        <p className="text-[12px] text-slate-300 leading-relaxed mb-2">
          {force.why_it_matters}
        </p>

        {/* Market impact */}
        <p className="text-[11px] text-slate-400 leading-relaxed">
          {force.market_impact}
        </p>
      </div>

      {/* Connected themes chips */}
      {force.connected_theme_labels.length > 0 && (
        <div className="px-4 pb-3 flex flex-wrap gap-1.5">
          {force.connected_theme_labels.map((label, i) => (
            <button
              key={i}
              onClick={() => {
                const id = force.connected_theme_ids[i];
                if (id) onThemeSelect(id);
              }}
              className="text-[10px] font-semibold px-2.5 py-1 rounded-full transition-all active:scale-95"
              style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {/* Connection tree */}
      {treeNode && treeNode.themes.length > 0 && (
        <div className="px-4 pb-3">
          <ConnectionTree
            node={treeNode}
            onThemeSelect={onThemeSelect}
            onGoToNames={onGoToNames}
          />
        </div>
      )}

      {/* Expandable risk section + Ask CTA */}
      <div
        className="px-4 pb-4 pt-2"
        style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}
      >
        <button
          onClick={() => setExpanded(o => !o)}
          className="w-full flex items-center justify-between gap-2 text-left mb-2"
        >
          <span className="text-[10px] font-semibold text-slate-500">
            Risk to monitor
          </span>
          {expanded ? (
            <ChevronUp size={12} className="text-slate-600" />
          ) : (
            <ChevronDown size={12} className="text-slate-600" />
          )}
        </button>
        {expanded && (
          <p className="text-[11px] text-amber-400 leading-relaxed mb-3">
            {force.risk_to_monitor}
          </p>
        )}
        {onAskAbout && (
          <button
            onClick={() => onAskAbout(`Why is ${force.label.toLowerCase()} moving markets today?`)}
            className="flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
            style={{ color: "#94a3b8" }}
          >
            Ask Decifer about this force
            <ArrowRight size={9} />
          </button>
        )}
      </div>
    </div>
  );
}

// ── Dormant force row ─────────────────────────────────────────────────────────

function DormantForceRow({ force }: { force: CustomerMarketForce }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: "#141b26", border: "1px solid rgba(255,255,255,0.06)" }}
    >
      <button
        onClick={() => setExpanded(o => !o)}
        className="w-full px-4 py-3 flex items-center justify-between gap-2 text-left"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: "#334155" }} />
          <span className="text-[12px] font-semibold text-slate-400 truncate">{force.label}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[9px] text-slate-600">Quiet</span>
          {expanded ? (
            <ChevronUp size={12} className="text-slate-600" />
          ) : (
            <ChevronDown size={12} className="text-slate-600" />
          )}
        </div>
      </button>
      {expanded && (
        <div
          className="px-4 pb-3 pt-2 space-y-2"
          style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}
        >
          <p className="text-[11px] text-slate-500 leading-relaxed">
            {force.why_it_matters}
          </p>
          <p className="text-[10px] text-slate-600 leading-relaxed">
            <span className="font-semibold text-slate-500">If activated: </span>
            {force.market_impact}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  activeForces: CustomerMarketForce[];
  dormantForces: CustomerMarketForce[];
  connectionTree: CustomerConnectionNode[];
  onThemeSelect: (themeId: string) => void;
  onAskAbout?: (context: string) => void;
  onGoToNames?: () => void;
}

export default function ForcesTab({
  data,
  activeForces,
  dormantForces,
  connectionTree,
  onThemeSelect,
  onAskAbout,
  onGoToNames,
}: Props) {
  const [dormantOpen, setDormantOpen] = useState(false);

  const treeByForce = Object.fromEntries(
    connectionTree.map(n => [n.force_id, n]),
  );

  const hasNoData = activeForces.length === 0 && dormantForces.length === 0;

  return (
    <div className="px-4 pt-3 pb-8 space-y-5">

      {/* ── Intro ──────────────────────────────────────────────────────────── */}
      <div>
        <p className="text-[10px] font-bold uppercase tracking-[0.15em] mb-1" style={{ color: "#f97316" }}>
          Market Forces
        </p>
        <p className="text-[12px] text-slate-400 leading-relaxed">
          {activeForces.length > 0
            ? `${activeForces.length} active force${activeForces.length !== 1 ? "s" : ""} are moving attention today. Dormant forces are below.`
            : "No forces are confirmed active right now. Structural themes remain available below."}
        </p>
      </div>

      {/* ── No data fallback ──────────────────────────────────────────────── */}
      {hasNoData && (
        <div
          className="rounded-2xl px-6 py-8 text-center"
          style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
        >
          <p className="text-sm text-slate-400">No force data available right now.</p>
          <p className="text-xs text-slate-500 mt-1.5 leading-relaxed max-w-xs mx-auto">
            Market forces appear when Decifer detects active drivers. Check the Theme Map for structural context.
          </p>
        </div>
      )}

      {/* ── Active forces ─────────────────────────────────────────────────── */}
      {activeForces.length > 0 && (
        <section className="space-y-4">
          {activeForces.map(force => (
            <ActiveForceCard
              key={force.id}
              force={force}
              treeNode={treeByForce[force.id]}
              onThemeSelect={onThemeSelect}
              onAskAbout={onAskAbout}
              onGoToNames={onGoToNames}
            />
          ))}
        </section>
      )}

      {/* ── Dormant forces (collapsed section) ────────────────────────────── */}
      {dormantForces.length > 0 && (
        <section>
          <button
            onClick={() => setDormantOpen(o => !o)}
            className="w-full flex items-center justify-between gap-2 rounded-xl px-4 py-3 transition-all active:scale-[0.98]"
            style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}
          >
            <div className="text-left">
              <p className="text-[12px] font-semibold text-slate-400">
                {dormantForces.length} quiet force{dormantForces.length !== 1 ? "s" : ""}
              </p>
              <p className="text-[10px] text-slate-600 mt-0.5">
                Not active today — tap to expand
              </p>
            </div>
            {dormantOpen ? (
              <ChevronUp size={14} className="text-slate-500" />
            ) : (
              <ChevronDown size={14} className="text-slate-500" />
            )}
          </button>

          {dormantOpen && (
            <div className="mt-2 space-y-1.5">
              {dormantForces.map(force => (
                <DormantForceRow key={force.id} force={force} />
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── Ask Decifer about forces ──────────────────────────────────────── */}
      {onAskAbout && activeForces.length > 0 && (
        <button
          onClick={() => onAskAbout("What are the key forces moving markets today and where should I focus?")}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-[11px] font-semibold transition-all active:scale-[0.98]"
          style={{
            background: "rgba(249,115,22,0.06)",
            border: "1px solid rgba(249,115,22,0.15)",
            color: "#fb923c",
          }}
        >
          <Zap size={11} />
          Ask Decifer about today's forces
        </button>
      )}

      {/* ── Disclaimer ─────────────────────────────────────────────────────── */}
      <p className="text-[10px] text-slate-700 text-center">
        Market intelligence only. Not financial advice. No trade execution.
      </p>
    </div>
  );
}
