import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Symbol Intelligence — Decifer",
  description: "Theme membership, intelligence feed status, and macro driver context for any tracked symbol.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-text antialiased">
        <header className="border-b border-border px-6 py-4 flex items-center gap-3">
          <span className="font-mono text-xs text-accent tracking-widest uppercase">Decifer</span>
          <span className="text-border">|</span>
          <span className="font-mono text-xs text-text-muted tracking-wider">Symbol Intelligence</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
