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
  /** 难度分级/命名档案解析后的模型 ID；空串/缺省 = 默认模型 */
  model?: string;
  /** 命名子代理档案名（delegate_task.agent_name，空 = 未指定） */
  agent?: string;
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
  /** 命名子代理档案名（delegate_task.agent_name，空 = 未指定） */
  agent?: string;
  elapsed_seconds: number;
}

// ── 全局子代理总览（Dashboard「子代理」面板 → /delegations/*） ──

/** GET /delegations/overview 的全局运行中委托条目（全 scope，含实时进度与用量） */
export interface DelegationOverviewItem {
  delegation_id: string;
  goal: string;
  role: "leaf" | "orchestrator";
  task_index: number;
  background: boolean;
  model?: string;
  agent?: string;
  elapsed_seconds: number;
  usage: {
    turns?: number;
    input_tokens?: number;
    output_tokens?: number;
    duration_ms?: number;
  };
  /** 归属会话 scope（user_{adapter}:{uid} 格式） */
  scope: string;
  chat_id: string;
  started_at: number;
  /** 实时进度：当前思考轮次（从 1 起，0 = 尚未进入首轮） */
  iteration: number;
  /** 实时进度：正在执行的工具名（空 = 无） */
  current_tool: string;
}

/** GET /delegations/history 的近期执行条目（账本 started/closed 配对折叠） */
export interface DelegationHistoryItem {
  delegation_id: string;
  goal: string;
  scope: string;
  agent?: string;
  model?: string;
  adapter_key: string;
  /** 终态：成功/失败/已取消/lost（进程中断） */
  status: string;
  started_at: number;
  finished_at: number;
  duration_seconds: number;
}

/** GET /delegations/{id}/progress 的进度流尾部 */
export interface DelegationProgress {
  delegation_id: string;
  lines: string[];
  truncated: boolean;
  running: boolean;
}
