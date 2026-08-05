import type { PlanStepStatus } from "./plan";
import type { ChatShareInfo } from "./share";

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
  /** epoch 秒：历史消息来自后端 ts_ns，本地/SSE 消息为到达时刻；时间线合排用 */
  ts?: number;
  id?: number;
  queued?: boolean;
  cid?: string;
  media_type?: string;
  url?: string;
  caption?: string;
  /** 结构化消息种类：tool_summary=工具执行摘要卡片 / system_notice=系统提示细条 */
  kind?: "tool_summary" | "system_notice";
  /** 警示色调（发送超时/失败等 system_notice 用） */
  tone?: "warn";
  /** 本轮工具调用记录（reply 到达时从流式区固化，渲染为消息内折叠卡片） */
  toolCalls?: ChatStreamingTool[];
  /** 分享卡片信息（share SSE 事件到达时挂载，渲染为 ShareCard） */
  share?: ChatShareInfo;
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
  /** 已加载历史中最早一条的 DB id（"加载更早"分页游标） */
  earliestId?: number;
  /** 是否可能还有更早历史（上次分页拉满 limit 时置真） */
  hasMore?: boolean;
  /** "加载更早"请求进行中 */
  loadingEarlier?: boolean;
}

export interface ContextUsage {
  tokens: number;
  threshold: number;
  window: number;
  percent: number;
  /** 供应商侧缓存命中 tokens（最近一次 LLM 调用，无缓存协议时为 0） */
  cache_read_input_tokens?: number;
  /** 供应商侧缓存写入 tokens（最近一次 LLM 调用） */
  cache_creation_input_tokens?: number;
  /** 本轮 prompt 缓存命中率 0~1 */
  cache_hit_rate?: number;
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

export type SseShareEvent = SseEventBase & ChatShareInfo;

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
  /** 难度分级解析后的模型 ID；空串/缺省 = 默认模型 */
  model?: string;
  ts?: number;
}

export interface SseDelegationProgressEvent extends SseEventBase {
  delegation_id: string;
  /** round=新思考轮次 / tool_start=工具开始 / tool_end=工具结束 */
  kind?: "round" | "tool_start" | "tool_end";
  iteration?: number;
  tool?: string;
  success?: boolean;
  ts?: number;
}

export interface SseDelegationResolvedEvent extends SseEventBase {
  delegation_id: string;
  success?: boolean;
  output?: string;
  error?: string;
  cancelled?: boolean;
}

/** SSE 事件名 → data 类型的判别映射（事件名为判别字段） */
export interface ChatSseEventMap {
  reply: SseReplyEvent;
  turn_end: SseTurnEndEvent;
  media: SseMediaEvent;
  share: SseShareEvent;
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
  delegation_progress: SseDelegationProgressEvent;
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
  /** epoch 秒（后端 ts_ns 换算），时间线合排用 */
  ts?: number;
  kind?: "tool_summary" | "system_notice";
}
