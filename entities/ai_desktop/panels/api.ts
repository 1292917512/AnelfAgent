import { api } from "@/lib/api";
import type {
  GeocodeCandidate,
  ModulesResponse,
  PreviewResponse,
  RefreshResponse,
} from "./types";

// AI 桌面（动态上下文注入）
export const aiDesktopApi = {
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
