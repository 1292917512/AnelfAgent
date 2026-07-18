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
  max_tokens: number;
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
  name?: string;
  path?: string;
  lines?: number;
  size?: string;
  [key: string]: unknown;
}

export interface UnifiedTag {
  name: string;
  description: string;
  builtin: boolean;
  sources: Array<"message" | "tool" | "custom">;
}
