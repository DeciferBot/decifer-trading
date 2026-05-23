const PREVIEW_CARDS = [
  {
    label: "Market mood",
    content: "Risk-on, but selective.",
    sub: "Growth appetite is leading. Breadth is not confirming a broad advance yet.",
    badge: null,
    color: "#3b82f6",
  },
  {
    label: "What changed",
    content: "Semiconductors leading. Defensives retreating.",
    sub: "The rotation began at the open and has held through the session without reversal.",
    badge: null,
    color: "#8b5cf6",
  },
  {
    label: "Why it matters",
    content: "The move is being driven by growth appetite, not broad market strength.",
    sub: "AI infrastructure names are being watched for follow-through.",
    badge: null,
    color: "#f97316",
  },
  {
    label: "Under review",
    content: "AI infrastructure names watching for follow-through.",
    sub: "Waiting for volume confirmation and sector breadth before a position view is formed.",
    badge: { text: "Under review", color: "rgba(245,158,11,0.10)", border: "rgba(245,158,11,0.25)", textColor: "#f59e0b" },
    color: "#f59e0b",
  },
  {
    label: "Blocked for now",
    content: "Extended moves not ready. Risk/reward is stretched.",
    sub: "Several high-conviction setups are being held back due to entry quality.",
    badge: { text: "Blocked", color: "rgba(100,116,139,0.10)", border: "rgba(100,116,139,0.25)", textColor: "#94a3b8" },
    color: "#64748b",
  },
  {
    label: "Ask DECIFER",
    content: '"Why are semiconductors moving today?"',
    sub: "Ask the market in plain English. Get a structured answer, not a summary.",
    badge: null,
    color: "#f97316",
    isAsk: true,
  },
];

export function Preview() {
  return (
    <section
      id="preview"
      className="section"
      style={{ background: "var(--bg-deep)" }}
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
            Product preview
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
            A market read you can actually use.
          </h2>
          <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7 }}>
            Example intelligence cards from DECIFER Trading. Content is illustrative.
            No real portfolio values, no live positions, no performance data.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {PREVIEW_CARDS.map((card) => (
            <div
              key={card.label}
              style={{
                background: card.isAsk ? "var(--orange-bg)" : "var(--surface)",
                border: `1px solid ${card.isAsk ? "var(--orange-border)" : "var(--border)"}`,
                borderRadius: "14px",
                padding: "24px 22px",
              }}
            >
              <div
                style={{
                  fontSize: "10px",
                  fontWeight: 700,
                  color: card.isAsk ? "var(--orange)" : "var(--text-3)",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginBottom: "12px",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                {card.isAsk && (
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
                  </svg>
                )}
                {card.label}
              </div>
              <p
                style={{
                  fontSize: "0.95rem",
                  fontWeight: card.isAsk ? 600 : 700,
                  color: card.isAsk ? "var(--orange-light)" : "var(--text-1)",
                  lineHeight: 1.4,
                  marginBottom: card.sub ? "10px" : 0,
                  fontStyle: card.isAsk ? "italic" : "normal",
                }}
              >
                {card.content}
              </p>
              {card.sub && (
                <p style={{ fontSize: "0.825rem", color: "var(--text-2)", lineHeight: 1.6, marginBottom: card.badge ? "12px" : 0 }}>
                  {card.sub}
                </p>
              )}
              {card.badge && (
                <span
                  style={{
                    display: "inline-flex",
                    padding: "3px 10px",
                    borderRadius: "100px",
                    fontSize: "10px",
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    background: card.badge.color,
                    color: card.badge.textColor,
                    border: `1px solid ${card.badge.border}`,
                  }}
                >
                  {card.badge.text}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
