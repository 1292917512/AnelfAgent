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
  temperature: number;
  top_p: number;
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
export type CreateModelConfig = Omit<
  ModelConfig,
  "name" | "is_default" | "input_cost" | "output_cost" | "context_window"
> & { context_window?: number };
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
  api_type: string;
  input_cost: number | null;
  output_cost: number | null;
  context_window: number | null;
}

export type CogneeModelSource = "auto" | "model" | "custom";

export type CogneeReasoningEffort = "" | "off" | "low" | "medium" | "high" | "max";

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
