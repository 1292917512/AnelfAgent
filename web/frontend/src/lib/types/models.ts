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
  frequency_penalty: number;
  presence_penalty: number;
  timeout: number;
  request_params: JsonObject;
  extra_body: JsonObject;
  chat_protocol: "chat_completions" | "responses" | "auto";
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

/** 子代理模型三挡池（1 简单/2 中等/3 困难） */
export type DelegationTiers = Record<"1" | "2" | "3", ModelPriorityItem[]>;

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
