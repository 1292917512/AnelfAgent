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
  patch_count: number;
  pinned: boolean;
  created_by: string;
  created_at: number;
  last_activity_at: number;
  content?: string;
}
