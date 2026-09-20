/** 模型域 API — 供应商 / 模型 / 子代理档案 / 组件凭据 / 本地模型。 */

import { api } from "./client";
import type {
  ApiTypeInfo,
  CreateModelConfig,
  CreateProviderConfig,
  LocalModelAsset,
  LocalModelsStatus,
  ModelConfig,
  ModelInfoResult,
  ModelPriorityItem,
  ProbeResult,
  ProviderConfig,
  ProviderKeyEntry,
  RemoteModelInfo,
  SubAgentFacets,
  SubAgentProfile,
  TestChatResult,
  UpdateModelConfig,
  UpdateProviderConfig,
} from "@/lib/types";

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
  /** 批量查询模型能力信息（litellm 本地表，一次往返） */
  modelInfoBatch: (models: string[], apiType = "openai") =>
    api.post<{ info: Record<string, ModelInfoResult> }>("/models/model-info/batch", { models, api_type: apiType }),
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
  test: (baseUrl: string, apiKey: string, providerId = "", apiType = "openai") =>
    api.post("/models/test", { base_url: baseUrl, api_key: apiKey, provider_id: providerId, api_type: apiType }),
  /** 真实链路对话测试（保存并测试）：draft 为编辑中的模型草稿 */
  testChat: (providerId: string, modelId = "", draft?: UpdateModelConfig) =>
    api.post<TestChatResult>("/models/test-chat", {
      provider_id: providerId,
      model_id: modelId,
      draft,
    }),
  apiTypes: () => api.get<{ api_types: ApiTypeInfo[] }>("/models/api-types"),
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

// 子代理统一注册表（内置难度档 + 自定义档案）

export const subAgentsApi = {
  list: () => api.get<{ sub_agents: SubAgentProfile[] }>("/models/sub-agents"),
  create: (data: { name: string; model_id: string; description?: string } & SubAgentFacets) =>
    api.post<{ status: string; message: string }>("/models/sub-agents", data),
  update: (
    name: string,
    data: { model_id?: string; models?: string[]; description?: string } & SubAgentFacets,
  ) =>
    api.put(`/models/sub-agents/${encodeURIComponent(name)}`, data),
  remove: (name: string) =>
    api.delete(`/models/sub-agents/${encodeURIComponent(name)}`),
};

// Tools

export const providerKeysApi = {
  list: (domain = "") =>
    api.get<{ providers: ProviderKeyEntry[] }>("/provider-keys", { params: domain ? { domain } : {} }),
  set: (provider: string, field: string, value: string) =>
    api.put<Record<string, unknown>>(`/provider-keys/${encodeURIComponent(provider)}`, { field, value }),
};

export const localModelsApi = {
  list: () => api.get<LocalModelsStatus>("/local-models"),
  download: (assetId: string) => api.post<LocalModelAsset>(`/local-models/${assetId}/download`, {}),
  remove: (assetId: string) => api.delete<Record<string, unknown>>(`/local-models/${assetId}`),
  installRuntime: (pkg = "onnxruntime") =>
    api.post<Record<string, unknown>>("/local-models/runtime/install", { package: pkg }),
};
