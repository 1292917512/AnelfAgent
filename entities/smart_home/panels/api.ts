import { api } from "@/lib/api";
import type {
  ControlResponse,
  DevicesResponse,
  DiscoverResponse,
  DomainsResponse,
  PreviewResponse,
  ProviderStatus,
  StatusResponse,
} from "./types";

// 智能家居（设备状态感知与控制）
export const smartHomeApi = {
  status: () => api.get<StatusResponse>("/entity/smart_home/status"),
  devices: (params?: { domain?: string; area?: string; name?: string }) =>
    api.get<DevicesResponse>("/entity/smart_home/devices", { params }),
  domains: () => api.get<DomainsResponse>("/entity/smart_home/domains"),
  preview: () => api.get<PreviewResponse>("/entity/smart_home/preview"),
  control: (entityId: string, action: string, value = "") =>
    api.post<ControlResponse>("/entity/smart_home/control", {
      entity_id: entityId,
      action,
      value,
    }),
  reconnect: (key: string) =>
    api.post<ProviderStatus>(`/entity/smart_home/providers/${encodeURIComponent(key)}/reconnect`),
  discover: (key: string) =>
    api.post<DiscoverResponse>(`/entity/smart_home/providers/${encodeURIComponent(key)}/discover`),
  // 配置写复用通用实体配置端点（连接 url/token、域启停、域配置项等）
  updateConfig: (key: string, value: unknown) =>
    api.put("/entities/smart_home/config", { key, value }),
  getConfig: () =>
    api.get<{ values: Record<string, unknown> }>("/entities/smart_home/config"),
};
