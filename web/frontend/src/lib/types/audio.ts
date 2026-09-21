/** 声纹识别与音色管理类型（/audio API）。 */

export interface FunasrStatus {
  configured: boolean;
  reachable: boolean;
  endpoint: string;
}

export interface GpuWorkerStatus {
  ok?: boolean;
  loaded?: boolean;
  model?: string;
  device?: string;
  vram_gb?: number;
  cuda?: boolean;
  pipeline?: boolean;
}

export interface GpuStatus {
  moss?: GpuWorkerStatus;
  asr?: GpuWorkerStatus;
  diarize?: GpuWorkerStatus;
  embedder?: GpuWorkerStatus;
}

export interface GpuUnloadResult {
  [target: string]: {
    ok?: boolean;
    vram_gb_before?: number;
    vram_gb_after?: number;
    error?: string;
  };
}

export interface VoicePresetEntry {
  id: string;
  name: string;
  voice_id: string;
  reference_audio: string;
  reference_text: string;
  note: string;
}

export interface VoicePresetPayload {
  id?: string;
  name: string;
  voice_id?: string;
  reference_audio?: string;
  reference_text?: string;
  note?: string;
}

export interface VoicePresetOverview {
  presets: VoicePresetEntry[];
  assignments: { default: string; realtime: string };
}

export interface AudioProviderInfo {
  name: string;
  kind: string;
  priority: number;
  available: boolean;
  hint?: string;
}

export interface AudioInjectionStatus {
  provider: string;
  active: boolean;
}

export interface VoiceEndpointStatus {
  configured: string;
  effective: "smart_turn" | "silero" | "energy";
  base: string;
  runtime_ready: boolean;
  models: Record<string, "ready" | "missing">;
}

export interface AudioStatus {
  providers: AudioProviderInfo[];
  asr_available: boolean;
  voiceprint_available: boolean;
  tts_providers: Array<{ name: string; priority: number; available: boolean }>;
  tts_available: boolean;
  realtime: {
    enabled: boolean; mode: string; sessions: number;
    owners: string[]; states: Record<string, string>;
    endpoint?: VoiceEndpointStatus;
  };
  voice_config: Record<string, unknown>;
  library: AudioLibraryStats;
  injection: AudioInjectionStatus;
}

export interface AudioLibraryStats {
  speakers?: number;
  pending_speakers?: number;
  bound_speakers?: number;
  samples?: number;
  segments?: number;
  unread_segments?: number;
  audio_ms?: number;
  missing_embeddings?: number;
  recordings?: number;
  voiceprint_dims?: number;
  vec_available?: boolean;
  fts_available?: boolean;
  db_path?: string;
  match_threshold?: number;
  asr_configured?: boolean;
}

export interface AudioSpeaker {
  id: number;
  speaker_key: string;
  name: string;
  role: string;
  status: string;
  threshold: number | null;
  notes: string;
  device_source: string;
  entity_scope: string;
  total_audio_ms: number;
  first_seen_ns: number;
  last_seen_ns: number;
  match_count: number;
  archived: boolean;
  anchor_weight?: number;
  sample_count?: number;
  channels?: Record<string, number>;
}

export interface AudioSpeakerListResult {
  items: AudioSpeaker[];
  total: number;
  limit: number;
  offset: number;
}

export interface AudioSpeakerDetail {
  speaker: AudioSpeaker;
  effective_threshold: number;
  samples: Array<{
    id: number; segment_id: number | null; score: number;
    channel: string; duration_ms: number;
    source: string; created_ns: number; dims: number;
  }>;
  recent_segments: AudioSegment[];
}

export interface AudioSpeakerUpdatePayload {
  name?: string;
  role?: string;
  status?: string;
  threshold?: number | null;
  notes?: string;
  device_source?: string;
}

export interface AudioSegment {
  id: number;
  recording_path: string;
  source_file: string;
  device_source: string;
  start_ms: number;
  part_start_ms: number;
  end_ms: number;
  speaker_id: number | null;
  speaker_name: string;
  speaker_key: string;
  entity_scope: string;
  is_new_speaker: boolean;
  similarity: number;
  transcript: string;
  has_embedding: boolean;
  ts_ns: number;
  read: boolean;
  score?: number;
}

export interface AudioSegmentListResult {
  items: AudioSegment[];
  total: number;
  limit?: number;
  offset?: number;
  speaker?: { id: number; name: string; speaker_key: string };
}

export interface AudioRecording {
  path: string;
  kind: string;
  fingerprint: string;
  started_ns: number;
  file_count: number;
  status: string;
  error: string;
  segments: number;
  files: Array<{ path: string; duration_s: number }>;
  synced_ns: number;
}

export interface AudioRecordingListResult {
  items: AudioRecording[];
  total: number;
  limit: number;
  offset: number;
}

export interface AudioIdentifyCandidate {
  id: number;
  speaker_key: string;
  name: string;
  role: string;
  status: string;
  threshold: number;
  similarity: number;
  matched: boolean;
  channel?: string;
  anchor_similarity?: number;
  sample_similarity?: number;
  channel_similarity?: number | null;
  separation?: number | null;
  entity_scope?: string;
}

export interface AudioIdentifyResult {
  ingested: boolean;
  segments: Array<{
    start_ms: number;
    end_ms: number;
    text: string;
    candidates: AudioIdentifyCandidate[];
  }>;
}

export interface AudioConsolidateResult {
  dry_run: boolean;
  threshold: number;
  clusters: Array<{
    keep_id: number;
    best_similarity: number;
    members: Array<{
      id: number; speaker_key: string; name: string;
      total_audio_ms: number; match_count: number; similarity: number;
    }>;
  }>;
  cluster_count: number;
  speakers_affected: number;
  merges: Array<{ from: string; into: string; samples_moved: number }>;
  insignificant: Array<{
    id: number; speaker_key: string; name: string;
    match_count: number; total_audio_ms: number;
  }>;
  insignificant_limits: { max_matches: number; max_audio_ms: number };
  pruned: Array<{
    id: number; speaker_key: string; name: string;
    match_count: number; total_audio_ms: number;
  }>;
}

export interface AudioSimilarityMapResult {
  status: string;
  threshold: number;
  speakers_total: number;
  estimated_persons: number;
  speakers: Array<{
    id: number; speaker_key: string; name: string; status: string;
    match_count: number; total_audio_ms: number; cluster_size: number;
    top_similar: Array<{
      id: number; speaker_key: string; name: string; status: string;
      similarity: number; mergable: boolean;
    }>;
  }>;
  clusters: AudioConsolidateResult["clusters"];
  matrix?: { order: number[]; values: number[][] } | null;
}

// Database（数据管理页 · 数据库管理）
