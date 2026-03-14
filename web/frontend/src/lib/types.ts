export interface ProviderConfig {
  id: string;
  base_url: string;
  api_key: string;
  api_type?: string;
  [key: string]: unknown;
}

export interface ModelConfig {
  id: string;
  provider_id: string;
  model_type?: string;
  [key: string]: unknown;
}

export interface PersonaData {
  name?: string;
  personality?: string[];
  [key: string]: unknown;
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
  name?: string;
  path?: string;
  lines?: number;
  size?: string;
  [key: string]: unknown;
}

export interface UnifiedTag {
  name: string;
  description: string;
  builtin: boolean;
  sources: Array<"message" | "tool" | "custom">;
}
