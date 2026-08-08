// ── 运维管理（devops 实体路由 /api/entity/devops） ──

export interface DevopsBuildResult {
  ok: boolean;
  duration: number;
  finished_at: string;
  log_tail: string;
}

export interface DevopsBuildState {
  building: boolean;
  last: DevopsBuildResult | null;
}

export interface DevopsActionResult {
  ok: boolean;
  error?: string;
  message?: string;
  building?: boolean;
  restarting?: boolean;
  conflict?: boolean;
  pull_result?: string;
  dirty_files?: string;
}
