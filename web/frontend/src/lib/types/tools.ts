/** 工具页类型（/tools、/tools/plugins API）。 */

export interface ModuleSyncResult {
  added: string[];
  removed: string[];
  reloaded: string[];
  failed: string[];
  total: number;
  skipped?: "in_progress";
}

export interface ToolItem {
  name: string;
  source: string;
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
