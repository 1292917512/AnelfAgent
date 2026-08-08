import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Eye, Wrench, Brain, Layers } from "lucide-react";
import { Badge, Select } from "@/components/ui";
import { modelsApi } from "@/lib/api";
import type { ApiTypeInfo, ModelConfig } from "@/lib/types";

export const API_TYPE_OPTIONS = [
  "openai", "anthropic", "ollama", "gemini", "azure", "deepseek",
  "groq", "bedrock", "vertex_ai", "mistral", "cohere", "huggingface",
  "cloudflare", "openrouter", "together_ai", "fireworks_ai", "perplexity",
  "cerebras", "xai", "sambanova", "volcengine", "dashscope",
];
export const MODEL_TYPE_OPTIONS = ["chat", "embedding", "image_gen", "image_edit", "asr", "tts", "video", "music", "rerank"];
// 图片/视频协议适配器（对应后端 agent.llm.image_adapters / video_adapters 注册名），空串表示按 host 自动识别
export const MEDIA_PROTOCOL_OPTIONS = ["siliconflow", "openai", "dashscope", "minimax", "minimax_v2"];

/** api_type 元信息：单一权威来源在后端 GET /models/api-types，前端不再硬编码 */
export function useApiTypes(): ApiTypeInfo[] {
  const { data } = useQuery({
    queryKey: ["apiTypes"],
    queryFn: () => modelsApi.apiTypes().then((r) => r.data.api_types),
    staleTime: Infinity,
  });
  return data ?? API_TYPE_OPTIONS.map((v) => ({
    value: v,
    group: v === "openai" || v === "anthropic" ? "common" : "other",
    default_base_url: "",
  }));
}

/**
 * api_type 下拉：主流协议（OpenAI/Anthropic 兼容）置顶分组。
 * 市面上绝大多数中转/国产模型都是这两种协议的兼容实现。
 */
export function ApiTypeSelect({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string, info?: ApiTypeInfo) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("models");
  const types = useApiTypes();
  const common = types.filter((x) => x.group === "common");
  const other = types.filter((x) => x.group !== "common");
  const select = (v: string) => onChange(v, types.find((x) => x.value === v));
  return (
    <Select className="w-full" value={value} disabled={disabled} onChange={(e) => select(e.target.value)}>
      <optgroup label={t("apiTypeCommon")}>
        {common.map((x) => <option key={x.value} value={x.value}>{x.value}</option>)}
      </optgroup>
      <optgroup label={t("apiTypeOther")}>
        {other.map((x) => <option key={x.value} value={x.value}>{x.value}</option>)}
        {/* 当前值不在列表时兜底显示，避免 select 显示错位 */}
        {!types.some((x) => x.value === value) && <option value={value}>{value}</option>}
      </optgroup>
    </Select>
  );
}

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
