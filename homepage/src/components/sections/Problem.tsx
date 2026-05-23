const PROBLEM_CARDS = [
  {
    title: "Access is solved.",
    body: "Every trader has charts, news feeds, screeners, and price alerts. Information is a commodity.",
    icon: "✓",
    state: "solved",
  },
  {
    title: "Information is saturated.",
    body: "Analyst commentary, social feeds, earnings summaries, and AI digests. There is more information now than any investor can process.",
    icon: "✓",
    state: "solved",
  },
  {
    title: "Execution is fast and cheap.",
    body: "Commission-free trading and fractional shares have removed the friction of acting. Execution is no longer the barrier.",
    icon: "✓",
    state: "solved",
  },
  {
    title: "Judgement is still missing.",
    body: "Knowing what matters right now, and why, in plain language you can act on. That layer has never been built.",
    icon: "→",
    state: "missing",
  },
];

export function Problem() {
  return (
    <section
      id="how-it-works"
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
            The problem
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
            More data did not create better judgement.
          </h2>
          <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7 }}>
            Modern traders have access to more information than any previous generation. The problem
            is no longer access. The problem is knowing what matters now.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
          {PROBLEM_CARDS.map((card) => (
            <div
              key={card.title}
              style={{
                background: card.state === "missing" ? "var(--orange-bg)" : "var(--surface)",
                border: `1px solid ${card.state === "missing" ? "var(--orange-border)" : "var(--border)"}`,
                borderRadius: "14px",
                padding: "28px 24px",
              }}
            >
              <div
                style={{
                  width: "34px",
                  height: "34px",
                  borderRadius: "8px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: card.state === "missing" ? "rgba(249,115,22,0.15)" : "var(--surface-2)",
                  color: card.state === "missing" ? "var(--orange)" : "var(--text-3)",
                  fontWeight: 800,
                  fontSize: "16px",
                  marginBottom: "18px",
                }}
              >
                {card.icon}
              </div>
              <h3
                style={{
                  fontSize: "1rem",
                  fontWeight: 700,
                  color: card.state === "missing" ? "var(--orange)" : "var(--text-2)",
                  marginBottom: "10px",
                  letterSpacing: "-0.01em",
                }}
              >
                {card.title}
              </h3>
              <p style={{ fontSize: "0.875rem", color: "var(--text-2)", lineHeight: 1.65 }}>
                {card.body}
              </p>
            </div>
          ))}
        </div>

        <div
          style={{
            marginTop: "3rem",
            padding: "22px 28px",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "12px",
            maxWidth: "680px",
          }}
        >
          <p style={{ fontSize: "0.95rem", color: "var(--text-2)", lineHeight: 1.7, margin: 0 }}>
            Most trading tools add more information. DECIFER Trading is designed to add{" "}
            <span style={{ color: "var(--text-1)", fontWeight: 600 }}>structure</span>.
          </p>
        </div>
      </div>
    </section>
  );
}
