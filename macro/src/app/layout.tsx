import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://macro.decifertrading.com"),
  title: "Macro Drivers — Decifer",
  description: "Live macro driver state — what forces are active and why they matter.",
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/favicon.svg",
  },
  openGraph: {
    title: "Macro Drivers — Decifer",
    description: "Live macro driver state — what forces are active and why they matter.",
    url: "https://macro.decifertrading.com",
    siteName: "Decifer",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Macro Drivers — Decifer",
    description: "Live macro driver state — what forces are active and why they matter.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
