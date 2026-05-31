import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Decifer Market Map",
  description: "Exploratory market intelligence graph. Find hot spots, trace connections, discover what's moving.",
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
