import type { Metadata } from "next";
import EarningsPageClient from "./EarningsPageClient";

export const metadata: Metadata = {
  title: "Earnings Calendar",
  description: "Upcoming earnings for tracked stocks — with theme context and Decifer signal coverage.",
  openGraph: {
    title: "Earnings Calendar — Decifer",
    description: "Upcoming earnings for tracked stocks — with theme context and Decifer signal coverage.",
    url: "https://mobile.decifertrading.com/earnings",
    siteName: "Decifer",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Earnings Calendar — Decifer",
    description: "Upcoming earnings for tracked stocks — with theme context and Decifer signal coverage.",
  },
};

export default function EarningsPage() {
  return <EarningsPageClient />;
}
