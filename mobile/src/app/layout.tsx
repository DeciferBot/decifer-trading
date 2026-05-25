import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Decifer",
  description: "Autonomous AI trading dashboard",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Decifer",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0a0a0a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark h-full">
      <head>
        {/* Unregister any stale service workers left by the previous tunnel-based app */}
        <script dangerouslySetInnerHTML={{ __html: `
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then(function(regs) {
    regs.forEach(function(r) { r.unregister(); });
  });
}
` }} />
      </head>
      <body suppressHydrationWarning className="h-full bg-[#0a0a0a] text-white antialiased">{children}</body>
    </html>
  );
}
