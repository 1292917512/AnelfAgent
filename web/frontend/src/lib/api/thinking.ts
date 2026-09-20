/** 思维观测域 API — 思维链路 / 上下文快照。 */

import { api } from "./client";
import type {
  ContextProviderStatus,
  ContextSnapshotData,
  SnapshotListItem,
  SnapshotRecord,
  SnapshotResponse,
} from "@/lib/types";

export const thinkingApi = {
  status: () => api.get("/thinking/status"),
  toggle: (enabled: boolean) => api.put("/thinking/toggle", { enabled }),
  sessions: (limit = 20) => api.get("/thinking/sessions", { params: { limit } }),
  session: (id: string) => api.get(`/thinking/sessions/${encodeURIComponent(id)}`),
};

// Context（上下文管理）

export const contextApi = {
  snapshotArm: () => api.post("/context/snapshot/arm"),
  snapshotDisarm: () => api.post("/context/snapshot/disarm"),
  snapshotGet: () => api.get<SnapshotResponse>("/context/snapshot"),
  snapshotClear: () => api.post("/context/snapshot/clear"),
  snapshotSetContinuous: (enabled: boolean) =>
    api.put<{ continuous: boolean }>("/context/snapshot/continuous", { enabled }),
  snapshotRecords: (limit = 100) =>
    api.get<{ records: SnapshotRecord[]; count: number }>("/context/snapshot/records", { params: { limit } }),
  snapshotsList: () => api.get<{ snapshots: SnapshotListItem[]; count: number }>("/context/snapshots"),
  snapshotDetail: (filename: string) => api.get<ContextSnapshotData>(`/context/snapshots/${encodeURIComponent(filename)}`),
  snapshotDelete: (filename: string) => api.delete(`/context/snapshots/${encodeURIComponent(filename)}`),
  snapshotsClear: () => api.post("/context/snapshots/clear"),
  providers: () => api.get<ContextProviderStatus>("/context/providers"),
};

// Config
