import { activeChatRun } from "@/lib/chat-activity";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { useDelegationStore } from "@/stores/delegation-store";

/** Web 会话的活动事实与提交等待分开订阅，消息送达不隐藏仍在执行的工作。 */
export function useChatActivity() {
  const chatId = useChatStore((state) => state.activeChatId);
  const waiting = useChatStore((state) => state.buckets[chatId]?.sending ?? false);
  const pending = useChatStore((state) => state.buckets[chatId]?.interruptPending ?? false);
  const run = useActivityStore((state) => activeChatRun(state.runs, chatId));
  const background = useDelegationStore((state) => Object.values(state.delegations[chatId] ?? {}).some((item) => item.status === "running"));
  return { run, background, stopping: pending || run?.control === "stopping",
    busy: waiting || !!run || background, canStop: waiting || !!run?.can_stop || background };
}
