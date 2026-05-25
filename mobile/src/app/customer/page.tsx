// Customer-only entry surface.
// Exposes Market Intelligence only — no operator tabs, no private bot data.
// All data comes from NEXT_PUBLIC_INTELLIGENCE_API_URL via customerApi.
import MarketView from "@/views/MarketView";

export default function CustomerPage() {
  return (
    <div
      className="flex min-h-screen flex-col"
      style={{ background: "#080b12" }}
    >
      <main
        className="flex-1 overflow-y-auto"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <MarketView />
      </main>
    </div>
  );
}
