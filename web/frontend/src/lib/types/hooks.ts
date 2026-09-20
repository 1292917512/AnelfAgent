/** 钩子配置类型（/hooks、/hooks-llm API）。 */

export interface HookEntry {
  matcher: string;
  command: string;
  timeout?: number;
}

export interface HooksConfig {
  path: string;
  exists: boolean;
  events: string[];
  hooks: Record<string, HookEntry[]>;
  active: Record<string, number>;
}

export interface LlmHookItem {
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
  debounce_seconds: number;
  model: string;
  allow_output_tools: boolean;
  tool_tags: string[];
}

export interface LlmHooksOverview {
  enabled: boolean;
  runtime_started: boolean;
  events: string[];
  hooks: LlmHookItem[];
  governance: {
    max_concurrent: number;
    transcript_enabled: boolean;
    transcript_max_chars: number;
  };
}
