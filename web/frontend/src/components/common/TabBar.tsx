import { useRef, type KeyboardEvent } from "react";
import type { LucideIcon } from "lucide-react";
import { TabStrip } from "./TabStrip";
import { cn } from "@/lib/utils";

export interface TabItem<T extends string = string> {
  key: T;
  label: string;
  icon?: LucideIcon;
  count?: number;
}

interface TabBarProps<T extends string> {
  tabs: TabItem<T>[];
  activeTab: T;
  onChange: (tab: T) => void;
  fill?: boolean;
  iconOnly?: boolean;
}

export function TabBar<T extends string>({ tabs, activeTab, onChange, fill = false, iconOnly = false }: TabBarProps<T>) {
  const root = useRef<HTMLDivElement>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    switch (event.key) {
      case "ArrowRight": next = (index + 1) % tabs.length; break;
      case "ArrowLeft": next = (index - 1 + tabs.length) % tabs.length; break;
      case "Home": next = 0; break;
      case "End": next = tabs.length - 1; break;
      default: return;
    }
    event.preventDefault();
    const item = tabs[next];
    if (!item) return;
    onChange(item.key);
    root.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus({ preventScroll: true });
  };
  return (
    <div ref={root} className={cn("min-h-11 min-w-0 max-w-full shrink-0 rounded-xl border border-border bg-card p-1", fill && "w-full")}>
      <TabStrip role="tablist" activeKey={activeTab}>
          {tabs.map((item, index) => <button key={item.key} type="button" role="tab"
            aria-selected={activeTab === item.key} tabIndex={activeTab === item.key ? 0 : -1}
            aria-label={item.label}
            onClick={() => onChange(item.key)} onKeyDown={(event) => onKeyDown(event, index)} title={item.label}
            className={cn("inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-xs font-medium transition-colors", iconOnly ? "min-w-10 px-2" : "px-3 md:px-4", fill && "flex-1",
              activeTab === item.key ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-heading")}>
            {item.icon && <item.icon size={15} />}{!iconOnly && item.label}
            {item.count !== undefined && <span data-tab-count className="text-xs tabular-nums">{item.count || ""}</span>}
          </button>)}
      </TabStrip>
    </div>
  );
}
