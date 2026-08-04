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
  /** 难度分级解析后的模型 ID；空串/缺省 = 默认模型 */
  model?: string;
  status: DelegationStatus;
  started_at: number;
  resolved_at?: number;
  output?: string;
  error?: string;
  /** 实时进度：当前思考轮次（delegation_progress 事件） */
  iteration?: number;
  /** 实时进度：最近使用的工具名 */
  current_tool?: string;
  /** 用户已点击取消、等待后端 resolved 确认 */
  cancelling?: boolean;
}

/** GET /chat/delegations 返回的运行中委托快照（刷新后恢复卡片用） */
export interface RunningDelegation {
  delegation_id: string;
  goal: string;
  role: "leaf" | "orchestrator";
  task_index: number;
  background: boolean;
  model?: string;
  elapsed_seconds: number;
}
