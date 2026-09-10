import { useEffect, useRef, useState } from "react";
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

/** 通用标签栏：标签过多时横向滚动（移动端友好）；fill 模式下均分宽度。
 * 溢出时按滚动位置在两侧渲染渐隐边缘，提示还有更多标签可滚动。 */
export function TabBar<T extends string = string>({ tabs, activeTab, onChange, fill = false }: TabBarProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState({ left: false, right: false });

  useEffect(() => {
    if (fill) return;
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      setOverflow({
        left: el.scrollLeft > 1,
        right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1,
      });
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [fill, tabs.length]);

  return (
    <div className={cn("relative border-b border-border", fill && "w-full")}>
      <div
        ref={scrollRef}
        className={cn(!fill && "overflow-x-auto overflow-y-hidden no-scrollbar")}
      >
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
      {!fill && overflow.left && (
        <div className="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-background to-transparent" />
      )}
      {!fill && overflow.right && (
        <div className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-background to-transparent" />
      )}
    </div>
  );
}
