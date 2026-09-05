import { api } from "@/lib/api";
import type {
  InstalledPlugin,
  MarketplaceInfo,
  RefreshResult,
  SearchResult,
  UpgradeAllResult,
  UpgradeResult,
} from "./types";

/** 插件管理实体专属 API（/api/entity/plugins）。 */
export const pluginsApi = {
  // 已安装插件
  list: () => api.get<InstalledPlugin[]>("/entity/plugins"),
  install: (name: string, marketplace = "") =>
    api.post<InstalledPlugin>("/entity/plugins", { name, marketplace }),
  installFromSource: (source: string, ref = "", subdir = "") =>
    api.post<InstalledPlugin>("/entity/plugins/from-source", { source, ref, subdir }),
  remove: (name: string) => api.delete(`/entity/plugins/${name}`),
  upgrade: (name: string) =>
    api.post<UpgradeResult>(`/entity/plugins/${name}/upgrade`),
  upgradeAll: () => api.post<UpgradeAllResult>("/entity/plugins/upgrade-all"),
  toggle: (name: string, enabled: boolean) =>
    api.post(`/entity/plugins/${name}/toggle`, { enabled }),

  // 市场订阅
  listMarketplaces: () => api.get<MarketplaceInfo[]>("/entity/plugins/marketplaces"),
  addMarketplace: (name: string, source: string, ref = "") =>
    api.post<MarketplaceInfo>("/entity/plugins/marketplaces", { name, source, ref }),
  removeMarketplace: (name: string) =>
    api.delete(`/entity/plugins/marketplaces/${name}`),
  refreshMarketplaces: (name = "") =>
    api.post<RefreshResult>("/entity/plugins/marketplaces/refresh", null, {
      params: { name },
    }),

  // 浏览检索
  search: (q = "") =>
    api.get<SearchResult>("/entity/plugins/search", { params: { q } }),
};
