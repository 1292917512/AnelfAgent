import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

/** 横向标签容器：选中项可见、滚轮导航与溢出提示共用一个实现。 */
export function TabStrip({ activeKey, children, className, contentClassName, role, label }: {
  activeKey: string; children: ReactNode; className?: string; contentClassName?: string;
  role?: "tablist" | "group"; label?: string;
}) {
  const { t } = useTranslation("common");
  const frame = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  const [edges, setEdges] = useState({ start: true, end: true });
  useEffect(() => {
    const container = root.current;
    const wrapper = frame.current;
    const strip = content.current;
    if (!container || !wrapper || !strip) return;
    const measure = () => {
      setOverflow(strip.scrollWidth > wrapper.clientWidth + 1);
      const start = container.scrollLeft <= 1;
      const end = container.scrollLeft + container.clientWidth >= container.scrollWidth - 1;
      setEdges((previous) => previous.start === start && previous.end === end ? previous : { start, end });
    };
    const reveal = () => {
      const tab = strip.querySelector<HTMLElement>('[aria-selected="true"], [aria-pressed="true"]');
      if (tab) {
        const bounds = container.getBoundingClientRect();
        const selected = tab.getBoundingClientRect();
        if (selected.left < bounds.left) container.scrollLeft += selected.left - bounds.left;
        else if (selected.right > bounds.right) container.scrollLeft += selected.right - bounds.right;
      }
      measure();
    };
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? container.clientWidth : 1);
      const next = Math.max(0, Math.min(container.scrollWidth - container.clientWidth, container.scrollLeft + delta));
      if (Math.abs(next - container.scrollLeft) < 1) return;
      event.preventDefault();
      container.scrollLeft = next;
    };
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(reveal);
    observer?.observe(wrapper);
    observer?.observe(container);
    observer?.observe(strip);
    container.addEventListener("scroll", measure, { passive: true });
    container.addEventListener("wheel", wheel, { passive: false });
    reveal();
    return () => {
      observer?.disconnect();
      container.removeEventListener("scroll", measure);
      container.removeEventListener("wheel", wheel);
    };
  }, [activeKey]);
  const scroll = (direction: number) => {
    const container = root.current;
    container?.scrollBy({ left: direction * container.clientWidth * .8,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };
  return <div ref={frame} className={cn("tab-strip flex min-w-0 max-w-full items-center", className)}>
    {overflow && <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" title={t("scrollTabsLeft")} disabled={edges.start} onClick={() => scroll(-1)}><ChevronLeft size={16} /></Button>}
    <div ref={root} role={role} aria-label={label} className="min-w-0 flex-1 overflow-x-auto no-scrollbar">
      <div ref={content} className={cn("flex w-max min-w-full items-center gap-1", contentClassName)}>{children}</div>
    </div>
    {overflow && <Button variant="ghost" size="icon" className="h-9 w-9 shrink-0" title={t("scrollTabsRight")} disabled={edges.end} onClick={() => scroll(1)}><ChevronRight size={16} /></Button>}
  </div>;
}
