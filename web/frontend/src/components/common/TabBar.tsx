import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

export interface TabItem<T extends string = string> {
  key: T;
  label: string;
  icon?: LucideIcon;
}

interface TabBarProps<T extends string = string> {
  tabs: TabItem<T>[];
  activeTab: T;
  onChange: (tab: T) => void;
}

export function TabBar<T extends string = string>({ tabs, activeTab, onChange }: TabBarProps<T>) {
  return (
    <div className="flex items-center gap-1 border-b border-[var(--border)]">
      {tabs.map((tabItem) => (
        <button
          key={tabItem.key}
          onClick={() => onChange(tabItem.key)}
          className={cn(
            "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px",
            activeTab === tabItem.key
              ? "border-[var(--accent)] text-[var(--accent)]"
              : "border-transparent text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {tabItem.icon && <tabItem.icon size={15} />}
          {tabItem.label}
        </button>
      ))}
    </div>
  );
}
