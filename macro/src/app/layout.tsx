import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DECIFER / MACRO DRIVERS",
  description: "Market Driver State Feed — live macro signal intelligence",
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/favicon.svg",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
