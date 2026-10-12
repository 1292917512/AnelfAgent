import { useTranslation } from "react-i18next";
import type { ModelConfig, ProviderConfig } from "@/lib/types";
import { Input, Select, Switch } from "@/components/ui";
import { ModelCombobox } from "./ModelCombobox";
import { ModelCapabilitiesSection } from "./ModelCapabilitiesSection";
import { ModelRequestSettings } from "./ModelRequestSettings";
import type { ModelEditorState } from "./useModelEditor";

export function ModelEditorFields({ provider, model, editor }: { provider: ProviderConfig; model: ModelConfig; editor: ModelEditorState }) {
  const { t } = useTranslation("models");
  const { draft, patch, fieldErrors, remoteIds, remoteQuery } = editor;
  return <div className="space-y-5">
    <div className="rounded-xl border border-border bg-elevated/50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="font-medium text-heading">{provider.name || provider.id}</span>
        <span className="rounded-md bg-secondary px-1.5 py-0.5 font-mono text-muted">{provider.api_type}</span>
        <span className="min-w-0 break-all text-muted">{provider.base_url}</span>
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-muted">{t("connManagedByProvider")}</p>
    </div>

    <section aria-label={t("editorBasics")} className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-heading">{t("editorBasics")}</h3>
          <p className="mt-1 text-xs text-muted">{t("editorBasicsHint")}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
          <span>{t("enableLabel")}</span>
          <Switch label={t("enableLabel")} checked={draft.enabled ?? true} onChange={(enabled) => patch({ enabled })} />
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <p className="text-xs font-medium text-muted">{t("modelIdLabel")}</p>
          <ModelCombobox value={draft.model ?? ""} onChange={(value) => patch({ model: value })}
            options={remoteIds} loading={remoteQuery.isFetching} onFetch={() => void remoteQuery.refetch()}
            placeholder={t("modelIdPlaceholder")} invalid={!!fieldErrors.model} />
          <p className={fieldErrors.model ? "text-xs text-danger" : "text-xs text-muted"}>
            {fieldErrors.model ? t(fieldErrors.model) : t("remoteOptionalHint")}
          </p>
          {remoteQuery.error && <p role="status" className="text-xs text-warn">{t("remoteOptionalFailed")}</p>}
        </div>
        {(["context_window", "max_tokens"] as const).map((key) => <div key={key} className="space-y-1.5">
          <p className="text-xs font-medium text-muted">{t(`modelFields.${key}`)}</p>
          <Input type="number" min={0} step={1} aria-label={t(`modelFields.${key}`)} aria-invalid={!!fieldErrors[key]}
            value={draft[key] ?? ""} placeholder={t(key === "context_window" ? "contextWindowPlaceholder" : "maxTokensPlaceholder")}
            onChange={(event) => patch({ [key]: event.target.value === "" ? (key === "context_window" ? 0 : null) : Number(event.target.value) })} />
          {fieldErrors[key] && <p role="alert" className="text-xs text-danger">{t(fieldErrors[key])}</p>}
        </div>)}
        <div className="space-y-1.5 sm:col-span-2">
          <p className="text-xs font-medium text-muted">{t("endpointLabel")}</p>
          <Select className="w-full" aria-label={t("endpointLabel")} value={draft.chat_protocol ?? "chat_completions"}
            onChange={(event) => {
              const value = event.target.value;
              if (value === "auto" || value === "responses" || value === "chat_completions") patch({ chat_protocol: value });
            }}>
            <option value="chat_completions">/v1/chat/completions</option>
            <option value="responses">/v1/responses</option>
            <option value="auto">{t("chatProtocol.auto")}</option>
          </Select>
          <p className="text-xs leading-relaxed text-muted">{t("endpointAutoHint")}</p>
        </div>
      </div>
    </section>
    <ModelCapabilitiesSection model={model} editor={editor} />
    <ModelRequestSettings editor={editor} />
  </div>;
}
