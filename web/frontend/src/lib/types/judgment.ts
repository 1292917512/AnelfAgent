/** 判断能力（Jev 通道）域类型。 */

export type JudgmentChannel = "disabled" | "typesafe" | "llm_fallback" | "unavailable";

export interface JudgmentStatus {
  enabled: boolean;
  /** 当前生效通道：typesafe 原生 / llm_fallback 普通模型回退 / disabled / unavailable */
  channel: JudgmentChannel;
  api_key_configured: boolean;
  base_url: string;
  model: string;
  timeout: number;
  fallback_enabled: boolean;
  fallback_model: string;
  fallback_effort: string;
}

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
  ok: boolean;
  error?: string;
  cause?: string;
  retryable?: boolean;
  source?: "typesafe" | "llm_fallback";
  model?: string;
  latency_ms: number;
  answers?: Record<string, JudgmentAnswer>;
  missing?: string[];
  usage?: { input_tokens: number; output_tokens: number };
}
