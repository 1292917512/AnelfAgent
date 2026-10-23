import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import { GitFork, Mic, Copy, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatRelativeTimestamp } from "@/lib/format";
import { useChatStore } from "@/stores/chat-store";
import { ConfirmDialog } from "@/components/ui/Modal";
import { MediaBubble } from "./render/MediaBubble";
import { MessageExtension } from "@/components/extensions/MessageExtension";
import { SystemNotice } from "./render/SystemNotice";
import { CollapsibleUserMessage } from "./render/CollapsibleUserMessage";
import { CollapsibleMarkdown } from "./render/CollapsibleMarkdown";
import type { ChatMessage } from "@/lib/types";

/** 单条消息气泡（memo：流式 delta 更新时历史消息行不重渲染） */
export const MessageRow = memo(function MessageRow({ msg, foldPivot }: { msg: ChatMessage; foldPivot?: boolean }) {
  const { t } = useTranslation("chat");
  const copyMessageToDraft = useChatStore((s) => s.copyMessageToDraft);
  const foldFrom = useChatStore((s) => s.foldFrom);
  const isUser = msg.role === "user";
  const [confirmFold, setConfirmFold] = useState(false);
  const [foldBusy, setFoldBusy] = useState(false);
  // 仅已落库的用户消息可作折叠起点。
  const canFold = isUser && msg.id != null && !foldPivot;

  const onFoldConfirm = async () => {
    if (msg.id == null) return;
    setFoldBusy(true);
    try {
      await foldFrom(msg.id);
    } finally {
      setFoldBusy(false);
      setConfirmFold(false);
    }
  };

  if (msg.role === "system" || msg.kind === "system_notice") {
    return <SystemNotice content={msg.content} tone={msg.tone} />;
  }

  return (
    <div data-message-key={`msg-${msg.id ?? msg.cid ?? msg.ts}-${msg.role}`} className={cn("flex group/msg", isUser ? "justify-end" : "justify-start")}>
      <div className={cn("message-content min-w-0", isUser ? "is-user text-right" : "is-assistant text-left")}>
        {msg.media_type && <MediaBubble msg={msg} />}
        {!isUser && msg.extension && <MessageExtension extension={msg.extension} />}
        {msg.content && (
          <div
            className={cn(
              "message-bubble rounded-2xl px-4 py-3 text-sm leading-relaxed inline-block text-left",
              isUser ? "bg-accent-subtle" : "bg-secondary",
              msg.delivery === "submitting" && "opacity-70",
              msg.delivery === "failed" && "border border-danger/50",
            )}
          >
            {isUser && msg.delivery && <p className={cn("mb-1 text-xs", msg.delivery === "failed" ? "text-danger" : "text-muted")} role="status">{t(`delivery.${msg.delivery}`)}</p>}
            {msg.voice && (
              <div className="flex items-center gap-1 text-xs text-muted mb-1">
                {msg.voice === "transcript"
                  ? <><Mic size={10} />{t("voice.transcript")}</>
                  : <><Volume2 size={10} />{t("voice.spoken")}</>}
              </div>
            )}
            {isUser ? (
              <CollapsibleUserMessage>
                <CollapsibleMarkdown
                  content={msg.content}
                  fadeClass="from-elevated"
                />
              </CollapsibleUserMessage>
            ) : (
              <CollapsibleMarkdown
                content={msg.content}
                fadeClass="from-card"
              />
            )}
          </div>
        )}
        {(msg.timestamp || msg.ts) && (
          <div
            className={cn(
              "text-[11px] text-muted mt-0.5 px-1 transition-opacity flex items-center gap-1.5",
              isUser ? "justify-end" : "justify-start",
            )}
            title={msg.timestamp}
          >
            {isUser && msg.cid && <button type="button" onClick={() => copyMessageToDraft(msg.cid!)}
              className="text-muted hover:text-accent" title={t("copyToDraft")} aria-label={t("copyToDraft")}><Copy size={12} /></button>}
            {canFold && (
              <button
                type="button"
                onClick={() => setConfirmFold(true)}
                className="md:opacity-0 group-hover/msg:opacity-100 focus-visible:opacity-100 transition-opacity text-muted hover:text-accent"
                title={t("fold.fromHere")}
              >
                <GitFork size={11} />
              </button>
            )}
            <span>{msg.ts ? formatRelativeTimestamp(msg.ts) : msg.timestamp}</span>
          </div>
        )}
      </div>
      <ConfirmDialog
        open={confirmFold}
        onClose={() => setConfirmFold(false)}
        onConfirm={() => void onFoldConfirm()}
        title={t("fold.confirmTitle")}
        message={t("fold.confirmMessage")}
        confirmText={t("fold.confirm")}
        loading={foldBusy}
      />
    </div>
  );
});

