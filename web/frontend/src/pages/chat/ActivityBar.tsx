/**
 * ActivityBar — 执行活动条（对话流下方、输入框上方）。
 *
 * 存在运行中子代理或执行中计划时出现：显示当前活动目标 + 子代理计数；
 * 点击展开 ActivityPanel 查看计划/子代理完整实时视图。空闲时零占位。
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { usePlanStore } from "@/stores/plan-store";
import { useDelegationStore } from "@/stores/delegation-store";
import { ActivityPanel } from "./ActivityPanel";

export function ActivityBar() {
  const { t } = useTranslation("plan");
  const activeChatId = useChatStore((s) => s.activeChatId);
  const chatDelegations = useDelegationStore((s) => s.delegations[activeChatId]);
  const activePlan = usePlanStore((s) => s.getActivePlan(activeChatId));
  const [open, setOpen] = useState(false);

  const running = useMemo(
    () => Object.values(chatDelegations ?? {}).filter((d) => d.status === "running"),
    [chatDelegations],
  );
  const planActive = activePlan?.status === "executing";
  if (running.length === 0 && !planActive) return null;

  const label = running[0]?.goal || activePlan?.goal || t("activity.title");

  return (
    <div className="relative shrink-0 mb-2">
      {open && <ActivityPanel onClose={() => setOpen(false)} />}
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 w-full px-3 py-1.5 rounded-md border text-xs transition-colors border-blue-400/40 bg-blue-50/30 dark:bg-blue-950/20 text-muted hover:text-foreground"
      >
        <Loader2 size={12} className="animate-spin shrink-0 text-blue-500" />
        <Bot size={13} className="shrink-0 text-blue-500" />
        <span className="truncate flex-1 text-left">{label}</span>
        {running.length > 0 && (
          <span className="shrink-0 px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300 font-mono">
            {t("activity.runningCount", { n: running.length })}
          </span>
        )}
        {open
          ? <ChevronDown size={13} className="shrink-0 opacity-60" />
          : <ChevronUp size={13} className="shrink-0 opacity-60" />}
      </button>
    </div>
  );
}
