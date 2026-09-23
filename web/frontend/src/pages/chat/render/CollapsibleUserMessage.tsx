/** 长用户消息折叠 — 超过阈值时压到 120px + 底部渐隐 + 悬浮展开钮（ZCode ConversationUserInputBody 移植）。
 *
 * ResizeObserver + rAF 合并测量；展开/收起走 max-height 过渡（reduced-motion 瞬时）。
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp } from "lucide-react";
import { usePrefersReducedMotion } from "@/components/common/Shimmer";
import { cn } from "@/lib/utils";

const COLLAPSED_MAX_HEIGHT = 120;

export function CollapsibleUserMessage({ children }: { children: ReactNode }) {
  const { t } = useTranslation("chat");
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    let raf = 0;
    const measure = () => {
      // 折叠阈值留 1px 余量，避免临界抖动
      setOverflowing(el.scrollHeight > COLLAPSED_MAX_HEIGHT + 1);
    };
    raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [children]);

  const collapsed = overflowing && !expanded;
  // 展开态的 max-height 需要实测内容高；渲染期不读 ref（react-hooks/refs），
  // 用 state 承载测量值（ResizeObserver 里同步刷新）
  const [expandedHeight, setExpandedHeight] = useState<number | null>(null);
  useEffect(() => {
    if (!expanded || !bodyRef.current) return;
    setExpandedHeight(bodyRef.current.scrollHeight);
  }, [expanded, children]);

  return (
    <div className="relative">
      <div
        ref={bodyRef}
        className={cn(
          "overflow-hidden",
          !reduced && "transition-[max-height] duration-300 ease-out",
        )}
        style={{
          maxHeight: collapsed ? COLLAPSED_MAX_HEIGHT : (expandedHeight ?? undefined),
          maskImage: collapsed
            ? "linear-gradient(to bottom, black 0%, black 70%, transparent 100%)"
            : undefined,
          WebkitMaskImage: collapsed
            ? "linear-gradient(to bottom, black 0%, black 70%, transparent 100%)"
            : undefined,
        }}
      >
        {children}
      </div>
      {overflowing && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className={cn(
            "absolute left-1/2 -translate-x-1/2 rounded-full border border-border bg-popover/90",
            "px-2.5 py-0.5 text-[11px] text-muted shadow-sm backdrop-blur-sm",
            "hover:text-foreground transition-colors",
            collapsed ? "bottom-1.5" : "-bottom-3",
          )}
        >
          <span className="inline-flex items-center gap-1">
            {expanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            {expanded ? t("collapse") : t("expand")}
          </span>
        </button>
      )}
    </div>
  );
}
