// ======================================================================
// 数据库管理（数据管理页 · 数据库 Tab）
// ======================================================================

export interface DbInfo {
  id: string;
  name: string;
  description: string;
  path: string;
  exists: boolean;
  size_bytes: number;
  table_count: number;
  external?: boolean;
  engine?: string;
  error?: string;
}

export interface DbTableInfo {
  name: string;
  type: string;
  virtual: boolean;
  shadow: boolean;
  readonly: boolean;
  row_count: number;
  column_count: number;
}

export interface DbColumnInfo {
  cid: number;
  name: string;
  type: string;
  notnull: boolean;
  default: string | null;
  pk: boolean;
}

/** 后端智能序列化的单元格值：原始标量或带 __type__ 的结构 */
export type CellValue =
  | null
  | string
  | number
  | {
      __type__: "blob" | "vec" | "ts" | "json" | "text";
      bytes?: number;
      dims?: number;
      preview?: number[];
      value?: unknown;
      raw?: string;
      text?: string;
      truncated?: boolean;
    };

export interface DbRow {
  __rowid__: number;
  values: Record<string, CellValue>;
}

export interface DbRowsResult {
  items: DbRow[];
  columns: DbColumnInfo[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  readonly: boolean;
}

export interface DbIndexInfo {
  name: string;
  unique: boolean;
  columns: string[];
}

export interface DbSchemaResult {
  table: string;
  type: string;
  readonly: boolean;
  ddl: string;
  columns: DbColumnInfo[];
  indexes: DbIndexInfo[];
}

export interface DbQueryResult {
  columns: string[];
  rows: Record<string, CellValue>[];
  row_count: number;
  elapsed_ms: number;
  truncated: boolean;
}

export interface DbSuggestion {
  id: string;
  level: "info" | "warn";
  action: string;
  detail: Record<string, unknown>;
}

export interface DbHealth {
  id: string;
  size_bytes: number;
  wal_bytes: number;
  page_count: number;
  freelist_count: number;
  fragmentation: number;
  top_tables: { name: string; row_count: number }[];
  suggestions: DbSuggestion[];
}

export interface DbOptimizeResult {
  actions: { action: string; elapsed_ms: number; detail?: Record<string, unknown> }[];
}

// ======================================================================
// 外部数据库连接（数据管理页 · 只读数据源）
// ======================================================================

export interface DbConnection {
  id: string;
  name: string;
  engine: "postgresql" | "mysql";
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  has_password: boolean;
  created_at: number;
}

export interface DbConnectionPayload {
  name: string;
  engine: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
}

export interface DbConnectionTestResult {
  ok: boolean;
  version?: string;
  error?: string;
  latency_ms?: number;
}

/** 行写入值（insert/update row 的 values payload；可编辑列不允许 blob/vec 结构） */
export type DbRowInput = Record<string, string | number | null>;

// ======================================================================
// 数据位置与迁移（数据管理页 · 存储位置 Tab）
// ======================================================================

export interface DbLocationEntry {
  name: string;
  kind: "dir" | "file";
  size_bytes: number;
}

export interface DbLocationInfo {
  path: string;
  source: "env" | "config" | "default";
  env_override: string;
  exists: boolean;
  total_bytes: number;
  entries: DbLocationEntry[];
}

export interface DbTargetCheck {
  target: string;
  ok: boolean;
  problems: string[];
  warnings: string[];
  required_bytes: number;
}

export interface DbMigrationStatus {
  state: "idle" | "running" | "done" | "error";
  target: string;
  current_file: string;
  done: number;
  total: number;
  started_at: number;
  finished_at: number;
  error: string;
}

// ======================================================================
// 存储卷（数据管理页 · 存储卷 Tab：模块化备份/恢复/迁移/SQL 导出）
// ======================================================================

export type VolumeKind = "sqlite" | "cognee_tree" | "notes_tree";

export interface VolumeInfo {
  volume_id: string;
  name: string;
  description: string;
  kind: VolumeKind;
  capabilities: string[];
  path: string;
  default_path: string;
  location_source: "env" | "assignment" | "default";
  assignment: string | null;
  active_path: string | null;
  needs_restart: boolean;
  pending_restore: boolean;
  exists: boolean;
  size_bytes: number;
  backup_count: number;
  last_backup_at: number;
}

export interface VolumeBackupInfo {
  backup_id: string;
  created_at: number;
  kind: string;
  artifact: string;
  size_bytes: number;
  table_count: number;
  file_count: number;
  consistency: string;
}

export interface VolumeOperationState {
  op: string | null;
  state: "idle" | "running" | "done" | "error";
  phase?: string;
  done?: number;
  total?: number;
  error?: string;
  result?: { needs_restart?: boolean; [key: string]: unknown };
  started_at?: number;
  finished_at?: number;
}
