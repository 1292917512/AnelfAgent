// ── Plan 模式（present_plan / update_goal 工具 → SSE 事件 → 前端 PlanPanel/PlanCard） ──

export type PlanStepStatus = "pending" | "in_progress" | "completed" | "skipped";

export interface PlanStep {
  index: number;
  content: string;
  status: PlanStepStatus;
  note: string;
}

export interface PlanRecord {
  plan_id: string;
  chat_id: string;
  goal: string;
  steps: PlanStep[];
  files: string;
  risks: string;
  status: "executing" | "completed" | "cancelled";
  created_at: number;
  updated_at: number;
  completed_at?: number;
  cancel_reason?: string;
}

// ── 子代理（delegate_task 工具 → SSE 事件 → 前端 DelegationCard） ──

export type DelegationStatus = "running" | "completed" | "failed" | "cancelled";

export interface DelegationNode {
  delegation_id: string;
  chat_id: string;
  goal: string;
  context_preview: string;
  role: "leaf" | "orchestrator";
  task_index: number;
  background: boolean;
  depth: number;
  status: DelegationStatus;
  started_at: number;
  resolved_at?: number;
  output?: string;
  error?: string;
}
