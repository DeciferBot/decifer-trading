import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DECIFER / MACRO DRIVERS",
  description: "Market Driver State Feed — live macro signal intelligence",
  icons: { icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' fill='%230A0A0A'/><text y='24' x='4' font-size='22' fill='%23F97316'>D</text></svg>" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
