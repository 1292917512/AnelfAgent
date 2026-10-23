/**
 * CollapsibleMarkdown — 超长消息折叠（渐变遮罩 + 展开全文）。
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { Markdown } from "./Markdown";

const COLLAPSE_THRESHOLD_PX = 360;

export function CollapsibleMarkdown({
  content,
  fadeClass = "from-secondary",
}: {
  content: string;
  /** 渐变遮罩的起始色（与所在气泡背景一致） */
  fadeClass?: string;
}) {
  const { t } = useTranslation("chat");
  const ref = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setOverflow(el.scrollHeight > COLLAPSE_THRESHOLD_PX + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [content]);

  const collapsed = overflow && !expanded;
  return (
    <div className="relative">
      <div
        className={cn(!expanded && "max-h-[360px] overflow-hidden")}
      >
        <div ref={ref}><Markdown content={content} /></div>
      </div>
      {collapsed && (
        <div className={cn("absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t to-transparent pointer-events-none", fadeClass)} />
      )}
      {overflow && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className={cn("inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs text-muted hover:text-foreground hover:border-accent/40 transition-colors",
            collapsed ? "absolute bottom-1 left-1 shadow-sm" : "mt-1")}
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? t("collapse") : t("expand")}
        </button>
      )}
    </div>
  );
}
