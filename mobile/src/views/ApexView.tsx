"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown, ChevronUp, Globe, Wind, Newspaper,
  TrendingUp, TrendingDown, AlertTriangle, Activity, Target,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type BotState } from "@/lib/api";
import { translateTheme, translateThemeState, themeDescription } from "@/lib/translate";
import {
  type IntelResponse, type IntelTheme, type IntelCandidate, type IntelEvidence,
  DRIVER_DICTIONARY, RISK_FLAG_DICTIONARY, ROLE_DICTIONARY, ALL_DRIVER_IDS,
  getScoreBand, explainDriverEvidence, getDormantThemeActivationNote,
  buildMarketStory, formatRouteHint,
} from "@/lib/intelligence";
import type { MarketEntry } from "@/app/api/markets/route";
import type { Headline }     from "@/app/api/headlines/route";

// ── Helpers ───────────────────────────────────────────────────────────────────

function pctColor(pct: number | null): string {
  if (pct === null) return "text-slate-500";
  return pct >= 0 ? "text-emerald-400" : "text-rose-400";
}

function pctLabel(pct: number | null): string {
  if (pct === null) return "—";
  return (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
}

function timeAgo(mins: number): string {
  if (mins < 60)   return `${mins}m ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)}d ago`;
}

function formatDate(): string {
  return new Date().toLocaleDateString("en-GB", {
    weekday: "long", day: "numeric", month: "long",
  });
}

function sentimentColors(s: string) {
  if (s === "risk-on")  return { border: "border-emerald-500/40", dot: "bg-emerald-400", text: "text-emerald-400", badge: "bg-emerald-500/10 text-emerald-400" };
  if (s === "risk-off") return { border: "border-rose-500/40",    dot: "bg-rose-400",    text: "text-rose-400",    badge: "bg-rose-500/10 text-rose-400"       };
  if (s === "mixed")    return { border: "border-amber-500/40",   dot: "bg-amber-400",   text: "text-amber-400",  badge: "bg-amber-500/10 text-amber-400"     };
  return                       { border: "border-slate-600/40",   dot: "bg-slate-400",   text: "text-slate-400",  badge: "bg-slate-500/10 text-slate-400"     };
}

// ── Market Story Card ─────────────────────────────────────────────────────────

function MarketStoryCard({ story }: { story: ReturnType<typeof buildMarketStory> }) {
  const [showRisks, setShowRisks] = useState(false);
  const sc = sentimentColors(story.overallSentiment);

  const SentimentIcon = story.overallSentiment === "risk-on"  ? TrendingUp
                      : story.overallSentiment === "risk-off" ? TrendingDown
                      : Activity;

  return (
    <div className={`rounded-2xl bg-[#0d1420] border ${sc.border} p-5`}>
      {/* Headline */}
      <div className="flex items-start gap-3 mb-4">
        <div className={`mt-1 p-2 rounded-xl ${sc.badge} shrink-0`}>
          <SentimentIcon size={16} className={sc.text} />
        </div>
        <div>
          <p className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-1">Market Story</p>
          <p className="text-base font-bold text-white leading-snug">{story.headline}</p>
        </div>
      </div>

      {/* Bullets */}
      {story.bullets.length > 0 && (
        <ul className="space-y-1.5 mb-4 pl-1">
          {story.bullets.map((b, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${sc.dot}`} />
              <p className="text-xs text-slate-300 leading-relaxed">{b}</p>
            </li>
          ))}
        </ul>
      )}

      {/* Three panels */}
      <div className="grid sm:grid-cols-3 gap-3 mb-3">
        <div className="bg-[#101622] rounded-xl p-3 border border-[#1e2a3a]">
          <p className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-1.5 flex items-center gap-1">
            <Target size={9} /> Expected next
          </p>
          <p className="text-xs text-slate-300 leading-relaxed">{story.expectation}</p>
        </div>
        <div className="bg-[#101622] rounded-xl p-3 border border-[#1e2a3a]">
          <p className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-1.5 flex items-center gap-1">
            <Activity size={9} /> Where to focus
          </p>
          <p className="text-xs text-slate-300 leading-relaxed">{story.attention}</p>
        </div>
        <div className="bg-[#101622] rounded-xl p-3 border border-[#1e2a3a]">
          <p className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-1.5 flex items-center gap-1">
            <TrendingUp size={9} /> Trade mode
          </p>
          <p className="text-xs text-slate-300 leading-relaxed">{story.tradingMode}</p>
        </div>
      </div>

      {/* Risks toggle */}
      {story.risks.length > 0 && (
        <button
          onClick={() => setShowRisks(r => !r)}
          className="w-full flex items-center justify-between text-left py-2 px-3 rounded-xl bg-[#101622] border border-[#1e2a3a] hover:border-amber-500/30 transition-colors"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle size={11} className="text-amber-400" />
            <span className="text-[11px] font-semibold text-amber-400/80">
              {story.risks.length} risk{story.risks.length !== 1 ? "s" : ""} that could break this view
            </span>
          </div>
          {showRisks ? <ChevronUp size={12} className="text-slate-500" /> : <ChevronDown size={12} className="text-slate-500" />}
        </button>
      )}
      {showRisks && (
        <ul className="mt-2 space-y-1 pl-1">
          {story.risks.map((r, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-amber-400/60 shrink-0" />
              <p className="text-xs text-slate-400 leading-relaxed">{r}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Active Driver Card ────────────────────────────────────────────────────────

function ActiveDriverCard({
  driverId, evidence, isActive,
}: {
  driverId: string;
  evidence: IntelEvidence;
  isActive: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const info    = DRIVER_DICTIONARY[driverId];
  const evData  = explainDriverEvidence(driverId, evidence, isActive);

  if (!info) return null;

  const statusColor = evData.status === "confirming" ? "text-emerald-400 bg-emerald-500/10"
                    : evData.status === "warning"     ? "text-amber-400 bg-amber-500/10"
                    :                                   "text-slate-500 bg-slate-500/10";
  const statusLabel = evData.status === "confirming" ? "Active — Confirming"
                    : evData.status === "warning"     ? "Active — Watch"
                    :                                   "Not signaling";
  const borderColor = evData.status === "confirming" ? "border-emerald-500/20"
                    : evData.status === "warning"     ? "border-amber-500/20"
                    :                                   "border-[#1e2a3a]";

  return (
    <div className={`rounded-2xl bg-[#101622] border ${borderColor} p-4`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-white leading-tight">{info.displayLabel}</p>
          <p className="text-[10px] text-slate-500 mt-0.5">via {info.proxySymbol}</p>
        </div>
        <span className={`text-[9px] font-bold px-2 py-1 rounded-full shrink-0 ${statusColor}`}>
          {statusLabel}
        </span>
      </div>

      {/* Measurement */}
      <div className="bg-[#0a1018] rounded-xl p-2.5 mb-2">
        <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-0.5">Evidence</p>
        <p className="text-xs font-semibold text-slate-200">{evData.measurement}</p>
      </div>

      {/* Interpretation */}
      <p className="text-xs text-slate-400 leading-relaxed mb-2">{evData.interpretation}</p>

      {/* Expandable: causal chain + affected themes */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1 text-[10px] font-semibold text-slate-600 hover:text-slate-400 transition-colors"
      >
        {expanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        Why this signal matters
      </button>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-[#1e2a3a] space-y-3">
          <div>
            <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1">Causal chain</p>
            <p className="text-xs text-slate-400 leading-relaxed">{evData.causalChain}</p>
          </div>
          <div>
            <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1">What to watch</p>
            <p className="text-xs text-slate-400 leading-relaxed">{info.invalidationTrigger}</p>
          </div>
          {evData.affectedThemeLabels.length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1.5">Themes activated</p>
              <div className="flex flex-wrap gap-1">
                {evData.affectedThemeLabels.map(t => (
                  <span key={t} className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-blue-500/10 text-blue-300">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}
          {evData.threshold && (
            <p className="text-[10px] text-slate-600 border-t border-[#1e2a3a] pt-2">{evData.threshold}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Inactive Driver Chips ─────────────────────────────────────────────────────

function InactiveDriversCard({
  inactiveIds, evidence,
}: {
  inactiveIds: string[];
  evidence: IntelEvidence;
}) {
  const [expanded, setExpanded] = useState(false);
  if (inactiveIds.length === 0) return null;

  return (
    <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
      <p className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-3">
        Not signaling ({inactiveIds.length})
      </p>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {inactiveIds.slice(0, expanded ? undefined : 8).map(id => {
          const info = DRIVER_DICTIONARY[id];
          if (!info) return null;
          const evData = explainDriverEvidence(id, evidence, false);
          return (
            <div
              key={id}
              className="group relative"
              title={`${info.displayLabel}: ${evData.measurement}`}
            >
              <span className="text-[10px] font-medium px-2.5 py-1 rounded-full bg-[#0a1018] text-slate-600 border border-[#1e2a3a] cursor-default">
                {info.displayLabel}
              </span>
            </div>
          );
        })}
        {!expanded && inactiveIds.length > 8 && (
          <button
            onClick={() => setExpanded(true)}
            className="text-[10px] text-slate-600 px-2.5 py-1 hover:text-slate-400 transition-colors"
          >
            +{inactiveIds.length - 8} more
          </button>
        )}
      </div>
      <p className="text-[10px] text-slate-700">
        These market forces are not currently signaling. They will appear as active cards when conditions change.
      </p>
    </div>
  );
}

// ── Theme Row ─────────────────────────────────────────────────────────────────

function ThemeRow({
  theme, tickers, expanded, onToggle, activeDrivers,
}: {
  theme: IntelTheme;
  tickers: IntelCandidate[];
  expanded: boolean;
  onToggle: () => void;
  activeDrivers: string[];
}) {
  const status     = translateThemeState(theme.state);
  const name       = translateTheme(theme.theme_id);
  const desc       = themeDescription(theme.theme_id);
  const isHeadwind = theme.direction === "headwind";
  const scoreBand  = getScoreBand(theme.confidence);
  const isDormant  = theme.state === "dormant";

  // Why it's active: translate active_drivers to display labels
  const activatedByLabels = (theme.active_drivers ?? [])
    .map(d => DRIVER_DICTIONARY[d]?.displayLabel ?? d)
    .filter(Boolean);

  // Dormant: what would activate it
  const activationNote = isDormant
    ? getDormantThemeActivationNote(theme.theme_id, activeDrivers)
    : null;

  const displayTickers = tickers
    .sort((a, b) => {
      const order = ["direct_beneficiary", "second_order_beneficiary", "etf_proxy"];
      return order.indexOf(a.role) - order.indexOf(b.role);
    })
    .slice(0, 5);

  const chipBg  = isHeadwind ? "bg-rose-500/10 text-rose-300"
                : isDormant  ? "bg-slate-700/50 text-slate-600"
                :              "bg-blue-500/10 text-blue-300";

  const cardBorder = isHeadwind ? "border-rose-500/15"
                   : isDormant  ? "border-[#1e2a3a]"
                   :              "border-[#1e2a3a] hover:border-blue-500/20";

  return (
    <button
      onClick={onToggle}
      className={`w-full text-left rounded-2xl bg-[#101622] border ${cardBorder} p-4 transition-all active:scale-[0.99]`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            {isHeadwind && <Wind size={11} className="text-rose-400 shrink-0" />}
            <p className="text-sm font-bold text-white leading-tight">{name}</p>
          </div>
          {!isDormant && (
            <p className={`text-[10px] font-semibold mt-0.5 ${isHeadwind ? "text-rose-400/70" : "text-emerald-400/60"}`}>
              {isHeadwind ? "Headwind — reduce exposure to this sector" : "Tailwind — opportunity sector"}
            </p>
          )}
          {isDormant && activationNote && (
            <p className="text-[10px] text-slate-600 mt-0.5 leading-snug">{activationNote}</p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${status.color}`}>
            {status.label}
          </span>
          {expanded ? <ChevronUp size={12} className="text-slate-600" /> : <ChevronDown size={12} className="text-slate-600" />}
        </div>
      </div>

      {/* Why active */}
      {!isDormant && activatedByLabels.length > 0 && (
        <div className="flex items-center gap-1.5 mb-2 flex-wrap">
          <p className="text-[10px] text-slate-600 font-medium">Activated by:</p>
          {activatedByLabels.map(label => (
            <span key={label} className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-400">
              {label}
            </span>
          ))}
        </div>
      )}

      {/* Collapsed description */}
      {!expanded && !isDormant && (
        <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed mb-2">{desc}</p>
      )}

      {/* Confidence band */}
      {!isDormant && (
        <div className="flex items-center gap-2 mb-2">
          <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold ${scoreBand.bgColor} ${scoreBand.color}`}>
            {(theme.confidence * 100).toFixed(0)}% · {scoreBand.label}
          </div>
          {theme.candidate_count !== undefined && (
            <p className="text-[10px] text-slate-600">{theme.candidate_count} names in universe</p>
          )}
        </div>
      )}

      {/* Ticker chips */}
      {displayTickers.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {displayTickers.map(c => (
            <span key={c.symbol} className={`text-[11px] font-bold px-2 py-0.5 rounded-md ${chipBg}`}>
              {c.symbol}
            </span>
          ))}
          {(theme.candidate_count ?? tickers.length) > displayTickers.length && (
            <span className="text-[11px] text-slate-600 py-0.5 px-1">
              +{(theme.candidate_count ?? tickers.length) - displayTickers.length} more
            </span>
          )}
        </div>
      )}

      {/* Expanded details */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-[#1e2a3a] space-y-3">
          <p className="text-sm text-slate-300 leading-relaxed">{desc}</p>

          {/* Per-ticker reasons */}
          {displayTickers.length > 0 && !isDormant && (
            <div className="space-y-2">
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Why these stocks</p>
              {displayTickers.map(c => {
                const role = ROLE_DICTIONARY[c.role];
                const reason = c.reason ?? c.reason_to_care;
                return (
                  <div key={c.symbol} className="flex gap-2">
                    <div className="shrink-0 h-fit mt-0.5">
                      <span className={`text-[11px] font-bold px-2 py-0.5 rounded-md ${chipBg}`}>
                        {c.symbol}
                      </span>
                    </div>
                    <div>
                      {role && (
                        <p className={`text-[9px] font-bold uppercase tracking-wider mb-0.5 ${role.color}`}>{role.displayLabel}</p>
                      )}
                      <p className="text-xs text-slate-400 leading-relaxed">{reason}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Invalidation rules */}
          {(theme.invalidation_rules ?? []).length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1">This theme loses confidence if</p>
              <ul className="space-y-0.5">
                {(theme.invalidation_rules ?? []).slice(0, 4).map((rule, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="mt-1.5 w-1 h-1 rounded-full bg-amber-400/40 shrink-0" />
                    <p className="text-xs text-slate-500 leading-relaxed">{rule}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Risk flags */}
          {(theme.risk_flags ?? []).length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1.5">Risk flags</p>
              <div className="flex flex-wrap gap-1.5">
                {(theme.risk_flags ?? []).map(flag => {
                  const info = RISK_FLAG_DICTIONARY[flag];
                  const label = info?.displayLabel ?? flag.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
                  const sev   = info?.severity;
                  const bg    = sev === "high" ? "bg-rose-500/10 text-rose-400/80"
                              : sev === "medium" ? "bg-amber-500/10 text-amber-400/80"
                              : "bg-slate-600/30 text-slate-500";
                  return (
                    <span key={flag} className={`text-[10px] font-medium px-2 py-0.5 rounded-md ${bg}`} title={info?.traderMeaning}>
                      {label}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </button>
  );
}

// ── Candidate Row ─────────────────────────────────────────────────────────────

function CandidateRow({ candidate }: { candidate: IntelCandidate }) {
  const [expanded, setExpanded] = useState(false);
  const band     = getScoreBand(candidate.confidence);
  const role     = ROLE_DICTIONARY[candidate.role];
  const theme    = translateTheme(candidate.theme);
  const rtcStripped = (candidate.reason_to_care ?? "").split(": ").slice(1).join(": ") || candidate.reason_to_care;
  const reason   = candidate.reason ?? rtcStripped;
  const tradeExp = formatRouteHint(candidate.route_hint);

  const riskFlagCount = (candidate.risk_flags ?? []).length;

  return (
    <button
      onClick={() => setExpanded(e => !e)}
      className="w-full text-left rounded-xl bg-[#0d1420] border border-[#1e2a3a] hover:border-slate-600/40 p-3 transition-all"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2.5 flex-1 min-w-0">
          {/* Symbol */}
          <span className="text-base font-black text-white shrink-0 w-12">{candidate.symbol}</span>
          <div className="flex-1 min-w-0">
            {/* Role + score on one line */}
            <div className="flex items-center gap-1.5 flex-wrap">
              {role && (
                <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${role.bgColor} ${role.color}`}>
                  {role.displayLabel}
                </span>
              )}
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${band.bgColor} ${band.color}`}>
                {(candidate.confidence * 100).toFixed(0)}% · {band.label}
              </span>
            </div>
            {/* Theme + trade expression */}
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              <span className="text-[10px] text-slate-600">{theme}</span>
              <span className="text-[10px] text-slate-700">·</span>
              <span className="text-[10px] text-slate-600">{tradeExp}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {riskFlagCount > 0 && (
            <span className="text-[9px] text-amber-400/70 bg-amber-500/10 px-1.5 py-0.5 rounded font-medium">
              {riskFlagCount} risk{riskFlagCount !== 1 ? "s" : ""}
            </span>
          )}
          {expanded ? <ChevronUp size={12} className="text-slate-600" /> : <ChevronDown size={12} className="text-slate-600" />}
        </div>
      </div>

      {/* Collapsed one-liner */}
      {!expanded && reason && (
        <p className="mt-1.5 ml-14 text-[11px] text-slate-600 line-clamp-1 leading-relaxed">{reason}</p>
      )}

      {/* Expanded */}
      {expanded && (
        <div className="mt-3 ml-1 pt-3 border-t border-[#1e2a3a] space-y-3">
          {reason && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1">Why it&apos;s in the universe</p>
              <p className="text-xs text-slate-400 leading-relaxed">{reason}</p>
            </div>
          )}

          {/* Risk flags detail */}
          {(candidate.risk_flags ?? []).length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1.5">Risk flags</p>
              <div className="space-y-1.5">
                {(candidate.risk_flags ?? []).map(flag => {
                  const info = RISK_FLAG_DICTIONARY[flag];
                  const label = info?.displayLabel ?? flag.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
                  const sev   = info?.severity;
                  const bg    = sev === "high" ? "bg-rose-500/10 text-rose-400/80"
                              : sev === "medium" ? "bg-amber-500/10 text-amber-400/80"
                              : "bg-slate-600/30 text-slate-500";
                  return (
                    <div key={flag} className="flex items-start gap-2">
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded shrink-0 ${bg}`}>{label}</span>
                      {info?.traderMeaning && (
                        <p className="text-[11px] text-slate-600 leading-relaxed">{info.traderMeaning}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Confirmation requirements */}
          {(candidate.confirmation_required ?? []).length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-600 uppercase tracking-wider mb-1">Before trading, confirm</p>
              <ul className="space-y-0.5">
                {(candidate.confirmation_required ?? []).slice(0, 3).map((req, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="mt-1.5 w-1 h-1 rounded-full bg-slate-600 shrink-0" />
                    <p className="text-[11px] text-slate-600 leading-relaxed">
                      {req.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </button>
  );
}

// ── Universe Panel ─────────────────────────────────────────────────────────────

function UniversePanel({ candidates }: { candidates: IntelCandidate[] }) {
  const sorted = [...candidates].sort((a, b) => {
    const roleOrder = ["direct_beneficiary", "second_order_beneficiary", "etf_proxy"];
    const roleDiff = roleOrder.indexOf(a.role) - roleOrder.indexOf(b.role);
    return roleDiff !== 0 ? roleDiff : b.confidence - a.confidence;
  });

  const highConviction = sorted.filter(c => c.confidence >= 0.80 && c.role !== "etf_proxy");
  const strongWatch    = sorted.filter(c => c.confidence >= 0.65 && c.confidence < 0.80 && c.role !== "etf_proxy");
  const developing     = sorted.filter(c => c.confidence < 0.65 && c.role !== "etf_proxy");
  const etfProxies     = sorted.filter(c => c.role === "etf_proxy");

  const groups = [
    { label: "High Conviction", meaning: "≥ 80% confidence — trade-ready if entry confirms", items: highConviction, color: "text-emerald-400/70" },
    { label: "Strong Watchlist", meaning: "65–79% — well-supported, needs entry confirmation", items: strongWatch,  color: "text-blue-400/70"    },
    { label: "Developing",       meaning: "50–64% — theme active, confidence partial",         items: developing,   color: "text-amber-400/70"   },
    { label: "ETF Proxies",      meaning: "Sector ETFs — broad theme exposure",                items: etfProxies,   color: "text-slate-500"      },
  ].filter(g => g.items.length > 0);

  if (candidates.length === 0) {
    return (
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-8 flex flex-col items-center gap-3">
        <Globe size={24} className="text-slate-600" />
        <p className="text-slate-600 text-sm text-center">No candidates in the intelligence universe</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {groups.map(g => (
        <div key={g.label}>
          <div className="flex items-baseline gap-2 mb-2">
            <p className={`text-[10px] font-bold uppercase tracking-wider ${g.color}`}>{g.label}</p>
            <span className="text-[10px] text-slate-700">{g.meaning}</span>
          </div>
          <div className="space-y-1.5">
            {g.items.map(c => (
              <CandidateRow key={c.symbol} candidate={c} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── World Markets Region ──────────────────────────────────────────────────────

function MarketRegion({ label, markets }: { label: string; markets: MarketEntry[] }) {
  return (
    <div>
      <p className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-1.5">{label}</p>
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${markets.length}, 1fr)` }}>
        {markets.map(m => (
          <div
            key={m.sym}
            className={`flex flex-col items-center py-2.5 px-1 rounded-xl border ${
              m.changePct === null ? "bg-[#101622] border-[#1e2a3a]"
              : m.changePct >= 0   ? "bg-emerald-500/8 border-emerald-500/20"
              :                      "bg-rose-500/8 border-rose-500/20"
            }`}
          >
            <span className="text-[9px] font-semibold text-slate-500 mb-0.5">{m.label}</span>
            <span className={`text-sm font-bold ${pctColor(m.changePct)}`}>{pctLabel(m.changePct)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── News Card ─────────────────────────────────────────────────────────────────

function NewsCard({ item }: { item: Headline }) {
  return (
    <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4">
      <p className="text-sm font-semibold text-white leading-snug mb-1.5">{item.title}</p>
      {item.summary && (
        <p className="text-xs text-slate-400 leading-relaxed line-clamp-3 mb-2">{item.summary}</p>
      )}
      <p className="text-[10px] text-slate-600 font-medium">
        {item.source} · {timeAgo(item.minutesAgo)}
      </p>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function ApexView() {
  const [state,     setState]     = useState<BotState | null>(null);
  const [intel,     setIntel]     = useState<IntelResponse | null>(null);
  const [markets,   setMarkets]   = useState<MarketEntry[] | null>(null);
  const [headlines, setHeadlines] = useState<Headline[] | null>(null);
  const [loading,   setLoading]   = useState(true);
  const [themeExp,  setThemeExp]  = useState<string | null>(null);

  const load = useCallback(async () => {
    const [s, i, m, h] = await Promise.allSettled([
      api.get<BotState>("/api/state"),
      api.get<IntelResponse>("/api/intelligence"),
      fetch("/api/markets").then(r => r.json()),
      fetch("/api/headlines").then(r => r.json()),
    ]);
    if (s.status === "fulfilled") setState(s.value);
    if (i.status === "fulfilled") setIntel(i.value as IntelResponse);
    if (m.status === "fulfilled") setMarkets((m.value as { markets: MarketEntry[] }).markets ?? null);
    if (h.status === "fulfilled") setHeadlines((h.value as { headlines: Headline[] }).headlines ?? null);
    setLoading(false);
  }, []);

  useEffect(() => {
    // setState calls inside load() are async (after awaited fetches) — not synchronous
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const session     = state?.session ?? "UNKNOWN";
  const isOpen      = session === "OPEN";
  const themes      = intel?.themes ?? [];
  const candidates  = intel?.candidates ?? [];
  const activeDrivers = intel?.market_map?.active_drivers ?? [];
  const evidence    = intel?.market_map?.evidence ?? {};
  const inactiveDriverIds = ALL_DRIVER_IDS.filter(d => !activeDrivers.includes(d));

  // Group candidates by theme
  const tickersByTheme: Record<string, IntelCandidate[]> = {};
  for (const c of candidates) {
    if (!tickersByTheme[c.theme]) tickersByTheme[c.theme] = [];
    tickersByTheme[c.theme].push(c);
  }

  const activeTailwinds = themes
    .filter(t => ["activated", "strengthening", "crowded"].includes(t.state) && t.direction !== "headwind")
    .sort((a, b) => {
      const order = ["activated", "strengthening", "crowded"];
      return order.indexOf(a.state) - order.indexOf(b.state);
    });
  const activeHeadwinds = themes.filter(t => t.direction === "headwind" && t.state !== "dormant");
  const dormantThemes   = themes.filter(t => t.state === "dormant");

  const usMarkets     = markets?.filter(m => m.region === "US")     ?? [];
  const asiaMarkets   = markets?.filter(m => m.region === "Asia")   ?? [];
  const europeMarkets = markets?.filter(m => m.region === "Europe") ?? [];

  const marketStory = buildMarketStory(
    activeDrivers,
    themes,
    candidates,
    state?.regime?.regime ?? null,
  );

  if (loading) return (
    <div className="px-5 pt-6 max-w-7xl mx-auto space-y-4">
      <Skeleton className="h-12 w-52 bg-[#161e2e]" />
      <Skeleton className="h-52 w-full rounded-2xl bg-[#161e2e]" />
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {[1,2,3].map(i => <Skeleton key={i} className="h-40 rounded-2xl bg-[#161e2e]" />)}
      </div>
      {[1,2,3].map(i => <Skeleton key={i} className="h-28 rounded-2xl bg-[#161e2e]" />)}
    </div>
  );

  return (
    <div className="px-4 md:px-6 pt-6 pb-6 max-w-7xl mx-auto space-y-5">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] font-bold tracking-[0.22em] text-slate-600 uppercase mb-0.5">
            Amit Chopra
          </p>
          <h1 className="text-xl font-bold text-white">Market Intelligence</h1>
          <p className="text-xs text-slate-500 mt-0.5">{formatDate()}</p>
        </div>
        <span className={`flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1.5 rounded-full shrink-0 mt-1 ${
          isOpen ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-700/50 text-slate-500"
        }`}>
          <span className={`w-1.5 h-1.5 rounded-full ${isOpen ? "bg-emerald-400 animate-pulse" : "bg-slate-600"}`} />
          {isOpen ? "Markets Open" : session === "PRE" ? "Pre-Market" : session === "AFTER_HOURS" ? "After Hours" : "Closed"}
        </span>
      </div>

      {/* ── Market Story ────────────────────────────────────────────────────── */}
      <MarketStoryCard story={marketStory} />

      {/* ── Market Forces ───────────────────────────────────────────────────── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            What the Market is Signaling
          </p>
          {activeDrivers.length > 0 && (
            <span className="text-[11px] font-bold text-emerald-400 bg-emerald-400/10 px-2.5 py-1 rounded-full">
              {activeDrivers.length} active signal{activeDrivers.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {activeDrivers.length > 0 && (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-3">
            {activeDrivers.map(id => (
              <ActiveDriverCard key={id} driverId={id} evidence={evidence} isActive={true} />
            ))}
          </div>
        )}

        {inactiveDriverIds.length > 0 && (
          <InactiveDriversCard inactiveIds={inactiveDriverIds} evidence={evidence} />
        )}

        {activeDrivers.length === 0 && (
          <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-8 flex flex-col items-center gap-3">
            <Activity size={24} className="text-slate-600" />
            <p className="text-slate-500 text-sm text-center">No market signals are active. System is in observation mode.</p>
          </div>
        )}
      </div>

      {/* ── Themes + Universe (2-column on desktop) ─────────────────────────── */}
      <div className="grid lg:grid-cols-5 gap-5">

        {/* Left: Themes */}
        <div className="lg:col-span-2 space-y-4">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            Theme Map
          </p>

          {/* Active Tailwinds */}
          {activeTailwinds.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp size={11} className="text-emerald-400" />
                <p className="text-[10px] font-bold text-emerald-400/60 uppercase tracking-wider">
                  Active Tailwinds ({activeTailwinds.length})
                </p>
              </div>
              <div className="space-y-2">
                {activeTailwinds.map(t => (
                  <ThemeRow
                    key={t.theme_id}
                    theme={t}
                    tickers={tickersByTheme[t.theme_id] ?? []}
                    expanded={themeExp === t.theme_id}
                    onToggle={() => setThemeExp(p => p === t.theme_id ? null : t.theme_id)}
                    activeDrivers={activeDrivers}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Active Headwinds */}
          {activeHeadwinds.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Wind size={11} className="text-rose-400" />
                <p className="text-[10px] font-bold text-rose-400/60 uppercase tracking-wider">
                  Active Headwinds ({activeHeadwinds.length})
                </p>
              </div>
              <div className="space-y-2">
                {activeHeadwinds.map(t => (
                  <ThemeRow
                    key={t.theme_id}
                    theme={t}
                    tickers={tickersByTheme[t.theme_id] ?? []}
                    expanded={themeExp === t.theme_id}
                    onToggle={() => setThemeExp(p => p === t.theme_id ? null : t.theme_id)}
                    activeDrivers={activeDrivers}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Dormant */}
          {dormantThemes.length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-700 uppercase tracking-wider mb-2">
                Dormant — Watching ({dormantThemes.length})
              </p>
              <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-3 space-y-2">
                {dormantThemes.map(t => (
                  <ThemeRow
                    key={t.theme_id}
                    theme={t}
                    tickers={tickersByTheme[t.theme_id] ?? []}
                    expanded={themeExp === t.theme_id}
                    onToggle={() => setThemeExp(p => p === t.theme_id ? null : t.theme_id)}
                    activeDrivers={activeDrivers}
                  />
                ))}
              </div>
            </div>
          )}

          {activeTailwinds.length === 0 && activeHeadwinds.length === 0 && dormantThemes.length === 0 && (
            <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-8 flex flex-col items-center gap-3">
              <Globe size={24} className="text-slate-600" />
              <p className="text-slate-500 text-sm text-center">Intelligence pipeline is loading</p>
            </div>
          )}
        </div>

        {/* Right: Universe */}
        <div className="lg:col-span-3">
          <div className="flex items-center justify-between mb-3">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
              Intelligence Universe
            </p>
            {candidates.length > 0 && (
              <span className="text-[11px] font-bold text-slate-500 bg-slate-600/20 px-2.5 py-1 rounded-full">
                {candidates.length} names
              </span>
            )}
          </div>
          <UniversePanel candidates={candidates} />
        </div>
      </div>

      {/* ── World Markets ──────────────────────────────────────────────────── */}
      <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-4 space-y-3">
        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">World Markets</p>
        {markets ? (
          <div className="space-y-3">
            {usMarkets.length     > 0 && <MarketRegion label="United States" markets={usMarkets}     />}
            {asiaMarkets.length   > 0 && <MarketRegion label="Asia"          markets={asiaMarkets}   />}
            {europeMarkets.length > 0 && <MarketRegion label="Europe"        markets={europeMarkets} />}
          </div>
        ) : (
          <p className="text-xs text-slate-600 italic">Loading market data…</p>
        )}
      </div>

      {/* ── News ───────────────────────────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Newspaper size={13} className="text-slate-500" />
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            What&apos;s Moving Markets
          </p>
        </div>
        {headlines && headlines.length > 0 ? (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {headlines.map((h, i) => <NewsCard key={i} item={h} />)}
          </div>
        ) : (
          <div className="rounded-2xl bg-[#101622] border border-[#1e2a3a] p-6 text-center">
            <p className="text-xs text-slate-600">News loading…</p>
          </div>
        )}
      </div>

    </div>
  );
}
