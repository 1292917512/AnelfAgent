/**
 * 子代理状态 store — 按 chat_id 分桶的子代理执行记录。
 *
 * 数据源：SSE delegation_started / delegation_progress / delegation_resolved 事件。
 * 消费方：DelegationCard 消息卡 + PlanPanel 浮窗内的子代理子树。
 */
import { create } from "zustand";
import type { DelegationNode } from "@/lib/types";

const MAX_DELEGATIONS_PER_CHAT = 50;

interface DelegationState {
  /** chat_id → delegation_id → DelegationNode */
  delegations: Record<string, Record<string, DelegationNode>>;

  upsertDelegation: (node: DelegationNode) => void;
  resolveDelegation: (
    chatId: string,
    delegationId: string,
    success: boolean,
    output?: string,
    error?: string,
  ) => void;
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

  resolveDelegation: (chatId, delegationId, success, output, error) =>
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
              status: success ? "completed" : "failed",
              resolved_at: Date.now() / 1000,
              output: output ?? node.output,
              error: error ?? node.error,
            },
          },
        },
      };
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
