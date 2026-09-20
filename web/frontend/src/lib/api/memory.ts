/** 记忆域 API — 长期记忆 / 便签 / 图谱 / 标签。 */

import { api } from "./client";
import type {
  CogneeConfig,
  CogneeDataset,
  CogneeStatus,
  GoalData,
  GoalStep,
  GraphData,
  GraphEdge,
  GraphNode,
  GraphNodeDetail,
  MemoryDocument,
  MemoryFileInfo,
  ProbeStatus,
} from "@/lib/types";

export const memoryApi = {
  health: () => api.get("/memory/health"),
  cognee: {
    status: () => api.get<CogneeStatus>("/memory/cognee/status"),
    getConfig: () => api.get<CogneeConfig>("/memory/cognee/config"),
    saveConfig: (data: Partial<CogneeConfig>) => api.put<CogneeConfig>("/memory/cognee/config", data),
    retry: () => api.post("/memory/cognee/retry"),
    rebuild: () => api.post("/memory/cognee/rebuild"),
    compact: () =>
      api.post<{ ok: boolean; scheduled?: boolean; result?: { bytes_reclaimed?: number }; error?: string }>(
        "/memory/cognee/compact",
      ),
    backfill: (limit = 0, dryRun = true) =>
      api.post("/memory/cognee/backfill", { limit, dry_run: dryRun }),
    datasets: () => api.get<CogneeDataset[]>("/memory/cognee/datasets"),
    probeStatus: () => api.get<ProbeStatus>("/memory/probe-status"),
    improve: (datasetName: string) =>
      api.post("/memory/cognee/improve", { dataset_name: datasetName }),
  },
  stm: {
    list: () => api.get("/memory/stm"),
    delete: (index: number) => api.delete(`/memory/stm/${index}`),
    clear: () => api.delete("/memory/stm"),
    status: () => api.get("/memory/stm/status"),
  },
  ltm: {
    list: (memoryType?: string, limit = 200) =>
      api.get("/memory/ltm", { params: { memory_type: memoryType, limit } }),
    get: (id: number) => api.get(`/memory/ltm/${id}`),
    create: (content: string, memoryType = "semantic", importance = 0.5, tags?: string[]) =>
      api.post("/memory/ltm", { content, memory_type: memoryType, importance, tags }),
    update: (id: number, content: string, importance = 0.5, tags?: string[]) =>
      api.put(`/memory/ltm/${id}`, { content, importance, tags }),
    delete: (id: number) => api.delete(`/memory/ltm/${id}`),
    clear: (memoryType?: string) => api.delete("/memory/ltm", { params: { memory_type: memoryType } }),
    stats: () => api.get("/memory/ltm/stats"),
    search: (query: string, tags?: string, limit = 20) =>
      api.get("/memory/ltm/search", { params: { query, tags, limit } }),
    paginated: (page = 1, pageSize = 50, memoryType?: string) =>
      api.get("/memory/ltm/paginated", { params: { page, page_size: pageSize, memory_type: memoryType } }),
    merge: (ids: number[], content: string) =>
      api.post("/memory/ltm/merge", { ids, content }),
    evidence: (id: number, signal: "confirm" | "dispute") =>
      api.post(`/memory/ltm/${id}/evidence`, { signal }),
  },
  recallTest: (data: {
    query: string;
    depth?: "shallow" | "deep";
    tags?: string[];
    entity_scope?: string;
    limit?: number;
    search_types?: string[];
  }) => api.post("/memory/recall-test", data),
  conv: {
    scopes: () => api.get("/memory/conversations/scopes"),
    messages: (
      scopeType: string,
      scopeId: string,
      opts: { limit?: number; beforeId?: number; tsFrom?: number; tsTo?: number } = {},
    ) =>
      api.get("/memory/conversations", {
        params: {
          scope_type: scopeType,
          scope_id: scopeId,
          limit: opts.limit ?? 200,
          ...(opts.beforeId != null ? { before_id: opts.beforeId } : {}),
          ...(opts.tsFrom != null ? { ts_from: opts.tsFrom } : {}),
          ...(opts.tsTo != null ? { ts_to: opts.tsTo } : {}),
        },
      }),
    delete: (rowId: number) => api.delete(`/memory/conversations/${rowId}`),
    clear: (scopeType: string, scopeId: string) =>
      api.post("/memory/conversations/clear", { scope_type: scopeType, scope_id: scopeId }),
  },
  entities: {
    list: () => api.get("/memory/entities"),
    save: (scopeType: string, scopeId: string, personality: string) =>
      api.put("/memory/entities", { scope_type: scopeType, scope_id: scopeId, personality }),
    delete: (scopeType: string, scopeId: string) =>
      api.post("/memory/entities/delete", { scope_type: scopeType, scope_id: scopeId }),
    aliases: () => api.get("/memory/entities/aliases"),
    link: (srcType: string, srcId: string, tgtType: string, tgtId: string) =>
      api.post("/memory/entities/link", {
        source_scope_type: srcType, source_scope_id: srcId,
        target_scope_type: tgtType, target_scope_id: tgtId,
      }),
    unlink: (scopeType: string, scopeId: string) =>
      api.post("/memory/entities/unlink", { scope_type: scopeType, scope_id: scopeId }),
  },
  notes: {
    read: () => api.get<{ content: string; path: string }>("/memory/notes"),
    write: (content: string) => api.put("/memory/notes", { content }),
  },
  rules: {
    get: () => api.get<{ content: string }>("/memory/rules"),
    save: (content: string) => api.put("/memory/rules", { content }),
  },
  files: {
    list: () => api.get<MemoryFileInfo[]>("/memory/files"),
    read: (path: string) => api.get<{ content: string }>("/memory/files/content", { params: { path } }),
    write: (path: string, content: string) => api.put("/memory/files/content", { path, content }),
    delete: (path: string) => api.delete("/memory/files", { params: { path } }),
  },
  index: {
    status: () => api.get("/memory/index/status"),
    resync: (force = false) => api.post("/memory/index/resync", null, { params: { force } }),
    cleanCache: () => api.post("/memory/index/clean-cache"),
    rebuildVectors: () => api.post("/memory/embedding/rebuild"),
  },
  documents: {
    list: () => api.get<MemoryDocument[]>("/memory/documents"),
    upload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.post<{ ok?: boolean; error?: string; chunks?: number }>("/memory/documents/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    delete: (path: string) => api.delete("/memory/documents", { params: { path } }),
  },
  goals: {
    list: (status = "all") => api.get<{ goals: GoalData[] }>("/memory/goals", { params: { status } }),
    get: (goalId: string) => api.get(`/memory/goals/${encodeURIComponent(goalId)}`),
    create: (title: string, description = "", steps?: string[], due_time?: string, recurring?: boolean) =>
      api.post("/memory/goals", { title, description, steps, ...(due_time ? { due_time } : {}), ...(recurring ? { recurring } : {}) }),
    update: (goalId: string, data: { title?: string; description?: string; status?: string; steps?: GoalStep[]; due_time?: string | null; recurring?: boolean }) =>
      api.put(`/memory/goals/${encodeURIComponent(goalId)}`, data),
    delete: (goalId: string) => api.delete(`/memory/goals/${encodeURIComponent(goalId)}`),
  },
};

// MCP

export const tagsApi = {
  unified: () => api.get("/tags/unified"),
  toolTags: () => api.get<string[]>("/tags/tool"),
  createMessageTag: (name: string, description: string) =>
    api.post("/tags/message", { name, description }),
  deleteMessageTag: (name: string) =>
    api.delete(`/tags/message/${encodeURIComponent(name)}`),
};

// System

export const graphApi = {
  get: (params?: { predicate?: string; origin?: string; include_archived?: boolean; limit?: number }) =>
    api.get<GraphData>("/memory/graph", { params }),
  nodeDetail: (node: string) =>
    api.get<GraphNodeDetail>("/memory/graph/node_detail", { params: { node } }),
  neighborhood: (node: string, depth = 1) =>
    api.get<{ found: boolean; node: GraphNode | null; nodes: GraphNode[]; edges: GraphEdge[] }>(
      "/memory/graph/neighborhood", { params: { node, depth } }),
  upsertNode: (data: { node_key: string; label?: string; metadata?: Record<string, unknown> }) =>
    api.post<{ ok: boolean; node: GraphNode }>("/memory/graph/nodes", data),
  deleteNode: (node_key: string) =>
    api.post<{ ok: boolean }>("/memory/graph/nodes/delete", { node_key }),
  addEdge: (data: {
    subject: string; predicate: string; object: string;
    symmetric?: boolean; strength?: number; evidence?: string;
  }) => api.post<{ ok: boolean; edge: GraphEdge }>("/memory/graph/edges", data),
  updateEdge: (edgeId: number, data: {
    predicate?: string; strength?: number; evidence?: string; symmetric?: boolean;
  }) => api.put<{ ok: boolean; edge: GraphEdge }>(`/memory/graph/edges/${edgeId}`, data),
  deleteEdge: (edgeId: number) =>
    api.post<{ ok: boolean }>(`/memory/graph/edges/${edgeId}/delete`),
  mergeNodes: (source_key: string, target_key: string) =>
    api.post<{ ok: boolean; edges_moved: number; edges_merged: number }>(
      "/memory/graph/merge", { source_key, target_key }),
};

// ── 用户 hooks（/hooks，config/hooks.json 管理面） ──
