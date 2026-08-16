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

export interface DevopsCrashIps {
  process: string;
  capture_time: string;
  exception_type: string;
  signal: string;
  codes: string;
  faulting_module: string;
  stack: string[];
  report_path?: string;
}

export interface DevopsCrashState {
  exit_code: number;
  signal?: string;
  crashed_at: string;
  crash_count?: number;
  reported?: boolean;
  ips?: DevopsCrashIps | null;
}

export interface DevopsCrashInfo {
  ok: boolean;
  has_crash: boolean;
  crash?: DevopsCrashState;
  summary?: string;
}
