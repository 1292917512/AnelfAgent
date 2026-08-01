/**
 * SystemNotice — 系统提示居中细条（替代原红色气泡）。
 *
 * 用于计划完成/取消、会话中断、发送超时/失败等元消息；
 * tone="warn" 时呈警示色，其余为中性弱化样式。
 */
import { AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  content: string;
  tone?: "warn";
}

export function SystemNotice({ content, tone }: Props) {
  return (
    <div className="flex justify-center py-0.5">
      <div
        className={cn(
          "inline-flex items-center gap-1.5 max-w-[85%] rounded-full px-3 py-1 text-[11px] leading-relaxed",
          tone === "warn"
            ? "bg-warn-subtle text-warn"
            : "bg-muted/50 text-muted",
        )}
      >
        {tone === "warn" ? <AlertTriangle size={11} className="shrink-0" /> : <Info size={11} className="shrink-0" />}
        <span className="break-words">{content}</span>
      </div>
    </div>
  );
}
