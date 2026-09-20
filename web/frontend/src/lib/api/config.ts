/** 配置域 API — 全局配置 / 配置元数据 / 心跳 / 任务 / 人设。 */

import { api } from "./client";
import type {
  ConfigMetaGroup,
  ConfigValues,
  HeartbeatConfig,
  HeartbeatStatus,
  PersonaItem,
  PersonaData,
  TaskConfig,
  TaskExecutionRecord,
} from "@/lib/types";

export const personasApi = {
  list: () => api.get<PersonaItem[]>("/personas/"),
  active: () => api.get("/personas/active"),
  get: (key: string) => api.get(`/personas/${encodeURIComponent(key)}`),
  save: (key: string, data: Partial<PersonaData>) =>
    api.put(`/personas/${encodeURIComponent(key)}`, data),
  create: (key: string) => api.post("/personas/", { key }),
  remove: (key: string) => api.delete(`/personas/${encodeURIComponent(key)}`),
  activate: (key: string) => api.put(`/personas/${encodeURIComponent(key)}/activate`),
};

// Memory

export const configApi = {
  webui: () => api.get("/config/webui"),
  navigation: () => api.get("/config/webui/navigation"),
  theme: () => api.get("/config/webui/theme"),
  snapshot: () => api.get<ConfigValues>("/config/snapshot"),
  getApp: () => api.get<ConfigValues>("/config/app"),
  getMind: () => api.get("/config/mind"),
  saveMind: (data: ConfigValues) => api.put("/config/mind", data),
};

// Heartbeat

export const heartbeatApi = {
  getConfig: () => api.get<HeartbeatConfig>("/config/heartbeat"),
  saveConfig: (data: Partial<HeartbeatConfig>) => api.put("/config/heartbeat", data),
  getStatus: () => api.get<HeartbeatStatus>("/config/heartbeat/status"),
  trigger: () => api.post<{ status: string }>("/config/heartbeat/trigger"),
};

// Task Units CRUD + trigger

export const tasksApi = {
  list: () => api.get<TaskConfig[]>("/config/tasks"),
  get: (name: string, folder = "") =>
    api.get<TaskConfig>(`/config/tasks/${encodeURIComponent(name)}`, { params: { folder: folder || undefined } }),
  create: (data: TaskConfig) => api.post<TaskConfig>("/config/tasks", data),
  update: (name: string, data: Partial<TaskConfig>, folder = "") =>
    api.put<TaskConfig>(`/config/tasks/${encodeURIComponent(name)}`, data, { params: { folder: folder || undefined } }),
  delete: (name: string, folder = "") =>
    api.delete(`/config/tasks/${encodeURIComponent(name)}`, { params: { folder: folder || undefined } }),
  trigger: (name: string, folder = "") =>
    api.post<{ status: string; task: string }>(`/config/tasks/trigger/${encodeURIComponent(name)}`, null, { params: { folder: folder || undefined } }),
  history: (name: string) =>
    api.get<TaskExecutionRecord[]>(`/config/tasks/${encodeURIComponent(name)}/history`),
};

// Workspace 文件浏览/编辑（root: workspace 工作区 / project 项目根，规则一致仅基准不同）

export const configMetaApi = {
  list: () => api.get<{ groups: ConfigMetaGroup[] }>("/config/meta"),
  save: (key: string, value: unknown) =>
    api.put(`/config/meta/${encodeURIComponent(key)}`, { value }),
};
