/** 检索提供者类型（/retrieval API）。 */

export interface RetrievalProviderInfo {
  name: string;
  display_name: string;
  description: string;
  enabled: boolean;
  configured: boolean;
  requires_credential: boolean;
  credential_source: string;
  capabilities: string[];
}

export interface RetrievalMatrix {
  capabilities: string[];
  selection: Record<string, string>;
  active: Record<string, string>;
  providers: RetrievalProviderInfo[];
}

export interface RetrievalTestResult {
  ok: boolean;
  latency_ms?: number;
  summary?: string;
  excerpt?: string;
  error?: string;
}

export interface RetrievalSettings {
  proxy: string;
  active: Record<string, string>;
  disabled_providers: string[];
  ssrf_protection: boolean;
  bigmodel_key_configured: boolean;
}
