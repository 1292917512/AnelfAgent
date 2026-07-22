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
  /** 均分容器宽度（窄面板内防止标签挤成一团） */
  fill?: boolean;
}

/** 通用标签栏：标签过多时横向滚动（移动端友好）；fill 模式下均分宽度 */
export function TabBar<T extends string = string>({ tabs, activeTab, onChange, fill = false }: TabBarProps<T>) {
  return (
    <div className={cn("border-b border-border", fill ? "w-full" : "overflow-x-auto no-scrollbar")}>
      <div className={cn("flex items-center", fill ? "w-full" : "gap-1 min-w-max")}>
        {tabs.map((tabItem) => (
          <button
            key={tabItem.key}
            onClick={() => onChange(tabItem.key)}
            title={tabItem.label}
            className={cn(
              "flex items-center gap-1.5 text-sm font-medium border-b-2 transition-colors -mb-px whitespace-nowrap",
              fill ? "flex-1 justify-center px-1 py-2.5 text-xs" : "px-4 py-2.5",
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
