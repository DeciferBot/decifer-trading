"use client";

import { useState } from "react";
import BottomNav, { type Tab } from "@/components/BottomNav";
import MarketView   from "@/views/MarketView";
import TodayView    from "@/views/TodayView";
import HoldingsView from "@/views/HoldingsView";
import ActivityView from "@/views/ActivityView";
import ResultsView  from "@/views/ResultsView";
import ApexView     from "@/views/ApexView";

export default function Page() {
  const [activeTab, setActiveTab] = useState<Tab>("market");

  return (
    <div className="flex min-h-screen flex-col" style={{ background: "#080b12" }}>
      <main className="flex-1 overflow-y-auto" style={{ paddingBottom: "calc(60px + env(safe-area-inset-bottom))" }}>
        {activeTab === "market"   && <MarketView   />}
        {activeTab === "today"    && <TodayView    onTabChange={setActiveTab} />}
        {activeTab === "holdings" && <HoldingsView />}
        {activeTab === "activity" && <ActivityView />}
        {activeTab === "results"  && <ResultsView  />}
        {activeTab === "apex"     && <ApexView     />}
      </main>
      <BottomNav active={activeTab} onChange={setActiveTab} />
    </div>
  );
}
