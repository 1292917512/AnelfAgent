import { api } from "@/lib/api";
import type {
  AudioIdentifyResult,
  ConsolidateResult,
  OpenListStatus,
  RecordingListResult,
  SegmentListResult,
  SimilarityMapResult,
  Speaker,
  SpeakerDetail,
  SpeakerListResult,
  SpeakerUpdatePayload,
  SyncCycleResult,
  SyncPreview,
  VoiceSegment,
  VoiceprintConfigResult,
  VoiceprintStats,
  WatchStatus,
} from "./types";

// Voiceprint（音源库实体专属路由 /api/entity/voiceprint）
export const voiceprintApi = {
  stats: () => api.get<VoiceprintStats>("/entity/voiceprint/stats"),
  speakers: (params?: { status?: string; keyword?: string; limit?: number; offset?: number }) =>
    api.get<SpeakerListResult>("/entity/voiceprint/speakers", { params }),
  speakerDetail: (id: number) =>
    api.get<SpeakerDetail>(`/entity/voiceprint/speakers/${id}`),
  updateSpeaker: (id: number, data: SpeakerUpdatePayload) =>
    api.patch<{ speaker: Speaker }>(`/entity/voiceprint/speakers/${id}`, data),
  confirmSpeaker: (id: number, name: string, role = "") =>
    api.post<{ speaker: Speaker }>(`/entity/voiceprint/speakers/${id}/confirm`, { name, role }),
  mergeSpeakers: (sourceId: number, targetId: number) =>
    api.post("/entity/voiceprint/speakers/merge", { source_id: sourceId, target_id: targetId }),
  pruneSpeakers: (includeWithSamples = false) =>
    api.post<{ pruned: number }>("/entity/voiceprint/speakers/prune",
      { include_with_samples: includeWithSamples }),
  consolidateSpeakers: (payload: {
    dry_run: boolean; threshold?: number; prune_insignificant?: boolean;
  }) => api.post<ConsolidateResult>("/entity/voiceprint/speakers/consolidate", payload),
  similarityMap: (params?: { status?: string; neighbors?: number }) =>
    api.get<SimilarityMapResult>("/entity/voiceprint/speakers/similarity-map", { params }),
  deleteSpeaker: (id: number) =>
    api.delete(`/entity/voiceprint/speakers/${id}`),
  deleteSample: (sampleId: number) =>
    api.delete(`/entity/voiceprint/samples/${sampleId}`),
  enrollAudio: (file: File, name: string, role = "", notes = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    form.append("role", role);
    form.append("notes", notes);
    return api.post<{ speaker: Speaker; samples_enrolled: number }>(
      "/entity/voiceprint/enroll/audio", form);
  },
  identifyAudio: (file: File, ingest = false) => {
    const form = new FormData();
    form.append("file", file);
    form.append("ingest", String(ingest));
    return api.post<AudioIdentifyResult>("/entity/voiceprint/identify/audio", form);
  },
  segments: (params: {
    speaker_id?: number; recording_path?: string; time_from?: string; time_to?: string;
    q?: string; unread_only?: boolean; limit?: number; offset?: number; order?: string;
  }) => api.get<SegmentListResult>("/entity/voiceprint/segments", { params }),
  updateSegment: (id: number, data: { speaker_id?: number | null; transcript?: string }) =>
    api.patch<{ segment: VoiceSegment }>(`/entity/voiceprint/segments/${id}`, data),
  deleteSegment: (id: number) =>
    api.delete(`/entity/voiceprint/segments/${id}`),
  markRead: (segmentIds?: number[]) =>
    api.post<{ marked_read: number }>("/entity/voiceprint/segments/mark-read", segmentIds ?? null),
  syncNow: () => api.post<SyncCycleResult>("/entity/voiceprint/sync"),
  syncStatus: () => api.get<WatchStatus>("/entity/voiceprint/sync/status"),
  syncPreview: () => api.get<SyncPreview>("/entity/voiceprint/sync/preview"),
  recordings: (params?: { limit?: number; offset?: number }) =>
    api.get<RecordingListResult>("/entity/voiceprint/sync/files", { params }),
  deleteRecording: (path: string) =>
    api.delete("/entity/voiceprint/sync/files", { params: { path } }),
  rebuildRecordings: (paths: string[]) =>
    api.post<{ error: string; results: Array<{ path: string; outcome: string; detail: string }> }>(
      "/entity/voiceprint/sync/rebuild", { paths }),
  config: () => api.get<VoiceprintConfigResult>("/entity/voiceprint/config"),
  updateConfig: (updates: Record<string, unknown>) =>
    api.put<{ updated: number }>("/entity/voiceprint/config", { updates }),
  openlistStatus: () => api.get<OpenListStatus>("/entity/voiceprint/openlist/status"),
};
