import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Decifer Options Flow",
  description: "Real-time unusual options flow — 1,000 symbol universe",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
