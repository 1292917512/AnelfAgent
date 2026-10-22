export interface MemoryIndexSyncResult {
  synced: number;
  unchanged: number;
  failed: number;
  removed: number;
  chunks: number;
}

/** GET /personas/ 的人设列表条目 */
export interface PersonaItem {
  key: string;
  name: string;
  description?: string;
}

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
  compact_enabled: boolean;
  compact_interval_seconds: number;
  compact_retention_days: number;
  native_weight: number;
  cognee_weight: number;
  rrf_k: number;
  recall_pool_multiplier: number;
  search_types: string[];
  chat: CogneeChatModelConfig;
  embedding: CogneeEmbeddingModelConfig;
  /** 后端下发的下拉词汇（名单单一权威在后端 cognee 配置） */
  options: CogneeConfigOptions;
}

export interface CogneeConfigOptions {
  chat_providers: string[];
  embed_providers: string[];
  instructor_modes: string[];
  reasoning_efforts: CogneeReasoningEffort[];
}

export interface CogneeResolvedInfo {
  provider?: string;
  model?: string;
  endpoint?: string;
  instructor_mode?: string;
  api_key_set?: boolean;
}

export interface CogneeStorageInfo {
  total_bytes: number;
  data_bytes: number;
  lance_bytes: number;
  graph_bytes: number;
  metadata_bytes: number;
  other_bytes: number;
  computed_at?: number;
  datasets?: Record<string, { total_bytes: number; graph_bytes: number; lance_bytes: number }>;
}

export interface CogneeProjectionState {
  phase: "required" | "requested" | "scanning" | "building" | "verifying" | "ready" | "failed";
  rebuild_requested: boolean;
  rebuild_from_scratch?: boolean;
  namespace: string;
  previous_namespaces: string[];
  backup_path: string;
  counts: Record<string, number>;
  verified: number;
  legacy_preserved?: number;
  error: string;
  completed_at: number;
  started_at: number;
  memory_scanned: boolean;
  graph_scanned: boolean;
}

export interface CogneeBatchProgress {
  stage: string;
  items: number;
  attempt: number;
  dataset_name: string;
  started_at: number;
  elapsed_seconds: number;
  stage_elapsed_seconds: number;
  quiet_seconds: number;
  last_change_at: number;
  timeout_seconds: number;
  progress_checked_at: number;
  progress_error: string;
  pipeline: {
    status: string;
    completed_items: number | null;
    total_items: number | null;
    current_stage: string;
  } | null;
}

export interface CogneeStatus {
  retirement?: {
    running: boolean;
    dataset_id: string;
    dataset_name: string;
    stage: string;
    documents: number;
    preserved: number;
    backup_path: string;
    error: string;
    completed_at: number;
  } | null;
  projection?: CogneeProjectionState | null;
  availability: {
    installed: boolean;
    enabled: boolean;
    ready: boolean;
    configuration_required: boolean;
    version: string;
    reason: string;
  };
  resolved: {
    chat?: CogneeResolvedInfo;
    embedding?: CogneeResolvedInfo;
  };
  storage?: CogneeStorageInfo;
  sync: {
    enabled: boolean;
    running: boolean;
    pending: number;
    failed: number;
    synced: number;
    processing?: number;
    retrying?: number;
    next_retry_at?: number;
    active?: CogneeBatchProgress | null;
    pause_requested?: boolean;
    paused?: boolean;
    paused_until?: number;
    pause_reason?: string;
    last_error: string;
    configuration_pending?: boolean;
    configuration_error?: string;
    last_compact_at?: number;
    last_compact_summary?: string;
  };
}

export interface ProbeScopeStatus {
  scope: string;
  running: boolean;
  rendered_chars: number;
  rendered_preview: string;
  age_seconds: number;
}

export interface ProbeStatus {
  enabled: boolean;
  inject_enabled: boolean;
  counters: Record<string, number>;
  scopes: ProbeScopeStatus[];
}

export interface CogneeDataset {
  id: string;
  name: string;
  [key: string]: unknown;
}

export interface RecallTestPlan {
  queries: string[];
  entities: string[];
  node_keys: string[];
  node_labels: string[];
  deep_needed: boolean;
  rationale: string;
}

export interface MemoryOrigin {
  kind: "conversation_summary" | "agent_note";
  scope: string;
  message_ids?: number[];
  started_at?: number;
  ended_at?: number;
}

export interface MemoryProvenance {
  origins?: MemoryOrigin[];
  origin_count?: number;
  activity_date?: string;
  temporal_scope?: string;
  recorded_by?: string;
  attribution?: "recorded" | "unrecorded";
  [key: string]: unknown;
}

export interface RecallTestItem {
  id: string;
  source: "memory" | "file" | "cognee_chunk" | "cognee_graph";
  score: number;
  content: string;
  type: string;
  tags: string[];
  dataset: string;
  path: string;
  provenance: MemoryProvenance;
  timestamp?: number;
  sensitivity?: string;
  start_line?: number;
  end_line?: number;
}

export interface RecallTestForgotten {
  id: string;
  kind?: string;
  score?: number;
  content?: string;
  gist?: string;
  [key: string]: unknown;
}

export interface RecallTestResult {
  plan: RecallTestPlan;
  depth: "shallow" | "deep" | "overview";
  cognee: {
    enabled: boolean;
    recall_enabled: boolean;
    datasets: string[];
    search_types: string[];
  };
  items: RecallTestItem[];
  relations: string[];
  forgotten: RecallTestForgotten[];
  timings: {
    plan_ms?: number;
    search_ms?: number;
    total_ms?: number;
    [key: string]: number | undefined;
  };
  error?: string;
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
  memory_id?: number;
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
  tool_count: number;
  tools: string[];
  message_key: boolean;
}
