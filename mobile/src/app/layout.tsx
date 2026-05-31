import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Decifer Market Intelligence",
  description: "Market intelligence — signals, themes, and evidence. Not financial advice.",
  manifest: "/manifest.json",
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
    apple: "/favicon.svg",
  },
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
  themeColor: "#0c1427",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark h-full">
      <head>
        {/* M11B.4 kill-switch: unregister any stale service workers from previous app shells */}
        <script dangerouslySetInnerHTML={{ __html: `
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then(function(regs) {
    regs.forEach(function(r) { r.unregister(); });
  });
  caches.keys().then(function(keys) {
    keys.forEach(function(k) {
      if (k.startsWith('decifer-') || k.startsWith('workbox-') || k.includes('mobile')) {
        caches.delete(k);
      }
    });
  });
}
` }} />
      </head>
      <body
        suppressHydrationWarning
        className="h-full antialiased"
        style={{ background: "#0c1427", color: "#fff" }}
      >
        {children}
      </body>
    </html>
  );
}
