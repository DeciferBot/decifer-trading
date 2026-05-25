import type { Metadata, Viewport } from "next";
import { GoogleAnalytics } from "@next/third-parties/google";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import "./globals.css";

const organizationJsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://www.decifertrading.com/#organization",
      name: "DECIFER Trading",
      url: "https://www.decifertrading.com",
      parentOrganization: { "@id": "https://www.decifer.io/#organization" },
      description:
        "DECIFER Trading turns market data, catalysts, macro forces, and portfolio context into plain-language intelligence for active investors.",
    },
    {
      "@type": "WebSite",
      "@id": "https://www.decifertrading.com/#website",
      url: "https://www.decifertrading.com",
      name: "DECIFER Trading",
      publisher: { "@id": "https://www.decifertrading.com/#organization" },
    },
  ],
};

export const metadata: Metadata = {
  title: {
    default: "DECIFER Trading — Market Decision Intelligence",
    template: "%s | DECIFER Trading",
  },
  description:
    "DECIFER Trading turns market data, catalysts, macro forces, and portfolio context into plain-language intelligence for active investors. Not investment advice.",
  metadataBase: new URL("https://www.decifertrading.com"),
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/apple-touch-icon.svg",
  },
  openGraph: {
    title: "DECIFER Trading — Market Decision Intelligence",
    description:
      "The missing decision intelligence layer between market noise and investor action.",
    url: "https://www.decifertrading.com",
    siteName: "DECIFER Trading",
    type: "website",
    locale: "en_GB",
  },
  twitter: {
    card: "summary_large_image",
    title: "DECIFER Trading — Market Decision Intelligence",
    description:
      "The missing decision intelligence layer between market noise and investor action.",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-video-preview": -1,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
  ...(process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION && {
    verification: { google: process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION },
  }),
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#070a12",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd) }}
        />
        <Analytics />
        <SpeedInsights />
      </body>
      {process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID && (
        <GoogleAnalytics gaId={process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID} />
      )}
    </html>
  );
}
