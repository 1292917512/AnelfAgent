// ── Thinking Tracer（thinking-store） ──

export interface TraceNode {
  id: string;
  type: string;
  label: string;
  status: "pending" | "running" | "completed" | "error" | "warning";
  timestamp: number;
  duration_ms: number | null;
  data: Record<string, unknown>;
  parent_id: string | null;
}

export interface SessionSummary {
  id: string;
  start_time: number;
  end_time: number | null;
  is_heartbeat: boolean;
  is_introspection?: boolean;
  /** 子代理委托会话（SubAgent 独立思维链路，与主 AI 会话区分） */
  is_delegation?: boolean;
  node_count: number;
  ended: boolean;
  duration_ms: number | null;
}

export interface ThinkingSession extends SessionSummary {
  nodes: TraceNode[];
  available_tools: string[];
}
