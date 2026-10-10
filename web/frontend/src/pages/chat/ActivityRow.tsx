import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useNow } from "@/hooks/useNow";
import { useWorkbenchStore } from "@/stores/workbench-store";

/** 当前 Web 消息的等待状态，不混入其他会话的工具活动。 */
export function ActivityRow() {
  const { t } = useTranslation("workbench");
  const since = useChatStore((state) => state.buckets[state.activeChatId]?.sendingSince);
  const now = useNow(!!since);
  return <button className="flex items-center gap-2 py-2 text-xs text-muted" onClick={() => useWorkbenchStore.getState().showExecution()}>
    <Loader2 size={14} className="animate-spin text-accent" />{t("execution.waiting")}
    {since && <span className="tabular-nums">{Math.max(0, Math.floor((now - since) / 1000))}s</span>}
  </button>;
}
