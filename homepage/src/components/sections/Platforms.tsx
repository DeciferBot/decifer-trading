const PLATFORM_BENEFITS = [
  {
    title: "Premium intelligence product",
    body: "Add a differentiated market intelligence layer without rebuilding core infrastructure.",
  },
  {
    title: "Engagement with substance",
    body: "Give users a reason to return to your platform beyond charts and price alerts.",
  },
  {
    title: "Explainable decision workflow",
    body: "Every output is structured and auditable. Not a black-box chatbot.",
  },
  {
    title: "Audit-ready intelligence layer",
    body: "Immutable decision records and structured outputs support compliance-conscious environments.",
  },
];

export function Platforms() {
  return (
    <section
      id="platforms"
      className="section"
      style={{ background: "var(--bg-deep)" }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row gap-16 items-center">
          {/* Left: copy */}
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
              For platforms
            </div>
            <h2
              style={{
                fontSize: "clamp(1.75rem, 3vw, 2.5rem)",
                fontWeight: 800,
                letterSpacing: "-0.025em",
                lineHeight: 1.15,
                color: "var(--text-1)",
                marginBottom: "1.25rem",
              }}
            >
              Add decision intelligence without rebuilding from zero.
            </h2>
            <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7, marginBottom: "1.25rem" }}>
              DECIFER can operate as a decision intelligence layer for trading platforms that
              already have market access, information, and execution infrastructure.
            </p>
            <p style={{ fontSize: "0.95rem", color: "var(--text-2)", lineHeight: 1.7, marginBottom: "2rem" }}>
              The conversation starts under NDA. Technical architecture, integration paths, and
              partnership structure are discussed only after baseline alignment.
            </p>
            <a
              href="mailto:chopraa@gmail.com?subject=DECIFER Trading — NDA demo request"
              style={{
                display: "inline-flex",
                alignItems: "center",
                padding: "14px 28px",
                background: "var(--orange)",
                color: "#fff",
                fontWeight: 700,
                fontSize: "0.95rem",
                borderRadius: "10px",
                textDecoration: "none",
              }}
            >
              Request NDA demo
            </a>
          </div>

          {/* Right: benefit grid */}
          <div className="flex-1 grid grid-cols-1 gap-4 max-w-lg w-full">
            {PLATFORM_BENEFITS.map((benefit) => (
              <div
                key={benefit.title}
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "12px",
                  padding: "20px 22px",
                  display: "flex",
                  gap: "14px",
                  alignItems: "flex-start",
                }}
              >
                <div
                  style={{
                    flexShrink: 0,
                    marginTop: "3px",
                    width: "8px",
                    height: "8px",
                    borderRadius: "50%",
                    background: "var(--orange)",
                    opacity: 0.8,
                  }}
                />
                <div>
                  <div style={{ fontSize: "0.925rem", fontWeight: 700, color: "var(--text-1)", marginBottom: "6px" }}>
                    {benefit.title}
                  </div>
                  <div style={{ fontSize: "0.85rem", color: "var(--text-2)", lineHeight: 1.65 }}>
                    {benefit.body}
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
