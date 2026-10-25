/** 工具页类型（/tools、/tools/plugins API）。 */

export interface ModuleSyncResult {
  added: string[];
  removed: string[];
  reloaded: string[];
  failed: string[];
  total: number;
  skipped?: "in_progress";
}

export type ToolOriginKind = "core" | "entity" | "channel" | "external";

export interface ToolOrigin {
  kind: ToolOriginKind;
  module: string;
}

export interface ToolItem {
  name: string;
  source: string;
  /** 由后端注册元数据提供；旧后端缺失时保持未分类。 */
  origin?: ToolOrigin;
  enabled: boolean;
  description: string;
  tags: string[];
}

export interface ToolGroup {
  group: string;
  reload_module?: string | null;
  description: string;
  tools: ToolItem[];
  all_enabled: boolean;
  any_enabled: boolean;
  enabled_count: number;
  total_count: number;
}

export interface PluginInfo {
  name: string;
  version: string;
  author: string;
  enabled: boolean;
  description: string;
}
