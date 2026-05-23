"use client";

import { useState } from "react";
import { Menu, X } from "lucide-react";
import { Logo } from "./Logo";

const NAV_LINKS = [
  { label: "Product", href: "#product" },
  { label: "How it works", href: "#how-it-works" },
  { label: "Intelligence", href: "#intelligence" },
  { label: "Private access", href: "#access" },
  { label: "For platforms", href: "#platforms" },
];

export function Nav() {
  const [open, setOpen] = useState(false);

  return (
    <header
      className="fixed top-0 left-0 right-0 z-50"
      style={{
        background: "rgba(7, 10, 18, 0.88)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex items-center justify-between h-16">
          <a href="/" aria-label="DECIFER Trading home">
            <Logo />
          </a>

          {/* Desktop nav */}
          <nav className="hidden lg:flex items-center gap-8">
            {NAV_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="text-sm font-medium transition-colors duration-150"
                style={{ color: "var(--text-2)" }}
                onMouseEnter={(e) =>
                  ((e.currentTarget as HTMLAnchorElement).style.color = "var(--text-1)")
                }
                onMouseLeave={(e) =>
                  ((e.currentTarget as HTMLAnchorElement).style.color = "var(--text-2)")
                }
              >
                {link.label}
              </a>
            ))}
          </nav>

          {/* Desktop CTAs */}
          <div className="hidden lg:flex items-center gap-3">
            <a
              href="https://mobile.decifertrading.com"
              className="text-sm font-medium px-4 py-2 rounded-lg transition-colors"
              style={{ color: "var(--text-2)" }}
              onMouseEnter={(e) =>
                ((e.currentTarget as HTMLAnchorElement).style.color = "var(--text-1)")
              }
              onMouseLeave={(e) =>
                ((e.currentTarget as HTMLAnchorElement).style.color = "var(--text-2)")
              }
            >
              Sign in
            </a>
            <a
              href="#access"
              className="text-sm font-semibold px-5 py-2 rounded-lg transition-all duration-150"
              style={{
                background: "var(--orange)",
                color: "#fff",
              }}
              onMouseEnter={(e) =>
                ((e.currentTarget as HTMLAnchorElement).style.background = "var(--orange-light)")
              }
              onMouseLeave={(e) =>
                ((e.currentTarget as HTMLAnchorElement).style.background = "var(--orange)")
              }
            >
              Request access
            </a>
          </div>

          {/* Mobile menu button */}
          <button
            className="lg:hidden p-2 rounded-lg"
            onClick={() => setOpen(!open)}
            aria-label={open ? "Close menu" : "Open menu"}
            style={{ color: "var(--text-2)" }}
          >
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {open && (
        <div
          className="lg:hidden px-6 pb-6 pt-2 flex flex-col gap-4"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          {NAV_LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              onClick={() => setOpen(false)}
              className="text-sm font-medium py-2"
              style={{ color: "var(--text-2)" }}
            >
              {link.label}
            </a>
          ))}
          <div className="flex flex-col gap-3 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
            <a
              href="https://mobile.decifertrading.com"
              className="text-sm font-medium text-center py-2 rounded-lg"
              style={{ color: "var(--text-2)", border: "1px solid var(--border)" }}
            >
              Sign in
            </a>
            <a
              href="#access"
              onClick={() => setOpen(false)}
              className="text-sm font-semibold text-center py-3 rounded-lg"
              style={{ background: "var(--orange)", color: "#fff" }}
            >
              Request access
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
