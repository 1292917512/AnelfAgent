import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useNow } from "@/hooks/useNow";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useChatActivity } from "./useChatActivity";

/** 当前 Web 消息的等待状态，不混入其他会话的工具活动。 */
export function ActivityRow() {
  const { t } = useTranslation("workbench");
  const submittedSince = useChatStore((state) => state.buckets[state.activeChatId]?.sendingSince);
  const submitting = useChatStore((state) => state.buckets[state.activeChatId]?.submitting);
  const { run, background, busy } = useChatActivity();
  const since = submittedSince ?? (run ? run.started_at * 1000 : null);
  const step = run?.entries.slice().reverse().find((entry) => "status" in entry && entry.status === "running");
  const label = submitting ? t("execution.submitting") : !run ? background ? t("execution.executing", { name: t("activity.kinds.delegation") }) : t("execution.accepted")
    : step?.kind === "phase" ? t(`activity.phases.${step.name}`) : step?.kind === "model" ? t("execution.generating")
      : step?.kind === "tool" ? t("execution.executing", { name: step.name }) : t("activity.contextPreparing");
  const now = useNow(!!since);
  if (!busy) return null;
  return <button className="flex items-center gap-2 py-2 text-xs text-muted" onClick={() => useWorkbenchStore.getState().showExecution()}>
    <Loader2 size={14} className="animate-spin text-accent" />{label}
    {since && <span className="tabular-nums">{Math.max(0, Math.floor((now - since) / 1000))}s</span>}
  </button>;
}
