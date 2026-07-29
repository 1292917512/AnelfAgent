export interface PersonaData {
  name?: string;
  personality?: string[];
  [key: string]: unknown;
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
