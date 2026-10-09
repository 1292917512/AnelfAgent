import { useTranslation } from "react-i18next";
import type { ModelConfig, ProviderConfig } from "@/lib/types";
import { Input, Select, Switch } from "@/components/ui";
import { cn } from "@/lib/utils";
import { ReasoningEffortOptions, highestContractEffort } from "@/components/common/ReasoningEffortSelect";
import { MODEL_TYPE_OPTIONS } from "./shared";
import { ModelCombobox } from "./ModelCombobox";
import { JsonSection } from "./ModelJsonSection";
import type { ModelEditorState } from "./useModelEditor";

export function ModelEditorFields({ provider, model, editor }: { provider: ProviderConfig; model: ModelConfig; editor: ModelEditorState }) {
  const { t } = useTranslation(["models", "common"]);
  const { draft, patch, jsonDrafts, setJsonDrafts, jsonEnabled, setJsonEnabled, jsonErrors, setJsonErrors,
    builtinToolsText, setBuiltinToolsText, builtinToolsError, setBuiltinToolsError, remoteIds, remoteQuery } = editor;
  return <div className="space-y-4">
        {/* 连接信息（供应商级，只读） */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("providerFields.base_url")}</label>
            <Input aria-label={t("providerFields.base_url")} value={provider.base_url} readOnly  />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("providerFields.api_key")}</label>
            <Input aria-label={t("providerFields.api_key")} type="password" value={provider.api_key} readOnly  />
          </div>
        </div>
        <p className="text-[11px] text-muted -mt-1">{t("connManagedByProvider")}</p>

        {/* 模型标识 + 上下文/输出预算 */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("displayNameLabel")}</label>
            <Input aria-label={t("displayNameLabel")} value={model.id} readOnly  />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("modelIdLabel")}</label>
            <ModelCombobox
              value={draft.model ?? ""}
              onChange={(v) => patch({ model: v })}
              options={remoteIds}
              loading={remoteQuery.isFetching}
              onFetch={() => remoteQuery.refetch()}
              placeholder={t("modelIdPlaceholder")}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("modelFields.context_window")}</label>
            <Input
              type="number" min={0} step={1}
              aria-label={t("modelFields.context_window")}
              value={draft.context_window ?? 0}
              placeholder={t("contextWindowPlaceholder")}
              onChange={(e) => patch({ context_window: Math.max(0, Number(e.target.value) || 0) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("modelFields.max_tokens")}</label>
            <Input
              type="number" min={0} step={1}
              aria-label={t("modelFields.max_tokens")}
              value={draft.max_tokens ?? ""}
              placeholder={t("maxTokensPlaceholder")}
              onChange={(e) => patch({ max_tokens: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("reasoningEffort")}</label>
            <div className="flex gap-2 items-center">
              <label className="flex items-center gap-1.5 cursor-pointer shrink-0" title={t("effortHint")}>
                <input
                  type="checkbox"
                  checked={draft.supports_reasoning ?? false}
                  onChange={(e) => {
                    const supports = e.target.checked;
                    if (!supports || draft.reasoning_effort) {
                      patch({ supports_reasoning: supports });
                      return;
                    }
                    let thinking: unknown = model.thinking;
                    try {
                      thinking = JSON.parse(jsonDrafts.thinking);
                    } catch {
                      // JSON 草稿无效时按已保存契约归一
                    }
                    patch({ supports_reasoning: supports, reasoning_effort: highestContractEffort(thinking) });
                  }}
                  className="accent-[rgb(168,85,247)] w-3.5 h-3.5"
                />
                <span className="text-xs text-foreground">{t("deepThinking")}</span>
              </label>
              {draft.supports_reasoning && (
                <Select
                  className="flex-1"
                  aria-label={t("reasoningEffort")}
                  value={draft.reasoning_effort ?? ""}
                  onChange={(e) => patch({ reasoning_effort: e.target.value })}
                >
                  <ReasoningEffortOptions t={t} />
                </Select>
              )}
            </div>
            <p className="text-[11px] text-muted">{t("effortHint")}</p>
            {draft.supports_reasoning && (
              <JsonSection
                label={t("modelFields.thinking")}
                hint={t("modelFields.thinkingHint")}
                enabled={jsonEnabled.thinking}
                onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, thinking: v }))}
                value={jsonDrafts.thinking}
                onChange={(v) => { setJsonDrafts((p) => ({ ...p, thinking: v })); setJsonErrors((p) => ({ ...p, thinking: undefined })); }}
                error={jsonErrors.thinking}
              />
            )}
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">{t("endpointLabel")}</label>
            <Select
              className="w-full"
              aria-label={t("endpointLabel")}
              value={draft.chat_protocol ?? "chat_completions"}
              onChange={(e) => patch({ chat_protocol: e.target.value as ModelConfig["chat_protocol"] })}
            >
              <option value="chat_completions">/v1/chat/completions</option>
              <option value="responses">/v1/responses</option>
              <option value="auto">{t("chatProtocol.auto")}</option>
            </Select>
            <p className="text-[11px] text-muted">{t("endpointAutoHint")}</p>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted" title={t("modelFields.temperatureHint")}>
              temperature
            </label>
            <Input
              type="number" step="any" min={0} max={2}
              aria-label="temperature"
              value={draft.temperature ?? ""}
              placeholder={t("modelFields.temperatureHint")}
              onChange={(e) => patch({ temperature: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted">top_p</label>
            <Input
              type="number" step="any" min={0} max={1}
              aria-label="top_p"
              value={draft.top_p ?? ""}
              placeholder={t("modelFields.temperatureHint")}
              onChange={(e) => patch({ top_p: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </div>
        </div>

        {/* 供应商内置工具 */}
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted" title={t("modelFields.builtin_toolsHint")}>
            {t("modelFields.builtin_tools")}
          </label>
          <Input
            aria-label={t("modelFields.builtin_tools")}
            value={builtinToolsText}
            placeholder="web_search, code_interpreter"
            onChange={(e) => { setBuiltinToolsText(e.target.value); setBuiltinToolsError(undefined); }}
            className={cn("font-mono text-xs", builtinToolsError && "border-danger")}
            spellCheck={false}
          />
          <p className={cn("text-[11px]", builtinToolsError ? "text-danger" : "text-muted")}>
            {builtinToolsError ?? t("modelFields.builtin_toolsHint")}
          </p>
        </div>

        {/* 可勾选启用的 JSON 区块 */}
        <JsonSection
          label={t("modelFields.extra_body")}
          hint={t("modelFields.extra_bodyHint")}
          enabled={jsonEnabled.extra_body}
          onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, extra_body: v }))}
          value={jsonDrafts.extra_body}
          onChange={(v) => { setJsonDrafts((p) => ({ ...p, extra_body: v })); setJsonErrors((p) => ({ ...p, extra_body: undefined })); }}
          error={jsonErrors.extra_body}
        />
        <JsonSection
          label={t("modelFields.extra_headers")}
          hint={t("modelFields.extra_headersHint")}
          enabled={jsonEnabled.extra_headers}
          onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, extra_headers: v }))}
          value={jsonDrafts.extra_headers}
          onChange={(v) => { setJsonDrafts((p) => ({ ...p, extra_headers: v })); setJsonErrors((p) => ({ ...p, extra_headers: undefined })); }}
          error={jsonErrors.extra_headers}
        />
        <JsonSection
          label={t("modelFields.request_params")}
          hint={t("modelFields.request_paramsHint")}
          enabled={jsonEnabled.request_params}
          onEnabledChange={(v) => setJsonEnabled((p) => ({ ...p, request_params: v }))}
          value={jsonDrafts.request_params}
          onChange={(v) => { setJsonDrafts((p) => ({ ...p, request_params: v })); setJsonErrors((p) => ({ ...p, request_params: undefined })); }}
          error={jsonErrors.request_params}
        />

        {/* 能力与类型 */}
        <div className="flex flex-wrap gap-3 items-center">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={draft.supports_vision ?? false}
              onChange={(e) => patch({ supports_vision: e.target.checked })}
              className="accent-accent2 w-3.5 h-3.5" />
            <span className="text-xs text-foreground">{t("vision")}</span>
          </label>
          {draft.supports_vision && (
            <Select
              value={draft.vision_format ?? "base64"}
              onChange={(e) => patch({ vision_format: e.target.value })}
              className="!h-7 text-xs"
            >
              <option value="base64">base64</option>
              <option value="url">url</option>
              <option value="both">both</option>
            </Select>
          )}
          {draft.supports_vision && (
            <label className="flex items-center gap-2 cursor-pointer" title={t("videoUnderstandingHint")}>
              <input type="checkbox" checked={draft.supports_video ?? false}
                onChange={(e) => patch({ supports_video: e.target.checked })}
                className="accent-accent2 w-3.5 h-3.5" />
              <span className="text-xs text-foreground">{t("videoUnderstanding")}</span>
            </label>
          )}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={draft.supports_tools ?? true}
              onChange={(e) => patch({ supports_tools: e.target.checked })}
              className="accent-accent w-3.5 h-3.5" />
            <span className="text-xs text-foreground">{t("toolCall")}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer" title={t("forcedToolChoiceHint")}>
            <input type="checkbox" checked={draft.supports_forced_tool_choice ?? true}
              onChange={(e) => patch({ supports_forced_tool_choice: e.target.checked })}
              className="accent-accent w-3.5 h-3.5" />
            <span className="text-xs text-foreground">{t("forcedToolChoice")}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer" title={t("enableHint")}>
            <Switch
              aria-label={t("enableLabel")}
              checked={draft.enabled ?? true}
              onChange={(v) => patch({ enabled: v })}
            />
            <span className="text-xs text-foreground">{t("enableLabel")}</span>
          </label>
        </div>

        <div>
          <p className="text-xs font-medium text-muted mb-1">{t("modelTypes")}</p>
          <div className="flex flex-wrap gap-1.5">
            {MODEL_TYPE_OPTIONS.map((mt) => {
              const active = (draft.model_types ?? []).includes(mt);
              return (
                <button
                  key={mt}
                  aria-pressed={active}
                  type="button"
                  onClick={() => {
                    const cur = draft.model_types ?? [];
                    patch({ model_types: active ? cur.filter((x) => x !== mt) : [...cur, mt] });
                  }}
                  className={cn(
                    "px-2.5 py-0.5 text-xs font-medium rounded-full border transition-all",
                    active ? "bg-accent-subtle text-accent border-accent" : "bg-secondary text-muted border-border",
                  )}
                >
                  {t(`modelTypeLabels.${mt}`, { defaultValue: mt })}
                </button>
              );
            })}
          </div>
        </div>

  </div>;
}
