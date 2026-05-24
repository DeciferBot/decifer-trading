import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DECIFER Trading — Market Decision Intelligence",
  description:
    "DECIFER Trading turns market data, catalysts, macro forces, and portfolio context into plain-language intelligence for active investors. Not investment advice.",
  keywords: [
    "market intelligence",
    "decision support",
    "active investor tools",
    "market context",
    "research companion",
  ],
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
    apple: "/apple-touch-icon.svg",
  },
  openGraph: {
    title: "DECIFER Trading — Market Decision Intelligence",
    description:
      "The missing decision intelligence layer between market noise and investor action.",
    type: "website",
    siteName: "DECIFER Trading",
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#070a12",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
