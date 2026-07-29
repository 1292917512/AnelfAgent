// ── 服务重启（system 路由） ──

export interface BuildResult {
  ok: boolean;
  duration: number;
  finished_at: string;
  log_tail: string;
}

export interface RestartBuildState {
  building: boolean;
  last: BuildResult | null;
}

/** 已安装 Python 包（GET /system/python/packages） */
export interface PythonPackage {
  name: string;
  version: string;
}
