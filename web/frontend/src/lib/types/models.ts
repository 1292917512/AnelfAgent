import type { JsonObject } from "./json";

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
  /** null = 不主动限制，由 provider/SDK 按模型默认决定 */
  max_tokens: number | null;
  frequency_penalty: number;
  presence_penalty: number;
  timeout: number;
  request_params: JsonObject;
  extra_body: JsonObject;
  /** 自定义请求头（最后应用到 HTTP 请求，可覆盖鉴权头） */
  extra_headers: Record<string, string>;
  chat_protocol: "chat_completions" | "responses" | "auto";
  /** 供应商内置工具声明（服务端执行，如 web_search）；与本地同名工具冲突时内置优先 */
  builtin_tools: Array<string | JsonObject>;
  is_default: boolean;
  /** 启用开关：禁用后不参与任何自动选择/回退/默认 */
  enabled: boolean;
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

export interface ModelPriorityItem {
  id: string;
  model: string;
  provider_id: string;
  provider_name: string;
  is_default: boolean;
  enabled: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
  supports_reasoning: boolean;
  reasoning_effort?: string;
  api_type: string;
  input_cost: number | null;
  output_cost: number | null;
  context_window: number | null;
}

/**
 * 子代理统一档案：名称 → 有序模型候选池。
 * 内置难度档（easy/medium/hard，tier 1-3）是 delegate_task.difficulty 的映射目标；
 * 自定义档案（tier 0）经 agent_name 直指。池内顺序即优先级，前者不可用依次回退。
 */
export interface SubAgentProfile {
  name: string;
  models: string[];
  description: string;
  /** 难度挡位：0 = 自定义档案；1/2/3 = 内置难度档 */
  tier: number;
  builtin: boolean;
  /** 池内首个可用模型（池空/全不可用时为 null） */
  first_available: string | null;
  /** 池内存在已删除的模型引用 */
  model_missing: boolean;
  /** 池内是否存在可用模型 */
  model_enabled: boolean;
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

/** 真实链路对话测试结果（保存并测试） */
export interface TestChatResult {
  ok: boolean;
  error?: string;
  /** 首字延迟（毫秒） */
  ttft_ms?: number;
  /** 总耗时（毫秒） */
  total_ms?: number;
  output_tokens?: number;
  /** 端点未返回 usage 时为本地估算值 */
  tokens_estimated?: boolean;
  reply_preview?: string;
}

/** api_type 元信息（GET /models/api-types，前端不再硬编码列表） */
export interface ApiTypeInfo {
  value: string;
  group: "common" | "other";
  default_base_url: string;
}
