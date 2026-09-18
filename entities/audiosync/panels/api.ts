import { api } from "@/lib/api";

/** 音源同步实体路由（/api/entity/audiosync）：同步业务与实体配置。
 *  音频库本体（说话人/片段/录制单元）走核心 audioApi（@/lib/api）。 */

export interface SyncWatchProgress {
  current: string;
  current_started_ns: number;
  done: number;
  total: number;
  stage?: "download" | "analyze" | "merge" | "transcribe" | "ingest";
  batch?: number;
  batches?: number;
}

export interface SyncWatchStatus {
  enabled: boolean;
  paused?: boolean;
  source: string;
  source_key?: string;
  running: boolean;
  syncing?: boolean;
  progress?: SyncWatchProgress | null;
  last_scan_ns: number;
  last_result: Record<string, unknown>;
  last_error: string;
}

/** 一轮同步的摘要（watcher.sync_now 返回值）。 */
export interface SyncCycleSummary {
  scanned: number;
  new: number;
  ingested: number;
  deleted?: number;
  failed?: number;
  no_speech?: number;
  paused?: boolean;
  error?: string;
}

/** POST /sync 返回（触发语义：后台执行立即返回，进度经 /sync/status 轮询）。 */
export interface SyncTriggerResult {
  started: boolean;
  completed: boolean;
  error?: string;
  reason?: string;
  hint?: string;
  result?: SyncCycleSummary;
  status?: SyncWatchStatus;
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

export interface SourceStatus {
  source: string;
  configured: boolean;
  reachable: boolean;
  latency_ms: number;
  error: string;
}

export interface SourceListItem {
  key: string;
  display_name: string;
  priority: number;
  configured: boolean;
  active: boolean;
}

export interface AudiosyncConfigItem {
  key: string;
  description: string;
  value_type: string;
  default_value: unknown;
  current_value: unknown;
}

export const audiosyncApi = {
  config: () => api.get<{ items: AudiosyncConfigItem[] }>("/entity/audiosync/config"),
  updateConfig: (updates: Record<string, unknown>) =>
    api.put<{ updated: number }>("/entity/audiosync/config", { updates }),
  syncNow: () => api.post<SyncTriggerResult>("/entity/audiosync/sync"),
  syncStatus: () => api.get<SyncWatchStatus>("/entity/audiosync/sync/status"),
  syncPreview: () => api.get<SyncPreview>("/entity/audiosync/sync/preview"),
  rebuildRecordings: (paths: string[]) =>
    api.post<{ error: string; results: Array<{ path: string; outcome: string; detail: string }> }>(
      "/entity/audiosync/sync/rebuild", { paths }),
  sourceList: () => api.get<{ sources: SourceListItem[] }>("/entity/audiosync/source/list"),
  sourceStatus: () => api.get<SourceStatus>("/entity/audiosync/source/status"),
};
