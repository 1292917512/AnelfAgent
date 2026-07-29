import type { PlanStepStatus } from "./plan";

// ── 多会话（chat_id 维度分桶） ──

export interface ChatMeta {
  chat_id: string;
  title: string;
  last_ts: number;
  message_count: number;
  unread?: number;
}

// ── 聊天消息与分桶状态（chat-store） ──

export interface ChatMessage {
  role: string;
  content: string;
  timestamp?: string;
  id?: number;
  queued?: boolean;
  cid?: string;
  media_type?: string;
  url?: string;
  caption?: string;
}

export interface PendingFile {
  file: File;
  preview?: string;
  type: string;
  uploading: boolean;
  path?: string;
}

export interface ChatStreamingTool {
  call_id: string;
  name: string;
  status: "running" | "done" | "error";
  arguments?: string;
  result_preview?: string;
  duration_ms?: number;
}

export interface ChatStreamingDiff {
  path: string;
  diff: string;
  additions: number;
  removals: number;
}

export interface ChatStreaming {
  turnId: string;
  text: string;
  reasoning: string;
  tools: ChatStreamingTool[];
  diffs: ChatStreamingDiff[];
}

export interface ChatBucket {
  messages: ChatMessage[];
  sending: boolean;
  sendingSince: number | null;
  streaming: ChatStreaming | null;
  pendingFiles: PendingFile[];
  historyLoaded: boolean;
  /** 非激活会话收到新消息时的未读计数（切换会话时清零） */
  unread: number;
}

export interface ContextUsage {
  tokens: number;
  threshold: number;
  window: number;
  percent: number;
}

/** ui_command SSE 事件 payload（工作台交互指令） */
export interface UiCommandPayload {
  command: string;
  id?: string;
  title?: string;
  content?: string;
  level?: string;
  ts?: number;
  ask_id?: string;
  question?: string;
  options?: string[];
  panel?: string;
  payload?: string;
  text?: string;
}

/** 工作台状态上报（POST /chat/ui-state 的 state 字段，供 AI ui_get_state 查询） */
export interface UiStateReport {
  active_tab: string;
  dock_open: boolean;
  left_open: boolean;
  open_file: string | null;
  has_draft: boolean;
  pending_asks: number;
}

// ── SSE 事件 data 类型（/api/chat/stream） ──
// 判别依据为 addEventListener 的事件名；所有事件均可选携带 chat_id 用于路由。

export interface SseEventBase {
  chat_id?: string;
}

export interface SseReplyEvent extends SseEventBase {
  content: string;
}

export type SseTurnEndEvent = SseEventBase;

export interface SseMediaEvent extends SseEventBase {
  media_type?: string;
  url?: string;
  caption?: string;
}

export interface SseDeltaEvent extends SseEventBase {
  turn_id: string;
  delta: string;
  reasoning?: boolean;
}

export interface SseToolCallEvent extends SseEventBase {
  turn_id?: string;
  call_id: string;
  name: string;
  status: "running" | "done" | "error";
  arguments?: string;
  result_preview?: string;
  duration_ms?: number;
}

export interface SseFileDiffEvent extends SseEventBase {
  turn_id?: string;
  path: string;
  diff: string;
  additions: number;
  removals: number;
}

export type SseContextUsageEvent = ContextUsage;

export interface SseApprovalRequestEvent {
  request_id: string;
  tool_name: string;
  tool_args?: string;
  risk_level?: string;
  reason?: string;
  timeout_seconds?: number;
}

export interface SsePlanStepInput {
  index: number;
  content: string;
  status?: PlanStepStatus;
  note?: string;
}

export interface SsePlanSubmittedEvent extends SseEventBase {
  plan_id: string;
  goal?: string;
  steps?: SsePlanStepInput[];
  files?: string;
  risks?: string;
  ts?: number;
}

export interface SsePlanStepUpdatedEvent extends SseEventBase {
  plan_id: string;
  step_index: number;
  step_status: PlanStepStatus;
  note?: string;
}

export interface SsePlanStatusChangedEvent extends SseEventBase {
  plan_id: string;
  goal_status?: string;
}

export interface SsePlanCancelledEvent extends SseEventBase {
  plan_id: string;
  reason?: string;
}

export interface SseDelegationStartedEvent extends SseEventBase {
  delegation_id: string;
  goal?: string;
  context_preview?: string;
  role?: "leaf" | "orchestrator";
  task_index?: number;
  background?: boolean;
  depth?: number;
  ts?: number;
}

export interface SseDelegationResolvedEvent extends SseEventBase {
  delegation_id: string;
  success?: boolean;
  output?: string;
  error?: string;
}

/** SSE 事件名 → data 类型的判别映射（事件名为判别字段） */
export interface ChatSseEventMap {
  reply: SseReplyEvent;
  turn_end: SseTurnEndEvent;
  media: SseMediaEvent;
  ui_command: UiCommandPayload;
  approval_request: SseApprovalRequestEvent;
  delta: SseDeltaEvent;
  tool_call: SseToolCallEvent;
  file_diff: SseFileDiffEvent;
  context_usage: SseContextUsageEvent;
  plan_submitted: SsePlanSubmittedEvent;
  plan_step_updated: SsePlanStepUpdatedEvent;
  plan_status_changed: SsePlanStatusChangedEvent;
  plan_cancelled: SsePlanCancelledEvent;
  delegation_started: SseDelegationStartedEvent;
  delegation_resolved: SseDelegationResolvedEvent;
}

export type ChatSseEventName = keyof ChatSseEventMap;

/** 所有 SSE 事件 data 的判别联合（按事件名收窄） */
export type ChatSseEventData = ChatSseEventMap[ChatSseEventName];

/** 历史消息（GET /chat/history 返回项） */
export interface ChatHistoryMessage {
  id?: number;
  role: string;
  content: string;
  timestamp?: string;
}
