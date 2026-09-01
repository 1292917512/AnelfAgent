/** web 实体（网络工具）面板类型 — 能力 × 提供者矩阵。 */

/** 能力标识：search 检索 / reader 网页读取 / repo 仓库文档 */
export type WebCapability = "search" | "reader" | "repo";

export interface WebProviderInfo {
  name: string;
  display_name: string;
  description: string;
  enabled: boolean;
  configured: boolean;
  requires_credential: boolean;
  /** 凭据来源：config（实体配置）/ llm（LLM 供应商回退）/ env（环境变量）/ ""（未配置） */
  credential_source: string;
  /** 该提供者实现的能力（未实现的不参与解析，面板灰显"不支持"） */
  capabilities: string[];
}

export interface WebProvidersMatrix {
  capabilities: WebCapability[];
  /** 各能力配置的固定选择（auto = 自动选取首个可用提供者） */
  selection: Record<string, string>;
  /** 各能力当前实际生效的提供者（"" = 无可用） */
  active: Record<string, string>;
  providers: WebProviderInfo[];
}

export interface WebProviderTestResult {
  ok: boolean;
  latency_ms: number;
  summary: string;
  excerpt: string;
  error: string;
}
