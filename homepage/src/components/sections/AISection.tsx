const PILLARS = [
  {
    label: "Raw AI commentary",
    description:
      "A language model reading unstructured headlines can produce confident-sounding output that is factually wrong, contextually thin, and impossible to verify.",
    state: "problem",
  },
  {
    label: "Structured context first",
    description:
      "DECIFER organises market data, catalysts, and portfolio state into a defined intelligence layer before any AI interpretation takes place. Structure reduces noise.",
    state: "solution",
  },
  {
    label: "Defined outputs",
    description:
      "DECIFER produces structured outputs with defined fields. Not free-form commentary. This makes the system more explainable and the investor more accountable.",
    state: "solution",
  },
];

export function AISection() {
  return (
    <section
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
            On AI
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
            AI alone is not decision intelligence.
          </h2>
          <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7 }}>
            DECIFER Trading is designed around structured context first. AI interpretation
            happens only after market context has been organised into a cleaner decision layer.
            Human users still decide.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {PILLARS.map((pillar) => (
            <div
              key={pillar.label}
              style={{
                background: pillar.state === "problem" ? "rgba(244, 63, 94, 0.06)" : "var(--surface)",
                border: `1px solid ${pillar.state === "problem" ? "rgba(244,63,94,0.18)" : "var(--border)"}`,
                borderRadius: "14px",
                padding: "28px 24px",
              }}
            >
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "3px 10px",
                  borderRadius: "100px",
                  marginBottom: "18px",
                  background: pillar.state === "problem" ? "rgba(244,63,94,0.12)" : "rgba(16,185,129,0.10)",
                  border: `1px solid ${pillar.state === "problem" ? "rgba(244,63,94,0.25)" : "rgba(16,185,129,0.25)"}`,
                }}
              >
                <span
                  style={{
                    fontSize: "10px",
                    fontWeight: 700,
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                    color: pillar.state === "problem" ? "#f43f5e" : "#10b981",
                  }}
                >
                  {pillar.state === "problem" ? "The problem" : "DECIFER approach"}
                </span>
              </div>
              <h3
                style={{
                  fontSize: "1rem",
                  fontWeight: 700,
                  color: "var(--text-1)",
                  letterSpacing: "-0.01em",
                  marginBottom: "12px",
                  lineHeight: 1.3,
                }}
              >
                {pillar.label}
              </h3>
              <p style={{ fontSize: "0.875rem", color: "var(--text-2)", lineHeight: 1.65, margin: 0 }}>
                {pillar.description}
              </p>
            </div>
          ))}
        </div>

        <div
          style={{
            marginTop: "2.5rem",
            padding: "16px 24px",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "10px",
            maxWidth: "600px",
          }}
        >
          <p style={{ fontSize: "0.825rem", color: "var(--text-3)", lineHeight: 1.6, margin: 0 }}>
            DECIFER Trading is designed to reduce hallucination risk through structured context.
            It does not claim to eliminate it. Human judgement remains the final layer.
          </p>
        </div>
      </div>
    </section>
  );
}
