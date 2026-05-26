"use client";
// Customer bottom navigation — M13B.
// 5-tab navigation: Today | Forces | Ask Decifer (centre) | Themes | Names
// Tab order: Today / Forces / Ask / Themes / Names

import { Home, Zap, Sparkles, Layers, Search } from "lucide-react";

export type CustomerTab = "today" | "forces" | "ask" | "themes" | "names";

interface NavItem {
  id: CustomerTab;
  label: string;
  Icon: React.ComponentType<{ size: number; style?: React.CSSProperties }>;
  center?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { id: "today",  label: "Today",  Icon: Home     },
  { id: "forces", label: "Forces", Icon: Zap      },
  { id: "ask",    label: "Ask",    Icon: Sparkles, center: true },
  { id: "themes", label: "Themes", Icon: Layers   },
  { id: "names",  label: "Names",  Icon: Search   },
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
                className="text-[10px] font-semibold tracking-wide"
                style={{ color: "#f97316", opacity: active ? 1 : 0.7 }}
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
            <Icon size={20} style={{ color: active ? "#f97316" : "#94a3b8" }} />
            <span
              className="text-[10px] font-medium tracking-wide"
              style={{ color: active ? "#f97316" : "#94a3b8" }}
            >
              {item.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
