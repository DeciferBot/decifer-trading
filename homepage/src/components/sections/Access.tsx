"use client";

import { useEffect, useState } from "react";

const INTEREST_OPTIONS = [
  { value: "personal", label: "Personal market intelligence" },
  { value: "demo", label: "Product demo" },
  { value: "platform", label: "Platform partnership" },
  { value: "other", label: "Other" },
];

const INVESTOR_TYPES = [
  { value: "retail", label: "Active retail investor" },
  { value: "professional", label: "Professional investor" },
  { value: "product", label: "Fintech product leader" },
  { value: "other", label: "Other" },
];

export function Access() {
  const [mounted, setMounted] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [investorType, setInvestorType] = useState("");
  const [interest, setInterest] = useState("");

  useEffect(() => { setMounted(true); }, []);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const name = data.get("name") as string;
    const email = data.get("email") as string;
    const investorType = data.get("investor_type") as string;
    const interest = data.get("interest") as string;

    const body = `Name: ${name}\nInvestor type: ${investorType}\nInterest: ${interest}`;
    const subject = encodeURIComponent("DECIFER Trading — Early access request");
    const bodyEncoded = encodeURIComponent(body);
    window.location.href = `mailto:chopraa@gmail.com?subject=${subject}&body=${bodyEncoded}&from=${encodeURIComponent(email)}`;
    setSubmitted(true);
  };

  return (
    <section
      id="access"
      className="section"
      style={{ background: "var(--bg)" }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="max-w-2xl mx-auto text-center mb-12">
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
            Private access
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
            Private access is opening soon.
          </h2>
          <p style={{ fontSize: "1.05rem", color: "var(--text-2)", lineHeight: 1.7 }}>
            DECIFER Trading is being released first as a private intelligence product. Early users
            will get access to a clearer market read, product previews, and future subscription options.
          </p>
        </div>

        {submitted ? (
          <div
            className="max-w-lg mx-auto text-center"
            style={{
              background: "var(--orange-bg)",
              border: "1px solid var(--orange-border)",
              borderRadius: "16px",
              padding: "40px 32px",
            }}
          >
            <div style={{ fontSize: "2rem", marginBottom: "16px" }}>
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#f97316" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ margin: "0 auto" }}>
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><path d="M22 4 12 14.01l-3-3" />
              </svg>
            </div>
            <h3 style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--text-1)", marginBottom: "10px" }}>
              Request sent.
            </h3>
            <p style={{ fontSize: "0.9rem", color: "var(--text-2)", lineHeight: 1.65 }}>
              Your email client should have opened with a draft. We will be in touch.
            </p>
          </div>
        ) : !mounted ? (
          <div style={{ height: "480px" }} aria-hidden="true" />
        ) : (
          <form
            onSubmit={handleSubmit}
            className="max-w-lg mx-auto"
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "20px",
              padding: "36px 32px",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
              <div>
                <label
                  htmlFor="name"
                  style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "var(--text-2)", marginBottom: "8px", letterSpacing: "0.04em", textTransform: "uppercase" }}
                >
                  Name
                </label>
                <input
                  id="name"
                  name="name"
                  type="text"
                  required
                  placeholder="Your name"
                  style={{
                    width: "100%",
                    padding: "12px 14px",
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "10px",
                    color: "var(--text-1)",
                    fontSize: "14px",
                    outline: "none",
                  }}
                />
              </div>

              <div>
                <label
                  htmlFor="email"
                  style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "var(--text-2)", marginBottom: "8px", letterSpacing: "0.04em", textTransform: "uppercase" }}
                >
                  Email
                </label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  required
                  placeholder="your@email.com"
                  style={{
                    width: "100%",
                    padding: "12px 14px",
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "10px",
                    color: "var(--text-1)",
                    fontSize: "14px",
                    outline: "none",
                  }}
                />
              </div>

              <div>
                <label
                  htmlFor="investor_type"
                  style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "var(--text-2)", marginBottom: "8px", letterSpacing: "0.04em", textTransform: "uppercase" }}
                >
                  Investor type
                </label>
                <select
                  id="investor_type"
                  name="investor_type"
                  required
                  value={investorType}
                  onChange={(e) => setInvestorType(e.target.value)}
                  suppressHydrationWarning
                  style={{
                    width: "100%",
                    padding: "12px 14px",
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "10px",
                    color: investorType ? "var(--text-1)" : "var(--text-3)",
                    fontSize: "14px",
                    outline: "none",
                    appearance: "none",
                  }}
                >
                  <option value="" disabled>Select one</option>
                  {INVESTOR_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="interest"
                  style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "var(--text-2)", marginBottom: "8px", letterSpacing: "0.04em", textTransform: "uppercase" }}
                >
                  Interest
                </label>
                <select
                  id="interest"
                  name="interest"
                  required
                  value={interest}
                  onChange={(e) => setInterest(e.target.value)}
                  suppressHydrationWarning
                  style={{
                    width: "100%",
                    padding: "12px 14px",
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "10px",
                    color: interest ? "var(--text-1)" : "var(--text-3)",
                    fontSize: "14px",
                    outline: "none",
                    appearance: "none",
                  }}
                >
                  <option value="" disabled>Select one</option>
                  {INTEREST_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>

              <button
                type="submit"
                style={{
                  width: "100%",
                  padding: "14px",
                  background: "var(--orange)",
                  color: "#fff",
                  fontWeight: 700,
                  fontSize: "0.95rem",
                  borderRadius: "10px",
                  border: "none",
                  cursor: "pointer",
                  marginTop: "4px",
                }}
              >
                Request early access
              </button>
            </div>

            <p style={{ fontSize: "0.775rem", color: "var(--text-3)", textAlign: "center", marginTop: "16px", lineHeight: 1.5 }}>
              No spam. No auto-enrolment. We will reach out directly.
            </p>
          </form>
        )}
      </div>
    </section>
  );
}
