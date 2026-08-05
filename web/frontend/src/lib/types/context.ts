// ======================================================================
// Context Providers（实体上下文注入）
// ======================================================================

export interface ProviderMetric {
  name: string;
  priority: number;
  max_tokens: number;
  scope_filter: string | null;
  description: string;
  tokens: number;
  bytes: number;
  cost_ms: number;
  ready: boolean;
  fetched_at: number;
  last_error: string;
  call_count: number;
}

export interface ContextProviderStatus {
  total_budget: number;
  static_estimate: number;
  current_used: number;
  peak_used: number;
  providers: ProviderMetric[];
}

// ======================================================================
// Context Snapshot（上下文快照）
// ======================================================================

export interface SnapshotMessage {
  role: string;
  content: string;
  tool_calls?: unknown[] | null;
  tool_call_id?: string | null;
}

export interface SnapshotSection {
  layer: string;
  label: string;
  count: number;
  chars: number;
  estimated_tokens: number;
  /** 本节内容哈希（快照间变更对比基线） */
  hash?: string;
  /** 与上一次快照对比是否变更；null/undefined = 首次快照无基线 */
  changed?: boolean | null;
  messages: SnapshotMessage[];
}

/** 单次 LLM 调用的缓存用量记录 */
export interface CacheCallRecord {
  ts: number;
  prompt_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  cache_hit_rate: number;
}

/** 缓存聚合统计（最近若干次调用） */
export interface CacheStatsSummary {
  sample_count: number;
  avg_cache_hit_rate: number;
  total_prompt_tokens: number;
  total_cache_read_tokens: number;
  total_cache_creation_tokens: number;
  /** 未返回 usage 的调用次数（流式端点/网关可能丢弃 usage） */
  no_usage_count: number;
}

/** 快照的缓存观测区块 */
export interface SnapshotCacheInfo {
  /** 快照捕获前最近一次 LLM 调用的真实缓存用量 */
  last_call: CacheCallRecord | null;
  /** 最近 N 次调用聚合 */
  recent: CacheStatsSummary;
  /** 从头连续未变更 section 的估算 tokens（近似可复用缓存前缀）；null = 首次快照无基线 */
  estimated_cacheable_prefix_tokens: number | null;
}

export interface ContextSnapshotData {
  captured_at: number;
  model: string;
  model_context_window: number;
  estimated_tokens: number;
  message_count: number;
  tool_count: number;
  tool_names: string[];
  tools: unknown[];
  sections: SnapshotSection[];
  cache?: SnapshotCacheInfo;
}

export interface SnapshotStatus {
  armed: boolean;
  /** 连续捕获模式（每次 LLM 调用都捕获快照） */
  continuous: boolean;
  has_snapshot: boolean;
  captured_at: number | null;
  model: string | null;
  model_context_window: number | null;
  estimated_tokens: number | null;
  message_count: number | null;
  tool_count: number | null;
}

/** 连续捕获的紧凑记录（records.jsonl 每行，供外部调试轮询） */
export interface SnapshotRecord {
  captured_at: number;
  /** 对应完整快照文件名（logs/context_snapshots/） */
  file: string;
  model: string;
  estimated_tokens: number;
  message_count: number;
  tool_count: number;
  sections: Array<{
    layer: string;
    count: number;
    chars: number;
    estimated_tokens: number;
    hash?: string;
    changed?: boolean | null;
  }>;
  cache?: SnapshotCacheInfo;
}

export interface SnapshotResponse {
  status: SnapshotStatus;
  snapshot: ContextSnapshotData | null;
}

export interface SnapshotListItem {
  filename: string;
  captured_at: number;
  model: string;
  model_context_window: number;
  estimated_tokens: number;
  message_count: number;
  tool_count: number;
  /** 捕获前最近一次调用的缓存命中率 0~1（无数据为 null） */
  cache_hit_rate?: number | null;
  /** 捕获前最近一次调用的缓存命中 tokens */
  cache_read_input_tokens?: number | null;
  /** 捕获前最近一次调用的缓存写入 tokens */
  cache_creation_input_tokens?: number | null;
  /** 从头连续未变更 section 的估算 tokens（近似可复用前缀） */
  estimated_cacheable_prefix_tokens?: number | null;
}
