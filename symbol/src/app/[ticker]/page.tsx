import { headers } from "next/headers";
import Link from "next/link";
import Image from "next/image";

/* ---------- types ---------- */
interface Theme {
  theme_id: string;
  theme_label: string;
  bucket_label: string;
  exposure_type: string;
  confidence: number;
  reason_to_care: string;
  risk_note: string;
  driver_id: string;
  driver_active: boolean;
  last_reviewed: string;
}
interface IntelFeed {
  in_feed: boolean;
  role: string;
  reason_to_care: string;
  confidence: number;
  risk_flags: string[];
  theme: string;
  driver: string;
}
interface OptionsFlow {
  call_volume: number;
  put_volume: number;
  call_expansion: number | null;
  unusual_calls: boolean;
  unusual_puts: boolean;
  unusual: boolean;
  oi_available: boolean;
  oi_note: string;
}
interface MarketContext {
  active_drivers: string[];
  blocked_conditions: string[];
  drivers_mode: string;
}
interface ConvictionBreakdown {
  signal: string;
  detail: string;
  pts: number;
}
interface SymbolData {
  symbol: string;
  conviction_score: number;
  conviction_tier: "high" | "medium" | "watchlist";
  conviction_breakdown: ConvictionBreakdown[];
  themes: Theme[];
  intelligence_feed: IntelFeed | null;
  options_flow: OptionsFlow | null;
  market_context: MarketContext;
  data_freshness: Record<string, string>;
  disclaimer: string;
}
interface PriceData {
  symbol: string;
  companyName: string | null;
  price: number | null;
  changesPercentage: number | null;
  image: string | null;
  description: string | null;
  sector: string | null;
  industry: string | null;
  mktCap: number | null;
}

/* ---------- fetch ---------- */
async function fetchSymbol(
  ticker: string
): Promise<{ data?: SymbolData; notFound?: boolean; error?: string }> {
  const host = (await headers()).get("host") ?? "localhost:3000";
  const proto = process.env.NODE_ENV === "production" ? "https" : "http";
  const url = `${proto}://${host}/api/symbol/${encodeURIComponent(ticker)}`;
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 404) return { notFound: true };
    if (!res.ok) return { error: "upstream" };
    return { data: await res.json() };
  } catch {
    return { error: "fetch_failed" };
  }
}

async function fetchPrice(
  ticker: string,
  host: string,
  proto: string
): Promise<PriceData | null> {
  try {
    const res = await fetch(
      `${proto}://${host}/api/symbol/${encodeURIComponent(ticker)}/price`,
      { next: { revalidate: 300 } }
    );
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ---------- bull/bear lean ---------- */
function computeLean(
  themes: Theme[],
  feed: IntelFeed | null,
  flow: OptionsFlow | null,
  ctx: MarketContext
): { label: string; variant: "bull" | "bear" | "neutral" } {
  let score = 0;
  const activeTheme = themes.find((t) => t.driver_active);
  if (activeTheme) score += 2;
  if (feed?.in_feed) score += 1;
  if (flow?.unusual_calls) score += 1;
  if (flow?.unusual_puts) score -= 1;
  const relevantBlocks = ctx.blocked_conditions.filter((b) =>
    themes.some((t) => b.includes(t.driver_id) || t.driver_id.includes(b.split("_")[0]))
  );
  score -= relevantBlocks.length;
  if (feed?.risk_flags?.length) score -= Math.min(feed.risk_flags.length, 2) * 0.5;

  if (score >= 2) return { label: "Leaning Bullish", variant: "bull" };
  if (score <= -1) return { label: "Leaning Bearish", variant: "bear" };
  return { label: "Mixed Signals", variant: "neutral" };
}

function formatMktCap(v: number | null): string | null {
  if (!v) return null;
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

/* ---------- ui components ---------- */
function Badge({
  label,
  variant = "neutral",
}: {
  label: string;
  variant?: "green" | "orange" | "red" | "neutral";
}) {
  const cls: Record<string, string> = {
    green: "bg-emerald-950 text-emerald-400 border-emerald-800",
    orange: "bg-orange-950 text-accent border-orange-800",
    red: "bg-red-950 text-red-400 border-red-800",
    neutral: "bg-surface-2 text-text-muted border-border",
  };
  return (
    <span
      className={`inline-block border rounded-sm px-2 py-0.5 text-xs font-mono tracking-wide ${cls[variant]}`}
    >
      {label}
    </span>
  );
}

function Section({
  title,
  children,
  delay,
}: {
  title: string;
  children: React.ReactNode;
  delay: number;
}) {
  return (
    <div className={`border border-border rounded-sm p-5 fade-up fade-up-${delay}`}>
      <p className="font-mono text-xs text-text-muted tracking-widest uppercase mb-4">
        {title}
      </p>
      {children}
    </div>
  );
}

/* ---------- page ---------- */
export default async function TickerPage({
  params,
}: {
  params: { ticker: string };
}) {
  const ticker = params.ticker.toUpperCase();
  const host = (await headers()).get("host") ?? "localhost:3000";
  const proto = process.env.NODE_ENV === "production" ? "https" : "http";

  const [{ data, notFound, error }, price] = await Promise.all([
    fetchSymbol(ticker),
    fetchPrice(ticker, host, proto),
  ]);

  /* 404 state */
  if (notFound || !data) {
    return (
      <div className="max-w-xl mx-auto px-4 py-20 fade-up">
        <p className="font-mono text-xs text-text-muted tracking-widest uppercase mb-3">
          Not found
        </p>
        <h1 className="text-3xl font-mono text-text mb-3">
          <span className="text-accent">{ticker}</span>
        </h1>
        <p className="text-text-muted text-sm mb-1">
          {ticker} is not in our intelligence graph.
        </p>
        <p className="text-text-muted text-sm mb-8">
          We track 125 curated symbols.
        </p>
        {error && (
          <p className="text-red-500 text-xs font-mono mb-6">Error: {error}</p>
        )}
        <Link
          href="/"
          className="font-mono text-xs text-accent tracking-widest uppercase border border-accent rounded-sm px-4 py-2 hover:bg-accent hover:text-background transition-colors"
        >
          Browse universe
        </Link>
      </div>
    );
  }

  const {
    themes,
    intelligence_feed: feed,
    options_flow: flow,
    market_context: ctx,
    data_freshness,
    conviction_score,
    conviction_tier,
    conviction_breakdown,
  } = data;
  const primaryTheme = themes[0];
  const lean = computeLean(themes, feed, flow, ctx);
  const leanCls =
    lean.variant === "bull"
      ? "text-emerald-400 border-emerald-800 bg-emerald-950"
      : lean.variant === "bear"
      ? "text-red-400 border-red-800 bg-red-950"
      : "text-text-muted border-border bg-surface-2";

  const tierColor =
    conviction_tier === "high"
      ? "text-emerald-400"
      : conviction_tier === "medium"
      ? "text-amber-400"
      : "text-text-muted";

  const tierLabel =
    conviction_tier === "high"
      ? "High conviction"
      : conviction_tier === "medium"
      ? "Building"
      : "Watchlist";

  const priceUp =
    price?.changesPercentage != null && price.changesPercentage >= 0;
  const priceColor = priceUp ? "text-emerald-400" : "text-red-400";

  return (
    <div className="max-w-2xl mx-auto px-4 py-10 space-y-5">
      {/* Header */}
      <div className="fade-up fade-up-1">
        <div className="flex items-start justify-between gap-4 mb-4">
          {/* Logo + identity */}
          <div className="flex items-start gap-4">
            {price?.image && (
              <div className="shrink-0 w-12 h-12 rounded-sm border border-border bg-white flex items-center justify-center overflow-hidden">
                <Image
                  src={price.image}
                  alt={`${ticker} logo`}
                  width={48}
                  height={48}
                  className="object-contain"
                />
              </div>
            )}
            <div>
              <h1
                className="text-5xl leading-none"
                style={{ fontFamily: "'Instrument Serif', serif" }}
              >
                <span className="text-accent">{data.symbol}</span>
              </h1>
              {price?.companyName && (
                <p className="text-text text-sm mt-1 leading-snug">
                  {price.companyName}
                </p>
              )}
            </div>
          </div>

          <div className="flex flex-col items-end gap-2 mt-1 shrink-0">
            <Link
              href="/"
              className="font-mono text-xs text-text-muted border border-border rounded-sm px-3 py-1.5 hover:border-accent hover:text-accent transition-colors"
            >
              ← Browse
            </Link>
            <span
              className={`font-mono text-xs border rounded-sm px-2.5 py-1 tracking-wide ${leanCls}`}
            >
              {lean.label}
            </span>
          </div>
        </div>

        {/* Price strip */}
        {price?.price != null && (
          <div className="flex flex-wrap items-baseline gap-4 mb-4">
            <span className="font-mono text-2xl text-text">
              ${price.price.toFixed(2)}
            </span>
            {price.changesPercentage != null && (
              <span className={`font-mono text-sm ${priceColor}`}>
                {price.changesPercentage >= 0 ? "+" : ""}
                {price.changesPercentage.toFixed(2)}%
              </span>
            )}
            {price.mktCap != null && (
              <span className="font-mono text-xs text-text-muted">
                Mkt cap {formatMktCap(price.mktCap)}
              </span>
            )}
            {price.sector && (
              <span className="font-mono text-xs text-text-muted">
                {price.sector}
              </span>
            )}
          </div>
        )}

        {/* Theme breadcrumb */}
        {primaryTheme && (
          <p className="text-text-muted font-mono text-xs tracking-wide mb-1">
            in <span className="text-text">{primaryTheme.theme_label}</span>
            {" · "}
            <span>{primaryTheme.bucket_label}</span>
          </p>
        )}
      </div>

      {/* Conviction score */}
      <div className={`fade-up fade-up-2 border rounded-sm p-5 ${
        conviction_tier === "high"
          ? "border-emerald-800 bg-emerald-950/20"
          : conviction_tier === "medium"
          ? "border-amber-800/60 bg-amber-950/10"
          : "border-border bg-surface"
      }`}>
        <div className="flex items-center justify-between mb-4">
          <p className="font-mono text-xs text-text-muted tracking-widest uppercase">
            Decifer Conviction
          </p>
          <div className="flex items-baseline gap-2">
            <span className={`font-mono text-3xl font-semibold ${tierColor}`}>
              {conviction_score}
            </span>
            <span className={`font-mono text-xs ${tierColor}`}>
              {tierLabel}
            </span>
          </div>
        </div>
        <div className="space-y-2">
          {conviction_breakdown.map((b, i) => (
            <div key={i} className="flex items-center justify-between gap-4">
              <div>
                <span className="text-text text-sm">{b.signal}</span>
                <span className="text-text-muted text-xs ml-2">{b.detail}</span>
              </div>
              <span className={`font-mono text-sm shrink-0 ${b.pts > 0 ? tierColor : "text-text-muted"}`}>
                {b.pts > 0 ? `+${b.pts}` : "—"}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Themes */}
      <Section title={`Themes (${themes.length})`} delay={2}>
        <div className="space-y-5">
          {themes.map((t) => (
            <div key={t.theme_id} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-text">
                  {t.theme_label}
                </span>
                <Badge
                  label={t.exposure_type.replace(/_/g, " ")}
                  variant="neutral"
                />
                {t.driver_active && (
                  <Badge label="driver active" variant="green" />
                )}
              </div>
              <div className="flex items-center gap-3">
                <div className="confidence-bar flex-1">
                  <div
                    className="confidence-fill"
                    style={{ width: `${Math.round(t.confidence * 100)}%` }}
                  />
                </div>
                <span className="font-mono text-xs text-text-muted shrink-0">
                  {Math.round(t.confidence * 100)}%
                </span>
              </div>
              <p className="text-sm text-text-muted leading-relaxed">
                {t.reason_to_care}
              </p>
              {t.risk_note && (
                <p className="text-xs text-text-muted border-l-2 border-accent-dim pl-3 italic">
                  {t.risk_note}
                </p>
              )}
              <p className="font-mono text-xs text-text-muted">
                reviewed {t.last_reviewed} · driver:{" "}
                <span className="text-text">
                  {t.driver_id.replace(/_/g, " ")}
                </span>
              </p>
            </div>
          ))}
        </div>
      </Section>

      {/* Intelligence Feed */}
      {feed && (
        <Section title="Intelligence Feed" delay={3}>
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 items-center">
              <Badge
                label={feed.in_feed ? "In feed" : "Not in feed"}
                variant={feed.in_feed ? "green" : "neutral"}
              />
              <Badge label={feed.role.replace(/_/g, " ")} variant="orange" />
              <span className="font-mono text-xs text-text-muted">
                conf: {Math.round(feed.confidence * 100)}%
              </span>
            </div>
            <p className="text-sm text-text-muted leading-relaxed">
              {feed.reason_to_care}
            </p>
            {feed.risk_flags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {feed.risk_flags.map((f) => (
                  <Badge key={f} label={f} variant="red" />
                ))}
              </div>
            )}
            <p className="font-mono text-xs text-text-muted">
              theme:{" "}
              <span className="text-text">{feed.theme}</span>
              {" · "}driver:{" "}
              <span className="text-text">
                {feed.driver.replace(/_/g, " ")}
              </span>
            </p>
          </div>
        </Section>
      )}

      {/* Intelligence Feed — not in feed */}
      {!feed && (
        <Section title="Intelligence Feed" delay={3}>
          <div className="flex flex-wrap gap-2 items-center">
            <Badge label="Not in feed" variant="neutral" />
            <span className="font-mono text-xs text-text-muted">
              Not currently in the live intelligence candidate feed.
            </span>
          </div>
        </Section>
      )}

      {/* Options Flow */}
      {flow && (
        <Section title="Options Flow" delay={4}>
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 items-center">
              {flow.unusual_calls && (
                <Badge label="Unusual call flow" variant="green" />
              )}
              {flow.unusual_puts && (
                <Badge label="Unusual put flow" variant="red" />
              )}
              {!flow.unusual && (
                <Badge label="Normal flow" variant="neutral" />
              )}
              {!flow.oi_available && (
                <Badge label="OI not available" variant="neutral" />
              )}
            </div>
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: "Calls", val: flow.call_volume.toLocaleString() },
                { label: "Puts", val: flow.put_volume.toLocaleString() },
                {
                  label: "Call expansion",
                  val:
                    flow.call_expansion != null
                      ? `${flow.call_expansion.toFixed(1)}×`
                      : "—",
                },
              ].map(({ label, val }) => (
                <div key={label}>
                  <p className="font-mono text-xs text-text-muted mb-1">
                    {label}
                  </p>
                  <p className="font-mono text-lg text-text">{val}</p>
                </div>
              ))}
            </div>
            {flow.oi_note && (
              <p className="text-xs text-text-muted italic">{flow.oi_note}</p>
            )}
          </div>
        </Section>
      )}

      {/* Market Context */}
      <Section title="Active Macro Drivers" delay={5}>
        <div className="space-y-3">
          {ctx.active_drivers.length > 0 && (
            <div>
              <p className="font-mono text-xs text-text-muted mb-2">Active</p>
              <div className="flex flex-wrap gap-2">
                {ctx.active_drivers.map((d) => (
                  <Badge
                    key={d}
                    label={d.replace(/_/g, " ")}
                    variant="green"
                  />
                ))}
              </div>
            </div>
          )}
          {ctx.blocked_conditions.length > 0 && (
            <div>
              <p className="font-mono text-xs text-text-muted mb-2">Blocked</p>
              <div className="flex flex-wrap gap-2">
                {ctx.blocked_conditions.map((d) => (
                  <Badge
                    key={d}
                    label={d.replace(/_/g, " ")}
                    variant="red"
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </Section>

      {/* Freshness + disclaimer */}
      <div className="fade-up fade-up-5 pt-2 space-y-2 border-t border-border">
        <div className="flex flex-wrap gap-4">
          {Object.entries(data_freshness)
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <p key={k} className="font-mono text-xs text-text-muted">
                <span className="text-text">{k}:</span> {v}
              </p>
            ))}
        </div>
        {data.disclaimer && (
          <p className="text-xs text-text-muted leading-relaxed">
            {data.disclaimer}
          </p>
        )}
      </div>
    </div>
  );
}
