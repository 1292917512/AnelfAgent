export interface WebToolsConfig {
  proxy: string;
}

export type ReasoningEffort = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface HeartbeatConfig {
  enabled: boolean;
  interval_seconds: number;
  analysis_temperature: number;
  min_conversations_for_analysis: number;
  task_schedules: TaskSchedule[];
}

export interface TaskSchedule {
  task_name: string;
  mode: "heartbeat" | "scheduled" | "manual";
  every_n_beats?: number;
  beat_count?: number;
  schedule_times?: string[];
  last_run_date?: string;
  model_id?: string;
  reasoning_effort?: ReasoningEffort | "";
}

export interface HeartbeatStatus {
  enabled: boolean;
  interval_seconds: number;
  total_ticks: number;
  task_count: number;
  schedule_count: number;
  schedules: (TaskSchedule & { task_exists: boolean; task_enabled: boolean })[];
}

export interface TaskConfig {
  name: string;
  display_name: string;
  description: string;
  scope: string;
  enabled: boolean;
  memory_type: string;
  importance: number;
  tags: string[];
  source: string;
  null_keywords: string[];
  tool_tags: string[];
  prompt: string;
  allow_output_tools?: boolean;
  save_result_to_memory?: boolean;
  model_id?: string | null;
  reasoning_effort?: ReasoningEffort | null;
  folder?: string;
}

// ── Config Meta ────────────────────────────────────────────────

export interface ConfigMetaItem {
  key: string;
  description: string;
  type: string;
  value: unknown;
  default: unknown;
  editable: boolean;
  options: string[] | null;
  source: "mind" | "config_manager";
}

/**
 * 动态配置袋：app/mind 配置、adapter configs 等由后端 schema 动态驱动的
 * 键值集合（配置表单按 key 读写，前端无法穷举字段）。
 */
export interface ConfigValues {
  [key: string]: unknown;
}

export interface ConfigMetaGroup {
  group: string;
  items: ConfigMetaItem[];
}
