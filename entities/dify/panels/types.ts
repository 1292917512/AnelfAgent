export interface DifyStatus {
  enabled: boolean;
  configured: boolean;
  base_url: string;
  reachable: boolean;
  setup_step: string;
  version: string;
  admin_configured: boolean;
  admin_email: string;
  apps_tracked: number;
}

export interface DifySettings {
  enabled: boolean;
  base_url: string;
  admin_email: string;
  auto_setup: boolean;
  context_inject: boolean;
  timeout: number;
}

export interface DifyConfigView {
  settings: DifySettings;
  admin: { email?: string; password?: string; created_by?: string };
}

export interface DifyApp {
  id: string;
  name: string;
  mode: string;
  description: string;
  enable_api?: boolean;
  has_api_key: boolean;
  mcp_server_code: string;
  updated_at?: number;
}

export interface DifyAppListResult {
  ok: boolean;
  count: number;
  apps: DifyApp[];
}

export interface DifyDslResult {
  ok: boolean;
  app_id: string;
  name: string;
  mode: string;
  summary: Record<string, unknown>;
  dsl: string;
}

export interface DifyActionResult {
  ok: boolean;
  message?: string;
  [key: string]: unknown;
}

export interface DifyRunResult {
  ok: boolean;
  workflow_run_id?: string;
  status?: string;
  outputs?: Record<string, unknown>;
  error?: string;
  elapsed_time?: number;
  total_tokens?: number;
}

export interface DifyChatResult {
  ok: boolean;
  message_id?: string;
  conversation_id?: string;
  answer?: string;
}

export interface DifyProvider {
  provider: string;
  label: string;
  has_credential: boolean;
}

export interface DifyProviderListResult {
  ok: boolean;
  count: number;
  providers: DifyProvider[];
}

export interface DifyDataset {
  id: string;
  name: string;
  doc_count?: number;
}

export interface DifyDatasetListResult {
  ok: boolean;
  count: number;
  datasets: DifyDataset[];
}

export interface DifyMcpStatus {
  ok: boolean;
  app_id: string;
  enabled: boolean;
  mcp_url: string;
}
