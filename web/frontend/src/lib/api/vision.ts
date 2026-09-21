/** 视觉域 API — 视觉能力 / 人脸识别。 */

import { api } from "./client";
import type {
  CapabilityStatus,
  FaceConsolidateResult,
  FaceEventListResult,
  FaceIdentifyResult,
  VisionStatus,
  FacePerson,
  FacePersonDetail,
  FacePersonListResult,
  FaceStats,
  FaceStatus,
  VisionSourceInfo,
} from "@/lib/types";

export const visionApi = {
  status: () => api.get<VisionStatus>("/vision/status"),
  sources: () => api.get<{ sources: VisionSourceInfo[] }>("/vision/sources"),
  watch: (action: string, source = "screen", interval = 0) =>
    api.post<{ watching: string[] }>("/vision/watch", { action, source, interval }),
  latestUrl: (source = "") =>
    `/api/vision/latest${source ? `?source=${encodeURIComponent(source)}` : ""}`,
  capabilities: () => api.get<CapabilityStatus>("/vision/capabilities"),
};

/** 能力路由状态（视觉/声音能力页共用）：提供者配置状态 + 各能力生效优先级链 */

export const faceApi = {
  status: (refresh = false) =>
    api.get<FaceStatus>("/face/status", { params: refresh ? { refresh: true } : {} }),
  stats: () => api.get<FaceStats>("/face/stats"),
  engineUnload: () => api.post<Record<string, unknown>>("/face/engine/unload"),
  imageUrl: (path: string) => `/api/face/image?path=${encodeURIComponent(path)}`,
  // 人物身份
  persons: (params?: { status?: string; keyword?: string; limit?: number; offset?: number }) =>
    api.get<FacePersonListResult>("/face/persons", { params }),
  personDetail: (id: number) => api.get<FacePersonDetail>(`/face/persons/${id}`),
  updatePerson: (id: number, data: {
    name?: string; role?: string; notes?: string; status?: string; threshold?: number | null;
  }) => api.patch<{ person: FacePerson }>(`/face/persons/${id}`, data),
  bindPerson: (id: number, entityScope: string) =>
    api.post<{ person: FacePerson }>(`/face/persons/${id}/bind`, { entity_scope: entityScope }),
  confirmPerson: (id: number, name: string, role = "") =>
    api.post<{ person: FacePerson }>(`/face/persons/${id}/confirm`, { name, role }),
  refinePerson: (id: number) =>
    api.post<{ samples: number; anchor_similarity: number | null }>(`/face/persons/${id}/refine`),
  deletePerson: (id: number) => api.delete(`/face/persons/${id}`),
  mergePersons: (sourceId: number, targetId: number) =>
    api.post("/face/persons/merge", { source_id: sourceId, target_id: targetId }),
  prunePersons: (includeWithSamples = false) =>
    api.post<{ pruned: number }>("/face/persons/prune",
      { include_with_samples: includeWithSamples }),
  consolidatePersons: (payload: {
    dry_run: boolean; threshold?: number; prune_insignificant?: boolean;
  }) => api.post<FaceConsolidateResult>("/face/persons/consolidate", payload),
  deleteSample: (sampleId: number) => api.delete(`/face/samples/${sampleId}`),
  // 出现事件
  events: (params?: {
    person_id?: number; entity_scope?: string; source?: string;
    unread_only?: boolean; limit?: number; offset?: number;
  }) => api.get<FaceEventListResult>("/face/events", { params }),
  markEventsRead: (eventIds?: number[], read = true) =>
    api.post<{ affected: number }>("/face/events/mark-read",
      { event_ids: eventIds ?? null, read }),
  deleteEvent: (id: number) => api.delete(`/face/events/${id}`),
  // 识别 / 注册 / 对比（上传图片）
  identifyImage: (file: File, ingest = false) => {
    const form = new FormData();
    form.append("file", file);
    form.append("ingest", String(ingest));
    return api.post<FaceIdentifyResult>("/face/identify", form);
  },
  enrollImage: (file: File, name: string, opts?: {
    faceIndex?: number; role?: string; notes?: string; entityScope?: string;
  }) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    form.append("face_index", String(opts?.faceIndex ?? 0));
    form.append("role", opts?.role ?? "");
    form.append("notes", opts?.notes ?? "");
    form.append("entity_scope", opts?.entityScope ?? "");
    return api.post<{ person: FacePerson; faces_in_image: number }>("/face/enroll", form);
  },
  compareImages: (fileA: File, fileB: File) => {
    const form = new FormData();
    form.append("file_a", fileA);
    form.append("file_b", fileB);
    return api.post<{
      faces_a: number; faces_b: number; same_person: boolean; threshold: number;
      best: { index_a: number; index_b: number; similarity: number };
    }>("/face/compare", form);
  },
};

// Audio（音频能力页 · 核心能力 + 音频库管理面）
