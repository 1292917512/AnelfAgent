import type { ActivityRun, ChatMessage } from "./types";
import { DEFAULT_CHAT_ID } from "@/stores/chat-shared";

/** 会话状态仅从已有活动事实派生，消息送达不代表执行结束。 */
export function isChatRun(run: ActivityRun, chatId: string): boolean {
  return run.kind === "conversation" && run.source.channel === "webui"
    && (run.source.session || DEFAULT_CHAT_ID) === chatId;
}

export function hasActiveWork(run: ActivityRun): boolean {
  return run.status === "running" || run.entries.some((entry) => "status" in entry && ["running", "queued"].includes(entry.status));
}

export function activeChatRun(runs: readonly ActivityRun[], chatId: string): ActivityRun | undefined {
  return [...runs].reverse().find((run) => isChatRun(run, chatId) && run.status === "running");
}

/** 只收束有服务端消息身份和明确执行覆盖证据的等待，不推断逐条已读。 */
export function submissionFinished(runs: readonly ActivityRun[], chatId: string, message: ChatMessage | undefined): boolean {
  if (message?.id == null) return false;
  return runs.some((run) => isChatRun(run, chatId) && run.status !== "running" && run.ended_at != null && (
    (message.ts != null && run.started_at >= message.ts)
    || run.entries.some((entry) => entry.kind === "notice" && entry.origin === "conversation"
      && entry.items.some((item) => item.message_id === String(message.id)))
  ));
}
