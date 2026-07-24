import { useTranslation } from "react-i18next";
import { Eye, Wrench, Brain, Layers } from "lucide-react";
import { Badge } from "@/components/ui";
import type { ModelConfig, UpdateModelConfig } from "@/lib/types";

export const API_TYPE_OPTIONS = [
  "openai", "anthropic", "ollama", "gemini", "azure", "deepseek",
  "groq", "bedrock", "vertex_ai", "mistral", "cohere", "huggingface",
  "cloudflare", "openrouter", "together_ai", "fireworks_ai", "perplexity",
  "cerebras", "xai", "sambanova", "volcengine", "dashscope",
];
export const MODEL_TYPE_OPTIONS = ["chat", "embedding", "image_gen", "image_edit", "asr", "tts", "video", "rerank"];
// 图片生成协议适配器（对应后端 agent.llm.image_adapters 注册名），空串表示按 host 自动识别
export const MEDIA_PROTOCOL_OPTIONS = ["siliconflow", "openai", "dashscope"];

export interface ManualModelForm {
  id: string;
  model: string;
  context_window: number;
  supports_tools: boolean;
  supports_vision: boolean;
  supports_reasoning: boolean;
  supports_forced_tool_choice: boolean;
}

export const EMPTY_MANUAL_MODEL: ManualModelForm = {
  id: "", model: "", context_window: 0,
  supports_tools: true, supports_vision: false, supports_reasoning: false,
  supports_forced_tool_choice: true,
};

export type JsonField = "request_params" | "extra_body";

export function toModelUpdate(model: ModelConfig): UpdateModelConfig {
  return {
    model: model.model,
    model_types: model.model_types,
    supports_vision: model.supports_vision,
    supports_tools: model.supports_tools,
    supports_forced_tool_choice: model.supports_forced_tool_choice,
    vision_format: model.vision_format,
    supports_reasoning: model.supports_reasoning,
    reasoning_effort: model.reasoning_effort ?? "",
    temperature: model.temperature,
    context_window: model.context_window ?? 0,
    timeout: model.timeout ?? 120,
    request_params: model.request_params,
    extra_body: model.extra_body,
    chat_protocol: model.chat_protocol ?? "chat_completions",
  };
}

/** 模型能力/类型/上下文/成本徽标组 */
export function ModelBadges({ model }: { model: ModelConfig }) {
  const { t } = useTranslation("models");
  return (
    <div className="flex gap-1 ml-1 flex-wrap">
      {model.supports_vision && (
        <Badge variant="accent2"><Eye size={9} /> {t("vision")}</Badge>
      )}
      {model.supports_tools && (
        <Badge variant="accent"><Wrench size={9} /> {t("toolCall")}</Badge>
      )}
      {model.supports_reasoning && (
        <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(168,85,247,0.1)] text-[rgb(168,85,247)] border border-[rgba(168,85,247,0.3)]">
          <Brain size={9} /> {t("deepThinking")}
        </span>
      )}
      {model.model_types.map((mt) => (
        <Badge key={mt}>{t(`modelTypeLabels.${mt}`, { defaultValue: mt })}</Badge>
      ))}
      {model.context_window != null && (
        <span title={t("contextWindowLabel")}
          className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(234,179,8,0.1)] text-[rgb(180,140,20)] border border-[rgba(234,179,8,0.25)]">
          <Layers size={9} /> {model.context_window >= 1000 ? `${Math.round(model.context_window / 1000)}K` : model.context_window}
        </span>
      )}
      {(model.input_cost != null || model.output_cost != null) && (
        <span title={t("costPerMillion")}
          className="text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(34,197,94,0.1)] text-[rgb(22,163,74)] border border-[rgba(34,197,94,0.25)]">
          ${model.input_cost ?? "?"}/{model.output_cost ?? "?"}
        </span>
      )}
    </div>
  );
}
