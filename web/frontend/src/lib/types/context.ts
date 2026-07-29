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
  messages: SnapshotMessage[];
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
}

export interface SnapshotStatus {
  armed: boolean;
  has_snapshot: boolean;
  captured_at: number | null;
  model: string | null;
  model_context_window: number | null;
  estimated_tokens: number | null;
  message_count: number | null;
  tool_count: number | null;
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
}
