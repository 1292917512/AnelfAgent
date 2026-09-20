/** 工具治理域 API — 工具 / 实体 / 技能 / MCP / 频道适配器 / 审批 / 钩子 / 检索。 */

import { api } from "./client";
import type {
  AdapterListResult,
  ApprovalHistoryResponse,
  ApprovalPendingResponse,
  ApprovalPoliciesPayload,
  ApprovalPoliciesResponse,
  ApprovalRulesResponse,
  ApprovalStats,
  ChannelTestHealthResult,
  ChannelTestSendResult,
  ChannelToolTestResult,
  ChannelToolToggleResult,
  ChannelToolsResult,
  ConfigValues,
  EntityDetail,
  EntityListItem,
  HookEntry,
  HooksConfig,
  LlmHooksOverview,
  MCPServer,
  MCPServerConfig,
  MCPToggleResult,
  MCPToolInfo,
  PermissionRuleItem,
  RetrievalMatrix,
  RetrievalSettings,
  RetrievalTestResult,
  SkillBuildState,
  SkillItem,
  SkillLibraryHealth,
} from "@/lib/types";

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

// Entities

export const entitiesApi = {
  list: () => api.get<EntityListItem[]>("/entities/"),
  catalog: () => api.get("/entities/catalog"),
  statistics: () => api.get("/entities/statistics"),
  detail: (name: string) => api.get<EntityDetail>(`/entities/${encodeURIComponent(name)}`),
  config: (name: string) => api.get(`/entities/${encodeURIComponent(name)}/config`),
  updateConfig: (name: string, key: string, value: unknown) =>
    api.put(`/entities/${encodeURIComponent(name)}/config`, { key, value }),
  updateConfigBatch: (name: string, updates: ConfigValues) =>
    api.put(`/entities/${encodeURIComponent(name)}/config/batch`, { updates }),
  toggle: (name: string, enabled: boolean) =>
    api.post(`/entities/${encodeURIComponent(name)}/enable`, { enabled }),
};

// Personas

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
  setStayAwake: (name: string, enabled: boolean) =>
    api.put<{ status: string; stay_awake: boolean; applied: boolean }>(
      `/mcp/${encodeURIComponent(name)}/stay-awake`, { enabled },
    ),
  tools: (name: string) =>
    api.get<MCPToolInfo[]>(`/mcp/${encodeURIComponent(name)}/tools`),
  oauthStatus: () =>
    api.get<Record<string, { authorized: boolean; pending_url: string }>>("/mcp/oauth-status"),
  oauthLogout: (name: string) =>
    api.delete<{ server: string; removed: boolean }>(`/mcp/${encodeURIComponent(name)}/oauth`),
};

/** 从 axios 错误中提取可读信息（统一用于 toast 反馈） */

export const adaptersApi = {
  list: () => api.get<AdapterListResult>("/adapters/"),
  reload: () => api.post("/adapters/reload"),
  toggle: (key: string) => api.put(`/adapters/${encodeURIComponent(key)}/toggle`),
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
  testChannelTool: (key: string, name: string, args: ConfigValues) =>
    api.post<ChannelToolTestResult>(
      `/adapters/${encodeURIComponent(key)}/tools/${encodeURIComponent(name)}/test`,
      { args },
    ),
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
  savePolicies: (policies: ApprovalPoliciesPayload) =>
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

export const skillsApi = {
  list: (includeArchived = false) =>
    api.get<SkillItem[]>("/skills/", { params: { include_archived: includeArchived } }),
  health: () => api.get<SkillLibraryHealth>("/skills/health"),
  rebuildVectors: () => api.post<{ ok: boolean; message?: string; state: SkillBuildState }>("/skills/vectors/rebuild"),
  embed: (name: string) => api.post<{ ok: boolean; name: string; embedded: boolean; message?: string }>(`/skills/${encodeURIComponent(name)}/embed`),
  get: (name: string) => api.get<SkillItem>(`/skills/${encodeURIComponent(name)}`),
  create: (data: { name: string; description: string; content: string; trigger_patterns?: string[] }) =>
    api.post("/skills/", data),
  update: (name: string, data: { content?: string; description?: string; add_trigger_patterns?: string[]; rationale?: string }) =>
    api.put(`/skills/${encodeURIComponent(name)}`, data),
  remove: (name: string) => api.delete(`/skills/${encodeURIComponent(name)}`),
  setState: (name: string, state: string) =>
    api.post(`/skills/${encodeURIComponent(name)}/state`, { state }),
  setPinned: (name: string, pinned: boolean) =>
    api.post(`/skills/${encodeURIComponent(name)}/pinned`, { pinned }),
};

// Config Meta（统一配置元数据，数据驱动配置中心）

export const hooksApi = {
  get: () => api.get<HooksConfig>("/hooks"),
  save: (hooks: Record<string, HookEntry[]>) =>
    api.put<{ saved: boolean; count: number }>("/hooks", hooks),
  example: () => api.get<Record<string, HookEntry[]>>("/hooks/example"),
};

// ── LLM 钩子面（/hooks-llm，agent/hooks_llm 注册表观测，只读） ──

export const hooksLlmApi = {
  get: () => api.get<LlmHooksOverview>("/hooks-llm"),
};

// Retrieval（检索能力页 · 提供者矩阵 + 抓取设置）

export const retrievalApi = {
  matrix: () => api.get<RetrievalMatrix>("/retrieval/matrix"),
  setActive: (capability: string, provider: string) =>
    api.put<RetrievalMatrix>("/retrieval/active", { capability, provider }),
  setEnabled: (name: string, enabled: boolean) =>
    api.put<RetrievalMatrix>(`/retrieval/providers/${name}/enabled`, { enabled }),
  setCredential: (name: string, apiKey: string) =>
    api.put<RetrievalMatrix>(`/retrieval/providers/${name}/credential`, { api_key: apiKey }),
  test: (name: string, capability: string, input = "") =>
    api.post<RetrievalTestResult>(`/retrieval/providers/${name}/test`, { capability, input }),
  settings: () => api.get<RetrievalSettings>("/retrieval/settings"),
  saveSettings: (data: { proxy?: string; ssrf_protection?: boolean }) =>
    api.put<RetrievalSettings>("/retrieval/settings", data),
};
