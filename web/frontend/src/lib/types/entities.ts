export interface EntityManifest {
  display_name: string;
  icon: string;
  description: string;
  version: string;
}

export interface EntityConfigItem {
  key: string;
  description: string;
  /** 配置类型（与配置中心同口径：boolean/integer/float/string/text/enum/...） */
  type: string;
  /** 当前值（PASSWORD 类型为掩码） */
  value: unknown;
  /** 默认值 */
  default: unknown;
  editable: boolean;
  required?: boolean;
  enum_options?: string[] | null;
}

export interface EntityToolInfo {
  name: string;
  enabled: boolean;
  description: string;
}

export interface EntityProviderInfo {
  name: string;
  priority: number;
  max_tokens: number;
  description: string;
}

export interface EntityListItem {
  name: string;
  type: string;
  description: string;
  enabled: boolean;
  group: string;
  source: string;
  tags: string[];
  config_group: string;
  has_instance: boolean;
  manifest: EntityManifest;
}

export interface EntityDetail {
  name: string;
  type: string;
  description: string;
  enabled: boolean;
  group: string;
  source: string;
  tags: string[];
  config_group: string;
  has_instance: boolean;
  apis: string[];
  config_items: EntityConfigItem[];
  configs: Record<string, unknown>;
  manifest: EntityManifest;
  tools: EntityToolInfo[];
  providers: EntityProviderInfo[];
}
