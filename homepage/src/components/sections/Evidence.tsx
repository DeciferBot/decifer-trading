const STATS = [
  { value: "10", label: "Orthogonal signal dimensions", sub: "Each measuring something fundamentally different" },
  { value: "400+", label: "Paper trades on real market data", sub: "Internal validation across multiple market regimes" },
  { value: "3,031", label: "Automated tests", sub: "Covering signal logic, execution paths, and data contracts" },
  { value: "1", label: "Synthesiser model", sub: "One structured Apex call per scan cycle, not a chatbot chain" },
];

const PROOF_POINTS = [
  "10 orthogonal signal dimensions — each measuring something different",
  "Structured market context built before any AI interpretation",
  "Live market sensors across 11 symbol inputs",
  "Theme intelligence across 23 tracked thematic categories",
  "Validated decision architecture with immutable audit trail",
  "Production paper-trading validation on real market data",
];

export function Evidence() {
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
            Built from the ground up
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
            Built from a production-grade decision architecture.
          </h2>
          <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7 }}>
            DECIFER Trading is not a prototype. It is a validated, structured intelligence system
            developed on real market data.
          </p>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-12">
          {STATS.map((stat) => (
            <div
              key={stat.value}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "14px",
                padding: "24px 22px",
              }}
            >
              <div
                style={{
                  fontSize: "2.5rem",
                  fontWeight: 800,
                  color: "var(--orange)",
                  letterSpacing: "-0.04em",
                  lineHeight: 1,
                  marginBottom: "10px",
                }}
              >
                {stat.value}
              </div>
              <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "var(--text-1)", marginBottom: "6px", lineHeight: 1.3 }}>
                {stat.label}
              </div>
              <div style={{ fontSize: "0.775rem", color: "var(--text-3)", lineHeight: 1.5 }}>
                {stat.sub}
              </div>
            </div>
          ))}
        </div>

        {/* Proof points */}
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "16px",
            padding: "32px 28px",
          }}
        >
          <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--text-2)", marginBottom: "20px" }}>
            Architecture proof points
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-y-4 gap-x-8">
            {PROOF_POINTS.map((point) => (
              <div key={point} style={{ display: "flex", gap: "12px", alignItems: "flex-start" }}>
                <div
                  style={{
                    flexShrink: 0,
                    marginTop: "5px",
                    width: "6px",
                    height: "6px",
                    borderRadius: "50%",
                    background: "var(--orange)",
                  }}
                />
                <span style={{ fontSize: "0.875rem", color: "var(--text-2)", lineHeight: 1.6 }}>{point}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Legal note */}
        <div
          style={{
            marginTop: "2rem",
            padding: "16px 22px",
            background: "rgba(26,40,64,0.4)",
            border: "1px solid var(--border)",
            borderRadius: "10px",
          }}
        >
          <p style={{ fontSize: "0.775rem", color: "var(--text-3)", lineHeight: 1.6, margin: 0 }}>
            Validation results are based on internal paper-trading and research data. They are not
            investment advice, not a trading recommendation, and not a performance representation.
            Past paper-trading results do not guarantee future outcomes.
          </p>
        </div>
      </div>
    </section>
  );
}
