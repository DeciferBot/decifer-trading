import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://map.decifertrading.com"),
  title: "Market Map — Decifer",
  description: "Exploratory market intelligence graph. Find hot spots, trace connections, discover what's moving.",
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/favicon.svg",
  },
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/favicon.svg",
  },
  openGraph: {
    title: "Market Map — Decifer",
    description: "Exploratory market intelligence graph. Find hot spots, trace connections, discover what's moving.",
    url: "https://map.decifertrading.com",
    siteName: "Decifer",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Market Map — Decifer",
    description: "Exploratory market intelligence graph. Find hot spots, trace connections, discover what's moving.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased" style={{ background: "#080d1a", margin: 0 }}>
        {children}
      </body>
    </html>
  );
}
