import axios from "axios";
import type { GoalStep, PersonaData, ProviderConfig } from "./types";
export type { GoalStep } from "./types";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    console.error("[API Error]", err.response?.data || err.message);
    return Promise.reject(err);
  },
);

export default api;

// ── 类型化 API 方法 ─────────────────────────────────────────────

// Chat
export const chatApi = {
  send: (message: string, userId = "web_user", userName = "用户", files?: string[]) =>
    api.post("/chat/send", {
      message,
      user_id: userId,
      user_name: userName,
      ...(files?.length ? { files } : {}),
    }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post("/chat/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  history: (scopeId = "web_user", limit = 50) =>
    api.get(`/chat/history`, { params: { scope_id: scopeId, limit } }),
  botName: () => api.get<{ name: string }>("/chat/bot-name"),
};

// Status
export const statusApi = {
  get: () => api.get("/status/"),
  components: () => api.get("/status/components"),
  events: () => api.get("/status/events"),
  pfc: () => api.get("/status/pfc"),
  mindConfig: () => api.get("/status/mind-config"),
  logs: (level?: string, tag?: string, keyword?: string, limit = 50) =>
    api.get("/status/logs", { params: { level: level || undefined, tag: tag || undefined, keyword: keyword || undefined, limit } }),
  logStats: () => api.get("/status/log-stats"),
  saveMindConfig: (data: Record<string, unknown>) => api.put("/status/mind-config", data),
};

// Providers
export const providersApi = {
  list: () => api.get("/models/providers"),
  create: (data: Partial<ProviderConfig>) => api.post("/models/providers", data),
  update: (pid: string, data: Partial<ProviderConfig>) =>
    api.put(`/models/providers/${encodeURIComponent(pid)}`, data),
  remove: (pid: string) => api.delete(`/models/providers/${encodeURIComponent(pid)}`),
  models: (pid: string) => api.get(`/models/providers/${encodeURIComponent(pid)}/models`),
  createModel: (pid: string, data: Record<string, unknown>) =>
    api.post(`/models/providers/${encodeURIComponent(pid)}/models`, data),
};

// Models
export const modelsApi = {
  get: (id: string) => api.get(`/models/${encodeURIComponent(id)}`),
  update: (id: string, data: Record<string, unknown>) =>
    api.put(`/models/${encodeURIComponent(id)}`, data),
  remove: (id: string) => api.delete(`/models/${encodeURIComponent(id)}`),
  rename: (id: string, newId: string) =>
    api.put(`/models/${encodeURIComponent(id)}/rename`, { new_id: newId }),
  setDefault: (modelId: string) => api.put("/models/config/default", { model_id: modelId }),
  priorities: () => api.get("/models/priorities"),
  setPriority: (modelType: string, modelIds: string[]) =>
    api.put(`/models/priorities/${encodeURIComponent(modelType)}`, { model_ids: modelIds }),
  movePriority: (modelId: string, modelType: string, direction: number) =>
    api.put(`/models/${encodeURIComponent(modelId)}/priority-move/${encodeURIComponent(modelType)}`, { direction }),
  test: (baseUrl: string, apiKey: string) =>
    api.post("/models/test", { base_url: baseUrl, api_key: apiKey }),
  probe: (baseUrl: string, apiKey: string, model: string, apiType = "openai") =>
    api.post("/models/probe", { base_url: baseUrl, api_key: apiKey, model, api_type: apiType }),
  costMapInfo: () => api.get<{ model_count: number }>("/models/cost-map/info"),
  updateCostMap: (proxyUrl = "") =>
    api.post<{ status: string; model_count: number }>("/models/cost-map/update", { proxy_url: proxyUrl }),
};

// Tools
export const toolsApi = {
  list: () => api.get("/tools/"),
  grouped: () => api.get("/tools/grouped"),
  toggle: (name: string) => api.put(`/tools/${encodeURIComponent(name)}/toggle`),
  toggleGroup: (group: string) => api.put(`/tools/group/${encodeURIComponent(group)}/toggle`),
  updateMeta: (name: string, data: { tags?: string[]; description?: string }) =>
    api.put(`/tools/${encodeURIComponent(name)}/meta`, data),
  reload: () => api.post("/tools/reload"),
  plugins: () => api.get("/tools/plugins"),
};

// Personas
export const personasApi = {
  list: () => api.get("/personas/"),
  active: () => api.get("/personas/active"),
  get: (key: string) => api.get(`/personas/${encodeURIComponent(key)}`),
  save: (key: string, data: Partial<PersonaData>) =>
    api.put(`/personas/${encodeURIComponent(key)}`, data),
  create: (key: string) => api.post("/personas/", { key }),
  remove: (key: string) => api.delete(`/personas/${encodeURIComponent(key)}`),
  activate: (key: string) => api.put(`/personas/${encodeURIComponent(key)}/activate`),
};

// Memory
export const memoryApi = {
  health: () => api.get("/memory/health"),
  stm: {
    list: () => api.get("/memory/stm"),
    delete: (index: number) => api.delete(`/memory/stm/${index}`),
    clear: () => api.delete("/memory/stm"),
    status: () => api.get("/memory/stm/status"),
  },
  ltm: {
    list: (memoryType?: string, limit = 200) =>
      api.get("/memory/ltm", { params: { memory_type: memoryType, limit } }),
    get: (id: number) => api.get(`/memory/ltm/${id}`),
    create: (content: string, memoryType = "semantic", importance = 0.5, tags?: string[]) =>
      api.post("/memory/ltm", { content, memory_type: memoryType, importance, tags }),
    update: (id: number, content: string, importance = 0.5, tags?: string[]) =>
      api.put(`/memory/ltm/${id}`, { content, importance, tags }),
    delete: (id: number) => api.delete(`/memory/ltm/${id}`),
    clear: (memoryType?: string) => api.delete("/memory/ltm", { params: { memory_type: memoryType } }),
    stats: () => api.get("/memory/ltm/stats"),
    search: (query: string, tags?: string, limit = 20) =>
      api.get("/memory/ltm/search", { params: { query, tags, limit } }),
    paginated: (page = 1, pageSize = 50, memoryType?: string) =>
      api.get("/memory/ltm/paginated", { params: { page, page_size: pageSize, memory_type: memoryType } }),
    merge: (ids: number[], content: string) =>
      api.post("/memory/ltm/merge", { ids, content }),
  },
  conv: {
    scopes: () => api.get("/memory/conversations/scopes"),
    messages: (scopeType: string, scopeId: string, limit = 200) =>
      api.get("/memory/conversations", { params: { scope_type: scopeType, scope_id: scopeId, limit } }),
    delete: (rowId: number) => api.delete(`/memory/conversations/${rowId}`),
    clear: (scopeType: string, scopeId: string) =>
      api.post("/memory/conversations/clear", { scope_type: scopeType, scope_id: scopeId }),
  },
  entities: {
    list: () => api.get("/memory/entities"),
    save: (scopeType: string, scopeId: string, personality: string) =>
      api.put("/memory/entities", { scope_type: scopeType, scope_id: scopeId, personality }),
    delete: (scopeType: string, scopeId: string) =>
      api.post("/memory/entities/delete", { scope_type: scopeType, scope_id: scopeId }),
    aliases: () => api.get("/memory/entities/aliases"),
    link: (srcType: string, srcId: string, tgtType: string, tgtId: string) =>
      api.post("/memory/entities/link", {
        source_scope_type: srcType, source_scope_id: srcId,
        target_scope_type: tgtType, target_scope_id: tgtId,
      }),
    unlink: (scopeType: string, scopeId: string) =>
      api.post("/memory/entities/unlink", { scope_type: scopeType, scope_id: scopeId }),
  },
  notes: {
    read: () => api.get("/memory/notes"),
    write: (content: string) => api.put("/memory/notes", { content }),
  },
  files: {
    list: () => api.get("/memory/files"),
    read: (path: string) => api.get("/memory/files/content", { params: { path } }),
    write: (path: string, content: string) => api.put("/memory/files/content", { path, content }),
  },
  index: {
    status: () => api.get("/memory/index/status"),
    resync: (force = false) => api.post("/memory/index/resync", null, { params: { force } }),
    cleanCache: () => api.post("/memory/index/clean-cache"),
  },
  goals: {
    list: (status = "all") => api.get("/memory/goals", { params: { status } }),
    get: (goalId: string) => api.get(`/memory/goals/${encodeURIComponent(goalId)}`),
    create: (title: string, description = "", steps?: string[], due_time?: string, recurring?: boolean) =>
      api.post("/memory/goals", { title, description, steps, ...(due_time ? { due_time } : {}), ...(recurring ? { recurring } : {}) }),
    update: (goalId: string, data: { title?: string; description?: string; status?: string; steps?: GoalStep[]; due_time?: string | null; recurring?: boolean }) =>
      api.put(`/memory/goals/${encodeURIComponent(goalId)}`, data),
    delete: (goalId: string) => api.delete(`/memory/goals/${encodeURIComponent(goalId)}`),
  },
};

// MCP
export const mcpApi = {
  list: () => api.get("/mcp/"),
  config: () => api.get("/mcp/config"),
  saveConfig: (content: string) => api.put("/mcp/config", { content }),
  add: (name: string, url: string) => api.post("/mcp/", { name, url }),
  remove: (name: string) => api.delete(`/mcp/${encodeURIComponent(name)}`),
  toggle: (name: string) =>
    api.put(`/mcp/${encodeURIComponent(name)}/toggle`, null, { timeout: 65000 }),
  tools: (name: string) => api.get(`/mcp/${encodeURIComponent(name)}/tools`),
};

// Adapters
export const adaptersApi = {
  list: () => api.get("/adapters/"),
  toggle: (key: string) => api.put(`/adapters/${encodeURIComponent(key)}/toggle`),
  configs: () => api.get("/adapters/configs"),
  saveConfigs: (values: Record<string, unknown>) => api.put("/adapters/configs", values),
};

// NoneBot Bridge
export const nonebotApi = {
  status: () => api.get("/nonebot/status"),
  adapters: () => api.get("/nonebot/adapters"),
  bots: () => api.get("/nonebot/bots"),
  config: () => api.get("/nonebot/config"),
  saveConfig: (config: Record<string, unknown>) => api.put("/nonebot/config", config),
};

// Thinking Tracer
export const thinkingApi = {
  status: () => api.get("/thinking/status"),
  toggle: (enabled: boolean) => api.put("/thinking/toggle", { enabled }),
  sessions: (limit = 20) => api.get("/thinking/sessions", { params: { limit } }),
  session: (id: string) => api.get(`/thinking/sessions/${encodeURIComponent(id)}`),
};

// Config
export const configApi = {
  webui: () => api.get("/config/webui"),
  navigation: () => api.get("/config/webui/navigation"),
  theme: () => api.get("/config/webui/theme"),
  snapshot: () => api.get("/config/snapshot"),
  getApp: () => api.get<Record<string, unknown>>("/config/app"),
  saveApp: (data: Record<string, unknown>) => api.put("/config/app", data),
  getMind: () => api.get("/config/mind"),
  saveMind: (data: Record<string, unknown>) => api.put("/config/mind", data),
};

// Heartbeat
export interface HeartbeatConfig {
  enabled: boolean;
  interval_seconds: number;
  analysis_temperature: number;
  min_conversations_for_analysis: number;
  task_schedules: TaskSchedule[];
}

export interface TaskSchedule {
  task_name: string;
  mode: "heartbeat" | "scheduled" | "manual";
  every_n_beats?: number;
  beat_count?: number;
  schedule_times?: string[];
  last_run_date?: string;
}

export interface HeartbeatStatus {
  enabled: boolean;
  interval_seconds: number;
  total_ticks: number;
  task_count: number;
  schedule_count: number;
  schedules: (TaskSchedule & { task_exists: boolean; task_enabled: boolean })[];
}

export const heartbeatApi = {
  getConfig: () => api.get<HeartbeatConfig>("/config/heartbeat"),
  saveConfig: (data: Partial<HeartbeatConfig>) => api.put("/config/heartbeat", data),
  getStatus: () => api.get<HeartbeatStatus>("/config/heartbeat/status"),
  trigger: () => api.post<{ status: string }>("/config/heartbeat/trigger"),
};

// Task Units CRUD + trigger
export interface TaskConfig {
  name: string;
  display_name: string;
  description: string;
  scope: string;
  enabled: boolean;
  memory_type: string;
  importance: number;
  tags: string[];
  source: string;
  null_keywords: string[];
  tool_tags: string[];
  prompt: string;
}

export const tasksApi = {
  list: () => api.get<TaskConfig[]>("/config/tasks"),
  get: (name: string) => api.get<TaskConfig>(`/config/tasks/${encodeURIComponent(name)}`),
  create: (data: TaskConfig) => api.post<TaskConfig>("/config/tasks", data),
  update: (name: string, data: Partial<TaskConfig>) =>
    api.put<TaskConfig>(`/config/tasks/${encodeURIComponent(name)}`, data),
  delete: (name: string) => api.delete(`/config/tasks/${encodeURIComponent(name)}`),
  trigger: (name: string) => api.post<{ status: string; task: string }>(`/config/tasks/trigger/${encodeURIComponent(name)}`),
};

// Tags
export const tagsApi = {
  unified: () => api.get("/tags/unified"),
  toolTags: () => api.get<string[]>("/tags/tool"),
  createMessageTag: (name: string, description: string) =>
    api.post("/tags/message", { name, description }),
  deleteMessageTag: (name: string) =>
    api.delete(`/tags/message/${encodeURIComponent(name)}`),
};

// System
export const systemApi = {
  info: () => api.get("/system/info"),
  python: () => api.get("/system/python"),
  pythonPackages: () => api.get("/system/python/packages"),
  pipMirror: () => api.get("/system/python/pip-mirror"),
  setPipMirror: (mirrorName: string) => api.post("/system/python/pip-mirror", { mirror_name: mirrorName }),
  git: () => api.get("/system/git"),
  setGit: (key: string, value: string) => api.put("/system/git", { key, value }),
  testGithub: () => api.post("/system/git/test"),
};
