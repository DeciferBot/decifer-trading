const PRODUCT_FEATURES = [
  {
    id: "now",
    tab: "Now",
    headline: "What is happening right now?",
    body: "Market mood, major forces, sector movement, and what changed since the last scan. The context you need before anything else.",
    color: "#3b82f6",
  },
  {
    id: "why",
    tab: "Why",
    headline: "Why is it happening?",
    body: "Macro drivers, catalysts, themes, and the cause-and-effect behind the move. Not what, but why.",
    color: "#8b5cf6",
  },
  {
    id: "alpha",
    tab: "Alpha",
    headline: "Where could opportunity be forming?",
    body: "Symbols and setups under review, explained through reason-to-care, not raw score noise. Each candidate comes with context.",
    color: "#f97316",
  },
  {
    id: "portfolio",
    tab: "Portfolio",
    headline: "What do we hold and why?",
    body: "Position thesis, what changed today, risk notes, and what would change the view. Every holding explained in plain language.",
    color: "#10b981",
  },
  {
    id: "ask",
    tab: "Ask",
    headline: "Ask the market in plain English.",
    body: "Ask why a move happened, why a trade was blocked, what changed, or what deserves attention. Plain-language answers from structured context.",
    color: "#f59e0b",
  },
];

export function Product() {
  return (
    <section
      id="product"
      className="section"
      style={{ background: "var(--bg)" }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="max-w-2xl mb-16">
          <div
            style={{
              fontSize: "12px",
              fontWeight: 700,
              color: "var(--text-3)",
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              marginBottom: "1rem",
            }}
          >
            The product
          </div>
          <h2
            style={{
              fontSize: "clamp(1.75rem, 3vw, 2.75rem)",
              fontWeight: 800,
              letterSpacing: "-0.025em",
              lineHeight: 1.15,
              color: "var(--text-1)",
              marginBottom: "1.25rem",
            }}
          >
            Five ways DECIFER Trading helps you read the market.
          </h2>
          <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7 }}>
            Each view is a different lens on the same intelligence layer. Together they give you
            a complete read before you act.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {PRODUCT_FEATURES.map((feature, i) => (
            <div
              key={feature.id}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "16px",
                padding: "28px 26px",
                gridColumn: i === 4 ? "auto" : undefined,
              }}
            >
              {/* Tab label */}
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "4px 12px",
                  borderRadius: "100px",
                  background: `${feature.color}14`,
                  border: `1px solid ${feature.color}30`,
                  marginBottom: "18px",
                }}
              >
                <span style={{ fontSize: "11px", fontWeight: 700, color: feature.color, letterSpacing: "0.05em" }}>
                  {feature.tab}
                </span>
              </div>

              <h3
                style={{
                  fontSize: "1.05rem",
                  fontWeight: 700,
                  color: "var(--text-1)",
                  letterSpacing: "-0.01em",
                  lineHeight: 1.3,
                  marginBottom: "12px",
                }}
              >
                {feature.headline}
              </h3>

              <p style={{ fontSize: "0.9rem", color: "var(--text-2)", lineHeight: 1.7, margin: 0 }}>
                {feature.body}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
