import { api } from "@/lib/api";
import type {
  GeocodeCandidate,
  ModulesResponse,
  PreviewResponse,
  RefreshResponse,
  SubscriptionLogin,
} from "./types";

// AI 桌面（动态上下文注入）
export const aiDesktopApi = {
  consoleCredential: () => api.get<{ available: boolean; configured: boolean; monitoring: boolean }>("/entity/ai_desktop/modules/subscription/login"),
  startLogin: () => api.post<SubscriptionLogin>("/entity/ai_desktop/modules/subscription/login"),
  loginStatus: (id: string) => api.get<SubscriptionLogin>(`/entity/ai_desktop/modules/subscription/login/${encodeURIComponent(id)}`),
  loginImage: (id: string) => api.get<Blob>(`/entity/ai_desktop/modules/subscription/login/${encodeURIComponent(id)}/image`, { responseType: "blob" }),
  confirmLogin: (id: string) => api.post(`/entity/ai_desktop/modules/subscription/login/${encodeURIComponent(id)}/confirm`),
  cancelLogin: (id: string) => api.delete(`/entity/ai_desktop/modules/subscription/login/${encodeURIComponent(id)}`),
  modules: () => api.get<ModulesResponse>("/entity/ai_desktop/modules"),
  preview: () => api.get<PreviewResponse>("/entity/ai_desktop/preview"),
  refresh: (key: string) =>
    api.post<RefreshResponse>(
      `/entity/ai_desktop/modules/${encodeURIComponent(key)}/refresh`,
    ),
  geocode: (query: string) =>
    api.get<{ candidates: GeocodeCandidate[] }>(
      "/entity/ai_desktop/modules/weather/geocode",
      { params: { query } },
    ),
  // 配置写复用通用实体配置端点（启停开关 / 地区列表 / 刷新间隔等）
  updateConfig: (key: string, value: unknown) =>
    api.put("/entities/ai_desktop/config", { key, value }),
};
