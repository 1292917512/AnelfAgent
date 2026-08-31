// ======================================================================
// Context Providers（实体上下文注入）
// ======================================================================

export interface ProviderMetric {
  name: string;
  priority: number;
  max_tokens: number;
  scope_filter: string | null;
  /** 所属工具分组（实体启停门控依据）；null = 全局常驻 */
  group?: string | null;
  /** 是否处于注入活动状态（分组工具全禁用时为 false，停止采集与注入） */
  active?: boolean;
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
  /** 面板口径：数据归属的会话 scope（全 scope 最近一次收集；无记录为空） */
  scope?: string;
  /** 该次收集的时间（epoch 秒，0=尚无收集记录） */
  collected_at?: number;
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
  /** 变动率（值越大变动越频繁，来自构建管线注册中心） */
  volatility?: number | null;
  /** 变动率分档（静态/低频/周期/追加/每会话/每轮） */
  volatility_label?: string | null;
  messages: SnapshotMessage[];
}

/** 单次 LLM 调用的缓存用量记录 */
export interface CacheCallRecord {
  ts: number;
  /** 调用用途（reply=主对话 / reflect=辅助调用） */
  kind?: string;
  /** 模型名 */
  model?: string;
  /** 该模型流式 usage 缺缓存字段（缓存生效但不可度量） */
  unobservable?: boolean;
  prompt_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  cache_hit_rate: number;
  /** 距快照捕获的秒数（回显过久的数据会被前端弱化展示） */
  age_sec?: number;
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
  /** 流式缺缓存字段、无法度量的调用次数 */
  unobservable_count?: number;
}

/** 快照的缓存观测区块 */
export interface SnapshotCacheInfo {
  /** 捕获前最近一次主对话（reply）调用的真实缓存用量 */
  last_call: CacheCallRecord | null;
  /** 捕获前最近一次任意类型调用（含辅助调用，kind 字段区分） */
  last_call_any?: CacheCallRecord | null;
  /** 最近 N 次主对话调用聚合 */
  recent: CacheStatsSummary;
  /** 最近 N 次全部调用聚合 */
  recent_all?: CacheStatsSummary;
  /** 从头连续未变更 section 的估算 tokens（近似可复用缓存前缀）；null = 首次快照无基线 */
  estimated_cacheable_prefix_tokens: number | null;
  /** 理论可命中前缀（断点锚点覆盖的字节稳定层 tokens 合计）；read 远低于它 ⇒ 非内容漂移 */
  expected_prefix_tokens?: number | null;
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
  /** 调用用途（reply=主对话 / reflect=任务·反思等辅助调用；旧快照为空） */
  kind?: string;
  /** 前缀字节是否稳定（除工具链/执行态外所有 section 未变；null=无基线） */
  prefix_stable?: boolean | null;
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
  /** 理论可命中前缀（字节稳定层 tokens 合计） */
  expected_prefix_tokens?: number | null;
}
