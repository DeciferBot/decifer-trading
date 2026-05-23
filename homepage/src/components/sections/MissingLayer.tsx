const INPUTS = [
  "Market data",
  "News and catalysts",
  "Macro forces",
  "Sector movement",
  "Portfolio context",
];

const OUTPUTS = [
  "Plain-language market read",
  "Opportunity context",
  "Risk explanation",
  "What changed and why",
];

function Arrow() {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "12px 0" }}>
      <div style={{ width: "1px", height: "24px", background: "linear-gradient(var(--border), var(--orange))" }} />
      <svg width="14" height="10" viewBox="0 0 14 10" fill="none" style={{ marginTop: "-1px" }}>
        <path d="M7 10L0 0h14L7 10z" fill="var(--orange)" fillOpacity={0.7} />
      </svg>
    </div>
  );
}

export function MissingLayer() {
  return (
    <section
      id="intelligence"
      className="section"
      style={{ background: "var(--bg-deep)" }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row gap-16 items-start">
          {/* Left: description */}
          <div className="flex-1 max-w-xl">
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
              The missing layer
            </div>
            <h2
              style={{
                fontSize: "clamp(1.75rem, 3vw, 2.75rem)",
                fontWeight: 800,
                letterSpacing: "-0.025em",
                lineHeight: 1.15,
                color: "var(--text-1)",
                marginBottom: "1.5rem",
              }}
            >
              The missing layer is decision intelligence.
            </h2>
            <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7, marginBottom: "1.25rem" }}>
              DECIFER Trading sits between market information and investor action. It turns live
              market context into a structured read of conditions, catalysts, themes, opportunity
              readiness, and risk.
            </p>
            <p style={{ fontSize: "0.95rem", color: "var(--text-2)", lineHeight: 1.7 }}>
              DECIFER does not replace the investor. It gives the investor a clearer intelligence
              layer before they act.
            </p>
          </div>

          {/* Right: flow diagram */}
          <div
            className="flex-shrink-0 w-full lg:w-auto"
            style={{ maxWidth: "380px", margin: "0 auto" }}
          >
            {/* Inputs */}
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "16px 20px",
              }}
            >
              <div style={{ fontSize: "10px", fontWeight: 700, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "12px" }}>
                Input sources
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {INPUTS.map((item) => (
                  <div key={item} style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: "var(--border-strong)", flexShrink: 0 }} />
                    <span style={{ fontSize: "13px", color: "var(--text-2)" }}>{item}</span>
                  </div>
                ))}
              </div>
            </div>

            <Arrow />

            {/* DECIFER layer */}
            <div
              style={{
                background: "var(--orange-bg)",
                border: "1px solid var(--orange-border)",
                borderRadius: "12px",
                padding: "18px 20px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--orange)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: "6px" }}>
                DECIFER
              </div>
              <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-1)" }}>
                Decision intelligence layer
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-2)", marginTop: "4px" }}>
                Structured context. Validated signals. Plain language.
              </div>
            </div>

            <Arrow />

            {/* Outputs */}
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "16px 20px",
              }}
            >
              <div style={{ fontSize: "10px", fontWeight: 700, color: "var(--text-3)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: "12px" }}>
                Investor intelligence
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {OUTPUTS.map((item) => (
                  <div key={item} style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: "var(--orange)", flexShrink: 0, opacity: 0.7 }} />
                    <span style={{ fontSize: "13px", color: "var(--text-2)" }}>{item}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
