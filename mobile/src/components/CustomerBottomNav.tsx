"use client";
// Customer bottom navigation — M13A.
// 5-tab navigation: Today | Discover | Ask Decifer (centre) | Signals | Universe
// No operator views. No execution language.

import { Home, Compass, Sparkles, Activity, Layers } from "lucide-react";

export type CustomerTab = "today" | "discover" | "ask" | "signals" | "universe";

interface NavItem {
  id: CustomerTab;
  label: string;
  Icon: React.ComponentType<{ size: number; style?: React.CSSProperties }>;
  center?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { id: "today",    label: "Today",    Icon: Home     },
  { id: "discover", label: "Discover", Icon: Compass  },
  { id: "ask",      label: "Ask",      Icon: Sparkles, center: true },
  { id: "signals",  label: "Signals",  Icon: Activity },
  { id: "universe", label: "Universe", Icon: Layers   },
];

interface Props {
  activeTab: CustomerTab;
  onTabChange: (tab: CustomerTab) => void;
}

export default function CustomerBottomNav({ activeTab, onTabChange }: Props) {
  return (
    <nav
      className="shrink-0 flex items-stretch"
      style={{
        background: "#111827",
        borderTop: "1px solid rgba(255,255,255,0.07)",
        paddingBottom: "max(env(safe-area-inset-bottom), 0.625rem)",
        paddingTop: "0.375rem",
      }}
    >
      {NAV_ITEMS.map((item) => {
        const active = activeTab === item.id;
        const { Icon } = item;

        if (item.center) {
          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id)}
              className="flex-1 flex flex-col items-center justify-center gap-0.5 transition-all active:scale-90"
              aria-label="Ask Decifer"
            >
              <span
                className="flex items-center justify-center w-11 h-7 rounded-full transition-all"
                style={{
                  background: active ? "#f97316" : "rgba(249,115,22,0.1)",
                  border: active ? "none" : "1px solid rgba(249,115,22,0.25)",
                }}
              >
                <Icon size={15} style={{ color: active ? "#fff" : "#f97316" }} />
              </span>
              <span
                className="text-[9px] font-semibold tracking-wide"
                style={{ color: active ? "#f97316" : "#f97316", opacity: active ? 1 : 0.6 }}
              >
                {item.label}
              </span>
            </button>
          );
        }

        return (
          <button
            key={item.id}
            onClick={() => onTabChange(item.id)}
            className="flex-1 flex flex-col items-center justify-center gap-0.5 py-1 transition-all active:scale-90"
            aria-label={item.label}
          >
            <Icon size={18} style={{ color: active ? "#f97316" : "#475569" }} />
            <span
              className="text-[9px] font-medium tracking-wide"
              style={{ color: active ? "#f97316" : "#475569" }}
            >
              {item.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
