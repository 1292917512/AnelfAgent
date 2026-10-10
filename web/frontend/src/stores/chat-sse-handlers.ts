/** Web 消息按会话归集，实时过程、计划与文件事件分发给各自的状态容器。 */
import type {
  ChatBucket,
  ChatMessage,
  ContextUsage,
  DelegationNode,
  PlanRecord,
  SseContextUsageEvent,
  SseDelegationProgressEvent,
  SseDelegationResolvedEvent,
  SseDelegationStartedEvent,
  SseEventBase,
  SseFileDiffEvent,
  SseMediaEvent,
  SsePlanCancelledEvent,
  SsePlanDeletedEvent,
  SsePlanStatusChangedEvent,
  SsePlanStepUpdatedEvent,
  SsePlanSubmittedEvent,
  SseReplyEvent,
  SseExtensionEvent,
  SseTurnEndEvent,
  UiCommandPayload,
} from "@/lib/types";
import type { ActivityRun } from "@/lib/types/activity";
import { useActivityStore } from "./activity-store";
import { useDelegationStore } from "./delegation-store";
import { usePlanStore } from "./plan-store";
import { useWorkbenchStore } from "./workbench-store";
import { useChangesStore } from "./changes-store";
import { useTreeChangesStore } from "@/stores/tree-changes-store";
import { clearSendWatchdog, touchSendWatchdog, nextCid, DEFAULT_CHAT_ID } from "./chat-shared";

export interface ChatSseContext {
  updateBucket: (chatId: string, fn: (b: ChatBucket) => Partial<ChatBucket>) => void;
  getActiveChatId: () => string;
  setContextUsage: (usage: ContextUsage) => void;
}

export function routeChatId(data: SseEventBase): string {
  return typeof data.chat_id === "string" && data.chat_id ? data.chat_id : DEFAULT_CHAT_ID;
}

function dispatchUiCommand(data: UiCommandPayload) {
  const wb = useWorkbenchStore.getState();
  switch (data.command) {
    case "notify":
      wb.pushNotification({
        id: data.id || nextCid(),
        title: data.title || "",
        content: data.content || "",
        level: (data.level as "info" | "success" | "warning" | "error") || "info",
        ts: data.ts || Date.now() / 1000,
      });
      break;
    case "ask":
      if (data.ask_id && data.question) {
        wb.pushAsk({
          ask_id: data.ask_id,
          question: data.question,
          options: data.options || [],
          ts: data.ts || Date.now() / 1000,
        });
      }
      break;
    case "open_panel":
      if (data.panel) wb.openPanel(data.panel, data.payload || "");
      break;
    case "compose":
      if (data.text) wb.setDraft(data.text);
      break;
  }
}

/** 往 EventSource 上挂载全部 chat SSE 事件监听 */
export function attachChatSseHandlers(es: EventSource, ctx: ChatSseContext): void {
  const { updateBucket } = ctx;

  es.addEventListener("message_failed", (event) => {
    try {
      const data = JSON.parse(event.data) as { chat_id?: string; message_id: string };
      updateBucket(routeChatId(data), (bucket) => ({
        messages: bucket.messages.map((message) => message.cid === data.message_id ? { ...message, delivery: "failed" } : message),
      }));
    } catch { /* Ignore invalid event data. */ }
  });


  // 实时通话：用户语音转写定稿 → 聊天流（语音形态消息）
  es.addEventListener("voice_transcript", (e) => {
    try {
      const data = JSON.parse(e.data) as { content: string; chat_id?: string };
      const chatId = routeChatId(data);
      updateBucket(chatId, (b) => ({
        messages: [
          ...b.messages,
          { role: "user", content: data.content, cid: nextCid(), ts: Date.now() / 1000, voice: "transcript" as const },
        ],
      }));
    } catch {
      /* 忽略畸形帧 */
    }
  });

  // 实时通话：AI 回复已同步语音播出 → 标记最近一条 assistant 消息
  es.addEventListener("voice_spoken", () => {
    updateBucket(ctx.getActiveChatId(), (b) => {
      const idx = [...b.messages].reverse().findIndex((m) => m.role === "assistant");
      if (idx === -1) return {};
      const real = b.messages.length - 1 - idx;
      const messages = [...b.messages];
      const target = messages[real];
      if (target) messages[real] = { ...target, voice: "spoken" as const };
      return { messages };
    });
  });

  es.addEventListener("reply", (e) => {
    try {
      const data = JSON.parse(e.data) as SseReplyEvent;
      const chatId = routeChatId(data);
      clearSendWatchdog(chatId);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const msg: ChatMessage = { role: "assistant", content: data.content, cid: nextCid(), ts: Date.now() / 1000 };
        return {
          messages: [...b.messages, msg],
          sending: false,
          sendingSince: null,
          unread: isBackground ? b.unread + 1 : b.unread,
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("turn_end", (e) => {
    try {
      const data = (e.data ? JSON.parse(e.data) : {}) as SseTurnEndEvent;
      const chatId = routeChatId(data);
      clearSendWatchdog(chatId);
      updateBucket(chatId, () => ({ sending: false, sendingSince: null }));
    } catch { /* Ignore invalid event data. */ }
  });

  es.addEventListener("media", (e) => {
    try {
      const data = JSON.parse(e.data) as SseMediaEvent;
      const chatId = routeChatId(data);
      clearSendWatchdog(chatId);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const msg: ChatMessage = {
          role: "assistant",
          content: data.caption || "",
          cid: nextCid(),
          ts: Date.now() / 1000,
          media_type: data.media_type,
          url: data.url,
          caption: data.caption,
        };
        return {
          messages: [...b.messages, msg],
          sending: false,
          sendingSince: null,
          unread: isBackground ? b.unread + 1 : b.unread,
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("extension", (e) => {
    try {
      const data = JSON.parse(e.data) as SseExtensionEvent;
      if (typeof data.chat_id !== "string" || !data.chat_id || typeof data.extension?.type !== "string" || typeof data.extension.fallback !== "string" || !data.extension.payload || typeof data.extension.payload !== "object" || Array.isArray(data.extension.payload)) return;
      const chatId = routeChatId(data);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const msg: ChatMessage = {
          role: "assistant",
          content: "",
          cid: nextCid(),
          ts: Date.now() / 1000,
          extension: data.extension,
        };
        return {
          messages: [
            ...b.messages,
            msg,
          ],
          unread: isBackground ? b.unread + 1 : b.unread,
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("ui_command", (e) => {
    try {
      dispatchUiCommand(JSON.parse(e.data) as UiCommandPayload);
    } catch { /* ignore */ }
  });

  const receiveActivity = (event: MessageEvent<string>) => {
    try {
      const data = JSON.parse(event.data) as { epoch: string; run: ActivityRun };
      if (!data.epoch || !data.run?.id || !Array.isArray(data.run.entries)) return;
      useActivityStore.getState().receive(data.epoch, data.run);
      const source = data.run.source;
      if (source?.channel === "webui" && source.kind === "user") touchSendWatchdog(source.session || DEFAULT_CHAT_ID);
    } catch { /* Ignore invalid event data. */ }
  };
  es.addEventListener("activity", receiveActivity);
  es.addEventListener("activity_end", receiveActivity);

  es.addEventListener("file_diff", (event) => {
    try {
      const data = JSON.parse(event.data) as SseFileDiffEvent;
      if (typeof data.path !== "string" || !data.path) return;
      useChangesStore.getState().bumpFileVersion(data.path);
      useTreeChangesStore.getState().record(data);
    } catch { /* Ignore invalid event data. */ }
  });

  es.addEventListener("context_usage", (e) => {
    try {
      const data = JSON.parse(e.data) as SseContextUsageEvent;
      if (routeChatId(data) !== ctx.getActiveChatId()) return;
      ctx.setContextUsage({
        tokens: data.tokens,
        threshold: data.threshold,
        window: data.window,
        percent: data.percent,
        cache_read_input_tokens: data.cache_read_input_tokens,
        cache_creation_input_tokens: data.cache_creation_input_tokens,
        cache_hit_rate: data.cache_hit_rate,
      });
    } catch { /* ignore */ }
  });

  // ── Plan 模式事件 ────────────────────────────────────────────
  es.addEventListener("plan_submitted", (e) => {
    try {
      const data = JSON.parse(e.data) as SsePlanSubmittedEvent;
      const chatId = routeChatId(data);
      const plan: PlanRecord = {
        plan_id: data.plan_id,
        chat_id: chatId,
        goal: data.goal ?? "",
        steps: (data.steps ?? []).map((s) => ({
          index: s.index,
          content: s.content,
          status: s.status ?? "pending",
          note: s.note ?? "",
        })),
        files: data.files ?? "",
        risks: data.risks ?? "",
        status: "executing",
        created_at: data.ts ?? Date.now() / 1000,
        updated_at: Date.now() / 1000,
      };
      usePlanStore.getState().upsertPlan(plan);
      // 新 plan 出现时自动展开浮窗
      usePlanStore.getState().setPanelHidden(false);
      usePlanStore.getState().setPanelCollapsed(false);
    } catch { /* ignore */ }
  });

  es.addEventListener("plan_step_updated", (e) => {
    try {
      const data = JSON.parse(e.data) as SsePlanStepUpdatedEvent;
      const chatId = routeChatId(data);
      // Web 会话计划同步到任务面板。
      usePlanStore.getState().updatePlanStep(
        chatId,
        data.plan_id,
        data.step_index,
        data.step_status,
        data.note,
      );
    } catch { /* ignore */ }
  });

  es.addEventListener("plan_status_changed", (e) => {
    try {
      const data = JSON.parse(e.data) as SsePlanStatusChangedEvent;
      const chatId = routeChatId(data);
      const status = data.goal_status === "completed"
        ? "completed"
        : data.goal_status === "cancelled"
          ? "cancelled"
          : "executing";
      // 终态反馈由悬浮窗/活动条/聊天卡徽标承担，不再插入消息流通知
      usePlanStore.getState().updatePlanStatus(chatId, data.plan_id, status);
    } catch { /* ignore */ }
  });

  es.addEventListener("plan_cancelled", (e) => {
    try {
      const data = JSON.parse(e.data) as SsePlanCancelledEvent;
      const chatId = routeChatId(data);
      usePlanStore.getState().updatePlanStatus(chatId, data.plan_id, "cancelled", data.reason);
    } catch { /* ignore */ }
  });

  es.addEventListener("plan_deleted", (e) => {
    try {
      const data = JSON.parse(e.data) as SsePlanDeletedEvent;
      const chatId = routeChatId(data);
      usePlanStore.getState().removePlan(chatId, data.plan_id);
    } catch { /* ignore */ }
  });

  // ── 子代理事件 ───────────────────────────────────────────────
  es.addEventListener("delegation_started", (e) => {
    try {
      const data = JSON.parse(e.data) as SseDelegationStartedEvent;
      const chatId = routeChatId(data);
      const node: DelegationNode = {
        delegation_id: data.delegation_id,
        chat_id: chatId,
        goal: data.goal ?? "",
        context_preview: data.context_preview ?? "",
        role: data.role ?? "leaf",
        task_index: data.task_index ?? 0,
        background: !!data.background,
        depth: data.depth ?? 0,
        model: data.model || undefined,
        agent: data.agent || undefined,
        status: "running",
        started_at: data.ts ?? Date.now() / 1000,
      };
      useDelegationStore.getState().upsertDelegation(node);
    } catch { /* ignore */ }
  });

  es.addEventListener("delegation_progress", (e) => {
    try {
      const data = JSON.parse(e.data) as SseDelegationProgressEvent;
      const chatId = routeChatId(data);
      useDelegationStore.getState().updateProgress(chatId, data);
    } catch { /* ignore */ }
  });

  es.addEventListener("delegation_resolved", (e) => {
    try {
      const data = JSON.parse(e.data) as SseDelegationResolvedEvent;
      const chatId = routeChatId(data);
      useDelegationStore.getState().resolveDelegation(
        chatId,
        data.delegation_id,
        !!data.success,
        data.output,
        data.error,
        !!data.cancelled,
      );
    } catch { /* ignore */ }
  });

  es.addEventListener("ping", () => {});
}
