import type { ChatStreamingDiff } from "./chat";
import type { SkillMCPStatus } from "./workspace";
import type { HookExecution } from "./hooks";

interface EntryBase { id: string; ts: number; generation?: number }
export interface ActivityTag { key: string; value: string; root?: string; path?: string }
export interface ActivitySource { scope: string; kind: string; channel: string; target: string; session: string }
export interface ActivityNoticeItem { source: ActivitySource; role: string; message_id: string; ts: number; content: string }
export interface ActivitySkill {
  name: string;
  description: string;
  mode: "candidate" | "read";
  state?: string;
  score?: number | null;
  truncated?: boolean;
  mcp_status?: SkillMCPStatus[];
}
export type ActivityEntry =
  | EntryBase & { kind: "phase"; name: string; status: string; duration_ms: number; detail?: string; error?: string }
  | EntryBase & { kind: "notice"; origin: "conversation" | "push" | "cross_channel" | "delivery" | "recovery"; iteration: number; items: ActivityNoticeItem[]; count: number; deferred: number; truncated?: boolean }
  | EntryBase & Omit<HookExecution, "started_at"> & { kind: "hook" }
  | EntryBase & { kind: "context"; status: string; duration_ms: number; blocks: { layer: string; label: string; content: string }[]; block_count: number; skills?: ActivitySkill[]; error?: string; truncated?: boolean }
  | EntryBase & { kind: "thinking"; content: string; truncated?: boolean }
  | EntryBase & { kind: "text"; content: string; truncated?: boolean }
  | EntryBase & { kind: "tool"; name: string; owner?: string; arguments: string; targets: ActivityTag[]; request_id: string; truncated?: boolean; status: "running" | "done" | "error" | "interrupted"; result?: string; duration_ms?: number }
  | EntryBase & { kind: "delegation"; goal: string; agent: string; status: string; run_id?: string; result?: string; duration_ms?: number; background: boolean }
  | EntryBase & { kind: "background"; task_kind: string; description: string; status: "running" | "done" | "error" | "interrupted"; result?: string; duration_ms?: number }
  | EntryBase & { kind: "plan"; goal: string; status: string; files?: string; risks?: string; steps: { content: string; status: string; note: string }[] }
  | EntryBase & { kind: "model"; name: string; purpose?: string; call_id?: string; status: string; duration_ms?: number; ttft_ms?: number | null; error?: string; usage?: { total_input_tokens?: number; completion_tokens?: number; cache_read_input_tokens?: number; cache_observable?: boolean } }
  | EntryBase & ChatStreamingDiff & { kind: "file" };

export interface ActivityRun {
  id: string;
  scope: string;
  origin_scope: string;
  actor: string;
  label: string;
  kind: string;
  trigger?: string;
  initiator?: { id: string; name: string } | null;
  input: string;
  source: ActivitySource;
  owner_id: string;
  parent_id: string;
  status: string;
  control?: "" | "stopping" | "resuming" | "resumed";
  can_stop?: boolean;
  error?: string;
  started_at: number;
  updated_at: number;
  ended_at: number | null;
  revision: number;
  entries: ActivityEntry[];
  skills?: ActivitySkill[];
  entry_count: number;
  truncated: boolean;
}

export interface ActivitySnapshot { epoch: string; revision: number; runs: ActivityRun[] }

export interface ActivityReadingPosition {
  mode: "follow" | "reading" | "paused";
  runId?: string;
  entryId?: string;
  offset: number;
  top: number;
}

export interface ActivityViewState {
  selected: string | null;
  family: string | null;
  expanded: Record<string, { current: boolean; open: boolean }>;
  position?: ActivityReadingPosition;
}

export interface ActivityDelta {
  epoch: string;
  base_revision: number;
  run: Partial<Omit<ActivityRun, "entries">> & Pick<ActivityRun, "id" | "revision">;
  entries: ActivityEntry[];
  appends: { id: string; text: string }[];
  entry_order?: string[];
}

export interface ActivityControlResult {
  status: "ok" | "idle" | "accepted" | "error";
  error?: string;
  background?: { ok: boolean; error?: string }[];
  interrupted?: boolean;
  cancelled_delegations?: number;
}
