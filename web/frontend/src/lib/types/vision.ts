/** 视觉能力与人脸识别类型（/vision、/face API）。 */

export interface CapabilityStatus {
  providers: {
    name: string;
    capabilities: string[];
    configured: Record<string, boolean>;
    details: Record<string, Record<string, unknown>>;
  }[];
  chains: Record<string, string[]>;
}

export interface VisionSourceInfo {
  key: string;
  display_name: string;
  description: string;
  pollable: boolean;
  can_capture: boolean;
  enabled: boolean;
  watching: boolean;
}

// Face（人脸识别 · 核心视觉的眼睛记忆：人物档案 / 识别 / 出现事件）

export interface FaceEngineHealth {
  status: string;
  model: string;
  dim: number;
  device: string;
  version: string;
}

export interface FaceStatus {
  engine: {
    configured: boolean;
    endpoint: string;
    reachable: boolean;
    health: FaceEngineHealth | null;
  };
  stats: FaceStats;
  thresholds: { match: number; merge: number; separation: number };
}

export interface FaceStats {
  persons?: number;
  pending_persons?: number;
  bound_persons?: number;
  samples?: number;
  events?: number;
  unread_events?: number;
  face_dims?: number;
  db_path?: string;
}

export interface FacePerson {
  id: number;
  person_key: string;
  name: string;
  role: string;
  status: string;
  threshold: number | null;
  notes: string;
  entity_scope: string;
  first_seen_ns: number;
  last_seen_ns: number;
  match_count: number;
  archived: boolean;
  anchor_weight: number;
  sample_count?: number;
  sources?: Record<string, number>;
}

export interface FaceSample {
  id: number;
  person_id: number;
  image_path: string;
  bbox: number[];
  quality: number;
  pose: { pitch?: number; yaw?: number; roll?: number };
  source: string;
  event_id: number | null;
  created_ns: number;
  dims: number;
}

export interface FaceHit {
  person_id: number | null;
  person_key: string;
  person_name: string;
  entity_scope: string;
  similarity: number;
  is_new: boolean;
  matched: boolean;
  det_score: number;
  bbox: number[];
  sample_added: boolean;
}

export interface FaceEvent {
  id: number;
  image_path: string;
  source: string;
  width: number;
  height: number;
  faces: FaceHit[];
  faces_count: number;
  ts_ns: number;
  read: boolean;
  created_ns: number;
}

export interface FacePersonListResult {
  items: FacePerson[];
  total: number;
  limit: number;
  offset: number;
}

export interface FaceEventListResult {
  items: FaceEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface FacePersonDetail {
  person: FacePerson;
  effective_threshold: number;
  samples: FaceSample[];
  recent_events: FaceEvent[];
}

export interface FaceIdentifyFace {
  index: number;
  bbox: number[];
  det_score: number;
  skipped?: string;
  best_match?: FaceMatchCandidate | null;
  candidates?: FaceMatchCandidate[];
}

export interface FaceMatchCandidate {
  id: number;
  person_key: string;
  name: string;
  role: string;
  status: string;
  threshold: number;
  similarity: number;
  matched: boolean;
  anchor_similarity: number;
  sample_similarity: number;
  separation: number | null;
  entity_scope: string;
}

export interface FaceIdentifyResult {
  ingested: boolean;
  width?: number;
  height?: number;
  faces_detected?: number;
  faces?: FaceIdentifyFace[];
  image_path?: string;
  source?: string;
  event_id?: number | null;
  hits?: FaceHit[];
  error?: string;
  skipped?: boolean;
}

export interface FaceConsolidateResult {
  dry_run: boolean;
  threshold: number;
  clusters: Array<{
    members: Array<{ id: number; person_key: string; name: string;
      match_count: number; similarity: number }>;
    keep_id: number;
    best_similarity: number;
  }>;
  cluster_count: number;
  persons_affected: number;
  merges: Array<{ from: string; into: string; samples_moved: number }>;
  insignificant: Array<{ id: number; person_key: string; name: string; match_count: number }>;
  insignificant_limits: { max_matches: number };
  pruned: Array<{ id: number; person_key: string; name: string }>;
}

/** 单路视觉采集源的实时状态 */
export interface VisionSourceStatus {
  last_capture_at?: number | null;
  last_error?: string;
  capture_count?: number;
}

/** GET /vision/status 的视觉系统状态 */
export interface VisionStatus {
  watching: string[];
  sources: Record<string, VisionSourceStatus>;
  latest?: { path: string; source: string; captured_at: number } | null;
  last_change_at?: number | null;
  injection?: {
    provider: string;
    active: boolean;
    last_text_inject_at?: number | null;
    last_media_inject?: { at: number; source: string; path: string } | null;
  };
}
