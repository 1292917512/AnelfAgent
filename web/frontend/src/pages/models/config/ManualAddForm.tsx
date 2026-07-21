import { useState } from "react";
import axios from "axios";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import type { CreateModelConfig } from "@/lib/types";
import { Button, Input } from "@/components/ui";
import { EMPTY_MANUAL_MODEL, type ManualModelForm } from "./shared";

/** 手动添加模型表单 */
export function ManualAddForm({
  onSubmit,
  onCancel,
  isPending,
}: {
  onSubmit: (data: CreateModelConfig) => Promise<void>;
  onCancel: () => void;
  isPending: boolean;
}) {
  const { t } = useTranslation(["models", "common"]);
  const [form, setForm] = useState<ManualModelForm>(EMPTY_MANUAL_MODEL);
  const [error, setError] = useState("");

  const handleSubmit = async () => {
    const modelId = form.id.trim();
    if (!modelId) return;
    setError("");
    try {
      await onSubmit({
        id: modelId,
        model: form.model.trim() || modelId,
        model_types: ["chat"],
        temperature: 0.7,
        top_p: 1.0,
        context_window: form.context_window,
        frequency_penalty: 0,
        presence_penalty: 0,
        supports_tools: form.supports_tools,
        supports_vision: form.supports_vision,
        supports_forced_tool_choice: form.supports_forced_tool_choice,
        vision_format: "base64",
        supports_reasoning: form.supports_reasoning,
        timeout: 120.0,
        chat_protocol: "chat_completions",
        request_params: {},
        extra_body: {},
      });
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null;
      setError(`${t("createFailed")}: ${typeof detail === "string" ? detail : String(err)}`);
    }
  };

  return (
    <div className="p-4 rounded-md border border-accent bg-elevated space-y-3">
      <p className="text-sm font-semibold text-heading">{t("manualAddTitle")}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("modelId")} *</label>
          <Input value={form.id} placeholder={t("modelIdHint")}
            onChange={(e) => setForm({ ...form, id: e.target.value })} />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("modelName")}</label>
          <Input value={form.model} placeholder={t("modelNameHint")}
            onChange={(e) => setForm({ ...form, model: e.target.value })} />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted">{t("contextWindowLabel")}</label>
          <Input type="number" step={1} value={form.context_window}
            onChange={(e) => setForm({ ...form, context_window: Number(e.target.value) })} />
        </div>
      </div>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={form.supports_tools}
            onChange={(e) => setForm({ ...form, supports_tools: e.target.checked })}
            className="accent-accent w-3.5 h-3.5" />
          <span className="text-xs text-foreground">{t("toolCall")}</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={form.supports_vision}
            onChange={(e) => setForm({ ...form, supports_vision: e.target.checked })}
            className="accent-accent2 w-3.5 h-3.5" />
          <span className="text-xs text-foreground">{t("vision")}</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={form.supports_reasoning}
            onChange={(e) => setForm({ ...form, supports_reasoning: e.target.checked })}
            className="accent-[rgb(168,85,247)] w-3.5 h-3.5" />
          <span className="text-xs text-foreground">{t("deepThinking")}</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer" title={t("forcedToolChoiceHint")}>
          <input type="checkbox" checked={form.supports_forced_tool_choice}
            onChange={(e) => setForm({ ...form, supports_forced_tool_choice: e.target.checked })}
            className="accent-accent w-3.5 h-3.5" />
          <span className="text-xs text-foreground">{t("forcedToolChoice")}</span>
        </label>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <Button
          variant="primary"
          onClick={handleSubmit}
          disabled={!form.id.trim()}
          loading={isPending}
        >
          <Plus size={14} /> {t("common:create")}
        </Button>
        <Button variant="secondary" onClick={onCancel}>{t("common:cancel")}</Button>
      </div>
    </div>
  );
}
