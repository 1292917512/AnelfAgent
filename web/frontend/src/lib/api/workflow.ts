/** 工作流域 API — 运行列表/详情/启动/停止/续跑。 */

import { api } from "./client";

import type { WorkflowDefinition, WorkflowDefinitionSummary, WorkflowRun, WorkflowRunDetail, WorkflowSpec } from "@/lib/types";
export type { WorkflowDefinition, WorkflowDefinitionSummary, WorkflowRun, WorkflowRunDetail, WorkflowNode, WorkflowEvent, WorkflowStep, WorkflowSpec } from "@/lib/types";

export const workflowApi = {
  listDefinitions: (limit = 30, offset = 0) =>
    api.get<{ definitions: WorkflowDefinitionSummary[] }>("/workflow/definitions", { params: { limit, offset } }),
  definitionDetail: (id: string) => api.get<WorkflowDefinition>(`/workflow/definitions/${encodeURIComponent(id)}`),
  saveDefinition: (spec: unknown, id = "") => id
    ? api.put<WorkflowDefinition>(`/workflow/definitions/${encodeURIComponent(id)}`, { spec })
    : api.post<WorkflowDefinition>("/workflow/definitions", { spec }),
  deleteDefinition: (id: string) => api.delete<{ ok: boolean }>(`/workflow/definitions/${encodeURIComponent(id)}`),
  startDefinition: (id: string) => api.post<WorkflowRun>("/workflow/runs", { workflow_id: id }),
  deleteRun: (id: string) => api.delete<{ ok: boolean }>(`/workflow/runs/${encodeURIComponent(id)}`),
  cleanupRuns: (days: number, includeStopped: boolean) =>
    api.post<{ ok: boolean; deleted: number }>("/workflow/runs/cleanup", { days, include_stopped: includeStopped }),
  validate: (spec: unknown) => api.post<{ spec: WorkflowSpec; layers: string[][] }>("/workflow/validate", { spec }),
  listRuns: (limit = 30, offset = 0) =>
    api.get<{ runs: WorkflowRun[] }>("/workflow/runs", { params: { limit, offset } }),
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
