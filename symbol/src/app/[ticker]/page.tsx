import { headers } from "next/headers";
import Link from "next/link";

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
  call_expansion: number;
  unusual: boolean;
  oi_available: boolean;
  oi_note: string;
}
interface MarketContext {
  active_drivers: string[];
  blocked_conditions: string[];
  drivers_mode: string;
}
interface SymbolData {
  symbol: string;
  themes: Theme[];
  intelligence_feed: IntelFeed;
  options_flow: OptionsFlow;
  market_context: MarketContext;
  data_freshness: Record<string, string>;
  disclaimer: string;
}

/* ---------- fetch ---------- */
async function fetchSymbol(ticker: string): Promise<{ data?: SymbolData; notFound?: boolean; error?: string }> {
  const host = headers().get("host") ?? "localhost:3000";
  const proto = process.env.NODE_ENV === "production" ? "https" : "http";
  const url = `${proto}://${host}/api/symbol/${encodeURIComponent(ticker.toUpperCase())}`;

  try {
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 404) return { notFound: true };
    if (!res.ok) return { error: "upstream" };
    return { data: await res.json() };
  } catch {
    return { error: "fetch_failed" };
  }
}

/* ---------- helpers ---------- */
function Badge({ label, variant = "neutral" }: { label: string; variant?: "green" | "orange" | "red" | "neutral" }) {
  const cls: Record<string, string> = {
    green:   "bg-emerald-950 text-emerald-400 border-emerald-800",
    orange:  "bg-orange-950 text-accent border-orange-800",
    red:     "bg-red-950 text-red-400 border-red-800",
    neutral: "bg-surface-2 text-text-muted border-border",
  };
  return (
    <span className={`inline-block border rounded-sm px-2 py-0.5 text-xs font-mono tracking-wide ${cls[variant]}`}>
      {label}
    </span>
  );
}

function Section({ title, children, delay }: { title: string; children: React.ReactNode; delay: number }) {
  return (
    <div className={`border border-border rounded-sm p-5 fade-up fade-up-${delay}`}>
      <p className="font-mono text-xs text-text-muted tracking-widest uppercase mb-4">{title}</p>
      {children}
    </div>
  );
}

/* ---------- page ---------- */
export default async function TickerPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const { data, notFound, error } = await fetchSymbol(ticker);

  /* 404 state */
  if (notFound || !data) {
    return (
      <div className="max-w-xl mx-auto px-4 py-20 fade-up">
        <p className="font-mono text-xs text-text-muted tracking-widest uppercase mb-3">Not found</p>
        <h1 className="text-3xl font-mono text-text mb-3">
          <span className="text-accent">{ticker}</span>
        </h1>
        <p className="text-text-muted text-sm mb-1">
          {ticker} is not in our intelligence graph.
        </p>
        <p className="text-text-muted text-sm mb-8">We track 125 curated symbols.</p>
        {error && <p className="text-red-500 text-xs font-mono mb-6">Error: {error}</p>}
        <Link href="/" className="font-mono text-xs text-accent tracking-widest uppercase border border-accent rounded-sm px-4 py-2 hover:bg-accent hover:text-background transition-colors">
          Search again
        </Link>
      </div>
    );
  }

  const { themes, intelligence_feed: feed, options_flow: flow, market_context: ctx, data_freshness } = data;
  const primaryTheme = themes[0];

  return (
    <div className="max-w-2xl mx-auto px-4 py-10 space-y-5">

      {/* Header */}
      <div className="fade-up fade-up-1">
        <div className="flex items-start justify-between gap-4 mb-1">
          <h1
            className="text-6xl leading-none"
            style={{ fontFamily: "'Instrument Serif', serif" }}
          >
            <span className="text-accent">{data.symbol}</span>
          </h1>
          <Link href="/" className="font-mono text-xs text-text-muted border border-border rounded-sm px-3 py-1.5 hover:border-accent hover:text-accent transition-colors mt-2 shrink-0">
            ← New
          </Link>
        </div>
        {primaryTheme && (
          <p className="text-text-muted font-mono text-xs tracking-wide mt-2">
            in{" "}
            <span className="text-text">{primaryTheme.theme_label}</span>
            {" · "}
            <span>{primaryTheme.bucket_label}</span>
          </p>
        )}
        <p className="font-mono text-xs text-text-muted mt-1">
          Drivers: {ctx.drivers_mode.replace(/_/g, " ")}
        </p>
      </div>

      {/* Themes */}
      <Section title={`Themes (${themes.length})`} delay={2}>
        <div className="space-y-5">
          {themes.map((t) => (
            <div key={t.theme_id} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-text">{t.theme_label}</span>
                <Badge label={t.exposure_type.replace(/_/g, " ")} variant="neutral" />
                {t.driver_active && <Badge label="driver active" variant="green" />}
              </div>
              {/* confidence bar */}
              <div className="flex items-center gap-3">
                <div className="confidence-bar flex-1">
                  <div className="confidence-fill" style={{ width: `${Math.round(t.confidence * 100)}%` }} />
                </div>
                <span className="font-mono text-xs text-text-muted shrink-0">
                  {Math.round(t.confidence * 100)}%
                </span>
              </div>
              <p className="text-sm text-text-muted leading-relaxed">{t.reason_to_care}</p>
              {t.risk_note && (
                <p className="text-xs text-text-muted border-l-2 border-accent-dim pl-3 italic">
                  {t.risk_note}
                </p>
              )}
              <p className="font-mono text-xs text-text-muted">
                reviewed {t.last_reviewed} · driver: <span className="text-text">{t.driver_id.replace(/_/g, " ")}</span>
              </p>
            </div>
          ))}
        </div>
      </Section>

      {/* Intelligence Feed */}
      <Section title="Intelligence Feed" delay={3}>
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2 items-center">
            <Badge label={feed.in_feed ? "In feed" : "Not in feed"} variant={feed.in_feed ? "green" : "neutral"} />
            <Badge label={feed.role.replace(/_/g, " ")} variant="orange" />
            <span className="font-mono text-xs text-text-muted">
              conf: {Math.round(feed.confidence * 100)}%
            </span>
          </div>
          <p className="text-sm text-text-muted leading-relaxed">{feed.reason_to_care}</p>
          {feed.risk_flags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {feed.risk_flags.map((f) => (
                <Badge key={f} label={f} variant="red" />
              ))}
            </div>
          )}
          <p className="font-mono text-xs text-text-muted">
            theme: <span className="text-text">{feed.theme}</span>
            {" · "}driver: <span className="text-text">{feed.driver.replace(/_/g, " ")}</span>
          </p>
        </div>
      </Section>

      {/* Options Flow */}
      <Section title="Options Flow" delay={4}>
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2 items-center">
            {flow.unusual && <Badge label="Unusual flow" variant="orange" />}
            {!flow.oi_available && <Badge label="OI not available" variant="neutral" />}
          </div>
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: "Calls", val: flow.call_volume.toLocaleString() },
              { label: "Puts",  val: flow.put_volume.toLocaleString() },
              { label: "Call expansion", val: `${flow.call_expansion.toFixed(1)}×` },
            ].map(({ label, val }) => (
              <div key={label}>
                <p className="font-mono text-xs text-text-muted mb-1">{label}</p>
                <p className="font-mono text-lg text-text">{val}</p>
              </div>
            ))}
          </div>
          {flow.oi_note && (
            <p className="text-xs text-text-muted italic">{flow.oi_note}</p>
          )}
        </div>
      </Section>

      {/* Market Context */}
      <Section title="Active Macro Drivers" delay={5}>
        <div className="space-y-3">
          {ctx.active_drivers.length > 0 && (
            <div>
              <p className="font-mono text-xs text-text-muted mb-2">Active</p>
              <div className="flex flex-wrap gap-2">
                {ctx.active_drivers.map((d) => (
                  <Badge key={d} label={d.replace(/_/g, " ")} variant="green" />
                ))}
              </div>
            </div>
          )}
          {ctx.blocked_conditions.length > 0 && (
            <div>
              <p className="font-mono text-xs text-text-muted mb-2">Blocked</p>
              <div className="flex flex-wrap gap-2">
                {ctx.blocked_conditions.map((d) => (
                  <Badge key={d} label={d.replace(/_/g, " ")} variant="red" />
                ))}
              </div>
            </div>
          )}
        </div>
      </Section>

      {/* Freshness + disclaimer */}
      <div className="fade-up fade-up-5 pt-2 space-y-2 border-t border-border">
        <div className="flex flex-wrap gap-4">
          {Object.entries(data_freshness).map(([k, v]) => (
            <p key={k} className="font-mono text-xs text-text-muted">
              <span className="text-text">{k}:</span> {v}
            </p>
          ))}
        </div>
        {data.disclaimer && (
          <p className="text-xs text-text-muted leading-relaxed">{data.disclaimer}</p>
        )}
      </div>
    </div>
  );
}
