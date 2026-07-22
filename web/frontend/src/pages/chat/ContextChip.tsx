/**
 * ContextChip — 上下文用量状态（对齐 Claude Code 状态栏的 context %）。
 *
 * 数据来自内核 context_usage 事件（usage 锚定：API 真实用量优先）。
 * 颜色：>90% 红（临近压缩），>70% 黄，其余灰。
 */
import { useTranslation } from "react-i18next";
import { useChatStore } from "@/stores/chat-store";
import { cn } from "@/lib/utils";

export function ContextChip() {
  const { t } = useTranslation("chat");
  const usage = useChatStore((s) => s.contextUsage);
  if (!usage || usage.threshold <= 0) return null;

  const pct = Math.min(999, Math.round(usage.percent));
  return (
    <span
      title={t("contextUsage.title", {
        tokens: usage.tokens,
        threshold: usage.threshold,
      })}
      className={cn(
        "text-xs font-mono px-2 py-1 rounded-full border shrink-0",
        pct >= 90
          ? "border-red-400/50 text-red-500"
          : pct >= 70
            ? "border-yellow-400/50 text-yellow-600"
            : "border-border text-muted",
      )}
    >
      {t("contextUsage.label", { pct })}
    </span>
  );
}
