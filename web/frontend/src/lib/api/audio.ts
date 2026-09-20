/** 音频域 API — 声纹 / 音色 / 语音片段。 */

import { api } from "./client";
import type {
  AudioConsolidateResult,
  AudioIdentifyResult,
  AudioLibraryStats,
  AudioRecordingListResult,
  AudioSegment,
  AudioSegmentListResult,
  AudioSimilarityMapResult,
  AudioSpeaker,
  AudioSpeakerDetail,
  AudioSpeakerListResult,
  AudioSpeakerUpdatePayload,
  AudioStatus,
  CapabilityStatus,
  FunasrStatus,
  VoicePresetEntry,
  VoicePresetOverview,
  VoicePresetPayload,
} from "@/lib/types";

export const audioApi = {
  status: () => api.get<AudioStatus>("/audio/status"),
  stats: () => api.get<AudioLibraryStats>("/audio/stats"),
  capabilities: () => api.get<CapabilityStatus>("/audio/capabilities"),
  funasrStatus: (refresh = false) =>
    api.get<FunasrStatus>("/audio/funasr/status", { params: refresh ? { refresh: true } : {} }),
  analyze: (path: string) => api.post("/audio/analyze", { path }),
  // 声纹身份
  speakers: (params?: { status?: string; keyword?: string; limit?: number; offset?: number }) =>
    api.get<AudioSpeakerListResult>("/audio/speakers", { params }),
  speakerDetail: (id: number) => api.get<AudioSpeakerDetail>(`/audio/speakers/${id}`),
  updateSpeaker: (id: number, data: AudioSpeakerUpdatePayload) =>
    api.patch<{ speaker: AudioSpeaker }>(`/audio/speakers/${id}`, data),
  confirmSpeaker: (id: number, name: string, role = "") =>
    api.post<{ speaker: AudioSpeaker }>(`/audio/speakers/${id}/confirm`, { name, role }),
  refineSpeaker: (id: number) =>
    api.post<{ samples: number; anchor_similarity: number | null }>(
      `/audio/speakers/${id}/refine`),
  bindSpeaker: (id: number, entityScope: string) =>
    api.post<{ speaker: AudioSpeaker }>(`/audio/speakers/${id}/bind`,
      { entity_scope: entityScope }),
  mergeSpeakers: (sourceId: number, targetId: number) =>
    api.post("/audio/speakers/merge", { source_id: sourceId, target_id: targetId }),
  pruneSpeakers: (includeWithSamples = false) =>
    api.post<{ pruned: number }>("/audio/speakers/prune",
      { include_with_samples: includeWithSamples }),
  consolidateSpeakers: (payload: {
    dry_run: boolean; threshold?: number; prune_insignificant?: boolean;
  }) => api.post<AudioConsolidateResult>("/audio/speakers/consolidate", payload),
  similarityMap: (params?: { status?: string; neighbors?: number }) =>
    api.get<AudioSimilarityMapResult>("/audio/speakers/similarity-map", { params }),
  deleteSpeaker: (id: number) => api.delete(`/audio/speakers/${id}`),
  deleteSample: (sampleId: number) => api.delete(`/audio/samples/${sampleId}`),
  enrollAudio: (file: File, name: string, role = "", notes = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    form.append("role", role);
    form.append("notes", notes);
    return api.post<{ speaker: AudioSpeaker; samples_enrolled: number }>(
      "/audio/enroll/audio", form);
  },
  identifyAudio: (file: File, ingest = false) => {
    const form = new FormData();
    form.append("file", file);
    form.append("ingest", String(ingest));
    return api.post<AudioIdentifyResult>("/audio/identify/audio", form);
  },
  // 语音片段（时间线 / 检索 / 编辑）
  segments: (params: {
    speaker_id?: number; recording_path?: string; time_from?: string; time_to?: string;
    q?: string; unread_only?: boolean; limit?: number; offset?: number; order?: string;
  }) => api.get<AudioSegmentListResult>("/audio/segments", { params }),
  updateSegment: (id: number, data: { speaker_id?: number | null; transcript?: string }) =>
    api.patch<{ segment: AudioSegment }>(`/audio/segments/${id}`, data),
  deleteSegment: (id: number) => api.delete(`/audio/segments/${id}`),
  markRead: (segmentIds?: number[], read = true) =>
    api.post<{ marked: number; read: boolean }>("/audio/segments/mark-read", {
      segment_ids: segmentIds ?? null,
      read,
    }),
  deleteSegments: (params: {
    speaker_id?: number; entity?: string; recording_path?: string;
    time_from?: string; time_to?: string; unread_only?: boolean;
  }) =>
    api.delete<{ deleted: number; samples_deleted: number }>("/audio/segments", { params }),
  // 录制单元
  recordings: (params?: { limit?: number; offset?: number }) =>
    api.get<AudioRecordingListResult>("/audio/recordings", { params }),
  deleteRecording: (path: string) =>
    api.delete("/audio/recordings", { params: { path } }),
  rebuildRecording: (path: string) =>
    api.post<{ error: string; results: Array<{ path: string; outcome: string }> }>(
      "/entity/audiosync/sync/rebuild", { paths: [path] }),
  // 音色预设（AI 与 Web 共用的音色库）
  voicePresets: () => api.get<VoicePresetOverview>("/audio/voice-presets"),
  saveVoicePreset: (data: VoicePresetPayload) =>
    api.post<VoicePresetEntry>("/audio/voice-presets", data),
  deleteVoicePreset: (id: string) => api.delete(`/audio/voice-presets/${id}`),
  assignVoice: (scene: "default" | "realtime", presetId: string) =>
    api.post("/audio/voice-presets/assign", { scene, preset_id: presetId }),
};
