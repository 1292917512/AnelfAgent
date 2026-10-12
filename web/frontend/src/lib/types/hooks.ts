/** 钩子配置类型（/hooks、/hooks-llm API）。 */

export interface HookEntry {
  mode?: "sync" | "async";
  matcher: string;
  command: string;
  timeout?: number;
  /** 停用保留配置但不执行（缺省视为 true） */
  enabled?: boolean;
}

/** 单条 hook 的执行明细（测试运行结果与最近执行记录共用结构） */
export interface HookRunDetail {
  at?: number;
  event: string;
  matcher: string;
  command: string;
  ok: boolean;
  returncode: number | null;
  duration_ms: number;
  blocked: boolean;
  test?: boolean;
  detail?: string;
}

export interface HookIntegration {
  event: string;
  where: string;
  note: string;
}

export interface HookEventStats {
  executed: number;
  blocked: number;
  failed: number;
  last_run_at: number | null;
}

export interface HooksConfig {
  execution: { active: HookExecution[]; recent: HookExecution[] };
  path: string;
  exists: boolean;
  /** 总开关（配置项 hooks_enabled 的热读值） */
  enabled: boolean;
  events: string[];
  integrations: HookIntegration[];
  hooks: Record<string, HookEntry[]>;
  active: Record<string, number>;
  stats: {
    events: Record<string, HookEventStats>;
    recent: HookRunDetail[];
  };
}

export interface HooksTestResult {
  event: string;
  enabled: boolean;
  allowed: boolean;
  executed: number;
  reason: string;
  results: HookRunDetail[];
}

export interface LlmHookItem {
  config_group: string;
  config_keys: Record<string, string>;
  mode: "sync" | "async";
  name: string;
  event: string;
  context: string;
  description: string;
  owner: string;
  source: string;
  priority: number;
  max_iterations: number;
  max_concurrent: number;
  cooldown_seconds: number;
  stall_seconds: number;
  debounce_seconds: number;
  model: string;
  allow_output_tools: boolean;
  route_output: boolean;
  tool_tags: string[];
  /** 启用状态（运行期开关，重启恢复代码声明初始值） */
  enabled: boolean;
}

export interface HookExecution {
  id: string;
  name: string;
  event: string;
  mode: "sync" | "async";
  source: string;
  scope: string;
  status: string;
  started_at: number;
  duration_ms: number;
  detail: string;
  task_id?: string;
  activity_ids?: string[];
  phase?: string;
  operation?: string;
  updated_at?: number;
  model_calls?: number | null;
  tool_calls?: number | null;
  context_mode?: string;
  context_messages?: number;
}

export interface LlmHooksOverview {
  enabled: boolean;
  runtime_started: boolean;
  events: string[];
  hooks: LlmHookItem[];
  governance: {
    max_concurrent: number;
    transcript_enabled: boolean;
  };
}
