/**
 * Chat SSE 事件路由（/api/chat/stream）。
 *
 * 从 chat-store 拆出：15 种事件的解析与分发；通过 ChatSseContext 回调操作
 * chat-store 状态，plan / delegation 事件分流到对应 store。
 */
import type {
  ChatBucket,
  ChatMessage,
  ChatStreaming,
  ChatStreamingTool,
  ContextUsage,
  DelegationNode,
  PlanRecord,
  SseContextUsageEvent,
  SseDelegationProgressEvent,
  SseDelegationResolvedEvent,
  SseDelegationStartedEvent,
  SseDeltaEvent,
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
  SseToolCallEvent,
  SseTurnEndEvent,
  UiCommandPayload,
} from "@/lib/types";
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
  /** turn_end 无 chat_id 时的兜底：复位所有 sending bucket */
  forEachBucket: (fn: (chatId: string) => void) => void;
}

export function routeChatId(data: SseEventBase): string {
  return typeof data.chat_id === "string" && data.chat_id ? data.chat_id : DEFAULT_CHAT_ID;
}

/**
 * 流式累积器归并：incoming turn_id 缺失（tool_call/file_diff 帧后端不带 turn_id）
 * 或与当前一致时复用当前累积器；仅当非空新 turn_id 到达才开新累积器。
 * 此前 tool_call 帧 turn_id 恒为 ""，不等于 delta 帧的真实 turn_id——每个工具帧
 * 都把已累积的 reasoning/text 整体清空（思考固化恒为空、工具被拆成多张单卡）。
 */
function mergeStreaming(cur: ChatStreaming | null, turnId: string): ChatStreaming {
  if (cur && (!turnId || cur.turnId === turnId)) return cur;
  return { turnId: turnId || cur?.turnId || "", text: "", reasoning: "", tools: [], diffs: [] };
}

/** 展示侧过滤的流程标记工具（end_reply 是收尾机制不是操作，入卡只会制造噪音） */
const META_TOOLS_HIDDEN = new Set(["end_reply"]);

/**
 * 把流式区的本轮工作记录（工具/思考）固化到正式消息上（reply/media/turn_end 到达时调用）。
 * 工具卡片随消息持久展示（默认折叠），刷新后由历史 [已执行操作摘要] 卡片接续；
 * 思考为纯内存态：不落库、不进 LLM 上下文，仅保留最近几轮（见 pruneThinking）。
 */
function solidifyStreaming(b: ChatBucket): {
  toolCalls?: ChatStreamingTool[];
  thinking?: string;
  turnId?: string;
} {
  const s = b.streaming;
  return {
    toolCalls: s && s.tools.length ? [...s.tools] : undefined,
    thinking: s?.reasoning || undefined,
    turnId: s?.turnId,
  };
}

/** 思考驻留上限：仅最近 N 轮保留（往前销毁；纯内存态，刷新即消失） */
const MAX_THINKING_MESSAGES = 5;

/** 思考驻留裁剪：仅保留最近 N 条带 thinking 的消息，更早的销毁（无裁剪时原数组透传） */
function pruneThinking(messages: ChatMessage[]): ChatMessage[] {
  let kept = 0;
  let pruned: ChatMessage[] | null = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (!m.thinking) continue;
    kept += 1;
    if (kept > MAX_THINKING_MESSAGES) {
      pruned ??= [...messages];
      pruned[i] = { ...m, thinking: undefined };
    }
  }
  return pruned ?? messages;
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
        const { toolCalls, thinking, turnId } = solidifyStreaming(b);
        const msg: ChatMessage = { role: "assistant", content: data.content, cid: nextCid(), ts: Date.now() / 1000 };
        if (toolCalls) msg.toolCalls = toolCalls;
        if (thinking) msg.thinking = thinking;
        const changes = turnId ? useChangesStore.getState().settleTurn(turnId) : [];
        if (changes.length) msg.changes = changes;
        return {
          messages: pruneThinking([
            ...b.messages,
            msg,
          ]),
          sending: false,
          sendingSince: null,
          streaming: null,
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
      updateBucket(chatId, (b) => {
        // 兜底固化：reply 未到达（异常/静默收尾）时流式区的工具/改动/思考不丢弃，
        // 合成一条仅卡片的消息承接（正常流程 reply 已固化，streaming 为 null 直接复位）
        const s = b.streaming;
        if (s && (s.tools.length > 0 || s.reasoning || s.diffs.length > 0)) {
          const { toolCalls, thinking, turnId } = solidifyStreaming(b);
          const changes = turnId ? useChangesStore.getState().settleTurn(turnId) : [];
          const msg: ChatMessage = { role: "assistant", content: "", cid: nextCid(), ts: Date.now() / 1000 };
          if (toolCalls) msg.toolCalls = toolCalls;
          if (thinking) msg.thinking = thinking;
          if (changes.length) msg.changes = changes;
          return {
            messages: pruneThinking([...b.messages, msg]),
            sending: false,
            sendingSince: null,
            streaming: null,
          };
        }
        return { sending: false, sendingSince: null, streaming: null };
      });
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
      const chatId = routeChatId(data);
      clearSendWatchdog(chatId);
      const isBackground = chatId !== ctx.getActiveChatId();
      updateBucket(chatId, (b) => {
        const { toolCalls, thinking } = solidifyStreaming(b);
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
        if (thinking) msg.thinking = thinking;
        return {
          messages: pruneThinking([
            ...b.messages,
            msg,
          ]),
          sending: false,
          sendingSince: null,
          streaming: null,
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

  es.addEventListener("delta", (e) => {
    try {
      const data = JSON.parse(e.data) as SseDeltaEvent;
      const chatId = routeChatId(data);
      touchSendWatchdog(chatId);
      updateBucket(chatId, (b) => {
        const cur = mergeStreaming(b.streaming, data.turn_id);
        if (data.reset) {
          // 流式回退重试：清空已渲染增量，等待全量文本重新到达
          return { streaming: { ...cur, text: "", reasoning: "" } };
        }
        const delta = data.delta ?? "";
        return {
          streaming: {
            ...cur,
            text: data.reasoning ? cur.text : cur.text + delta,
            reasoning: data.reasoning ? cur.reasoning + delta : cur.reasoning,
          },
        };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("tool_call", (e) => {
    try {
      const data = JSON.parse(e.data) as SseToolCallEvent;
      const chatId = routeChatId(data);
      touchSendWatchdog(chatId);
      // 流程标记工具不进展示累积（end_reply 等；否则 reply 后会再造一张噪音卡）
      if (META_TOOLS_HIDDEN.has(data.name)) return;
      const frame = {
        call_id: data.call_id,
        name: data.name,
        status: data.status,
        arguments: data.arguments,
        result_preview: data.result_preview,
        duration_ms: data.duration_ms,
      };
      updateBucket(chatId, (b) => {
        // reply 已固化后到达的 TOOL_END（典型：send_message 的结果帧）：
        // 回填最后一条消息卡片里对应工具的状态，而不是新开累积器
        // （否则 turn_end 兜底会再造一张重复卡，且卡片里的工具永远停在 running）
        if (!b.streaming) {
          const last = b.messages[b.messages.length - 1];
          const idx = last?.toolCalls?.findIndex((t) => t.call_id === data.call_id) ?? -1;
          if (last && idx >= 0) {
            const toolCalls = [...last.toolCalls!];
            toolCalls[idx] = { ...toolCalls[idx]!, ...frame };
            const messages = [...b.messages];
            messages[messages.length - 1] = { ...last, toolCalls };
            return { messages };
          }
        }
        const cur = mergeStreaming(b.streaming, data.turn_id ?? "");
        const idx = cur.tools.findIndex((t) => t.call_id === data.call_id);
        const tools = [...cur.tools];
        if (idx >= 0) tools[idx] = { ...tools[idx]!, ...frame };
        else tools.push(frame);
        return { streaming: { ...cur, tools } };
      });
    } catch { /* ignore */ }
  });

  es.addEventListener("file_diff", (e) => {
    try {
      const data = JSON.parse(e.data) as SseFileDiffEvent;
      const chatId = routeChatId(data);
      touchSendWatchdog(chatId);
      const entry = {
        path: data.path,
        diff: data.diff,
        additions: data.additions,
        removals: data.removals,
        move_from: data.move_from,
        binary: data.binary,
      };
      updateBucket(chatId, (b) => {
        const cur = mergeStreaming(b.streaming, data.turn_id ?? "");
        // 改动集聚合必须用归并后的真实 turnId：file_diff 帧后端不带 turn_id，
        // 直接拿帧里的空串会漏聚合（改动集卡片从未出现的原因之一）
        if (cur.turnId) useChangesStore.getState().recordDiff(cur.turnId, entry);
        useChangesStore.getState().bumpFileVersion(data.path);
        // 树变更装饰（徽章/闪现/目录局部刷新的数据源）
        useTreeChangesStore.getState().record(entry);
        return {
          streaming: {
            ...cur,
            diffs: [...cur.diffs, entry],
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
      // 步骤进度只更新 PlanCard/执行记录面板，不再插入消息流（避免刷屏）
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
