import { ProductMockup } from "../ProductMockup";

export function Hero() {
  return (
    <section
      className="bg-grid relative overflow-hidden"
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        paddingTop: "96px",
        paddingBottom: "72px",
        background: "var(--bg-deep)",
      }}
    >
      {/* Radial glow — top center */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          top: 0,
          left: "50%",
          transform: "translateX(-50%)",
          width: "900px",
          height: "600px",
          background: "radial-gradient(ellipse at 50% 0%, rgba(249,115,22,0.07) 0%, transparent 70%)",
          pointerEvents: "none",
        }}
      />

      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row items-center gap-16 lg:gap-20">
          {/* Left: copy */}
          <div className="flex-1 text-center lg:text-left max-w-2xl lg:max-w-none">
            {/* Category label */}
            <div
              className="inline-flex items-center gap-2 mb-8"
              style={{
                padding: "5px 14px",
                borderRadius: "100px",
                background: "var(--orange-bg)",
                border: "1px solid var(--orange-border)",
              }}
            >
              <span
                style={{
                  width: "6px",
                  height: "6px",
                  borderRadius: "50%",
                  background: "var(--orange)",
                  display: "inline-block",
                }}
              />
              <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--orange)", letterSpacing: "0.04em" }}>
                Market decision intelligence
              </span>
            </div>

            <h1
              style={{
                fontSize: "clamp(2.5rem, 5vw, 4.25rem)",
                fontWeight: 800,
                lineHeight: 1.08,
                letterSpacing: "-0.03em",
                color: "var(--text-1)",
                marginBottom: "1.5rem",
              }}
            >
              Make sense of the market{" "}
              <span style={{ color: "var(--orange)" }}>before</span>{" "}
              you make a move.
            </h1>

            <p
              style={{
                fontSize: "clamp(1rem, 1.4vw, 1.2rem)",
                color: "var(--text-2)",
                lineHeight: 1.7,
                marginBottom: "0.75rem",
                maxWidth: "540px",
              }}
            >
              DECIFER Trading turns market data, catalysts, macro forces, and portfolio context
              into plain-language intelligence for active investors.
            </p>

            <p
              style={{
                fontSize: "0.875rem",
                color: "var(--text-3)",
                lineHeight: 1.6,
                marginBottom: "2.5rem",
                maxWidth: "480px",
              }}
            >
              Built around structured market context and validated signals. Not raw AI commentary.
            </p>

            {/* CTAs */}
            <div className="flex flex-wrap gap-3 justify-center lg:justify-start">
              <a
                href="#access"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "14px 28px",
                  background: "var(--orange)",
                  color: "#fff",
                  fontWeight: 700,
                  fontSize: "0.95rem",
                  borderRadius: "10px",
                  transition: "background 0.15s",
                  textDecoration: "none",
                }}
              >
                Request early access
              </a>
              <a
                href="#preview"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "14px 28px",
                  background: "var(--surface)",
                  color: "var(--text-1)",
                  fontWeight: 600,
                  fontSize: "0.95rem",
                  borderRadius: "10px",
                  border: "1px solid var(--border-strong)",
                  transition: "border-color 0.15s",
                  textDecoration: "none",
                }}
              >
                View product preview
              </a>
              <a
                href="#platforms"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "14px 28px",
                  color: "var(--text-2)",
                  fontWeight: 600,
                  fontSize: "0.95rem",
                  borderRadius: "10px",
                  transition: "color 0.15s",
                  textDecoration: "none",
                }}
              >
                Request NDA demo &rarr;
              </a>
            </div>

            {/* Trust note */}
            <p
              style={{
                marginTop: "2.5rem",
                fontSize: "0.75rem",
                color: "var(--text-3)",
                lineHeight: 1.5,
                maxWidth: "440px",
              }}
            >
              Not investment advice. No performance promise. DECIFER Trading provides market
              intelligence and decision-support context only.
            </p>
          </div>

          {/* Right: product mockup */}
          <div
            className="flex-shrink-0 flex justify-center lg:justify-end"
            style={{ width: "100%", maxWidth: "380px" }}
          >
            <ProductMockup />
          </div>
        </div>
      </div>
    </section>
  );
}
