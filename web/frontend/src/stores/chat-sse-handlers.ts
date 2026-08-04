/**
 * Chat SSE 事件路由（/api/chat/stream）。
 *
 * 从 chat-store 拆出：15 种事件的解析与分发；通过 ChatSseContext 回调操作
 * chat-store 状态，plan / delegation / approval 事件分流到对应 store。
 */
import type {
  ChatBucket,
  ChatMessage,
  ChatStreamingTool,
  ContextUsage,
  DelegationNode,
  PlanRecord,
  SseApprovalRequestEvent,
  SseContextUsageEvent,
  SseDelegationProgressEvent,
  SseDelegationResolvedEvent,
  SseDelegationStartedEvent,
  SseDeltaEvent,
  SseEventBase,
  SseFileDiffEvent,
  SseMediaEvent,
  SsePlanCancelledEvent,
  SsePlanStatusChangedEvent,
  SsePlanStepUpdatedEvent,
  SsePlanSubmittedEvent,
  SseReplyEvent,
  SseShareEvent,
  SseToolCallEvent,
  SseTurnEndEvent,
  UiCommandPayload,
} from "@/lib/types";
import { useApprovalPopupStore } from "./approval-popup-store";
import { useDelegationStore } from "./delegation-store";
import { usePlanStore } from "./plan-store";
import { useWorkbenchStore } from "./workbench-store";
import { clearSendWatchdog, nextCid, DEFAULT_CHAT_ID } from "./chat-shared";

export interface ChatSseContext {
  updateBucket: (chatId: string, fn: (b: ChatBucket) => Partial<ChatBucket>) => void;
  getActiveChatId: () => string;
  setContextUsage: (usage: ContextUsage) => void;
  /** turn_end 无 chat_id 时的兜底：复位所有 sending bucket */
  forEachBucket: (fn: (chatId: string) => void) => void;
}

export function routeChatId(data: SseEventBase): string {
  return typeof data.chat_id === "string" && data.chat_id ? data.chat_id : DEFAULT_CHAT_ID;
}

/**
 * 把流式区的工具调用记录固化到正式消息上（reply/media 到达时调用）。
 * 工具卡片随消息持久展示（默认折叠），刷新后由历史 [已执行操作摘要] 卡片接续。
 */
function solidifyToolCalls(b: ChatBucket): { toolCalls?: ChatStreamingTool[]; streaming: null } {
  const tools = b.streaming?.tools;
  return { toolCalls: tools && tools.length ? [...tools] : undefined, streaming: null };
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

  es.addEventListener("reply", (e) => {
    try {
      const data = JSON.parse(e.data) as SseReplyEvent;
      clearSendWatchdog();
      const chatId = routeChatId(data);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const { toolCalls, streaming } = solidifyToolCalls(b);
        const msg: ChatMessage = { role: "assistant", content: data.content, cid: nextCid(), ts: Date.now() / 1000 };
        if (toolCalls) msg.toolCalls = toolCalls;
        return {
          messages: [
            ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
            msg,
          ],
          sending: false,
          sendingSince: null,
          streaming,
          unread: isBackground ? b.unread + 1 : b.unread,
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("turn_end", (e) => {
    clearSendWatchdog();
    try {
      const data = (e.data ? JSON.parse(e.data) : {}) as SseTurnEndEvent;
      const chatId = routeChatId(data);
      updateBucket(chatId, () => ({
        sending: false,
        sendingSince: null,
        streaming: null,
      }));
    } catch {
      // 无 chat_id：对所有 sending bucket 复位（兜底）
      ctx.forEachBucket((cid) => {
        updateBucket(cid, () => ({ sending: false, sendingSince: null, streaming: null }));
      });
    }
  });

  es.addEventListener("media", (e) => {
    try {
      const data = JSON.parse(e.data) as SseMediaEvent;
      clearSendWatchdog();
      const chatId = routeChatId(data);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const { toolCalls, streaming } = solidifyToolCalls(b);
        const msg: ChatMessage = {
          role: "assistant",
          content: data.caption || "",
          cid: nextCid(),
          ts: Date.now() / 1000,
          media_type: data.media_type,
          url: data.url,
          caption: data.caption,
        };
        if (toolCalls) msg.toolCalls = toolCalls;
        return {
          messages: [
            ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
            msg,
          ],
          sending: false,
          sendingSince: null,
          streaming,
          unread: isBackground ? b.unread + 1 : b.unread,
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("share", (e) => {
    try {
      const data = JSON.parse(e.data) as SseShareEvent;
      if (!data.token) return;
      const chatId = routeChatId(data);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const msg: ChatMessage = {
          role: "assistant",
          content: "",
          cid: nextCid(),
          ts: Date.now() / 1000,
          share: {
            token: data.token,
            url: data.url,
            download_url: data.download_url,
            share_type: data.share_type,
            media_kind: data.media_kind,
            target_url: data.target_url,
            file_name: data.file_name,
            file_size: data.file_size,
            description: data.description,
          },
        };
        return {
          messages: [
            ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
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

  es.addEventListener("approval_request", (e) => {
    try {
      const data = JSON.parse(e.data) as SseApprovalRequestEvent;
      useApprovalPopupStore.getState().push({
        request_id: data.request_id,
        tool_name: data.tool_name,
        tool_args: data.tool_args ?? "",
        risk_level: data.risk_level ?? "medium",
        reason: data.reason ?? "",
        timeout_seconds: data.timeout_seconds ?? 60,
        received_at: Date.now(),
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("delta", (e) => {
    try {
      const data = JSON.parse(e.data) as SseDeltaEvent;
      clearSendWatchdog();
      const chatId = routeChatId(data);
      updateBucket(chatId, (b) => {
        const cur = b.streaming && b.streaming.turnId === data.turn_id
          ? b.streaming
          : { turnId: data.turn_id, text: "", reasoning: "", tools: [], diffs: [] };
        return {
          streaming: {
            ...cur,
            text: data.reasoning ? cur.text : cur.text + data.delta,
            reasoning: data.reasoning ? cur.reasoning + data.delta : cur.reasoning,
          },
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("tool_call", (e) => {
    try {
      const data = JSON.parse(e.data) as SseToolCallEvent;
      const chatId = routeChatId(data);
      updateBucket(chatId, (b) => {
        const turnId = data.turn_id ?? "";
        const cur = b.streaming && b.streaming.turnId === turnId
          ? b.streaming
          : { turnId, text: "", reasoning: "", tools: [], diffs: [] };
        const idx = cur.tools.findIndex((t) => t.call_id === data.call_id);
        const tools = [...cur.tools];
        const frame = {
          call_id: data.call_id,
          name: data.name,
          status: data.status,
          arguments: data.arguments,
          result_preview: data.result_preview,
          duration_ms: data.duration_ms,
        };
        if (idx >= 0) tools[idx] = { ...tools[idx], ...frame };
        else tools.push(frame);
        return { streaming: { ...cur, tools } };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("file_diff", (e) => {
    try {
      const data = JSON.parse(e.data) as SseFileDiffEvent;
      const chatId = routeChatId(data);
      updateBucket(chatId, (b) => {
        const turnId = data.turn_id ?? "";
        const cur = b.streaming && b.streaming.turnId === turnId
          ? b.streaming
          : { turnId, text: "", reasoning: "", tools: [], diffs: [] };
        return {
          streaming: {
            ...cur,
            diffs: [...cur.diffs, {
              path: data.path,
              diff: data.diff,
              additions: data.additions,
              removals: data.removals,
            }].slice(-3),
          },
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("context_usage", (e) => {
    try {
      const data = JSON.parse(e.data) as SseContextUsageEvent;
      ctx.setContextUsage({
        tokens: data.tokens,
        threshold: data.threshold,
        window: data.window,
        percent: data.percent,
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
      // 步骤进度只更新 PlanCard/PlanPanel 浮窗，不再插入消息流（避免刷屏）
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
