/** 换向折叠 chip：折叠段在时间线上的内联占位（可展开查看 / 恢复回放）。

折叠段消息不再回放进 AI 上下文（DB 保留），chip 显示弃置路径摘要；
「展开」仅为本地查看（不影响上下文），「恢复」让消息重新进入上下文。
 */

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, GitFork, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { ChatMessage, ConversationFold } from "@/lib/types";

export const FoldChip = memo(function FoldChip({
  fold,
  messages,
}: {
  fold: ConversationFold;
  messages: ChatMessage[];
}) {
  const { t } = useTranslation("chat");
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const unfoldFold = useChatStore((s) => s.unfoldFold);

  const onRestore = async () => {
    setBusy(true);
    try {
      await unfoldFold(fold.id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-center gap-1.5" data-fold-id={fold.id}>
      <div className="inline-flex items-center gap-2 text-[11px] text-muted rounded-full bg-muted/50 px-3 py-1 max-w-full">
        <GitFork size={11} className="shrink-0" />
        <span className="shrink-0">{t("fold.foldedMessages", { count: fold.folded_count })}</span>
        {fold.summary && (
          <span className="truncate text-muted/80 max-w-[280px]" title={fold.summary}>
            {fold.summary}
          </span>
        )}
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex items-center gap-0.5 text-accent hover:underline shrink-0"
        >
          {expanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
          {expanded ? t("fold.collapse") : t("fold.expand")}
        </button>
        <button
          type="button"
          onClick={() => void onRestore()}
          disabled={busy}
          className="inline-flex items-center gap-0.5 text-accent hover:underline disabled:opacity-50 shrink-0"
        >
          <RotateCcw size={10} />
          {t("fold.restore")}
        </button>
      </div>
      {expanded && (
        <div className="w-full space-y-1 opacity-60 border-l-2 border-muted ml-4 pl-3">
          {messages.map((m, i) => (
            <div
              key={m.id ?? m.cid ?? i}
              className={cn(
                "text-xs leading-relaxed rounded px-2 py-1",
                m.role === "user" ? "text-right" : "text-left",
              )}
            >
              <span className="text-muted">[{m.role}]</span>{" "}
              {m.content.length > 200 ? `${m.content.slice(0, 200)}…` : m.content}
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
