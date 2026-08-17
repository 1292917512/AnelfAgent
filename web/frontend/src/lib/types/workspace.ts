import type { LogEntry } from "./logs";

export interface WorkspaceNode {
  name: string;
  path: string;
  type: "dir" | "file";
  size?: number;
  modified: number;
  binary?: boolean;
  children?: WorkspaceNode[];
}

export interface WorkspaceFile {
  path: string;
  name: string;
  size: number;
  modified: number;
  binary: boolean;
  truncated: boolean;
  content: string;
}

export interface WorkspaceSearchHit {
  path: string;
  name: string;
  match: "name" | "content";
  snippet?: string;
}

export interface GlobalSearchResult {
  query: string;
  memory: { id: number; snippet: string; memory_type: string; tags: string[]; score: number }[];
  logs: LogEntry[];
  files: WorkspaceSearchHit[];
  conversations: { id: number; scope: string; role: string; snippet: string; time: string }[];
}

// ── Skills ─────────────────────────────────────────────────────

export interface SkillItem {
  name: string;
  description: string;
  trigger_patterns: string[];
  state: "active" | "stale" | "archived";
  use_count: number;
  match_count: number;
  patch_count: number;
  pinned: boolean;
  created_by: string;
  rationale: string;
  merged_into: string;
  /** 向量就绪状态：缓存命中=已嵌入；索引不可用时为 null（不显示） */
  embedded: boolean | null;
  created_at: number;
  last_activity_at: number;
  last_match_at: number;
  content?: string;
}

export interface SkillBuildState {
  state: "idle" | "warming" | "rebuilding";
  embedded: number;
  total: number;
  model: string;
  rebuilding: boolean;
  progress: { done: number; total: number };
  last_rebuild: {
    at: number;
    count: number;
    model: string;
  } | null;
}

export interface SkillLibraryHealth {
  counts: {
    active: number;
    stale: number;
    archived: number;
    pinned: number;
  };
  embedding: {
    embedded: number;
    total: number;
    cache_keys: number;
    model: string;
    /** 模型手动切换后全量重建进行中/待执行 */
    rebuilding: boolean;
  };
  /** 完整构建状态机（Mind 侧索引可用时） */
  build?: SkillBuildState;
  /** 解析失败的技能（严格契约下的脏文件，技能名 → 错误摘要） */
  parse_errors: Record<string, string>;
  capacity_reference: number;
  zero_engagement: string[];
  high_match_low_use: { name: string; match_count: number }[];
  patch_churn: { name: string; patch_count: number }[];
  trigger_collisions: Record<string, string[]>;
}
