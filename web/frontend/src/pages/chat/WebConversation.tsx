import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MessageSquare, Trash2, X, Maximize2, Minimize2 } from "lucide-react";
import { chatApi } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { Button } from "@/components/ui";
import { ChatDropZone } from "./ChatDropZone";
import { ChatTabs } from "./ChatTabs";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

/** Web 频道交流窗口，只呈现消息、附件与发送状态。 */
export function WebConversation({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation("workbench");
  const expanded = useWorkbenchStore((state) => state.chatExpanded);
  const toggleExpanded = useWorkbenchStore((state) => state.toggleChatExpanded);
  const clearMessages = useChatStore((state) => state.clearMessages);
  const activeChatId = useChatStore((state) => state.activeChatId);
  const { data: botName } = useQuery({ queryKey: ["botName"], queryFn: () => chatApi.botName().then((r) => r.data.name) });
  return <section aria-label={t("webChat")} className="h-full min-h-0"><ChatDropZone className={`conversation-pane ${expanded ? "is-expanded" : ""} flex h-full min-h-0 min-w-0 flex-col`}>
    <header className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
      <MessageSquare size={16} className="text-accent" />
      <div className="min-w-0 flex-1"><h2 className="text-sm font-semibold text-heading">{t("webChat")}</h2><p className="truncate text-[11px] text-muted">{botName ?? "AnelfAgent"}</p></div>
      <Button size="icon" variant="ghost" className="hidden lg:inline-flex" title={t(expanded ? "restoreChat" : "expandChat")} onClick={toggleExpanded}>{expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</Button>
      <Button size="icon" variant="ghost" title={t("common:close")} onClick={onClose}><X size={16} /></Button>
    </header>
    <div className="conversation-heading"><ChatTabs /><Button variant="ghost" size="icon" title={t("clearView")} onClick={clearMessages}><Trash2 size={14} /></Button></div>
    <div className="conversation-body relative flex min-h-0 flex-1 flex-col"><MessageList key={activeChatId} />
      <ChatInput />
    </div>
  </ChatDropZone></section>;
}
