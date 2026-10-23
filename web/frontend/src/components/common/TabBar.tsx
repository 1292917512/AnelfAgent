import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
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
  iconOnly?: boolean;
}

export function TabBar<T extends string>({ tabs, activeTab, onChange, fill = false, iconOnly = false }: TabBarProps<T>) {
  const { t } = useTranslation("common");
  const frame = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  const [edges, setEdges] = useState({ start: true, end: true });
  const revealActiveTab = () => {
    const container = root.current;
    const tab = container?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!container || !tab) return;
    const bounds = container.getBoundingClientRect();
    const selected = tab.getBoundingClientRect();
    if (selected.left < bounds.left) container.scrollLeft += selected.left - bounds.left;
    else if (selected.right > bounds.right) container.scrollLeft += selected.right - bounds.right;
  };
  useEffect(revealActiveTab, [activeTab]);
  useEffect(() => {
    const container = root.current;
    const wrapper = frame.current;
    const strip = content.current;
    if (!container || !wrapper || !strip) return;
    const measure = () => {
      setOverflow(strip.scrollWidth > wrapper.clientWidth - 8 + 1);
      setEdges({ start: container.scrollLeft <= 1, end: container.scrollLeft + container.clientWidth >= container.scrollWidth - 1 });
    };
    const resize = () => { revealActiveTab(); measure(); };
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? container.clientWidth : 1);
      const next = Math.max(0, Math.min(container.scrollWidth - container.clientWidth, container.scrollLeft + delta));
      if (Math.abs(next - container.scrollLeft) < 1) return;
      event.preventDefault();
      container.scrollLeft = next;
    };
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(resize);
    observer?.observe(wrapper);
    observer?.observe(container);
    observer?.observe(strip);
    container.addEventListener("scroll", measure, { passive: true });
    container.addEventListener("wheel", wheel, { passive: false });
    measure();
    return () => {
      observer?.disconnect();
      container.removeEventListener("scroll", measure);
      container.removeEventListener("wheel", wheel);
    };
  }, []);
  const scroll = (direction: number) => {
    const container = root.current;
    if (!container) return;
    container.scrollBy({ left: direction * container.clientWidth * 0.8,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };
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
    <div ref={frame} className={cn("flex min-h-11 min-w-0 max-w-full shrink-0 items-center rounded-xl border border-border bg-card p-1", fill && "w-full")}>
      {overflow && <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" title={t("scrollTabsLeft")} disabled={edges.start} onClick={() => scroll(-1)}><ChevronLeft size={18} /></Button>}
      <div ref={root} role="tablist" className="min-w-0 flex-1 overflow-x-auto no-scrollbar">
        <div ref={content} className="flex w-max min-w-full items-center gap-1">
          {tabs.map((item, index) => <button key={item.key} type="button" role="tab"
            aria-selected={activeTab === item.key} tabIndex={activeTab === item.key ? 0 : -1}
            aria-label={item.label}
            onClick={() => onChange(item.key)} onKeyDown={(event) => onKeyDown(event, index)} title={item.label}
            className={cn("inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-xs font-medium transition-colors", iconOnly ? "min-w-10 px-2" : "px-3 md:px-4", fill && "flex-1",
              activeTab === item.key ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-heading")}>
            {item.icon && <item.icon size={15} />}{!iconOnly && item.label}
          </button>)}
        </div>
      </div>
      {overflow && <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" title={t("scrollTabsRight")} disabled={edges.end} onClick={() => scroll(1)}><ChevronRight size={18} /></Button>}
    </div>
  );
}
