"use client";
// Name research detail sheet — bottom sheet opened when a story-group card is tapped.
// Fetches /api/name-fundamentals and renders company, financials, analyst context.
// Customer-safe language only. No execution, broker, order, or P&L language.

import { useState, useEffect } from "react";
import { X, ArrowRight } from "lucide-react";
import type { NamePriceEntry } from "@/lib/namePriceUtils";
import type { ResearchNameCard, NameFundamentalsResponse } from "@/lib/nameResearchModel";
import {
  buildCompanyLine,
  buildFundamentalsLine,
  buildAnalystLine,
  buildDetailQuestions,
  mergeFreshPrice,
  buildPriceFreshnessLabel,
} from "@/lib/nameResearchModel";

// ── Loading skeleton ───────────────────────────────────────────────────────────

function SkeletonLine({ width = "w-full" }: { width?: string }) {
  return (
    <div
      className={`h-3 rounded-full animate-pulse ${width}`}
      style={{ background: "rgba(255,255,255,0.06)" }}
    />
  );
}

// ── Section label ─────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[9px] font-bold uppercase tracking-[0.15em] text-slate-500 mb-1.5">
      {children}
    </p>
  );
}

// ── Watch type badge ───────────────────────────────────────────────────────────

function WatchBadge({ watchType }: { watchType: ResearchNameCard["watchType"] }) {
  const styles: Record<string, { bg: string; color: string }> = {
    "Catalyst watch":   { bg: "rgba(16,185,129,0.12)",  color: "#34d399" },
    "Structural watch": { bg: "rgba(99,102,241,0.12)",  color: "#818cf8" },
    "Market attention": { bg: "rgba(148,163,184,0.10)", color: "#94a3b8" },
  };
  const s = styles[watchType] ?? styles["Market attention"];
  return (
    <span
      className="text-[9px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: s.bg, color: s.color }}
    >
      {watchType}
    </span>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  card: ResearchNameCard;
  onClose: () => void;
  onAskAbout?: (context: string) => void;
}

export default function NameResearchSheet({ card, onClose, onAskAbout }: Props) {
  const [fundData, setFundData] = useState<NameFundamentalsResponse | null>(null);
  const [fundLoading, setFundLoading] = useState(true);
  const [freshPrice, setFreshPrice] = useState<NamePriceEntry | null>(null);
  const [priceTs, setPriceTs] = useState<string | null>(null);

  // Fetch fundamentals — keyed by symbol in parent so never mid-mount change
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const res = await fetch(`/api/name-fundamentals?symbol=${encodeURIComponent(card.symbol)}`);
        if (!cancelled) setFundData(res.ok ? await res.json() : null);
      } catch { /* graceful */ } finally {
        if (!cancelled) setFundLoading(false);
      }
    };
    run();
    return () => { cancelled = true; };
  }, [card.symbol]);

  // Refresh latest price on sheet open — fails gracefully, card.priceAction used as fallback
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const res = await fetch(`/api/name-prices?symbols=${encodeURIComponent(card.symbol)}`);
        if (!cancelled && res.ok) {
          const json: { prices: NamePriceEntry[]; ts: string } = await res.json();
          const entry = json.prices.find(p => p.symbol === card.symbol) ?? null;
          setFreshPrice(entry);
          setPriceTs(json.ts);
        }
      } catch { /* graceful — freshPrice stays null, card.priceAction used */ }
    };
    run();
    return () => { cancelled = true; };
  }, [card.symbol]);

  const companyLine = buildCompanyLine(card.symbol, fundData?.profile, card.storyGroup);
  const fundamentalsLine = buildFundamentalsLine(fundData?.fundamentals);
  const analystLine = buildAnalystLine(fundData?.analyst);
  const questions = buildDetailQuestions(
    card.symbol,
    card.storyGroup,
    card.companyName !== card.symbol ? card.companyName : undefined,
  );

  const priceAction = mergeFreshPrice(freshPrice, card.priceAction);
  const freshnessLabel = buildPriceFreshnessLabel(priceTs);
  const { tone, displayText, price } = priceAction;
  const priceColor =
    tone === "positive" ? "#34d399" : tone === "negative" ? "#f87171" : "#94a3b8";

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)" }}
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        className="fixed bottom-0 left-0 right-0 z-50 flex flex-col rounded-t-3xl overflow-hidden"
        style={{
          background: "#0d1829",
          border: "1px solid rgba(255,255,255,0.08)",
          maxHeight: "88vh",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
      >
        {/* Handle */}
        <div className="flex justify-center pt-3 pb-1 shrink-0">
          <div className="w-10 h-1 rounded-full" style={{ background: "rgba(255,255,255,0.15)" }} />
        </div>

        {/* Header */}
        <div
          className="flex items-start justify-between px-5 pt-2 pb-4 shrink-0"
          style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap mb-1.5">
              <span className="text-2xl font-black text-slate-100">{card.symbol}</span>
              {card.companyName && card.companyName !== card.symbol && (
                <span className="text-sm text-slate-500 truncate max-w-[200px]">
                  {card.companyName}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <WatchBadge watchType={card.watchType} />
              {tone !== "unknown" && (
                <span className="text-[10px] font-semibold" style={{ color: priceColor }}>
                  {displayText}
                  {price != null && (
                    <span className="text-slate-500 font-normal ml-1">
                      ${price.toFixed(2)}
                    </span>
                  )}
                  {freshnessLabel && (
                    <span className="text-slate-600 font-normal ml-1.5 text-[9px]">
                      {freshnessLabel}
                    </span>
                  )}
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

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">

          {/* Story context chip */}
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="text-[9px] font-semibold px-2 py-0.5 rounded-full"
              style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
            >
              {card.storyGroup}
            </span>
            <span className="text-[9px] text-slate-600">{card.confidenceLanguage}</span>
            {card.driverActive && (
              <span className="text-[9px] font-bold" style={{ color: "#34d399" }}>
                ● In play
              </span>
            )}
          </div>

          {/* Why connected */}
          <section>
            <SectionLabel>Why it matters now</SectionLabel>
            <p className="text-[12px] text-slate-300 leading-relaxed">{card.reasonToCare}</p>
          </section>

          {/* Company context */}
          <section>
            <SectionLabel>Company context</SectionLabel>
            {fundLoading ? (
              <div className="space-y-2">
                <SkeletonLine />
                <SkeletonLine width="w-3/4" />
              </div>
            ) : (
              <p className="text-[12px] text-slate-300 leading-relaxed">{companyLine}</p>
            )}
          </section>

          {/* Financials */}
          <section>
            <SectionLabel>Financial context</SectionLabel>
            {fundLoading ? (
              <div className="space-y-2">
                <SkeletonLine width="w-5/6" />
                <SkeletonLine width="w-2/3" />
              </div>
            ) : (
              <p className="text-[12px] text-slate-400 leading-relaxed">{fundamentalsLine}</p>
            )}
          </section>

          {/* Analyst context — shown only when data loaded */}
          {!fundLoading && (fundData?.analyst || fundData?.available === false) && (
            <section>
              <SectionLabel>Market view</SectionLabel>
              <p className="text-[12px] text-slate-400 leading-relaxed">{analystLine}</p>
            </section>
          )}

          {/* Risk note */}
          {card.riskNote && (
            <div
              className="rounded-xl px-4 py-3"
              style={{
                background: "rgba(245,158,11,0.06)",
                border: "1px solid rgba(245,158,11,0.15)",
              }}
            >
              <SectionLabel>Risk to watch</SectionLabel>
              <p className="text-[11px] leading-relaxed" style={{ color: "#fbbf24" }}>
                {card.riskNote}
              </p>
            </div>
          )}

          {/* Suggested questions */}
          <section>
            <SectionLabel>Ask Decifer</SectionLabel>
            <div className="space-y-1.5">
              {questions.map((q, i) => (
                <button
                  key={i}
                  onClick={() => onAskAbout?.(q)}
                  className="w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl text-left transition-all active:scale-[0.98]"
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.06)",
                  }}
                >
                  <span className="text-[11px] text-slate-300 leading-snug">{q}</span>
                  <ArrowRight size={10} className="text-slate-600 shrink-0" />
                </button>
              ))}
            </div>
          </section>

          {/* Disclaimer */}
          <div className="pt-1 pb-2 text-center">
            <p className="text-[9px] text-slate-600 leading-relaxed">
              Market intelligence only. Not financial advice. Not a recommendation. No trade execution.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
