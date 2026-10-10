/** devops 实体类型（/api/entity/devops 运维管理路由）。 */

export interface DevopsBuildResult {
  ok: boolean;
  duration: number;
  finished_at: string;
  log_tail: string;
}

export interface DevopsBuildState {
  building: boolean;
  last: DevopsBuildResult | null;
  runtime_id: string;
  operation_id: string | null;
  restarting: boolean;
  phase: "idle" | "pulling" | "building" | "restarting" | "failed" | "updated";
  result: DevopsActionResult | null;
}

export interface DevopsActionResult {
  ok: boolean;
  error?: string;
  message?: string;
  detail?: string;
  building?: boolean;
  restarting?: boolean;
  conflict?: boolean;
  pull_result?: string;
  dirty_files?: string;
  runtime_id?: string;
  operation_id?: string;
  build?: DevopsBuildResult;
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
