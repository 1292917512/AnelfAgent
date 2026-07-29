/**
 * Plan 状态 store — 按 chat_id 分桶的计划记录。
 *
 * 数据源：SSE plan_submitted / plan_step_updated / plan_status_changed / plan_cancelled
 * 事件（由 webui adapter 转发），消费方：PlanPanel 浮窗 + PlanCard 消息卡 + FlowView 叠加。
 *
 * 与 chat-store 解耦：plan 状态独立订阅，可跨页面持久；切 chat 时按 activeChatId 过滤。
 */
import { create } from "zustand";
import type { PlanRecord, PlanStep } from "@/lib/types";

const MAX_PLANS_PER_CHAT = 50;

interface PlanState {
  /** chat_id → plan_id → PlanRecord */
  plans: Record<string, Record<string, PlanRecord>>;
  /** chat_id → 最近活跃 plan_id（浮窗默认展示） */
  activePlanByChat: Record<string, string>;
  /** 浮窗展开状态（用户可折叠成 icon） */
  panelCollapsed: boolean;
  /** 浮窗用户主动隐藏（仅本次会话，新建 plan 重新出现） */
  panelHidden: boolean;

  upsertPlan: (plan: PlanRecord) => void;
  updatePlanStep: (
    chatId: string,
    planId: string,
    stepIndex: number,
    status: PlanStep["status"],
    note?: string,
  ) => void;
  updatePlanStatus: (
    chatId: string,
    planId: string,
    status: PlanRecord["status"],
    reason?: string,
  ) => void;
  setActivePlan: (chatId: string, planId: string) => void;
  getActivePlan: (chatId: string) => PlanRecord | null;
  getChatPlans: (chatId: string) => PlanRecord[];
  setPanelCollapsed: (v: boolean) => void;
  setPanelHidden: (v: boolean) => void;
  clear: (chatId: string) => void;
}

function sortPlans(plans: PlanRecord[]): PlanRecord[] {
  return [...plans].sort((a, b) => b.created_at - a.created_at);
}

export const usePlanStore = create<PlanState>((set, get) => ({
  plans: {},
  activePlanByChat: {},
  panelCollapsed: false,
  panelHidden: false,

  upsertPlan: (plan) =>
    set((s) => {
      const chatPlans = { ...(s.plans[plan.chat_id] ?? {}) };
      chatPlans[plan.plan_id] = plan;
      // 每个 chat 只保留最近 N 条
      const all = Object.values(chatPlans);
      if (all.length > MAX_PLANS_PER_CHAT) {
        const sorted = sortPlans(all).slice(0, MAX_PLANS_PER_CHAT);
        const keep: Record<string, PlanRecord> = {};
        for (const p of sorted) keep[p.plan_id] = p;
        return {
          plans: { ...s.plans, [plan.chat_id]: keep },
          activePlanByChat: { ...s.activePlanByChat, [plan.chat_id]: plan.plan_id },
        };
      }
      return {
        plans: { ...s.plans, [plan.chat_id]: chatPlans },
        activePlanByChat: { ...s.activePlanByChat, [plan.chat_id]: plan.plan_id },
      };
    }),

  updatePlanStep: (chatId, planId, stepIndex, status, note) =>
    set((s) => {
      const plan = s.plans[chatId]?.[planId];
      if (!plan) return {};
      const steps = plan.steps.map((st) =>
        st.index === stepIndex
          ? { ...st, status, note: note ?? st.note }
          : st,
      );
      return {
        plans: {
          ...s.plans,
          [chatId]: {
            ...s.plans[chatId],
            [planId]: { ...plan, steps, updated_at: Date.now() / 1000 },
          },
        },
      };
    }),

  updatePlanStatus: (chatId, planId, status, reason) =>
    set((s) => {
      const plan = s.plans[chatId]?.[planId];
      if (!plan) return {};
      return {
        plans: {
          ...s.plans,
          [chatId]: {
            ...s.plans[chatId],
            [planId]: {
              ...plan,
              status,
              cancel_reason: reason,
              completed_at: status === "completed" ? Date.now() / 1000 : plan.completed_at,
              updated_at: Date.now() / 1000,
            },
          },
        },
      };
    }),

  setActivePlan: (chatId, planId) =>
    set((s) => ({
      activePlanByChat: { ...s.activePlanByChat, [chatId]: planId },
    })),

  getActivePlan: (chatId) => {
    const s = get();
    const planId = s.activePlanByChat[chatId];
    if (planId && s.plans[chatId]?.[planId]) {
      return s.plans[chatId][planId];
    }
    // fallback：返回该 chat 最近的 executing plan
    const plans = s.plans[chatId] ? Object.values(s.plans[chatId]) : [];
    const executing = plans.filter((p) => p.status === "executing");
    if (executing.length) {
      const sorted = sortPlans(executing);
      return sorted[0] ?? null;
    }
    return null;
  },

  getChatPlans: (chatId) => {
    const s = get();
    const plans = s.plans[chatId] ? Object.values(s.plans[chatId]) : [];
    return sortPlans(plans);
  },

  setPanelCollapsed: (v) => set({ panelCollapsed: v }),
  setPanelHidden: (v) => set({ panelHidden: v }),

  clear: (chatId) =>
    set((s) => {
      const plans = { ...s.plans };
      const active = { ...s.activePlanByChat };
      delete plans[chatId];
      delete active[chatId];
      return { plans, activePlanByChat: active };
    }),
}));
