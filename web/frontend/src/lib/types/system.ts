export interface AgentStatusResponse {
  ready: boolean;
  status?: {
    status: string;
    mind_phase: string;
    uptime: number;
    message_count: number;
    error_count: number;
    last_message_time: number;
    last_error: string;
    queue_size: number;
  } | null;
}

/** 已安装 Python 包（GET /system/python/packages） */
export interface PythonPackage {
  name: string;
  version: string;
}

export interface PfcSnapshot {
  tool_recall?: { name: string; count: number }[];
  tool_recall_top_n?: number;
  tag_activated_tools?: string[];
  pending_messages?: { scope: string; preview: string; adapter_key: string }[];
  general_tasks?: { type: string; scope: string; preview: string }[];
  pending_analysis_count?: number;
  short_term_memory_count?: number;
  short_term_memory_max?: number;
  active_tools?: string[];
}

export interface RuntimeComponents {
  ready: boolean;
  error?: string;
  llm?: { impl: string; model?: string | null };
  storage?: { impl: string; sqlite: string };
  tools?: { enabled: number; total: number; by_source: Record<string, number> };
  persona_prompts?: number;
  short_term_memory?: number;
}
