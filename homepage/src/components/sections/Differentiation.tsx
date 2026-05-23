const POINTS = [
  {
    title: "Reason-first, not score-first",
    body: "Every candidate comes with a reason to care. Not a number you have to interpret.",
  },
  {
    title: "Plain English, not terminal language",
    body: "Written for the investor making a decision, not the developer who built the system.",
  },
  {
    title: "Structured context, not headline reaction",
    body: "DECIFER organises market context before any AI interpretation takes place.",
  },
  {
    title: "Read-only intelligence, not impulsive execution",
    body: "DECIFER informs your decision. You remain in control of every action.",
  },
  {
    title: "Validated architecture, not chatbot theatre",
    body: "Built on 10 orthogonal signal dimensions and a production-tested decision layer.",
  },
  {
    title: "Designed around what changed",
    body: "Every session answers the same three questions: what changed, why it matters, and what could move next.",
  },
];

export function Differentiation() {
  return (
    <section
      className="section"
      style={{ background: "var(--bg-deep)" }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row gap-16">
          {/* Left: heading */}
          <div className="flex-shrink-0 lg:w-80">
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
              Why DECIFER
            </div>
            <h2
              style={{
                fontSize: "clamp(1.75rem, 3vw, 2.5rem)",
                fontWeight: 800,
                letterSpacing: "-0.025em",
                lineHeight: 1.15,
                color: "var(--text-1)",
                marginBottom: "1.5rem",
              }}
            >
              Built to reduce noise, not add another dashboard.
            </h2>
            <p style={{ fontSize: "0.95rem", color: "var(--text-2)", lineHeight: 1.7 }}>
              The goal is not more information. The goal is a clearer read before you act.
            </p>
          </div>

          {/* Right: points */}
          <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-6">
            {POINTS.map((point) => (
              <div key={point.title} style={{ display: "flex", gap: "16px" }}>
                <div
                  style={{
                    flexShrink: 0,
                    marginTop: "3px",
                    width: "20px",
                    height: "20px",
                    borderRadius: "50%",
                    background: "var(--orange-bg)",
                    border: "1px solid var(--orange-border)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <div
                    style={{
                      width: "6px",
                      height: "6px",
                      borderRadius: "50%",
                      background: "var(--orange)",
                    }}
                  />
                </div>
                <div>
                  <div
                    style={{
                      fontSize: "0.95rem",
                      fontWeight: 700,
                      color: "var(--text-1)",
                      marginBottom: "6px",
                      letterSpacing: "-0.01em",
                    }}
                  >
                    {point.title}
                  </div>
                  <div style={{ fontSize: "0.875rem", color: "var(--text-2)", lineHeight: 1.65 }}>
                    {point.body}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
