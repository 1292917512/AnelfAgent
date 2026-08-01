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
    setOverflow(el.scrollHeight > COLLAPSE_THRESHOLD_PX);
  }, [content]);

  const collapsed = overflow && !expanded;
  return (
    <div className="relative">
      <div
        ref={ref}
        className={cn(collapsed && "max-h-[360px] overflow-hidden")}
      >
        <Markdown content={content} />
      </div>
      {collapsed && (
        <div className={cn("absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t to-transparent pointer-events-none", fadeClass)} />
      )}
      {overflow && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted hover:text-foreground transition-colors"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? t("collapse") : t("expand")}
        </button>
      )}
    </div>
  );
}
