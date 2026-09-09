import { api } from "@/lib/api";
import type {
  DifyActionResult,
  DifyAppListResult,
  DifyChatResult,
  DifyConfigView,
  DifyDatasetListResult,
  DifyDslResult,
  DifyMcpStatus,
  DifyProviderListResult,
  DifyRunResult,
  DifyStatus,
} from "./types";

// Dify 平台实体面板 API（后端 entities/dify/router.py，自动挂载 /api/entity/dify）
export const difyApi = {
  status: () => api.get<DifyStatus>("/entity/dify/status"),
  connect: () => api.post<DifyActionResult>("/entity/dify/connect"),
  config: () => api.get<DifyConfigView>("/entity/dify/config"),
  saveAdmin: (email: string, password: string) =>
    api.put<DifyActionResult>("/entity/dify/config/admin", { email, password }),
  // 连接地址等配置项走核心实体配置接口（与详情页配置 tab 同源）
  saveBaseUrl: (url: string) =>
    api.put("/entities/dify/config/batch", { updates: { dify_base_url: url } }),

  listApps: (name = "") =>
    api.get<DifyAppListResult>("/entity/dify/apps", { params: { name } }),
  createApp: (name: string, mode: string, description = "") =>
    api.post<DifyActionResult>("/entity/dify/apps", { name, mode, description }),
  importDsl: (yaml_content: string, new_name = "") =>
    api.post<DifyActionResult>("/entity/dify/apps/import", { yaml_content, new_name }),
  exportDsl: (appId: string) =>
    api.get<DifyDslResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/dsl`),
  applyDsl: (appId: string, yaml_content: string, publish: boolean) =>
    api.put<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/dsl`, {
      yaml_content,
      publish,
    }),
  publish: (appId: string, marked_name = "", marked_comment = "") =>
    api.post<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/publish`, {
      marked_name,
      marked_comment,
    }),
  copyApp: (appId: string, name = "") =>
    api.post<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/copy`, { name }),
  deleteApp: (appId: string) =>
    api.delete<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}`),

  runWorkflow: (appId: string, inputs: Record<string, unknown>) =>
    api.post<DifyRunResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/run`, { inputs }),
  chat: (appId: string, query: string, inputs: Record<string, unknown>, conversationId = "") =>
    api.post<DifyChatResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/chat`, {
      query,
      inputs,
      conversation_id: conversationId,
    }),

  createApiKey: (appId: string) =>
    api.post<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/api-keys`),

  listProviders: () => api.get<DifyProviderListResult>("/entity/dify/providers"),
  setCredential: (provider: string, credentials: Record<string, unknown>) =>
    api.post<DifyActionResult>(
      `/entity/dify/providers/${encodeURIComponent(provider)}/credentials`,
      { credentials },
    ),

  listDatasets: () => api.get<DifyDatasetListResult>("/entity/dify/datasets"),

  mcpStatus: (appId: string) =>
    api.get<DifyMcpStatus>(`/entity/dify/apps/${encodeURIComponent(appId)}/mcp`),
  mcpEnable: (appId: string, description = "") =>
    api.post<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/mcp`, {
      description,
    }),
  mcpDisable: (appId: string) =>
    api.delete<DifyActionResult>(`/entity/dify/apps/${encodeURIComponent(appId)}/mcp`),
};
