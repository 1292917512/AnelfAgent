import type { QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { refreshToolCatalog } from "@/lib/tool-catalog";
import type {
  InstalledPlugin,
  PluginInspection,
  PluginOperation,
  PluginRuntime,
  PluginSource,
  MarketplaceInfo,
  RefreshResult,
  SearchResult,
  UpgradeAllResult,
  UpgradeResult,
} from "./types";

export async function refreshPluginViews(client: QueryClient): Promise<void> {
  await Promise.all([
    refreshToolCatalog(client),
    ...["plugins-installed", "plugins-detail", "plugins-operations", "plugins-search", "mcpServers", "mcpConfig"].map((key) =>
      client.invalidateQueries({ queryKey: [key] })),
  ]);
}

// Git fetches and the official DSH installer enforce their own subprocess deadlines.
const lifecycleRequest = { timeout: 0 };

/** 插件管理实体专属 API（/api/entity/plugins）。 */
export const pluginsApi = {
  // 已安装插件
  list: () => api.get<InstalledPlugin[]>("/entity/plugins"),
  detail: (name: string) => api.get<InstalledPlugin & { runtime: PluginRuntime }>(`/entity/plugins/${encodeURIComponent(name)}`),
  operations: () => api.get<PluginOperation[]>("/entity/plugins/diagnostics/operations"),
  inspect: (target: PluginSource, signal?: AbortSignal) =>
    api.post<PluginInspection>("/entity/plugins/inspect", target, { ...lifecycleRequest, signal }),
  install: (name: string, marketplace = "", expected_revision = "") =>
    api.post<InstalledPlugin>("/entity/plugins", { name, marketplace, expected_revision }, lifecycleRequest),
  installFromSource: (source: string, ref = "", subdir = "", expected_revision = "") =>
    api.post<InstalledPlugin>("/entity/plugins/from-source", { source, ref, subdir, expected_revision }, lifecycleRequest),
  remove: (name: string) => api.delete(`/entity/plugins/${name}`),
  upgrade: (name: string) =>
    api.post<UpgradeResult>(`/entity/plugins/${name}/upgrade`, null, lifecycleRequest),
  upgradeAll: () => api.post<UpgradeAllResult>("/entity/plugins/upgrade-all", null, lifecycleRequest),
  toggle: (name: string, enabled: boolean) =>
    api.post(`/entity/plugins/${name}/toggle`, { enabled }, lifecycleRequest),

  // 市场订阅
  listMarketplaces: () => api.get<MarketplaceInfo[]>("/entity/plugins/marketplaces"),
  addMarketplace: (name: string, source: string, ref = "") =>
    api.post<MarketplaceInfo>("/entity/plugins/marketplaces", { name, source, ref }, lifecycleRequest),
  removeMarketplace: (name: string) =>
    api.delete(`/entity/plugins/marketplaces/${name}`),
  toggleMarketplace: (name: string, enabled: boolean) =>
    api.post(`/entity/plugins/marketplaces/${name}/toggle`, { enabled }),
  refreshMarketplaces: (name = "") =>
    api.post<RefreshResult>("/entity/plugins/marketplaces/refresh", null, {
      ...lifecycleRequest, params: { name },
    }),

  // 浏览检索
  search: (q: string, marketplace: string, offset: number, limit: number, signal: AbortSignal) =>
    api.get<SearchResult>("/entity/plugins/search", { ...lifecycleRequest, signal, params: { q, marketplace, offset, limit } }),
};
