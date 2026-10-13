/** 判断能力（Jev 通道）域类型。 */

export type JudgmentChannel = "disabled" | "typesafe" | "llm_fallback" | "unavailable";

export interface JudgmentStatus {
  enabled: boolean;
  /** 当前生效通道：typesafe 协议（云端或本地） / llm_fallback 普通模型回退 / disabled / unavailable */
  channel: JudgmentChannel;
  error?: string;
  model_id?: string;
  provider_id?: string;
  api_key_configured: boolean;
  base_url: string;
  use_proxy: boolean;
  model: string;
  timeout: number;
  fallback_enabled: boolean;
  fallback_model: string;
  fallback_effort: string;
}

export interface DecisionModel { name: string; model: string }
export interface DecisionProvider { name: string; base_url: string; api_key: string; use_proxy: boolean; models: Record<string, DecisionModel> }
export interface JudgmentHealth { status: "unknown" | "ready" | "offline" | "checking"; checked_at: number; retry_at: number; latency_ms: number; error: string; cause?: string; check_kind?: "inference" | "connectivity" }
export interface JudgmentCatalog { providers: Record<string, DecisionProvider>; default_model: string; fallback_models: string[]; revision: string; health?: Record<string, JudgmentHealth> }

export interface JudgmentChoiceAnswer {
  type: "choice";
  choice: string;
  probabilities: Record<string, number>;
  confidence: number;
}

export interface JudgmentScoreAnswer {
  type: "score";
  score: number;
  probabilities: Record<string, number>;
  legend: Record<string, unknown>;
  confidence: number;
}

export interface JudgmentNoulAnswer {
  type: "noul";
  noul: number;
}

export type JudgmentAnswer = JudgmentChoiceAnswer | JudgmentScoreAnswer | JudgmentNoulAnswer;

export interface JudgmentTestResult {
  kind?: "connectivity" | "inference";
  ok: boolean;
  error?: string;
  cause?: string;
  retryable?: boolean;
  source?: JudgmentChannel;
  model?: string;
  latency_ms: number;
  slow: boolean;
  slow_after_ms: number;
  gate_budget_ms: number;
  answers?: Record<string, JudgmentAnswer>;
  missing?: string[];
  usage?: { input_tokens: number; output_tokens: number };
}
