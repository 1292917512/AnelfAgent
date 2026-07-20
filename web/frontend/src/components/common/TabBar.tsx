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

/** 通用标签栏：标签过多时横向滚动（移动端友好） */
export function TabBar<T extends string = string>({ tabs, activeTab, onChange }: TabBarProps<T>) {
  return (
    <div className="overflow-x-auto no-scrollbar border-b border-border">
      <div className="flex items-center gap-1 min-w-max">
        {tabs.map((tabItem) => (
          <button
            key={tabItem.key}
            onClick={() => onChange(tabItem.key)}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px whitespace-nowrap",
              activeTab === tabItem.key
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-foreground",
            )}
          >
            {tabItem.icon && <tabItem.icon size={15} />}
            {tabItem.label}
          </button>
        ))}
      </div>
    </div>
  );
}
