import type { PlanStepStatus } from "./plan";
import type { JsonObject } from "./json";

export interface ChatExtension {
  type: string;
  payload: JsonObject;
  fallback: string;
}

// ── 多会话（chat_id 维度分桶） ──

export interface ChatMeta {
  chat_id: string;
  title: string;
  last_ts: number;
  message_count: number;
  unread?: number;
}

// ── 聊天消息与分桶状态（chat-store） ──

export interface ToolSummaryEntry {
  call: string;
  result: string;
}

/** 工具执行摘要的结构化数据（后端历史清洗随 kind=tool_summary 消息附带） */
export interface ToolSummaryData {
  count: number;
  entries: ToolSummaryEntry[];
}

export interface ChatMessage {
  role: string;
  content: string;
  timestamp?: string;
  /** epoch 秒：历史消息来自后端 ts_ns，本地/SSE 消息为到达时刻；时间线合排用 */
  ts?: number;
  id?: number;
  delivery?: "submitting" | "submitted" | "failed";
  cid?: string;
  media_type?: string;
  url?: string;
  caption?: string;
  /** 结构化消息种类：tool_summary=工具执行摘要卡片 / system_notice=系统提示细条 */
  kind?: "tool_summary" | "system_notice";
  /** kind=tool_summary 时的结构化摘要条目 */
  summary?: ToolSummaryData;
  /** 语音形态：transcript=实时通话的用户转写 / spoken=已同步语音播出（AI 回复） */
  voice?: "transcript" | "spoken";
  /** 警示色调（发送超时/失败等 system_notice 用） */
  tone?: "warn";
  /** 模块自注册的消息内容与未安装模块时的文本降级。 */
  extension?: ChatExtension;
}

export interface PendingFile {
  file: File;
  preview?: string;
  type: string;
  uploading: boolean;
  /** 工作区/项目内文件的路径（相对所属根）；外部上传文件为后端绝对路径 */
  path?: string;
  /** 所属根（workspace / project）；外部上传文件缺省 */
  root?: "workspace" | "project";
}

export interface ChatStreamingDiff {
  path: string;
  diff: string;
  additions: number;
  removals: number;
  /** rename/move 的源路径（A → B 形态；普通编辑缺省） */
  move_from?: string;
  /** 二进制文件改动（无 unified diff，显示占位卡） */
  binary?: boolean;
}

export interface ChatBucket {
  workspaceContextEnabled: boolean;
  inputDraft: string;
  submitting: boolean;
  messages: ChatMessage[];
  sending: boolean;
  sendingSince: number | null;
  pendingFiles: PendingFile[];
  historyLoaded: boolean;
  historyLoading?: boolean;
  historyError?: unknown;
  earlierError?: unknown;
  /** 非激活会话收到新消息时的未读计数（切换会话时清零） */
  unread: number;
  /** 已加载历史中最早一条的 DB id（"加载更早"分页游标） */
  earliestId?: number;
  /** 是否可能还有更早历史（上次分页拉满 limit 时置真） */
  hasMore?: boolean;
  /** "加载更早"请求进行中 */
  loadingEarlier?: boolean;
  /** 生效中的换向折叠段（折叠 chip 渲染数据源） */
  folds?: ConversationFold[];
}

/** 换向折叠段（/chat/folds 返回）：from_msg_id（不含）到 to_msg_id（含）的消息被折叠出上下文 */
export interface ConversationFold {
  id: number;
  from_msg_id: number;
  to_msg_id: number;
  summary: string;
  folded_count: number;
  created_ns: number;
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
  chat_visible: boolean;
  execution_visible: boolean;
  page: string;
  active_tab: string;
  dock_open: boolean;
  left_open: boolean;
  open_file: string | null;
  has_draft: boolean;
  pending_asks: number;
  /** 工作台实时快照，供 AI 查询界面状态。 */
  active_file?: string | null;
  selection?: EditorSelectionPayload | null;
  open_tabs?: { label: string; path: string }[];
}

/** 编辑器选区上报负载（与后端 services/workspace_context 的渲染契约一致） */
export interface EditorSelectionPayload {
  path: string;
  ranges: { start_line: number; end_line: number }[];
  content: string;
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

export interface SseExtensionEvent extends SseEventBase { extension: ChatExtension }

export interface SseFileDiffEvent extends SseEventBase {
  turn_id?: string;
  path: string;
  diff: string;
  additions: number;
  removals: number;
  move_from?: string;
  binary?: boolean;
}

export type SseContextUsageEvent = ContextUsage & SseEventBase;



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

export interface SsePlanDeletedEvent extends SseEventBase {
  plan_id: string;
}

export interface SseDelegationStartedEvent extends SseEventBase {
  delegation_id: string;
  goal?: string;
  context_preview?: string;
  role?: "leaf" | "orchestrator";
  task_index?: number;
  background?: boolean;
  depth?: number;
  /** 难度分级/命名档案解析后的模型 ID；空串/缺省 = 默认模型 */
  model?: string;
  /** 命名子代理档案名（delegate_task.agent_name，空 = 未指定） */
  agent?: string;
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
  extension: SseExtensionEvent;
  ui_command: UiCommandPayload;
  activity: { epoch: string; run: import("./activity").ActivityRun };
  activity_end: { epoch: string; run: import("./activity").ActivityRun };
  file_diff: SseFileDiffEvent;
  context_usage: SseContextUsageEvent;
  plan_submitted: SsePlanSubmittedEvent;
  plan_step_updated: SsePlanStepUpdatedEvent;
  plan_status_changed: SsePlanStatusChangedEvent;
  plan_cancelled: SsePlanCancelledEvent;
  plan_deleted: SsePlanDeletedEvent;
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
  cid?: string;
  role: string;
  content: string;
  timestamp?: string;
  /** epoch 秒（后端 ts_ns 换算），时间线合排用 */
  ts?: number;
  kind?: "tool_summary" | "system_notice";
  /** kind=tool_summary 时的结构化摘要条目 */
  summary?: ToolSummaryData;
}
