"use client";
// Names tab — story-grouped research cards.
// Phase 1: TTG structural themes from /api/intelligence/themes.
// Phase 2: live prices via /api/name-prices for top-50 priority symbols.
// Customer-safe language only — no execution, broker, or trading-control terms.

import { useState, useEffect } from "react";
import { ArrowRight, Zap, ChevronDown, ChevronUp } from "lucide-react";
import type {
  MarketNowPayload,
  RadarItem,
  TtgThemeDetail,
  TtgSymbolCard,
} from "@/lib/customerApi";
import { fetchTtgThemes, fetchTtgThemeDetail } from "@/lib/customerApi";
import type { NamePriceEntry } from "@/lib/namePriceUtils";
import { MAX_SYMBOLS } from "@/lib/namePriceUtils";
import {
  buildStoryGroups,
  buildRadarCards,
  prioritySymbols,
  type ResearchNameCard,
  type ResearchStoryGroup,
} from "@/lib/nameResearchModel";
import NameResearchSheet from "./NameResearchSheet";

// ── Watch type badge ───────────────────────────────────────────────────────────

function WatchBadge({ watchType }: { watchType: ResearchNameCard["watchType"] }) {
  const styles: Record<string, { bg: string; color: string }> = {
    "Catalyst watch":  { bg: "rgba(16,185,129,0.12)",  color: "#34d399" },
    "Structural watch":{ bg: "rgba(99,102,241,0.12)",  color: "#818cf8" },
    "Market attention":{ bg: "rgba(148,163,184,0.10)", color: "#94a3b8" },
  };
  const s = styles[watchType] ?? styles["Market attention"];
  return (
    <span
      className="text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0"
      style={{ background: s.bg, color: s.color }}
    >
      {watchType}
    </span>
  );
}

// ── Price action display ───────────────────────────────────────────────────────

function PriceChip({ card }: { card: ResearchNameCard }) {
  const { tone, displayText } = card.priceAction;
  if (tone === "unknown") {
    return <span className="text-[9px] text-slate-600">{displayText}</span>;
  }
  const color = tone === "positive" ? "#34d399" : tone === "negative" ? "#f87171" : "#94a3b8";
  return (
    <span className="text-[10px] font-semibold" style={{ color }}>
      {displayText}
    </span>
  );
}

// ── Research card ─────────────────────────────────────────────────────────────

function ResearchCard({
  card,
  onTap,
  onAskAbout,
}: {
  card: ResearchNameCard;
  onTap?: (card: ResearchNameCard) => void;
  onAskAbout?: (context: string) => void;
}) {
  const borderColor = card.isPressure
    ? "rgba(239,68,68,0.15)"
    : "rgba(255,255,255,0.07)";

  const inner = (
    <>
      {/* Header row */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-base font-black text-slate-100">{card.symbol}</span>
            {card.companyName && card.companyName !== card.symbol && (
              <span className="text-[11px] text-slate-500 truncate max-w-[180px]">
                {card.companyName}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className="text-[9px] text-slate-500">{card.confidenceLanguage}</span>
          </div>
        </div>
        <WatchBadge watchType={card.watchType} />
      </div>

      {/* Price action */}
      <div className="mb-2">
        <PriceChip card={card} />
      </div>

      {/* Reason to care */}
      <p className="text-[11px] text-slate-300 leading-relaxed line-clamp-3 mb-2">
        {card.reasonToCare}
      </p>

      {/* Risk note */}
      {card.riskNote && (
        <p className="text-[9px] text-amber-600 leading-relaxed line-clamp-2 mb-1.5">
          ⚠ {card.riskNote}
        </p>
      )}

      {/* Ask CTA */}
      {onAskAbout && !onTap && (
        <button
          onClick={() =>
            onAskAbout(
              `Tell me about ${card.symbol} and why it is connected to ${card.customerStory}`,
            )
          }
          className="mt-1 flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95"
          style={{ color: "#94a3b8" }}
        >
          Ask Decifer about this
          <ArrowRight size={9} />
        </button>
      )}
    </>
  );

  if (onTap) {
    return (
      <button
        onClick={() => onTap(card)}
        className="w-full rounded-2xl p-4 text-left transition-all active:scale-[0.98]"
        style={{ background: "#141b26", border: `1px solid ${borderColor}` }}
      >
        {inner}
      </button>
    );
  }

  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: "#141b26", border: `1px solid ${borderColor}` }}
    >
      {inner}
    </div>
  );
}

// ── Story group section ────────────────────────────────────────────────────────

const CARDS_DEFAULT_VISIBLE = 5;

function StoryGroupSection({
  group,
  onAskAbout,
  onThemeSelect,
  onCardTap,
}: {
  group: ResearchStoryGroup;
  onAskAbout?: (context: string) => void;
  onThemeSelect: (themeId: string) => void;
  onCardTap: (card: ResearchNameCard) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? group.cards : group.cards.slice(0, CARDS_DEFAULT_VISIBLE);
  const hasMore = group.cards.length > CARDS_DEFAULT_VISIBLE;

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <p
            className="text-[10px] font-bold uppercase tracking-[0.15em]"
            style={{ color: "#f97316" }}
          >
            {group.storyLabel}
          </p>
          {group.driverActive && (
            <span
              className="text-[8px] font-bold px-1.5 py-0.5 rounded-full"
              style={{ background: "rgba(16,185,129,0.12)", color: "#34d399" }}
            >
              In play
            </span>
          )}
          <span className="text-[9px] text-slate-600">{group.cards.length}</span>
        </div>
        {group.themeId && (
          <button
            onClick={() => onThemeSelect(group.themeId)}
            className="text-[9px] font-semibold px-2 py-0.5 rounded-full shrink-0"
            style={{ background: "rgba(249,115,22,0.1)", color: "#fb923c" }}
          >
            Theme Map →
          </button>
        )}
      </div>

      <div className="space-y-2">
        {visible.map((card, i) => (
          <ResearchCard
            key={`${card.symbol}-${i}`}
            card={card}
            onTap={onCardTap}
            onAskAbout={onAskAbout}
          />
        ))}
      </div>

      {hasMore && (
        <button
          onClick={() => setExpanded(e => !e)}
          className="mt-2 w-full flex items-center justify-center gap-1 py-2 rounded-xl text-[10px] font-semibold transition-all active:scale-[0.98]"
          style={{
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.06)",
            color: "#64748b",
          }}
        >
          {expanded ? (
            <>
              <ChevronUp size={10} />
              Show less
            </>
          ) : (
            <>
              <ChevronDown size={10} />
              {group.cards.length - CARDS_DEFAULT_VISIBLE} more in this story
            </>
          )}
        </button>
      )}
    </section>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  data: MarketNowPayload;
  onNameSelect: (name: RadarItem) => void;
  onThemeSelect: (themeId: string) => void;
  onSymbolSelect?: (card: TtgSymbolCard) => void;
  onAskAbout?: (context: string) => void;
}

export default function UniverseTab(props: Props) {
  const { data, onNameSelect, onThemeSelect, onAskAbout } = props;
  // props.onSymbolSelect preserved in interface for CustomerApp compatibility

  const [ttgData, setTtgData] = useState<TtgThemeDetail[]>([]);
  const [priceMap, setPriceMap] = useState<Map<string, NamePriceEntry>>(new Map());
  const [loading, setLoading] = useState(true);
  const [pricesLoading, setPricesLoading] = useState(false);
  const [selectedCard, setSelectedCard] = useState<ResearchNameCard | null>(null);

  const radar = data.radar ?? [];

  // Phase 1 → TTG data; Phase 2 → prices for priority symbols
  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      try {
        const themes = await fetchTtgThemes();
        const results = await Promise.allSettled(
          themes.map(t => fetchTtgThemeDetail(t.theme_id)),
        );
        if (cancelled) return;
        const loaded = results
          .filter(r => r.status === "fulfilled" && r.value !== null)
          .map(r => (r as PromiseFulfilledResult<TtgThemeDetail | null>).value as TtgThemeDetail);
        setTtgData(loaded);
        setLoading(false);

        const syms = prioritySymbols(loaded, MAX_SYMBOLS);
        if (syms.length === 0 || cancelled) return;
        setPricesLoading(true);
        const res = await fetch(`/api/name-prices?symbols=${syms.join(",")}`);
        if (cancelled) return;
        if (res.ok) {
          const json: { prices: NamePriceEntry[] } = await res.json();
          if (!cancelled) setPriceMap(new Map(json.prices.map(p => [p.symbol, p])));
        }
      } catch {
        if (!cancelled) { setTtgData([]); setLoading(false); }
      } finally {
        if (!cancelled) setPricesLoading(false);
      }
    };

    run();
    return () => { cancelled = true; };
  }, []);

  const storyGroups = buildStoryGroups(ttgData, priceMap);
  const radarCards = buildRadarCards(radar, priceMap);
  const totalNames = ttgData.reduce((acc, t) => acc + t.symbols.length, 0);

  // ── Loading skeleton
  if (loading) {
    return (
      <div className="px-4 pt-8 space-y-3">
        {[1, 2, 3].map(i => (
          <div
            key={i}
            className="rounded-2xl h-24 animate-pulse"
            style={{ background: "rgba(255,255,255,0.04)" }}
          />
        ))}
        <p className="text-[10px] text-slate-600 text-center pt-2">
          Loading connected names…
        </p>
      </div>
    );
  }

  // ── Empty state
  if (storyGroups.length === 0 && radarCards.length === 0) {
    return (
      <div className="px-4 pt-12 flex flex-col items-center gap-3 text-center">
        <p className="text-slate-400 text-sm">
          No connected names available right now.
        </p>
        <p className="text-xs text-slate-500 leading-relaxed max-w-xs">
          Market story context is available in the Theme Map. Connected names will appear here as the intelligence layer refreshes.
        </p>
      </div>
    );
  }

  return (
    <>
    <div className="px-4 pt-2 pb-8 space-y-6">

      {/* Intro card */}
      <div
        className="rounded-2xl px-4 py-3"
        style={{
          background: "rgba(249,115,22,0.05)",
          border: "1px solid rgba(249,115,22,0.12)",
        }}
      >
        <p className="text-[11px] font-semibold text-slate-300 leading-snug">
          Where today&rsquo;s market stories connect to names
        </p>
        <p className="text-[10px] text-slate-500 mt-0.5">
          {totalNames} evidence-verified names · {storyGroups.length} market stories
          {pricesLoading && " · Updating prices…"}
        </p>
      </div>

      {/* Live intelligence overlay */}
      {radarCards.length > 0 && (
        <section>
          <p
            className="text-[10px] font-bold uppercase tracking-[0.15em] mb-2.5 flex items-center gap-1.5"
            style={{ color: "#f97316" }}
          >
            <Zap size={9} />
            Live Intelligence
          </p>
          <div className="space-y-2">
            {radar.map((radarItem, i) => {
              const card = radarCards[i];
              if (!card) return null;
              return (
                <button
                  key={`radar-${radarItem.symbol}-${i}`}
                  onClick={() => onNameSelect(radarItem)}
                  className="w-full text-left"
                >
                  <ResearchCard card={card} onAskAbout={onAskAbout} />
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* Story groups */}
      {storyGroups.map(group => (
        <StoryGroupSection
          key={group.themeId}
          group={group}
          onAskAbout={onAskAbout}
          onThemeSelect={onThemeSelect}
          onCardTap={setSelectedCard}
        />
      ))}

      <p className="text-[10px] text-slate-600 text-center pt-2">
        Market intelligence only. Not financial advice. No trade execution.
      </p>
    </div>

    {/* Name research detail sheet — keyed by symbol so each card mounts fresh */}
    {selectedCard && (
      <NameResearchSheet
        key={selectedCard.symbol}
        card={selectedCard}
        onClose={() => setSelectedCard(null)}
        onAskAbout={(q) => {
          setSelectedCard(null);
          onAskAbout?.(q);
        }}
      />
    )}
    </>
  );
}
