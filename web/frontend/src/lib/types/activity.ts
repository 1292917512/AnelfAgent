import type { ChatStreamingDiff } from "./chat";

interface EntryBase { id: string; ts: number }
export interface ActivityTag { key: string; value: string }
export interface ActivitySource { scope: string; kind: string; channel: string; target: string; session: string }
export type ActivityEntry =
  | EntryBase & { kind: "context"; status: string; duration_ms: number; blocks: { layer: string; label: string; content: string }[]; block_count: number; error?: string; truncated?: boolean }
  | EntryBase & { kind: "thinking"; content: string; truncated?: boolean }
  | EntryBase & { kind: "text"; content: string; truncated?: boolean }
  | EntryBase & { kind: "tool"; name: string; arguments: string; targets: ActivityTag[]; request_id: string; truncated?: boolean; status: "running" | "done" | "error" | "interrupted"; result?: string; duration_ms?: number }
  | EntryBase & { kind: "delegation"; goal: string; agent: string; status: string; run_id?: string; result?: string; duration_ms?: number; background: boolean }
  | EntryBase & { kind: "plan"; goal: string; status: string; files?: string; risks?: string; steps: { content: string; status: string; note: string }[] }
  | EntryBase & { kind: "model"; name: string; status: string; duration_ms?: number; error?: string; usage?: { total_input_tokens?: number; completion_tokens?: number; cache_read_input_tokens?: number; cache_observable?: boolean } }
  | EntryBase & ChatStreamingDiff & { kind: "file" };

export interface ActivityRun {
  id: string;
  scope: string;
  origin_scope: string;
  actor: string;
  label: string;
  kind: string;
  input: string;
  source: ActivitySource;
  owner_id: string;
  parent_id: string;
  status: string;
  error?: string;
  started_at: number;
  updated_at: number;
  ended_at: number | null;
  revision: number;
  entries: ActivityEntry[];
  entry_count: number;
  truncated: boolean;
}

export interface ActivitySnapshot { epoch: string; revision: number; runs: ActivityRun[] }
