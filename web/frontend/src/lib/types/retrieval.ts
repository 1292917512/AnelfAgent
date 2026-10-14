/** 检索提供者类型（/retrieval API）。 */

export interface RetrievalProviderInfo {
  name: string;
  display_name: string;
  description: string;
  enabled: boolean;
  configured: boolean;
  requires_credential: boolean;
  config_group: string;
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
  active: Record<string, string>;
  disabled_providers: string[];
  ssrf_protection: boolean;
  bigmodel_key_configured: boolean;
}

export type RetrievalDeploymentAction = "init" | "up" | "stop" | "connect" | "update" | "rollback";

export interface RetrievalDeploymentStatus {
  available: boolean;
  initialized: boolean;
  rollback_available: boolean;
  images: Record<string, string>;
  ports: Record<string, string>;
  operation: {
    id: string;
    action: RetrievalDeploymentAction;
    status: "running" | "succeeded" | "failed" | "interrupted";
    phase: string;
    started_at: number;
    finished_at: number | null;
    error: string;
    log: string;
    elapsed_seconds?: number;
    phase_elapsed_seconds?: number;
    output_idle_seconds?: number | null;
  } | null;
  observed_at?: number;
  next_step?: string;
  containers?: { service: string; image: string; state: string; health: string }[];
  error?: string;
  requirements: { platform: string; docker_cli: boolean; install_guide: string };
}
