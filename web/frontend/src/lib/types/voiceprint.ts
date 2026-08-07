/** 音源库（voiceprint 实体）类型定义。 */

export type SpeakerStatus = "confirmed" | "pending";

export interface Speaker {
  id: number;
  speaker_key: string;
  name: string;
  role: string;
  status: SpeakerStatus;
  threshold: number | null;
  notes: string;
  device_source: string;
  total_audio_ms: number;
  first_seen_ns: number;
  last_seen_ns: number;
  match_count: number;
  archived: boolean;
}

export interface SpeakerListItem extends Speaker {
  sample_count: number;
}

export interface SpeakerListResult {
  items: SpeakerListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface VoiceSample {
  id: number;
  speaker_id: number;
  segment_id: number | null;
  score: number;
  source: string;
  created_ns: number;
  dims: number;
}

export interface VoiceSegment {
  id: number;
  recording_path: string;
  source_file: string;
  device_source: string;
  start_ms: number;
  end_ms: number;
  speaker_id: number | null;
  speaker_name: string;
  speaker_key: string;
  is_new_speaker: boolean;
  similarity: number;
  transcript: string;
  has_embedding: boolean;
  ts_ns: number;
  read: boolean;
  score?: number;
}

export interface SegmentListResult {
  items: VoiceSegment[];
  total: number;
  limit?: number;
  offset?: number;
}

export interface SpeakerDetail {
  speaker: Speaker;
  effective_threshold: number;
  samples: VoiceSample[];
  recent_segments: VoiceSegment[];
}

export interface IdentifyCandidate {
  id: number;
  speaker_key: string;
  name: string;
  role: string;
  status: SpeakerStatus;
  threshold: number;
  similarity: number;
  matched: boolean;
}

export interface AudioIdentifySegment {
  start_ms: number;
  end_ms: number;
  text: string;
  candidates: IdentifyCandidate[];
}

export interface AudioIdentifyResult {
  ingested: boolean;
  segments: AudioIdentifySegment[];
}

export interface SpeakerUpdatePayload {
  name?: string;
  role?: string;
  status?: SpeakerStatus;
  threshold?: number;
  notes?: string;
  device_source?: string;
}

export interface ConsolidateMember {
  id: number;
  speaker_key: string;
  name: string;
  total_audio_ms: number;
  match_count: number;
  similarity: number;
}

export interface ConsolidateCluster {
  members: ConsolidateMember[];
  keep_id: number;
  best_similarity: number;
}

export interface InsignificantSpeaker {
  id: number;
  speaker_key: string;
  name: string;
  match_count: number;
  total_audio_ms: number;
}

export interface ConsolidateResult {
  dry_run: boolean;
  threshold: number;
  clusters: ConsolidateCluster[];
  cluster_count: number;
  speakers_affected: number;
  merges: Array<{ from: string; into: string; samples_moved: number }>;
  insignificant: InsignificantSpeaker[];
  insignificant_limits: { max_matches: number; max_audio_ms: number };
  pruned: InsignificantSpeaker[];
}

export interface SimilarityNeighbor {
  id: number;
  speaker_key: string;
  name: string;
  status: SpeakerStatus;
  similarity: number;
  mergable: boolean;
}

export interface SimilaritySpeaker {
  id: number;
  speaker_key: string;
  name: string;
  status: SpeakerStatus;
  match_count: number;
  total_audio_ms: number;
  cluster_size: number;
  top_similar: SimilarityNeighbor[];
}

export interface SimilarityMapResult {
  status: string;
  threshold: number;
  speakers_total: number;
  estimated_persons: number;
  speakers: SimilaritySpeaker[];
  clusters: ConsolidateCluster[];
  matrix: { order: number[]; values: number[][] } | null;
}

export interface WatchProgress {
  current: string;
  current_started_ns: number;
  done: number;
  total: number;
  stage?: "download" | "analyze" | "merge" | "transcribe" | "ingest";
  batch?: number;
  batches?: number;
}

export interface WatchStatus {
  enabled: boolean;
  paused?: boolean;
  source: string;
  running: boolean;
  syncing?: boolean;
  progress?: WatchProgress | null;
  last_scan_ns: number;
  last_result: Record<string, unknown>;
  last_error: string;
}

export interface SyncCycleResult {
  scanned: number;
  new: number;
  ingested: number;
  deleted?: number;
  failed?: number;
  no_speech?: number;
  error: string;
  status?: WatchStatus;
}

export interface OpenListStatus {
  configured: boolean;
  reachable: boolean;
  latency_ms: number;
  error: string;
}

export interface SyncPendingUnit {
  path: string;
  kind: "folder" | "file";
  started_ns: number;
  file_count: number;
  reason: "new" | "changed" | "retry";
}

export interface SyncPreview {
  busy: boolean;
  error: string;
  nas_total: number;
  pending: SyncPendingUnit[];
  synced: Record<string, number>;
  excluded?: number;
}

export interface Recording {
  path: string;
  kind: "folder" | "file";
  fingerprint: string;
  started_ns: number;
  file_count: number;
  status: "done" | "no_speech" | "error";
  error: string;
  segments: number;
  synced_ns: number;
}

export interface RecordingListResult {
  items: Recording[];
  total: number;
  limit: number;
  offset: number;
}

export interface VoiceprintConfigItem {
  key: string;
  description: string;
  value_type: string;
  default_value: unknown;
  current_value: unknown;
}

export interface VoiceprintConfigResult {
  items: VoiceprintConfigItem[];
}

export interface VoiceprintStats {
  speakers: number;
  pending_speakers: number;
  samples: number;
  segments: number;
  unread_segments: number;
  missing_embeddings: number;
  recordings?: number;
  vec_available: boolean;
  fts_available: boolean;
  match_threshold: number;
  funasr_configured: boolean;
  ingest_enabled: boolean;
  text_embedding_model?: string;
  watch?: WatchStatus;
}
