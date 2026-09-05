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
  mode: "heartbeat" | "scheduled" | "idle" | "manual";
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
  /** 距上次真实思考的秒数（null = 进程启动后尚未思考） */
  last_activity_sec?: number | null;
  /** 是否存在待空闲窗口消费的反思 */
  reflection_pending?: boolean;
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
  /** 生效截止时间（"YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM"，空/缺省 = 永久有效），到期自动停用 */
  expires_at?: string;
  /** 创建/最近更新时间（epoch 秒，0 = 未知） */
  created_at?: number;
  updated_at?: number;
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
  /** 高级项（折叠到高级区，基础项直接展示） */
  advanced: boolean;
  /** RANGE 类型滑条边界与步进（未声明为 null） */
  min: number | null;
  max: number | null;
  step: number | null;
  /** 单位展示（秒/%/条/分钟…） */
  unit: string;
  /** 条件显示标记（如频道 ws_mode 的 forward/reverse，仅供卡片分组过滤） */
  tag: string;
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
