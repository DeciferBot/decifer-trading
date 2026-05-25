"use client";

import { TrendingUp, Wallet, Clock, BarChart3, Sparkles, type LucideIcon } from "lucide-react";

export type Tab = "today" | "holdings" | "activity" | "results" | "apex";

const TABS: { id: Tab; icon: LucideIcon; label: string }[] = [
  { id: "apex",     icon: Sparkles,   label: "Apex"      },
  { id: "today",    icon: TrendingUp, label: "Portfolio" },
  { id: "holdings", icon: Wallet,     label: "Holdings"  },
  { id: "activity", icon: Clock,      label: "Activity"  },
  { id: "results",  icon: BarChart3,  label: "Results"   },
];

interface Props {
  active: Tab;
  onChange: (tab: Tab) => void;
}

export default function BottomNav({ active, onChange }: Props) {
  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 flex items-stretch justify-around border-t border-[#1e2a3a] bg-[#080b12]/95 backdrop-blur-xl"
      style={{ paddingBottom: "env(safe-area-inset-bottom)", minHeight: "calc(60px + env(safe-area-inset-bottom))" }}
    >
      {TABS.map(({ id, icon: Icon, label }) => {
        const isActive = active === id;
        return (
          <button
            key={id}
            onClick={() => onChange(id)}
            className="flex flex-1 flex-col items-center justify-center gap-1 py-3 transition-all"
            aria-label={label}
          >
            <Icon
              size={20}
              strokeWidth={isActive ? 2.5 : 1.75}
              className={isActive ? "text-blue-400" : "text-slate-600"}
            />
            <span className={`text-[10px] font-semibold tracking-wide transition-colors ${isActive ? "text-blue-400" : "text-slate-600"}`}>
              {label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
