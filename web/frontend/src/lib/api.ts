import axios from "axios";
import i18n from "@/i18n";
import type {
  AdapterListResult,
  ApiKeyCreated,
  ApiKeyInfo,
  ApprovalHistoryResponse,
  ApprovalPendingResponse,
  ApprovalPoliciesResponse,
  ApprovalRulesResponse,
  ApprovalStats,
  AuthStatus,
  ChannelTestHealthResult,
  ChannelTestSendResult,
  ChannelToolTestResult,
  ChannelToolToggleResult,
  ChannelToolsResult,
  CogneeConfig,
  CogneeDataset,
  CogneeStatus,
  ConfigMetaGroup,
  CreateModelConfig,
  CreateProviderConfig,
  DbInfo,
  DbQueryResult,
  DbRow,
  DbRowsResult,
  DbSchemaResult,
  DbTableInfo,
  GlobalSearchResult,
  GoalStep,
  HeartbeatConfig,
  HeartbeatStatus,
  LogEntry,
  LogStats,
  MCPServer,
  MCPServerConfig,
  MCPToggleResult,
  MCPToolInfo,
  MemoryFileInfo,
  MemoryDocument,
  ModelConfig,
  ModelInfoResult,
  ModelPriorityItem,
  PermissionRuleItem,
  PersonaData,
  ProbeResult,
  ProviderConfig,
  RemoteModelInfo,
  SkillItem,
  StickerItem,
  StickerListResult,
  StickerStats,
  IndexedImageListResult,
  TaskConfig,
  UpdateModelConfig,
  UpdateProviderConfig,
  WebToolsConfig,
  WeixinQrStartResult,
  WeixinQrStatusResult,
  WorkspaceFile,
  WorkspaceNode,
  WorkspaceSearchHit,
} from "./types";

export type {
  ApiKeyCreated,
  ApiKeyInfo,
  ApprovalHistoryItem,
  ApprovalHistoryResponse,
  ApprovalPendingItem,
  ApprovalPendingResponse,
  ApprovalPoliciesResponse,
  ApprovalPolicyItem,
  ApprovalRulesResponse,
  ApprovalStats,
  AuthStatus,
  ConfigMetaGroup,
  ConfigMetaItem,
  GlobalSearchResult,
  GoalStep,
  HeartbeatConfig,
  HeartbeatStatus,
  ModelInfoResult,
  PermissionRuleItem,
  ProbeResult,
  ReasoningEffort,
  RemoteModelInfo,
  SkillItem,
  StickerItem,
  StickerListResult,
  StickerStats,
  IndexedImageListResult,
  TaskConfig,
  TaskSchedule,
  WebToolsConfig,
  WeixinQrStartResult,
  WeixinQrStatusResult,
  WorkspaceFile,
  WorkspaceNode,
  WorkspaceSearchHit,
} from "./types";

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

// ── Auth ────────────────────────────────────────────────────────

export const authApi = {
  check: () => api.get<AuthStatus>("/auth/check"),
  login: (password: string) => api.post("/auth/login", { password }),
  logout: () => api.post("/auth/logout"),
  updatePassword: (newPassword: string) =>
    api.put("/auth/password", { new_password: newPassword }),
  listApiKeys: () => api.get<{ keys: ApiKeyInfo[] }>("/auth/api-keys"),
  createApiKey: (name = "default") =>
    api.post<ApiKeyCreated>("/auth/api-keys", { name }),
  rotateApiKey: (keyId: string) =>
    api.post<ApiKeyCreated>(`/auth/api-keys/${keyId}/rotate`),
  deleteApiKey: (keyId: string) =>
    api.delete<{ status: string }>(`/auth/api-keys/${keyId}`),
};

// ── 类型化 API 方法 ─────────────────────────────────────────────

// Chat
export const chatApi = {
  send: (message: string, userId = "web_user", userName?: string, files?: string[]) =>
    api.post("/chat/send", {
      message,
      user_id: userId,
      user_name: userName ?? i18n.t("user", { ns: "chat" }),
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
    api.get<{ logs: LogEntry[]; count: number }>("/status/logs", { params: { level: level || undefined, tag: tag || undefined, keyword: keyword || undefined, limit } }),
  logStats: () => api.get<LogStats>("/status/log-stats"),
  clearLogs: () => api.post<{ status: string; cleared: number }>("/status/logs/clear"),
  saveMindConfig: (data: Record<string, unknown>) => api.put("/status/mind-config", data),
};

// Providers
export const providersApi = {
  list: () => api.get<ProviderConfig[]>("/models/providers"),
  create: (data: CreateProviderConfig) => api.post("/models/providers", data),
  update: (pid: string, data: UpdateProviderConfig) =>
    api.put(`/models/providers/${encodeURIComponent(pid)}`, data),
  remove: (pid: string) => api.delete(`/models/providers/${encodeURIComponent(pid)}`),
  models: (pid: string) => api.get<ModelConfig[]>(`/models/providers/${encodeURIComponent(pid)}/models`),
  createModel: (pid: string, data: CreateModelConfig) =>
    api.post(`/models/providers/${encodeURIComponent(pid)}/models`, data),
  remoteModels: (pid: string) =>
    api.get<{ models: RemoteModelInfo[] }>(`/models/providers/${encodeURIComponent(pid)}/remote-models`),
  modelInfo: (model: string, apiType = "openai") =>
    api.post<ModelInfoResult>("/models/model-info", { model, api_type: apiType }),
};

// Models
export const modelsApi = {
  get: (id: string) => api.get<ModelConfig>(`/models/${encodeURIComponent(id)}`),
  update: (id: string, data: UpdateModelConfig) =>
    api.put(`/models/${encodeURIComponent(id)}`, data),
  remove: (id: string) => api.delete(`/models/${encodeURIComponent(id)}`),
  rename: (id: string, newId: string) =>
    api.put(`/models/${encodeURIComponent(id)}/rename`, { new_id: newId }),
  setDefault: (modelId: string) => api.put("/models/config/default", { model_id: modelId }),
  priorities: () => api.get<Record<string, ModelPriorityItem[]>>("/models/priorities"),
  setPriority: (modelType: string, modelIds: string[]) =>
    api.put(`/models/priorities/${encodeURIComponent(modelType)}`, { model_ids: modelIds }),
  movePriority: (modelId: string, modelType: string, direction: number) =>
    api.put(`/models/${encodeURIComponent(modelId)}/priority-move/${encodeURIComponent(modelType)}`, { direction }),
  test: (baseUrl: string, apiKey: string, providerId = "") =>
    api.post("/models/test", { base_url: baseUrl, api_key: apiKey, provider_id: providerId }),
  probe: (baseUrl: string, apiKey: string, model: string, apiType = "openai", providerId = "") =>
    api.post<ProbeResult>("/models/probe", {
      base_url: baseUrl,
      api_key: apiKey,
      model,
      api_type: apiType,
      provider_id: providerId,
    }),
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
  cognee: {
    status: () => api.get<CogneeStatus>("/memory/cognee/status"),
    getConfig: () => api.get<CogneeConfig>("/memory/cognee/config"),
    saveConfig: (data: Partial<CogneeConfig>) => api.put<CogneeConfig>("/memory/cognee/config", data),
    retry: () => api.post("/memory/cognee/retry"),
    backfill: (limit = 0, dryRun = true) =>
      api.post("/memory/cognee/backfill", { limit, dry_run: dryRun }),
    datasets: () => api.get<CogneeDataset[]>("/memory/cognee/datasets"),
    improve: (datasetName: string) =>
      api.post("/memory/cognee/improve", { dataset_name: datasetName }),
  },
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
    read: () => api.get<{ content: string; path: string }>("/memory/notes"),
    write: (content: string) => api.put("/memory/notes", { content }),
  },
  files: {
    list: () => api.get<MemoryFileInfo[]>("/memory/files"),
    read: (path: string) => api.get<{ content: string }>("/memory/files/content", { params: { path } }),
    write: (path: string, content: string) => api.put("/memory/files/content", { path, content }),
    delete: (path: string) => api.delete("/memory/files", { params: { path } }),
  },
  index: {
    status: () => api.get("/memory/index/status"),
    resync: (force = false) => api.post("/memory/index/resync", null, { params: { force } }),
    cleanCache: () => api.post("/memory/index/clean-cache"),
  },
  documents: {
    list: () => api.get<MemoryDocument[]>("/memory/documents"),
    upload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.post<{ ok?: boolean; error?: string; chunks?: number }>("/memory/documents/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    delete: (path: string) => api.delete("/memory/documents", { params: { path } }),
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
  list: () => api.get<MCPServer[]>("/mcp/"),
  config: () => api.get<{ content: string }>("/mcp/config"),
  saveConfig: (content: string) => api.put("/mcp/config", { content }),
  add: (name: string, config: MCPServerConfig) =>
    api.post("/mcp/", { name, ...config }),
  get: (name: string) =>
    api.get<MCPServerConfig>(`/mcp/${encodeURIComponent(name)}`),
  update: (name: string, config: MCPServerConfig) =>
    api.put(`/mcp/${encodeURIComponent(name)}`, config),
  remove: (name: string) => api.delete(`/mcp/${encodeURIComponent(name)}`),
  toggle: (name: string) =>
    api.put<MCPToggleResult>(`/mcp/${encodeURIComponent(name)}/toggle`, null, { timeout: 65000 }),
  tools: (name: string) =>
    api.get<MCPToolInfo[]>(`/mcp/${encodeURIComponent(name)}/tools`),
};

/** 从 axios 错误中提取可读信息（统一用于 toast 反馈） */
export function apiErrorMessage(err: unknown, fallback: string): string {
  const axErr = err as { response?: { data?: { detail?: string } }; message?: string };
  return axErr?.response?.data?.detail || axErr?.message || fallback;
}

// Adapters
export const adaptersApi = {
  list: () => api.get<AdapterListResult>("/adapters/"),
  toggle: (key: string) => api.put(`/adapters/${encodeURIComponent(key)}/toggle`),
  configs: () => api.get("/adapters/configs"),
  saveConfigs: (values: Record<string, unknown>) => api.put("/adapters/configs", values),
  testHealth: (key: string) =>
    api.post<ChannelTestHealthResult>(`/adapters/${encodeURIComponent(key)}/test/health`),
  testSend: (key: string, payload: { chat_id: string; text: string }) =>
    api.post<ChannelTestSendResult>(`/adapters/${encodeURIComponent(key)}/test/send`, payload),
  channelTools: (key: string) =>
    api.get<ChannelToolsResult>(`/adapters/${encodeURIComponent(key)}/tools`),
  toggleChannelTool: (key: string, name: string) =>
    api.put<ChannelToolToggleResult>(
      `/adapters/${encodeURIComponent(key)}/tools/${encodeURIComponent(name)}/toggle`,
    ),
  testChannelTool: (key: string, name: string, args: Record<string, unknown>) =>
    api.post<ChannelToolTestResult>(
      `/adapters/${encodeURIComponent(key)}/tools/${encodeURIComponent(name)}/test`,
      { args },
    ),
};

// Weixin QR Login（微信扫码登录）
export const weixinQrApi = {
  start: () => api.post<WeixinQrStartResult>("/channels/weixin/qr/start"),
  status: (sessionId: string) =>
    api.get<WeixinQrStatusResult>(`/channels/weixin/qr/${encodeURIComponent(sessionId)}/status`),
  discard: (sessionId: string) =>
    api.delete(`/channels/weixin/qr/${encodeURIComponent(sessionId)}`),
};

// NoneBot Bridge
export const nonebotApi = {
  status: () => api.get("/nonebot/status"),
  adapters: () => api.get("/nonebot/adapters"),
  bots: () => api.get("/nonebot/bots"),
  config: () => api.get("/nonebot/config"),
  saveConfig: (config: Record<string, unknown>) => api.put("/nonebot/config", config),
};

// Approvals
export const approvalsApi = {
  pending: () => api.get<ApprovalPendingResponse>("/approvals/pending"),
  history: (limit = 50) => api.get<ApprovalHistoryResponse>("/approvals/history", { params: { limit } }),
  approve: (requestId: string, reason?: string, remember: string = "once") =>
    api.post(`/approvals/${encodeURIComponent(requestId)}/approve`, { reason, remember }),
  deny: (requestId: string, reason?: string) =>
    api.post(`/approvals/${encodeURIComponent(requestId)}/deny`, { reason }),
  stats: () => api.get<ApprovalStats>("/approvals/stats"),
  policies: () => api.get<ApprovalPoliciesResponse>("/approvals/policies"),
  savePolicies: (policies: Record<string, unknown>) =>
    api.put("/approvals/policies", policies),
  // 统一权限规则
  rules: () => api.get<ApprovalRulesResponse>("/approvals/rules"),
  saveRules: (data: { rules: Partial<PermissionRuleItem>[]; default_effect: string }) =>
    api.put("/approvals/rules", data),
  addRule: (rule: Partial<PermissionRuleItem>) => api.post("/approvals/rules", rule),
  deleteRule: (ruleId: string) =>
    api.delete(`/approvals/rules/${encodeURIComponent(ruleId)}`),
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
  getWebTools: () => api.get<WebToolsConfig>("/config/web-tools"),
  saveWebTools: (data: Partial<WebToolsConfig>) => api.put("/config/web-tools", data),
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
};

// Workspace 文件浏览/编辑
export const workspaceApi = {
  tree: (path = "", depth = 2) =>
    api.get<{ path: string; children: WorkspaceNode[]; truncated: boolean }>("/workspace/tree", { params: { path: path || undefined, depth } }),
  read: (path: string) => api.get<WorkspaceFile>("/workspace/file", { params: { path } }),
  write: (path: string, content: string) => api.put("/workspace/file", { path, content }),
  mkdir: (path: string) => api.post("/workspace/mkdir", { path }),
  remove: (path: string) => api.delete("/workspace/file", { params: { path } }),
  search: (q: string, limit = 30) =>
    api.get<{ query: string; files: WorkspaceSearchHit[] }>("/workspace/search", { params: { q, limit } }),
  /** 原始字节服务 URL（图片/音视频预览；inline 供 iframe 内联渲染，如 PDF） */
  rawUrl: (path: string, inline = false) =>
    `/api/workspace/raw?path=${encodeURIComponent(path)}${inline ? "&inline=1" : ""}`,
};

/** 按文件名判断可预览的媒体类型 */
export function workspaceMediaKind(name: string): "image" | "video" | "audio" | null {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico"].includes(ext)) return "image";
  if (["mp4", "webm", "mov", "mkv", "avi", "flv"].includes(ext)) return "video";
  if (["mp3", "wav", "ogg", "flac", "m4a", "opus"].includes(ext)) return "audio";
  return null;
}

/** 可按富格式预览的文件类型（按扩展名分类） */
export type WorkspaceFileKind = "markdown" | "html" | "csv" | "pdf" | "docx" | "xlsx";

/** 按文件名判断富格式预览类型，不命中返回 null */
export function workspaceFileKind(name: string): WorkspaceFileKind | null {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["md", "markdown"].includes(ext)) return "markdown";
  if (["html", "htm"].includes(ext)) return "html";
  if (["csv", "tsv"].includes(ext)) return "csv";
  if (ext === "pdf") return "pdf";
  if (ext === "docx") return "docx";
  if (["xlsx", "xls"].includes(ext)) return "xlsx";
  return null;
}

/** 是否为可预览的二进制文档（pdf/docx/xlsx，媒体类型由 workspaceMediaKind 覆盖） */
export function isPreviewableBinary(name: string): boolean {
  const kind = workspaceFileKind(name);
  return kind === "pdf" || kind === "docx" || kind === "xlsx";
}

/** 视频文件的浏览器播放支持级别：native 原生可播 / flv 经 mpegts.js 可播 / unsupported 无法在线播放 */
export function workspaceVideoSupport(name: string): "native" | "flv" | "unsupported" | null {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["mp4", "webm", "mov"].includes(ext)) return "native";
  if (ext === "flv") return "flv";
  if (["mkv", "avi"].includes(ext)) return "unsupported";
  return null;
}

// 全局搜索
export const searchApi = {
  global: (q: string, limit = 10) =>
    api.get<GlobalSearchResult>("/search/global", { params: { q, limit } }),
};

// UI 交互（ui_ask 回答 / 工作台状态上报）
export const uiApi = {
  answer: (askId: string, answer: string) =>
    api.post<{ status: string }>("/chat/ui-answer", { ask_id: askId, answer }),
  reportState: (state: Record<string, unknown>) =>
    api.post("/chat/ui-state", { state }),
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

// Skills
export const skillsApi = {
  list: (includeArchived = false) =>
    api.get<SkillItem[]>("/skills/", { params: { include_archived: includeArchived } }),
  get: (name: string) => api.get<SkillItem>(`/skills/${encodeURIComponent(name)}`),
  create: (data: { name: string; description: string; content: string; trigger_patterns?: string[] }) =>
    api.post("/skills/", data),
  update: (name: string, data: { content?: string; description?: string; add_trigger_patterns?: string[] }) =>
    api.put(`/skills/${encodeURIComponent(name)}`, data),
  remove: (name: string) => api.delete(`/skills/${encodeURIComponent(name)}`),
  setState: (name: string, state: string) =>
    api.post(`/skills/${encodeURIComponent(name)}/state`, { state }),
  setPinned: (name: string, pinned: boolean) =>
    api.post(`/skills/${encodeURIComponent(name)}/pinned`, { pinned }),
};

// Config Meta（统一配置元数据，数据驱动配置中心）
export const configMetaApi = {
  list: () => api.get<{ groups: ConfigMetaGroup[] }>("/config/meta"),
  save: (key: string, value: unknown) =>
    api.put(`/config/meta/${encodeURIComponent(key)}`, { value }),
};

// Stickers（表情包与图片索引）
export const stickersApi = {
  list: (params: { query?: string; page?: number; page_size?: number }) =>
    api.get<StickerListResult>("/stickers", { params }),
  stats: () => api.get<StickerStats>("/stickers/stats"),
  upload: (data: FormData) =>
    api.post<{ success: boolean; sticker: StickerItem }>("/stickers", data, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120000,
    }),
  update: (id: string, data: { description?: string; tags?: string[]; emotion?: string }) =>
    api.put(`/stickers/${encodeURIComponent(id)}`, data),
  reindex: (id: string) =>
    api.post(`/stickers/${encodeURIComponent(id)}/reindex`, null, { timeout: 120000 }),
  remove: (id: string) => api.delete(`/stickers/${encodeURIComponent(id)}`),
  fileUrl: (id: string) => `/api/stickers/${encodeURIComponent(id)}/file`,
  listImages: (params: { page?: number; page_size?: number }) =>
    api.get<IndexedImageListResult>("/stickers/images/list", { params }),
  imageFileUrl: (path: string) => `/api/stickers/images/file?path=${encodeURIComponent(path)}`,
  removeImage: (path: string) =>
    api.delete("/stickers/images", { params: { path } }),
};

// Database（数据管理页 · 数据库管理）
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
  insertRow: (db: string, table: string, values: Record<string, unknown>) =>
    api.post<{ rowid: number }>(
      `/database/${encodeURIComponent(db)}/tables/${encodeURIComponent(table)}/rows`,
      { values },
    ),
  updateRow: (db: string, table: string, rowid: number, values: Record<string, unknown>) =>
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
};
