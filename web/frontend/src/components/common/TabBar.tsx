import { useEffect, useRef, type KeyboardEvent } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface TabItem<T extends string = string> {
  key: T;
  label: string;
  icon?: LucideIcon;
}

interface TabBarProps<T extends string> {
  tabs: TabItem<T>[];
  activeTab: T;
  onChange: (tab: T) => void;
  fill?: boolean;
}

export function TabBar<T extends string>({ tabs, activeTab, onChange, fill = false }: TabBarProps<T>) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    root.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeTab]);
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
    root.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };
  return (
    <div ref={root} role="tablist" className={cn("flex min-h-11 shrink-0 items-center gap-1 overflow-x-auto rounded-xl border border-border bg-card p-1 no-scrollbar", fill && "w-full")}>
      {tabs.map((item, index) => <button key={item.key} type="button" role="tab"
        aria-selected={activeTab === item.key} tabIndex={activeTab === item.key ? 0 : -1}
        onClick={() => onChange(item.key)} onKeyDown={(event) => onKeyDown(event, index)} title={item.label}
        className={cn("inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3 text-xs font-medium transition-colors md:px-4", fill && "flex-1 px-2",
          activeTab === item.key ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-heading")}>
        {item.icon && <item.icon size={15} />}{item.label}
      </button>)}
    </div>
  );
}
