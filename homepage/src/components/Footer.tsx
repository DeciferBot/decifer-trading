import { Logo } from "./Logo";

const PRODUCT_LINKS = [
  { label: "Product", href: "#product" },
  { label: "How it works", href: "#how-it-works" },
  { label: "Intelligence", href: "#intelligence" },
  { label: "For platforms", href: "#platforms" },
  { label: "Request access", href: "#access" },
];

const FAMILY_LINKS = [
  { label: "DECIFER Trading", href: "/" },
  { label: "DECIFER Learning", href: "https://deciferlearning.com", external: true },
  { label: "DECIFER (parent)", href: "https://decifer.io", external: true },
];

const ACCOUNT_LINKS = [
  { label: "Sign in", href: "https://mobile.decifertrading.com", external: true },
];

export function Footer() {
  return (
    <footer
      style={{
        background: "var(--bg-deep)",
        borderTop: "1px solid var(--border)",
        paddingTop: "56px",
        paddingBottom: "40px",
      }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row gap-12 mb-12">
          {/* Brand column */}
          <div className="flex-shrink-0 lg:w-64">
            <Logo size="md" />
            <p
              style={{
                marginTop: "16px",
                fontSize: "0.875rem",
                color: "var(--text-3)",
                lineHeight: 1.65,
                maxWidth: "240px",
              }}
            >
              Market decision intelligence for active investors.
            </p>
          </div>

          {/* Links */}
          <div className="flex flex-wrap gap-12 flex-1">
            <div>
              <div
                style={{
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-3)",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginBottom: "16px",
                }}
              >
                Product
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {PRODUCT_LINKS.map((link) => (
                  <a
                    key={link.label}
                    href={link.href}
                    style={{ fontSize: "0.875rem", color: "var(--text-2)", textDecoration: "none" }}
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            </div>

            <div>
              <div
                style={{
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-3)",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginBottom: "16px",
                }}
              >
                DECIFER Family
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {FAMILY_LINKS.map((link) => (
                  <a
                    key={link.label}
                    href={link.href}
                    target={link.external ? "_blank" : undefined}
                    rel={link.external ? "noopener noreferrer" : undefined}
                    style={{ fontSize: "0.875rem", color: "var(--text-2)", textDecoration: "none" }}
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            </div>

            <div>
              <div
                style={{
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--text-3)",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  marginBottom: "16px",
                }}
              >
                Account
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {ACCOUNT_LINKS.map((link) => (
                  <a
                    key={link.label}
                    href={link.href}
                    target={link.external ? "_blank" : undefined}
                    rel={link.external ? "noopener noreferrer" : undefined}
                    style={{ fontSize: "0.875rem", color: "var(--text-2)", textDecoration: "none" }}
                  >
                    {link.label}
                  </a>
                ))}
                <a
                  href="mailto:chopraa@gmail.com?subject=DECIFER Trading — contact"
                  style={{ fontSize: "0.875rem", color: "var(--text-2)", textDecoration: "none" }}
                >
                  Contact
                </a>
              </div>
            </div>
          </div>
        </div>

        {/* Legal */}
        <div
          style={{
            paddingTop: "28px",
            borderTop: "1px solid var(--border)",
          }}
        >
          <p
            style={{
              fontSize: "0.775rem",
              color: "var(--text-3)",
              lineHeight: 1.65,
              maxWidth: "680px",
              marginBottom: "14px",
            }}
          >
            DECIFER Trading provides market intelligence and decision-support context. It does not
            provide investment advice, trading recommendations, portfolio management, brokerage,
            order services, or guaranteed outcomes. All content is for informational purposes only.
            Past performance in research environments does not predict future results. Legal and
            regulatory review is required before any commercial launch in any jurisdiction.
          </p>
          <p style={{ fontSize: "0.75rem", color: "var(--text-3)" }}>
            &copy; {new Date().getFullYear()} DECIFER. All rights reserved.
          </p>
        </div>
      </div>
    </footer>
  );
}
