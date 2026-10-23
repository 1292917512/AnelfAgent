/** 系统状态域 API — 运行状态 / 系统信息 / 界面控制。 */

import { api } from "./client";
import type {
  AgentStatusResponse,
  ConfigValues,
  LifecycleService,
  LogEntry,
  LogStats,
  PythonPackage,
  StartupNode,
  ThinkingSnapshot,
  RuntimeComponents,
  UiStateReport,
} from "@/lib/types";

export const statusApi = {
  get: (signal?: AbortSignal) => api.get<AgentStatusResponse>("/status/", { signal }),
  components: () => api.get<{ structured: RuntimeComponents; lines: string[] }>("/status/components"),
  events: () => api.get<{ stats: Record<string, number> }>("/status/events"),
  thinking: (signal?: AbortSignal) => api.get<ThinkingSnapshot>("/status/thinking", { signal }),
  logs: (level?: string, tag?: string, keyword?: string, limit = 50) =>
    api.get<{ logs: LogEntry[]; count: number }>("/status/logs", { params: { level: level || undefined, tag: tag || undefined, keyword: keyword || undefined, limit } }),
  logStats: () => api.get<LogStats>("/status/log-stats"),
  services: () => api.get<{ services: LifecycleService[] }>("/status/services"),
  startup: () => api.get<{ timeline: StartupNode[] }>("/status/startup"),
  clearLogs: () => api.post<{ status: string; cleared: number }>("/status/logs/clear"),
};

// Providers

export const uiApi = {
  answer: (askId: string, answer: string) =>
    api.post<{ status: "ok" | "expired" }>("/chat/ui-answer", { ask_id: askId, answer }, { errorFeedback: "local" }),
  reportState: (state: UiStateReport) =>
    api.post("/chat/ui-state", { state }, { errorFeedback: "local" }),
};

// Tags

export const systemApi = {
  info: () => api.get("/system/info"),
  python: () => api.get<ConfigValues>("/system/python"),
  pythonPackages: () => api.get<PythonPackage[]>("/system/python/packages"),
  pipMirror: () => api.get<ConfigValues>("/system/python/pip-mirror"),
  setPipMirror: (mirrorName: string) => api.post("/system/python/pip-mirror", { mirror_name: mirrorName }),
  git: () => api.get("/system/git"),
  setGit: (key: string, value: string) => api.put("/system/git", { key, value }),
  testGithub: () => api.post("/system/git/test"),
};


// Skills
