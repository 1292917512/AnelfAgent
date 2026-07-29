/**
 * Chat SSE 事件路由（/api/chat/stream）。
 *
 * 从 chat-store 拆出：15 种事件的解析与分发；通过 ChatSseContext 回调操作
 * chat-store 状态，plan / delegation / approval 事件分流到对应 store。
 */
import i18n from "@/i18n";
import type {
  ChatBucket,
  ContextUsage,
  DelegationNode,
  PlanRecord,
  SseApprovalRequestEvent,
  SseContextUsageEvent,
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
  SseToolCallEvent,
  SseTurnEndEvent,
  UiCommandPayload,
} from "@/lib/types";
import { useApprovalPopupStore } from "./approval-popup-store";
import { useDelegationStore } from "./delegation-store";
import { usePlanStore } from "./plan-store";
import { useWorkbenchStore } from "./workbench-store";
import { clearSendWatchdog, nextCid, DEFAULT_CHAT_ID } from "./chat-shared";

/**
 * 程序级自动事件的 note 文案白名单（"阶段反馈"系统消息据此过滤）。
 *
 * 注意：后端 agent/planning/tracker.py 的 plan_step_updated 事件 payload 仅有
 * note 文案、无结构化类型字段（scope/chat_id/plan_id/step_index/step_status/note/ts），
 * 且前端禁止改动 Python 文件，故这里只能依赖后端固定文案匹配。
 * "自动推进"（每轮兜底推进）、"会话结束自动收束"/"会话结束未执行"（finalize_plan
 * 收敛）由程序触发、每轮一次，全部插入会刷屏；PlanCard/PlanPanel 已实时反映状态。
 */
const PLAN_STEP_AUTO_NOTES: readonly string[] = [
  "自动推进",
  "会话结束自动收束",
  "会话结束未执行",
];

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
      updateBucket(chatId, (b) => ({
        messages: [
          ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
          { role: "assistant", content: data.content, cid: nextCid() },
        ],
        sending: false,
        sendingSince: null,
        streaming: null,
        unread: isBackground ? b.unread + 1 : b.unread,
      }));
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
      updateBucket(chatId, (b) => ({
        messages: [
          ...b.messages.map((m) => (m.queued ? { ...m, queued: undefined } : m)),
          {
            role: "assistant",
            content: data.caption || "",
            cid: nextCid(),
            media_type: data.media_type,
            url: data.url,
            caption: data.caption,
          },
        ],
        sending: false,
        sendingSince: null,
        streaming: null,
        unread: isBackground ? b.unread + 1 : b.unread,
      }));
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
      usePlanStore.getState().updatePlanStep(
        chatId,
        data.plan_id,
        data.step_index,
        data.step_status,
        data.note,
      );
      // 阶段反馈：**只在 AI 精确标记时**插入系统消息；程序级自动事件
      // （见 PLAN_STEP_AUTO_NOTES 注释）每轮一次，全部插入会刷屏。
      if (data.note && PLAN_STEP_AUTO_NOTES.includes(data.note)) {
        return;
      }
      const plan = usePlanStore.getState().plans[chatId]?.[data.plan_id];
      if (plan && data.step_status !== "pending") {
        const step = plan.steps.find((s) => s.index === data.step_index);
        const stepText = step?.content ?? i18n.t("plan.stepFallback", { ns: "plan", index: (data.step_index ?? 0) + 1 });
        const icon = data.step_status === "completed" ? "✓"
          : data.step_status === "skipped" ? "⊘"
            : "→";
        updateBucket(chatId, (b) => ({
          messages: [...b.messages, {
            role: "system",
            content: i18n.t("plan.stepProgress", {
              ns: "plan",
              icon,
              index: (data.step_index ?? 0) + 1,
              total: plan.steps.length,
              step: stepText,
              note: data.note ? ` — ${data.note}` : "",
            }),
            cid: nextCid(),
          }],
        }));
      }
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
      usePlanStore.getState().updatePlanStatus(chatId, data.plan_id, status);
      // 整体完成/取消时插入消息
      if (status === "completed" || status === "cancelled") {
        const plan = usePlanStore.getState().plans[chatId]?.[data.plan_id];
        const goalText = plan?.goal ?? i18n.t("plan.fallbackTitle", { ns: "plan" });
        updateBucket(chatId, (b) => ({
          messages: [...b.messages, {
            role: "system",
            content: status === "completed"
              ? i18n.t("plan.completedMsg", { ns: "plan", goal: goalText })
              : i18n.t("plan.cancelledMsg", { ns: "plan", goal: goalText }),
            cid: nextCid(),
          }],
        }));
      }
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
        status: "running",
        started_at: data.ts ?? Date.now() / 1000,
      };
      useDelegationStore.getState().upsertDelegation(node);
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
      );
    } catch { /* ignore */ }
  });

  es.addEventListener("ping", () => {});
}
