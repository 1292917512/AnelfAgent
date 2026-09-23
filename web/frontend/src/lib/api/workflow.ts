/** 工作流域 API — 运行列表/详情/启动/停止/续跑。 */

import { api } from "./client";

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
  result_preview: string;
  error: string;
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
  nodes: WorkflowNode[];
  events: WorkflowEvent[];
}

export const workflowApi = {
  listRuns: (limit = 30) =>
    api.get<{ runs: WorkflowRun[] }>("/workflow/runs", { params: { limit } }),
  runDetail: (runId: string) =>
    api.get<WorkflowRunDetail>(`/workflow/runs/${encodeURIComponent(runId)}`),
  startRun: (spec: unknown, resumeOf = "") =>
    api.post<WorkflowRun>("/workflow/runs", { spec, resume_of: resumeOf }),
  stopRun: (runId: string) =>
    api.post<{ ok: boolean; note?: string }>(
      `/workflow/runs/${encodeURIComponent(runId)}/stop`,
    ),
  resumeRun: (runId: string) =>
    api.post<WorkflowRun>(`/workflow/runs/${encodeURIComponent(runId)}/resume`),
};
