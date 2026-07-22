import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import { Markdown } from "./render/Markdown";
import { MediaBubble } from "./render/MediaBubble";
import { ActivityRow } from "./ActivityRow";

/** 消息列表：气泡渲染 + 自动滚动 */
export function MessageList() {
  const { t } = useTranslation("chat");
  const messages = useChatStore((s) => s.messages);
  const sending = useChatStore((s) => s.sending);
  const historyLoaded = useChatStore((s) => s.historyLoaded);
  const scrollRef = useRef<HTMLDivElement>(null);
  const initialLoad = useRef(true);

  useEffect(() => {
    if (!historyLoaded) return;
    if (initialLoad.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      initialLoad.current = false;
      return;
    }
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, historyLoaded]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-1 mb-3 min-h-0">
      {messages.length === 0 && (
        <div className="flex items-center justify-center h-full text-muted text-sm">
          {t("startConversation")}
        </div>
      )}
      {messages.map((msg) => (
        <div
          key={msg.id ?? msg.cid ?? `${msg.role}-${msg.timestamp}`}
          className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}
        >
          <div className={cn("max-w-[85%] sm:max-w-[80%]", msg.role === "user" ? "text-right" : "text-left")}>
            {msg.media_type && <MediaBubble msg={msg} />}
            {msg.content && (
              <div
                className={cn(
                  "rounded-lg px-4 py-2.5 text-sm leading-relaxed inline-block text-left",
                  msg.role === "user"
                    ? "bg-accent-subtle"
                    : msg.role === "system"
                      ? "bg-danger-subtle text-danger"
                      : "bg-secondary",
                  msg.queued && "border border-dashed border-muted-foreground/50 opacity-70",
                )}
              >
                {msg.queued && (
                  <div className="text-[10px] text-muted mb-1">{t("queued", "排队中 · 将于当前回复后处理")}</div>
                )}
                <Markdown content={msg.content} />
              </div>
            )}
            {msg.timestamp && (
              <div className="text-[11px] text-muted mt-0.5 px-1">{msg.timestamp}</div>
            )}
          </div>
        </div>
      ))}
      {sending && <ActivityRow />}
    </div>
  );
}
