/** 插件管理面板类型（与 /api/entity/plugins 响应对齐）。 */

/** 已安装插件记录 */
export interface InstalledPlugin {
  name: string;
  version: string;
  sha: string;
  marketplace: string;
  source_type: string;
  source: string;
  ref: string;
  subdir: string;
  enabled: boolean;
  description: string;
  display_name: string;
  category: string;
  installed_at: number;
  updated_at: number;
  skills: string[];
  tools: string[];
  mcp_servers: string[];
}

/** 已订阅市场 */
export interface MarketplaceInfo {
  name: string;
  source_type: string;
  url: string;
  path: string;
  ref: string;
  added_at: number;
  plugin_count: number;
}

/** 市场插件条目（检索结果） */
export interface MarketplacePluginEntry {
  name: string;
  source_type: string;
  path: string;
  url: string;
  ref: string;
  subdir: string;
  category: string;
  description: string;
  installation: string;
  marketplace: string;
  installed: boolean;
  installed_version: string;
}

/** 检索响应 */
export interface SearchResult {
  count: number;
  plugins: MarketplacePluginEntry[];
}

/** 升级响应 */
export interface UpgradeResult {
  name: string;
  upgraded: boolean;
  version: string;
}

/** 批量升级响应 */
export interface UpgradeAllResult {
  results: Record<string, boolean>;
}

/** 市场刷新响应 */
export interface RefreshResult {
  results: Record<string, number>;
}
