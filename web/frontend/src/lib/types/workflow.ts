export interface WorkflowRun {
  run_id: string;
  name: string;
  status: "running" | "completed" | "failed" | "stopped";
  stop_reason?: string;
  scope?: string;
  parent_run_id?: string;
  spec_hash?: string;
  created_at?: number;
  finished_at?: number | null;
  running?: boolean;
}

export interface WorkflowNode {
  key: string;
  ordinal: number;
  kind: "ask" | "tool" | "repair";
  status: "running" | "completed" | "failed";
  result: string;
  error: string;
  usage: Record<string, unknown> | null;
  delegation_id: string;
  created_at: number;
  updated_at: number;
}

export interface WorkflowEvent {
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  ts: number;
}

export interface WorkflowRunDetail {
  run: WorkflowRun;
  spec: WorkflowSpec;
  nodes: WorkflowNode[];
  events: WorkflowEvent[];
  events_truncated: boolean;
}

export interface WorkflowStep {
  key: string;
  kind: "ask" | "tool";
  depends_on?: string[];
  continue_from?: string;
  goal?: string;
  tool?: string;
  phase?: string | null;
  [field: string]: unknown;
}

export interface WorkflowSpec {
  name: string;
  description?: string;
  steps: WorkflowStep[];
}

export interface WorkflowDefinitionSummary {
  workflow_id: string;
  name: string;
  created_at: number;
  updated_at: number;
}

export interface WorkflowDefinition extends WorkflowDefinitionSummary {
  spec: WorkflowSpec;
}
