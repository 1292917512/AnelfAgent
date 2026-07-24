export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export interface JsonObject {
  [key: string]: JsonValue;
}

export interface ProviderConfig {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  api_type: string;
  proxy_url: string;
  media_protocol: string;
  model_count: number;
}

export interface ModelConfig {
  id: string;
  name: string;
  model: string;
  model_types: string[];
  supports_vision: boolean;
  supports_tools: boolean;
  supports_forced_tool_choice: boolean;
  vision_format: string;
  supports_reasoning: boolean;
  /** 每模型专属思考等级（off/minimal/low/medium/high/xhigh/max）；空串/缺省 = 跟随全局 */
  reasoning_effort?: string;
  /** null = 不下发，由 provider/SDK 按模型默认决定 */
  temperature: number | null;
  /** null = 不下发，由 provider/SDK 按模型默认决定 */
  top_p: number | null;
  frequency_penalty: number;
  presence_penalty: number;
  timeout: number;
  request_params: JsonObject;
  extra_body: JsonObject;
  chat_protocol: "chat_completions" | "responses" | "auto";
  is_default: boolean;
  input_cost: number | null;
  output_cost: number | null;
  context_window: number | null;
}

export type CreateProviderConfig = Omit<ProviderConfig, "model_count">;
export type UpdateProviderConfig = Partial<Omit<CreateProviderConfig, "id">>;
export type CreateModelConfig = { id: string } & Partial<
  Omit<ModelConfig, "id" | "name" | "is_default" | "input_cost" | "output_cost">
>;
export type UpdateModelConfig = Partial<Omit<CreateModelConfig, "id">>;

export interface PersonaData {
  name?: string;
  personality?: string[];
  [key: string]: unknown;
}

export interface ModelPriorityItem {
  id: string;
  model: string;
  provider_id: string;
  provider_name: string;
  is_default: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
  supports_reasoning: boolean;
  reasoning_effort?: string;
  api_type: string;
  input_cost: number | null;
  output_cost: number | null;
  context_window: number | null;
}

export type CogneeModelSource = "auto" | "model" | "custom";

export type CogneeReasoningEffort = "" | "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface CogneeChatModelConfig {
  source: CogneeModelSource;
  model_id: string;
  provider: string;
  model: string;
  api_key: string;
  endpoint: string;
  api_version: string;
  instructor_mode: string;
  max_completion_tokens: number;
  reasoning_effort: CogneeReasoningEffort;
  extra_args: Record<string, unknown>;
}

export interface CogneeEmbeddingModelConfig {
  source: CogneeModelSource;
  model_id: string;
  provider: string;
  model: string;
  api_key: string;
  endpoint: string;
  dimensions: number;
}

export interface CogneeConfig {
  enabled: boolean;
  sync_enabled: boolean;
  recall_enabled: boolean;
  data_root: string;
  dataset_prefix: string;
  timeout_seconds: number;
  pipeline_timeout_seconds: number;
  improve_interval_seconds: number;
  sync_interval_seconds: number;
  sync_batch_size: number;
  max_retries: number;
  native_weight: number;
  cognee_weight: number;
  rrf_k: number;
  recall_pool_multiplier: number;
  search_types: string[];
  chat: CogneeChatModelConfig;
  embedding: CogneeEmbeddingModelConfig;
}

export interface CogneeResolvedInfo {
  provider?: string;
  model?: string;
  endpoint?: string;
  instructor_mode?: string;
  api_key_set?: boolean;
}

export interface CogneeStatus {
  availability: {
    installed: boolean;
    enabled: boolean;
    ready: boolean;
    version: string;
    reason: string;
  };
  resolved: {
    chat?: CogneeResolvedInfo;
    embedding?: CogneeResolvedInfo;
  };
  sync: {
    enabled: boolean;
    running: boolean;
    pending: number;
    failed: number;
    synced: number;
    last_error: string;
  };
}

export interface CogneeDataset {
  id: string;
  name: string;
  [key: string]: unknown;
}

export interface LTMItem {
  id: number;
  content: string;
  memory_type: string;
  importance: number;
  tags?: string[];
  created_at?: string;
  updated_at?: string;
  source?: string;
}

export interface GoalStep {
  index?: number;
  content?: string;
  step?: string;
  status: "pending" | "in_progress" | "completed" | "skipped";
  note?: string;
}

export interface GoalData {
  goal_id: string;
  title: string;
  description?: string;
  status: string;
  steps: GoalStep[];
  due_time?: string;
  recurring?: boolean;
  created_at: string;
  updated_at: string;
}

export interface EntityProfile {
  scope_type: string;
  scope_id: string;
  personality?: string;
  [key: string]: unknown;
}

export interface ConvMessage {
  id?: number;
  role: string;
  content: string;
  timestamp?: string;
  [key: string]: unknown;
}

export interface ConvScope {
  scope_type: string;
  scope_id: string;
  [key: string]: unknown;
}

export interface MemoryFileInfo {
  path: string;
  lines: string;
  size: string;
}

export interface MemoryDocument {
  path: string;
  name: string;
  size: number;
  chunks: number;
  indexed_at: number;
}

export interface UnifiedTag {
  name: string;
  description: string;
  builtin: boolean;
  sources: Array<"message" | "tool" | "custom">;
}

// ── MCP ─────────────────────────────────────────────────────────

export type MCPTransport = "stdio" | "streamable_http" | "sse";

export interface MCPServer {
  name: string;
  /** 展示用地址（stdio 为命令），后端已脱敏 */
  url: string;
  transport: MCPTransport | string;
  enabled: boolean;
  connected: boolean;
  tool_count: number;
  tools: string[];
  last_error: string;
}

/** MCP server 完整配置（创建/编辑共用，字段均可选） */
export interface MCPServerConfig {
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  transport?: MCPTransport;
  enabled?: boolean;
  timeout?: number;
  sse_read_timeout?: number;
  call_timeout?: number;
}

export interface MCPToolParam {
  name: string;
  description: string;
  type: string;
  required: boolean;
  enum: string[] | null;
}

export interface MCPToolInfo {
  name: string;
  description: string;
  params: MCPToolParam[];
}

export interface MCPToggleResult {
  success: boolean;
  message: string;
  tool_count?: number;
}

// ── Channels ────────────────────────────────────────────────────

export interface AdapterInfo {
  key: string;
  name: string;
  status: string;
  status_display: string;
  detail?: string;
  ws_mode?: string;
  ws_connected?: boolean;
  online?: boolean;
  self_id?: string;
  capabilities: string[];
}

export interface AdapterListResult {
  ready: boolean;
  adapters: AdapterInfo[];
}

export interface ChannelSelfInfo {
  user_id: string;
  user_name: string;
  platform: string;
}

export interface ChannelTestHealthResult {
  ready: boolean;
  running?: boolean;
  status?: string;
  detail?: string;
  capabilities?: string[];
  healthy?: boolean;
  health_detail?: string;
  latency_ms?: number | null;
  last_error?: string | null;
  self_info?: ChannelSelfInfo;
  error?: string;
}

export interface ChannelTestSendResult {
  ready: boolean;
  success: boolean;
  chat_id?: string;
  message_id?: string | null;
  error?: string;
}

export interface ChannelToolParam {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface ChannelToolInfo {
  name: string;
  description: string;
  /** 是否跨频道共享的公共能力接口（开关仅按频道生效） */
  common: boolean;
  sensitive: boolean;
  enabled: boolean;
  globally_enabled: boolean;
  supporting_channels?: string[];
  params: ChannelToolParam[];
}

export interface ChannelToolsResult {
  ready: boolean;
  running?: boolean;
  tools: ChannelToolInfo[];
}

export interface ChannelToolToggleResult {
  name: string;
  enabled: boolean;
  common: boolean;
}

export interface ChannelToolTestResult {
  ready: boolean;
  success: boolean;
  result?: string;
  latency_ms?: number;
  error?: string;
}

// ── Logs ────────────────────────────────────────────────────────

export interface LogEntry {
  level: string;
  message: string;
  tag: string;
  time: string;
}

export interface LogStats {
  total: number;
  capacity: number;
  by_level: Record<string, number>;
  by_tag: Record<string, number>;
}

// ── Approvals ──────────────────────────────────────────────────

export interface ApprovalPendingItem {
  request_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  risk_level: string;
  reason: string;
  requester_channel: string;
  requester_chat_id: string;
  requester_user_id: string;
  expires_at: number;
  created_at: number;
  matched_rule: string;
}

export interface ApprovalPendingResponse {
  pending: ApprovalPendingItem[];
}

export interface ApprovalHistoryItem {
  request_id: string;
  tool_name: string;
  risk_level: string;
  decision: string;
  decided_by: string;
  decided_at: number;
  decision_reason: string;
  requester_user_id: string;
  requester_channel: string;
  matched_rule: string;
}

export interface ApprovalHistoryResponse {
  history: ApprovalHistoryItem[];
}

export interface ApprovalStats {
  pending_count: number;
  history_size: number;
  history_by_decision: Record<string, number>;
}

export interface ApprovalPolicyItem {
  tool_name_pattern: string;
  risk_level: string;
  requires_approval: boolean;
  timeout_seconds: number;
  on_timeout: string;
  trust_after_n_approvals: number;
  auto_approve_users: string[];
  auto_deny_users: string[];
  description: string;
}

export interface ApprovalPoliciesResponse {
  policies: ApprovalPolicyItem[];
}

export interface PermissionRuleItem {
  id: string;
  pattern: string;
  effect: string;
  scope: string;
  users: string[];
  risk_level: string;
  timeout_seconds: number;
  on_timeout: string;
  trust_after_n_approvals: number;
  description: string;
  enabled: boolean;
  created_by: string;
  created_at: number;
}

export interface ApprovalRulesResponse {
  default_effect: string;
  rules: PermissionRuleItem[];
  persisted_count: number;
  session_count: number;
}

// ── Auth ───────────────────────────────────────────────────────

export interface AuthStatus {
  required: boolean;
  authenticated: boolean;
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  key_prefix: string;
  masked_key: string;
  created_at: number;
  last_used_at: number | null;
}

export interface ApiKeyCreated extends ApiKeyInfo {
  api_key: string;
}

// ── Models (inline from api.ts) ────────────────────────────────

export interface RemoteModelInfo {
  id: string;
  owned_by: string;
  created: number | null;
  already_added: boolean;
}

export interface ModelInfoResult {
  found: boolean;
  max_output_tokens?: number;
  max_input_tokens?: number;
  supports_vision?: boolean;
  supports_tools?: boolean;
  input_cost_per_token?: number | null;
  output_cost_per_token?: number | null;
}

export interface ProbeResult {
  error?: string;
  supports_vision?: boolean;
  supports_tools?: boolean;
  vision_format?: string;
}

// ── Weixin QR Login ────────────────────────────────────────────

export interface WeixinQrStartResult {
  session_id: string;
  qr_png: string;
  qr_url: string;
}

export interface WeixinQrStatusResult {
  status: "wait" | "scaned" | "confirmed" | "timeout" | "error";
  qr_png?: string;
  qr_url?: string;
  refreshed?: boolean;
  account_id?: string;
  error?: string;
}

// ── Config (inline from api.ts) ────────────────────────────────

export interface WebToolsConfig {
  baidu_api_key: string;
  proxy: string;
}

export type ReasoningEffort = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface HeartbeatConfig {
  enabled: boolean;
  interval_seconds: number;
  analysis_temperature: number;
  min_conversations_for_analysis: number;
  task_schedules: TaskSchedule[];
}

export interface TaskSchedule {
  task_name: string;
  mode: "heartbeat" | "scheduled" | "manual";
  every_n_beats?: number;
  beat_count?: number;
  schedule_times?: string[];
  last_run_date?: string;
  model_id?: string;
  reasoning_effort?: ReasoningEffort | "";
}

export interface HeartbeatStatus {
  enabled: boolean;
  interval_seconds: number;
  total_ticks: number;
  task_count: number;
  schedule_count: number;
  schedules: (TaskSchedule & { task_exists: boolean; task_enabled: boolean })[];
}

export interface TaskConfig {
  name: string;
  display_name: string;
  description: string;
  scope: string;
  enabled: boolean;
  memory_type: string;
  importance: number;
  tags: string[];
  source: string;
  null_keywords: string[];
  tool_tags: string[];
  prompt: string;
  allow_output_tools?: boolean;
  save_result_to_memory?: boolean;
  model_id?: string | null;
  reasoning_effort?: ReasoningEffort | null;
  folder?: string;
}

// ── Workspace ──────────────────────────────────────────────────

export interface WorkspaceNode {
  name: string;
  path: string;
  type: "dir" | "file";
  size?: number;
  modified: number;
  binary?: boolean;
  children?: WorkspaceNode[];
}

export interface WorkspaceFile {
  path: string;
  name: string;
  size: number;
  modified: number;
  binary: boolean;
  truncated: boolean;
  content: string;
}

export interface WorkspaceSearchHit {
  path: string;
  name: string;
  match: "name" | "content";
  snippet?: string;
}

export interface GlobalSearchResult {
  query: string;
  memory: { id: number; snippet: string; memory_type: string; tags: string[]; score: number }[];
  logs: LogEntry[];
  files: WorkspaceSearchHit[];
  conversations: { id: number; scope: string; role: string; snippet: string; time: string }[];
}

// ── Skills ─────────────────────────────────────────────────────

export interface SkillItem {
  name: string;
  description: string;
  trigger_patterns: string[];
  state: "active" | "stale" | "archived";
  use_count: number;
  patch_count: number;
  pinned: boolean;
  created_by: string;
  created_at: number;
  last_activity_at: number;
  content?: string;
}

// ── Config Meta ────────────────────────────────────────────────

export interface ConfigMetaItem {
  key: string;
  description: string;
  type: string;
  value: unknown;
  default: unknown;
  editable: boolean;
  options: string[] | null;
  source: "mind" | "config_manager";
}

export interface ConfigMetaGroup {
  group: string;
  items: ConfigMetaItem[];
}

// ── Stickers / 图片索引 ────────────────────────────────────────

export interface StickerItem {
  id: string;
  description: string;
  tags: string[];
  emotion: string;
  file_path: string;
  content_hash: string;
  phash: string;
  source: string;
  use_count: number;
  created_ns: number;
  updated_ns: number;
  has_embedding: boolean;
}

export interface StickerListResult {
  items: StickerItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface StickerStats {
  stickers: number;
  total_uses: number;
  indexed_images: number;
  described_images: number;
  vec_available: boolean;
}

export interface IndexedImage {
  path: string;
  description: string;
  content_hash: string;
  phash: string;
  source: string;
  ts_ns: number;
  has_embedding: boolean;
}

export interface IndexedImageListResult {
  items: IndexedImage[];
  total: number;
  page: number;
  page_size: number;
}

// ======================================================================
// 数据库管理（数据管理页 · 数据库 Tab）
// ======================================================================

export interface DbInfo {
  id: string;
  name: string;
  description: string;
  path: string;
  exists: boolean;
  size_bytes: number;
  table_count: number;
  error?: string;
}

export interface DbTableInfo {
  name: string;
  type: string;
  virtual: boolean;
  shadow: boolean;
  readonly: boolean;
  row_count: number;
  column_count: number;
}

export interface DbColumnInfo {
  cid: number;
  name: string;
  type: string;
  notnull: boolean;
  default: string | null;
  pk: boolean;
}

/** 后端智能序列化的单元格值：原始标量或带 __type__ 的结构 */
export type CellValue =
  | null
  | string
  | number
  | {
      __type__: "blob" | "vec" | "ts" | "json" | "text";
      bytes?: number;
      dims?: number;
      preview?: number[];
      value?: unknown;
      raw?: string;
      text?: string;
      truncated?: boolean;
    };

export interface DbRow {
  __rowid__: number;
  values: Record<string, CellValue>;
}

export interface DbRowsResult {
  items: DbRow[];
  columns: DbColumnInfo[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  readonly: boolean;
}

export interface DbIndexInfo {
  name: string;
  unique: boolean;
  columns: string[];
}

export interface DbSchemaResult {
  table: string;
  type: string;
  readonly: boolean;
  ddl: string;
  columns: DbColumnInfo[];
  indexes: DbIndexInfo[];
}

export interface DbQueryResult {
  columns: string[];
  rows: Record<string, CellValue>[];
  row_count: number;
  elapsed_ms: number;
  truncated: boolean;
}
