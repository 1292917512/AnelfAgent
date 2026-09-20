/** 数据库域 API — 库浏览 / 外部连接 / 存储卷。 */

import { api } from "./client";
import type {
  DbConnection,
  DbConnectionPayload,
  DbConnectionTestResult,
  DbHealth,
  DbInfo,
  DbLocationInfo,
  DbMigrationStatus,
  DbOptimizeResult,
  DbQueryResult,
  DbRow,
  DbRowInput,
  DbRowsResult,
  DbSchemaResult,
  DbTableInfo,
  DbTargetCheck,
  VolumeBackupInfo,
  VolumeInfo,
  VolumeOperationState,
} from "@/lib/types";

export const databaseApi = {
  databases: () => api.get<{ items: DbInfo[] }>("/database/databases"),
  tables: (db: string, includeShadow = false) =>
    api.get<{ items: DbTableInfo[] }>(`/database/${encodeURIComponent(db)}/tables`, {
      params: { include_shadow: includeShadow },
    }),
  schema: (db: string, table: string) =>
    api.get<DbSchemaResult>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/schema`,
    ),
  rows: (
    db: string,
    table: string,
    params: {
      page?: number;
      page_size?: number;
      sort?: string;
      order?: "asc" | "desc";
      filter_col?: string;
      filter_text?: string;
    },
  ) =>
    api.get<DbRowsResult>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/rows`,
      { params },
    ),
  row: (db: string, table: string, rowid: number) =>
    api.get<DbRow>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/rows/${rowid}`,
    ),
  insertRow: (db: string, table: string, values: DbRowInput) =>
    api.post<{ rowid: number }>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/rows`,
      { values },
    ),
  updateRow: (db: string, table: string, rowid: number, values: DbRowInput) =>
    api.put<{ success: boolean }>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/rows/${rowid}`,
      { values },
    ),
  deleteRow: (db: string, table: string, rowid: number) =>
    api.delete<{ success: boolean }>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/rows/${rowid}`,
    ),
  query: (db: string, sql: string) =>
    api.post<DbQueryResult>(`/database/${encodeURIComponent(db)}/query`, { sql }),
  health: (db: string) => api.get<DbHealth>(`/database/${encodeURIComponent(db)}/health`),
  backup: (db: string) =>
    api.post<Blob>(`/database/${encodeURIComponent(db)}/backup`, null, {
      responseType: "blob",
      timeout: 300000,
    }),
  optimize: (db: string, actions: string[]) =>
    api.post<DbOptimizeResult>(`/database/${encodeURIComponent(db)}/optimize`, { actions }),
};

// DbConnection（数据管理页 · 外部只读数据源）

export const connectionApi = {
  list: () => api.get<{ items: DbConnection[] }>("/database/connections"),
  create: (data: DbConnectionPayload) => api.post<DbConnection>("/database/connections", data),
  update: (id: string, data: DbConnectionPayload) =>
    api.put<DbConnection>(`/database/connections/${encodeURIComponent(id)}`, data),
  remove: (id: string) =>
    api.delete<{ success: boolean }>(`/database/connections/${encodeURIComponent(id)}`),
  test: (data: DbConnectionPayload & { id?: string }) =>
    api.post<DbConnectionTestResult>("/database/connections/test", data),
};

// Storage（数据管理页 · 存储位置与迁移）

export const storageApi = {
  location: () => api.get<DbLocationInfo>("/database/location"),
  checkTarget: (target: string) =>
    api.post<DbTargetCheck>("/database/location/check", { target }),
  migrate: (target: string) => api.post<DbMigrationStatus>("/database/migrate", { target }),
  migrationStatus: () => api.get<DbMigrationStatus>("/database/migrate/status"),
};

// Volumes（数据管理页 · 存储卷：模块化备份/恢复/迁移/SQL 导出）

export const volumeApi = {
  list: () =>
    api.get<{ items: VolumeInfo[]; pending_restore: string | null }>("/database/volumes"),
  operation: (volumeId: string) =>
    api.get<VolumeOperationState>(
      `/database/volumes/${encodeURIComponent(volumeId)}/operation`,
    ),
  backups: (volumeId: string) =>
    api.get<{ items: VolumeBackupInfo[] }>(
      `/database/volumes/${encodeURIComponent(volumeId)}/backups`,
    ),
  backup: (volumeId: string) =>
    api.post<VolumeOperationState>(
      `/database/volumes/${encodeURIComponent(volumeId)}/backup`,
      null,
      { timeout: 300000 },
    ),
  restore: (volumeId: string, backupId: string) =>
    api.post<VolumeOperationState>(
      `/database/volumes/${encodeURIComponent(volumeId)}/backups/${encodeURIComponent(backupId)}/restore`,
      null,
    ),
  downloadBackup: (volumeId: string, backupId: string) =>
    api.get<Blob>(
      `/database/volumes/${encodeURIComponent(volumeId)}/backups/${encodeURIComponent(backupId)}/download`,
      { responseType: "blob", timeout: 300000 },
    ),
  deleteBackup: (volumeId: string, backupId: string) =>
    api.delete<{ success: boolean }>(
      `/database/volumes/${encodeURIComponent(volumeId)}/backups/${encodeURIComponent(backupId)}`,
    ),
  checkRelocate: (volumeId: string, target: string) =>
    api.post<DbTargetCheck>(
      `/database/volumes/${encodeURIComponent(volumeId)}/relocate/check`,
      { target },
    ),
  relocate: (volumeId: string, target: string) =>
    api.post<VolumeOperationState>(
      `/database/volumes/${encodeURIComponent(volumeId)}/relocate`,
      { target },
    ),
  exportSql: (
    volumeId: string,
    data: { connection_id: string; table_prefix?: string; drop_existing?: boolean },
  ) =>
    api.post<VolumeOperationState>(
      `/database/volumes/${encodeURIComponent(volumeId)}/export`,
      data,
    ),
  importSql: (volumeId: string, connectionId: string) =>
    api.post<VolumeOperationState>(
      `/database/volumes/${encodeURIComponent(volumeId)}/import`,
      { connection_id: connectionId },
    ),
};


// ── 关系图谱（/memory/graph，权威存储在记忆库 graph_nodes/graph_edges） ──
