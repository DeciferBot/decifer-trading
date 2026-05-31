import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://options.decifertrading.com"),
  title: "Options Flow — Decifer",
  description: "Real-time unusual options flow — 1,000 symbol universe.",
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/favicon.svg",
  },
  openGraph: {
    title: "Options Flow — Decifer",
    description: "Real-time unusual options flow — 1,000 symbol universe.",
    url: "https://options.decifertrading.com",
    siteName: "Decifer",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Options Flow — Decifer",
    description: "Real-time unusual options flow — 1,000 symbol universe.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
