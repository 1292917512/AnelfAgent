import { useMemo } from "react";
import { usePlanStore } from "@/stores/plan-store";
import type { PlanRecord, ThinkingSession, TraceNode } from "@/lib/types";
import { textValue } from "./trace-model";

export function sessionChatId(session: ThinkingSession | null): string | null {
  const scope = session?.scope || textValue(session?.nodes.find((node) => node.type === "session_start")?.data.scope);
  const match = /^user_webui:web_user(?:#(.+))?$/.exec(scope);
  return match ? match[1] ?? "default" : null;
}

/** Includes plans created in this trace's conversation and recorded time range. */
export function tracePlanNodes(session: ThinkingSession, plans: Record<string, PlanRecord> | undefined): TraceNode[] {
  const chatId = sessionChatId(session);
  if (!chatId || !plans) return [];
  return Object.values(plans).filter((plan) => plan.chat_id === chatId && plan.created_at >= session.start_time &&
    (!session.end_time || plan.updated_at <= session.end_time)).flatMap((plan) => {
    const rootId = `plan:${plan.plan_id}`;
    const root: TraceNode = {
      id: rootId, type: "plan_root", label: plan.goal, parent_id: null, timestamp: plan.created_at,
      status: plan.status === "completed" ? "completed" : plan.status === "cancelled" ? "warning" : "running",
      duration_ms: plan.completed_at ? (plan.completed_at - plan.created_at) * 1000 : null,
      data: { goal: plan.goal, files: plan.files, risks: plan.risks, step_count: plan.steps.length },
    };
    return [root, ...plan.steps.map((step): TraceNode => ({
      id: `${rootId}:${step.index}`, type: "plan_step", label: step.content, parent_id: rootId,
      timestamp: plan.created_at, duration_ms: null,
      status: step.status === "completed" ? "completed" : step.status === "in_progress" ? "running" : step.status === "skipped" ? "warning" : "pending",
      data: { note: step.note, step_status: step.status },
    }))];
  });
}

export function useTracePlanNodes(session: ThinkingSession | null): TraceNode[] {
  const chatId = sessionChatId(session);
  const plans = usePlanStore((state) => chatId ? state.plans[chatId] : undefined);
  return useMemo(() => session ? tracePlanNodes(session, plans) : [], [session, plans]);
}
