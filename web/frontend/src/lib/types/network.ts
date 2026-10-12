import type { ProxyMode } from "./config";

export interface NetworkProbeRequest {
  url: string;
  mode: ProxyMode | "compare";
  samples: number;
  timeout_seconds: number;
}

export interface ProbeSample {
  index: number;
  elapsed_ms: number;
  reachable: boolean;
  http_ok: boolean;
  status_code: number | null;
  path: "direct" | "proxy" | null;
  source: string;
  attempts: number;
  error_code: string | null;
}

export interface ProbeRouteReport {
  mode: ProxyMode;
  status: "passed" | "partial" | "failed" | "http_error" | "unavailable";
  samples: ProbeSample[];
  reachable_count: number;
  http_ok_count: number;
  median_ms: number | null;
  spread_ms: number | null;
  error_code: string | null;
}

export interface NetworkProbeReport {
  url: string;
  checked_at: number;
  elapsed_ms: number;
  configuration_changed: boolean;
  routes: ProbeRouteReport[];
}
