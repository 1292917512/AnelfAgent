import { api } from "@/lib/api";
import type {
  WebProviderTestResult,
  WebProvidersMatrix,
} from "./types";

// Web 实体（搜索引擎管理，实体专属路由 /api/entity/web）
export const webEntityApi = {
  matrix: () => api.get<WebProvidersMatrix>("/entity/web/matrix"),
  setActive: (capability: string, provider: string) =>
    api.put<WebProvidersMatrix>("/entity/web/active", { capability, provider }),
  setEnabled: (name: string, enabled: boolean) =>
    api.put<WebProvidersMatrix>(`/entity/web/providers/${encodeURIComponent(name)}/enabled`, { enabled }),
  setKey: (name: string, apiKey: string) =>
    api.put<WebProvidersMatrix>(`/entity/web/providers/${encodeURIComponent(name)}/credential`, { api_key: apiKey }),
  test: (name: string, capability: string, input = "") =>
    api.post<WebProviderTestResult>(`/entity/web/providers/${encodeURIComponent(name)}/test`, { capability, input }),
};
