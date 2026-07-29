export interface EntityManifest {
  display_name: string;
  icon: string;
  description: string;
  version: string;
}

export interface EntityConfigItem {
  key: string;
  description: string;
  value_type: string;
  default_value: unknown;
  current_value: unknown;
  editable: boolean;
  enum_options?: string[];
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
