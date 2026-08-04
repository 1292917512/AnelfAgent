/**
 * 子代理状态 store — 按 chat_id 分桶的子代理执行记录。
 *
 * 数据源：SSE delegation_started / delegation_progress / delegation_resolved 事件。
 * 消费方：DelegationCard 消息卡 + PlanPanel 浮窗内的子代理子树。
 */
import { create } from "zustand";
import type { DelegationNode, RunningDelegation, SseDelegationProgressEvent } from "@/lib/types";

const MAX_DELEGATIONS_PER_CHAT = 50;

interface DelegationState {
  /** chat_id → delegation_id → DelegationNode */
  delegations: Record<string, Record<string, DelegationNode>>;

  upsertDelegation: (node: DelegationNode) => void;
  updateProgress: (chatId: string, data: SseDelegationProgressEvent) => void;
  markCancelling: (chatId: string, delegationId: string) => void;
  resolveDelegation: (
    chatId: string,
    delegationId: string,
    success: boolean,
    output?: string,
    error?: string,
    cancelled?: boolean,
  ) => void;
  /** 刷新后恢复运行中的委托卡片（GET /chat/delegations） */
  rehydrate: (chatId: string, running: RunningDelegation[]) => void;
  getChatDelegations: (chatId: string) => DelegationNode[];
  clear: (chatId: string) => void;
}

function sortDelegations(list: DelegationNode[]): DelegationNode[] {
  return [...list].sort((a, b) => b.started_at - a.started_at);
}

export const useDelegationStore = create<DelegationState>((set, get) => ({
  delegations: {},

  upsertDelegation: (node) =>
    set((s) => {
      const chatNodes = { ...(s.delegations[node.chat_id] ?? {}) };
      chatNodes[node.delegation_id] = node;
      const all = Object.values(chatNodes);
      if (all.length > MAX_DELEGATIONS_PER_CHAT) {
        const sorted = sortDelegations(all).slice(0, MAX_DELEGATIONS_PER_CHAT);
        const keep: Record<string, DelegationNode> = {};
        for (const n of sorted) keep[n.delegation_id] = n;
        return { delegations: { ...s.delegations, [node.chat_id]: keep } };
      }
      return { delegations: { ...s.delegations, [node.chat_id]: chatNodes } };
    }),

  updateProgress: (chatId, data) =>
    set((s) => {
      const node = s.delegations[chatId]?.[data.delegation_id];
      if (!node || node.status !== "running") return {};
      const patch: Partial<DelegationNode> = {};
      if (data.kind === "round" && data.iteration != null) {
        patch.iteration = data.iteration + 1; // 后端从 0 起，展示从 1 起
      }
      if (data.kind === "tool_start" && data.tool) {
        patch.current_tool = data.tool;
      }
      if (data.kind === "tool_end") {
        patch.current_tool = "";
      }
      if (Object.keys(patch).length === 0) return {};
      return {
        delegations: {
          ...s.delegations,
          [chatId]: {
            ...s.delegations[chatId],
            [data.delegation_id]: { ...node, ...patch },
          },
        },
      };
    }),

  markCancelling: (chatId, delegationId) =>
    set((s) => {
      const node = s.delegations[chatId]?.[delegationId];
      if (!node || node.status !== "running") return {};
      return {
        delegations: {
          ...s.delegations,
          [chatId]: {
            ...s.delegations[chatId],
            [delegationId]: { ...node, cancelling: true },
          },
        },
      };
    }),

  resolveDelegation: (chatId, delegationId, success, output, error, cancelled) =>
    set((s) => {
      const node = s.delegations[chatId]?.[delegationId];
      if (!node) return {};
      return {
        delegations: {
          ...s.delegations,
          [chatId]: {
            ...s.delegations[chatId],
            [delegationId]: {
              ...node,
              status: cancelled ? "cancelled" : success ? "completed" : "failed",
              resolved_at: Date.now() / 1000,
              output: output ?? node.output,
              error: error ?? node.error,
              cancelling: undefined,
              current_tool: undefined,
            },
          },
        },
      };
    }),

  rehydrate: (chatId, running) =>
    set((s) => {
      if (!running.length) return {};
      const chatNodes = { ...(s.delegations[chatId] ?? {}) };
      const now = Date.now() / 1000;
      let changed = false;
      for (const r of running) {
        const existing = chatNodes[r.delegation_id];
        // 已存在且非 running（ SSE 已推 resolved）时不回退状态
        if (existing && existing.status !== "running") continue;
        chatNodes[r.delegation_id] = {
          delegation_id: r.delegation_id,
          chat_id: chatId,
          goal: r.goal,
          context_preview: existing?.context_preview ?? "",
          role: r.role,
          task_index: r.task_index,
          background: r.background,
          depth: existing?.depth ?? 0,
          model: r.model ?? existing?.model,
          status: "running",
          started_at: existing?.started_at ?? now - r.elapsed_seconds,
          iteration: existing?.iteration,
          current_tool: existing?.current_tool,
        };
        changed = true;
      }
      if (!changed) return {};
      return { delegations: { ...s.delegations, [chatId]: chatNodes } };
    }),

  getChatDelegations: (chatId) => {
    const s = get();
    const nodes = s.delegations[chatId] ? Object.values(s.delegations[chatId]) : [];
    return sortDelegations(nodes);
  },

  clear: (chatId) =>
    set((s) => {
      const delegations = { ...s.delegations };
      delete delegations[chatId];
      return { delegations };
    }),
}));
