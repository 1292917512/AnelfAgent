import { useTranslation } from "react-i18next";
import type { ModelConfig } from "@/lib/types";
import { Select, Switch } from "@/components/ui";
import { ReasoningEffortOptions, highestContractEffort } from "@/components/common/ReasoningEffortSelect";
import { cn } from "@/lib/utils";
import { MODEL_TYPE_OPTIONS } from "./shared";
import { JsonSection } from "./ModelJsonSection";
import type { ModelEditorState } from "./useModelEditor";

export function ModelCapabilitiesSection({ model, editor }: { model: ModelConfig; editor: ModelEditorState }) {
  const { t } = useTranslation("models");
  const { draft, patch, jsonDrafts, setJsonDrafts, jsonEnabled, setJsonEnabled, jsonErrors, setJsonErrors } = editor;
  return <section aria-label={t("editorCapabilities")} className="space-y-4 border-t border-border pt-5">
    <div>
      <h3 className="text-sm font-semibold text-heading">{t("editorCapabilities")}</h3>
      <p className="mt-1 text-xs text-muted">{t("editorCapabilitiesHint")}</p>
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-border p-3">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span>{t("vision")}</span>
          <Switch label={t("vision")} checked={draft.supports_vision ?? false} onChange={(supports_vision) => patch({ supports_vision })} />
        </div>
        {draft.supports_vision && <div className="mt-3 space-y-2 border-t border-border pt-3">
          <Select className="w-full" aria-label={t("visionFormat")} value={draft.vision_format || "base64"}
            onChange={(event) => patch({ vision_format: event.target.value })}>
            <option value="base64">base64</option><option value="url">URL</option><option value="both">base64 + URL</option>
          </Select>
          <label className="flex items-center gap-2 text-xs leading-relaxed" title={t("videoUnderstandingHint")}>
            <input type="checkbox" className="accent-accent" checked={draft.supports_video ?? false} onChange={(event) => patch({ supports_video: event.target.checked })} />
            {t("videoUnderstanding")}
          </label>
        </div>}
      </div>
      <div className="rounded-xl border border-border p-3">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span>{t("toolCall")}</span>
          <Switch label={t("toolCall")} checked={draft.supports_tools ?? true} onChange={(supports_tools) => patch({ supports_tools })} />
        </div>
        {draft.supports_tools && <div className="mt-3 space-y-2 border-t border-border pt-3">
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" className="accent-accent" checked={draft.supports_forced_tool_choice ?? true}
              onChange={(event) => patch({ supports_forced_tool_choice: event.target.checked })} />
            {t("forcedToolChoice")}
          </label>
          <p className="text-xs leading-relaxed text-muted">{t("forcedToolChoiceHint")}</p>
        </div>}
      </div>
    </div>
    <div className="rounded-xl border border-border p-3 sm:p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <span className="text-sm font-medium">{t("deepThinking")}</span>
          <p className="mt-1 text-xs leading-relaxed text-muted">{t("effortHint")}</p>
        </div>
        <Switch label={t("deepThinking")} checked={draft.supports_reasoning ?? false} onChange={(supports_reasoning) => {
          let thinking: unknown = model.thinking;
          try { thinking = JSON.parse(jsonDrafts.thinking); } catch { /* Use the saved contract until the draft is valid. */ }
          patch({ supports_reasoning, ...(supports_reasoning && !draft.reasoning_effort ? { reasoning_effort: highestContractEffort(thinking) } : {}) });
        }} />
      </div>
      {(draft.supports_reasoning || jsonEnabled.thinking) && <div className="mt-4 space-y-3">
        {draft.supports_reasoning && <Select className="w-full" aria-label={t("reasoningEffort")} value={draft.reasoning_effort ?? ""}
          onChange={(event) => patch({ reasoning_effort: event.target.value })}><ReasoningEffortOptions t={t} /></Select>}
        {draft.chat_protocol === "responses" && <p className="text-xs leading-relaxed text-muted">{t("responsesThinkingHint")}</p>}
        <JsonSection label={t("modelFields.thinking")} hint={t("modelFields.thinkingHint")}
          enabled={jsonEnabled.thinking} onEnabledChange={(value) => setJsonEnabled((prev) => ({ ...prev, thinking: value }))}
          value={jsonDrafts.thinking} onChange={(value) => { setJsonDrafts((prev) => ({ ...prev, thinking: value })); setJsonErrors((prev) => ({ ...prev, thinking: undefined })); }}
          error={jsonErrors.thinking} />
      </div>}
    </div>
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted">{t("modelTypes")}</p>
      <div className="flex flex-wrap gap-2">
        {MODEL_TYPE_OPTIONS.map((type) => {
          const types = draft.model_types ?? [];
          const active = types.includes(type);
          return <button key={type} type="button" aria-pressed={active}
            onClick={() => patch({ model_types: active ? types.filter((value) => value !== type) : [...types, type] })}
            className={cn("min-h-9 rounded-lg border px-3 text-xs font-medium transition-colors", active ? "border-accent/40 bg-accent-subtle text-accent" : "border-border bg-card text-muted hover:bg-hover")}>
            {t(`modelTypeLabels.${type}`, { defaultValue: type })}
          </button>;
        })}
      </div>
    </div>
  </section>;
}
