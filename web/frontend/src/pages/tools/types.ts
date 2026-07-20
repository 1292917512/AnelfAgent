export interface ToolItem {
  name: string;
  source: string;
  enabled: boolean;
  description: string;
  tags: string[];
}

export interface ToolGroup {
  group: string;
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

export interface EditState {
  name: string;
  tags: string[];
  description: string;
}
