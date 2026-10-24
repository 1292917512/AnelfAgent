import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Save, RotateCcw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { cn } from "@/lib/utils";
import { memoryApi } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api/client";
import type {
  CogneeChatModelConfig,
  CogneeEmbeddingModelConfig,
  CogneeModelSource,
  CogneeReasoningEffort,
  CogneeResolvedInfo,
} from "@/lib/types";
import { Button, Input, Select } from "@/components/ui";
import { ModelSelect, usePriorities } from "@/components/models/ModelSelect";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { ModelConfigField as Field } from "./ModelConfigField";

type ModelKind = "chat" | "embedding";
type KindConfig = CogneeChatModelConfig | CogneeEmbeddingModelConfig;

export function ModelConfigCard({ kind, resolved }: { kind: ModelKind; resolved?: CogneeResolvedInfo }) {
  const { t } = useTranslation("memory");
  const { t: ta } = useTranslation("appconfig");
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<KindConfig | null>(null);
  const [saved, triggerSaved] = useCopyFeedback(2000);

  const { data: config, error: loadError, refetch } = useQuery({
    queryKey: ["cogneeConfig"],
    queryFn: () => memoryApi.cognee.getConfig().then((r) => r.data),
    throwOnError: false,
  });
  const { data: priorities = {} } = usePriorities();

  const form = draft ?? config?.[kind];
  const hasChanges = draft !== null;
  const requestTimeout = form && "extra_args" in form ? form.extra_args.timeout : undefined;
  const validTimeout = requestTimeout === undefined
    || (typeof requestTimeout === "number" && Number.isFinite(requestTimeout) && requestTimeout > 0);

  const saveMutation = useMutation({
    mutationFn: (values: KindConfig) =>
      memoryApi.cognee.saveConfig({ [kind]: values } as Parameters<typeof memoryApi.cognee.saveConfig>[0]),
    onSuccess: (response, submitted) => {
      queryClient.setQueryData(["cogneeConfig"], response.data);
      queryClient.invalidateQueries({ queryKey: ["cogneeConfig"] });
      queryClient.invalidateQueries({ queryKey: ["cogneeStatus"] });
      queryClient.invalidateQueries({ queryKey: ["memoryHealth"] });
      setDraft((current) => current === submitted ? null : current);
      triggerSaved();
    },
  });

  if (!form) {
    return (
      <Card title={t(`cognee.${kind}ConfigTitle`)}>
        {loadError ? <QueryError error={loadError} retry={() => void refetch()} compact /> : <p className="text-sm text-muted">{t("common:loading")}</p>}
      </Card>
    );
  }

  const update = (patch: Partial<KindConfig>) => {
    setDraft({ ...form, ...patch });
    if (saveMutation.isError) saveMutation.reset();
  };

  const selectedModel = (priorities[kind] || []).find((item) => item.id === form.model_id);
  const providers = config?.options
    ? (kind === "chat" ? config.options.chat_providers : config.options.embed_providers)
    : [];
  const sourceDesc: Record<CogneeModelSource, string> = {
    auto: t("cognee.sourceAutoDesc"),
    model: t("cognee.sourceModelDesc"),
    custom: t("cognee.sourceCustomDesc"),
  };

  return (
    <Card
      className="flex h-full min-w-0 flex-col"
      title={t(`cognee.${kind}ConfigTitle`)}
      subtitle={t(`cognee.${kind}ConfigSubtitle`)}
    >
      <form className="flex flex-1 flex-col gap-4" onSubmit={(event) => {
        event.preventDefault();
        if (hasChanges && validTimeout && !saveMutation.isPending) saveMutation.mutate(form);
      }}>
        <fieldset className="space-y-1">
          <legend className="text-xs font-medium text-muted">{t("cognee.source")}</legend>
          <div className="flex gap-1.5 flex-wrap">
            {(["auto", "model", "custom"] as CogneeModelSource[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => update({ source: s })}
                className={cn(
                  "px-3 py-1.5 text-xs font-medium rounded-md border transition-[color,background-color,border-color,box-shadow,opacity]",
                  form.source === s
                    ? "bg-accent text-white border-accent"
                    : "bg-elevated text-muted border-border hover:bg-hover",
                )}
              >
                {t(`cognee.source_${s}`)}
              </button>
            ))}
          </div>
          <span className="block text-xs text-muted opacity-70 pt-1">{sourceDesc[form.source]}</span>
        </fieldset>

        {(form.source === "model" || kind === "chat") && <div className={cn("grid grid-cols-1 gap-3", kind === "chat" && form.source === "model" && "sm:grid-cols-2")}>
          {form.source === "model" && (
            <Field label={t("cognee.selectModel")}>
              <ModelSelect
                modelType={kind}
                label={t("cognee.selectModel")}
                value={form.model_id}
                showDefaultWhenEmpty={false}
                placeholder={t("cognee.selectModelPlaceholder")}
                onChange={(id) => update({ model_id: id })}
              />
              {kind === "chat" && selectedModel?.supports_reasoning && (
                <span className="block text-xs text-warn pt-1">
                  {t("cognee.reasoningModelHint")}
                </span>
              )}
            </Field>
          )}
          {kind === "chat" && "reasoning_effort" in form && (
            <Field label={t("cognee.reasoningEffort")} desc={t("cognee.reasoningEffortHint")}>
              <Select
                className="w-full"
                aria-label={t("cognee.reasoningEffort")}
                value={form.reasoning_effort ?? ""}
                onChange={(e) => update({ reasoning_effort: e.target.value as CogneeReasoningEffort })}
              >
                {(config?.options?.reasoning_efforts ?? []).map((effort) => (
                  <option key={effort || "auto"} value={effort}>
                    {t(`cognee.reasoning_${effort || "auto"}`)}
                  </option>
                ))}
              </Select>
            </Field>
          )}
        </div>}

        {form.source === "custom" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t("cognee.provider")}>
              <Select
                className="w-full"
                value={form.provider || providers[0]}
                onChange={(e) => update({ provider: e.target.value })}
              >
                {providers.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </Select>
            </Field>
            <Field label={t("cognee.model")} desc={t("cognee.modelDesc")}>
              <Input
                value={form.model}
                placeholder={kind === "chat" ? "openai/gpt-4o-mini" : "openai/text-embedding-3-large"}
                onChange={(e) => update({ model: e.target.value })}
              />
            </Field>
            <Field label={t("cognee.endpoint")}>
              <Input
                value={form.endpoint}
                placeholder="https://api.example.com/v1"
                onChange={(e) => update({ endpoint: e.target.value })}
              />
            </Field>
            <Field label={t("cognee.apiKey")}>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => update({ api_key: e.target.value })}
              />
            </Field>
            {kind === "chat" && "api_version" in form && (
              <Field label={t("cognee.apiVersion")}>
                <Input
                  value={(form as CogneeChatModelConfig).api_version}
                  onChange={(e) => update({ api_version: e.target.value })}
                />
              </Field>
            )}
            {kind === "embedding" && "dimensions" in form && (
              <Field label={t("cognee.dimensions")}>
                <Input
                  type="number"
                  value={(form as CogneeEmbeddingModelConfig).dimensions || ""}
                  onChange={(e) => update({ dimensions: parseInt(e.target.value, 10) || 0 })}
                />
              </Field>
            )}
          </div>
        )}

        {kind === "chat" && <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-3">
          {"extra_args" in form && <Field label={t("cognee.requestTimeout")} desc={t("cognee.requestTimeoutHint")}>
            <Input type="number" min={0} step="any" aria-label={t("cognee.requestTimeout")}
              aria-invalid={!validTimeout}
              placeholder={t("cognee.requestTimeoutInherited")}
              value={typeof requestTimeout === "number" ? requestTimeout : ""}
              onChange={(event) => {
                const extra_args = { ...form.extra_args };
                if (event.target.value === "") delete extra_args.timeout;
                else extra_args.timeout = event.target.valueAsNumber;
                update({ extra_args });
              }} />
            {!validTimeout && <span role="alert" className="text-xs text-danger">{t("cognee.requestTimeoutInvalid")}</span>}
          </Field>}

          {"instructor_mode" in form && (
            <>
              <Field label={t("cognee.instructorModeLabel")} desc={t("cognee.instructorModeHint")}>
                <Select
                  className="w-full"
                  value={form.instructor_mode}
                  onChange={(e) => update({ instructor_mode: e.target.value })}
                >
                  <option value="" title={t("cognee.instructorModeAuto")}>{t("cognee.instructorModeAutoShort")}</option>
                  {(config?.options?.instructor_modes ?? []).filter(Boolean).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </Select>
              </Field>
              <Field label={t("cognee.maxCompletionTokens")} desc={t("cognee.maxCompletionTokensHint")}>
                <Input
                  type="number"
                  min={0}
                  step={1}
                  placeholder={t("cognee.maxCompletionTokensPlaceholder")}
                  value={form.max_completion_tokens || ""}
                  onChange={(e) => update({ max_completion_tokens: parseInt(e.target.value, 10) || 0 })}
                />
              </Field>
            </>
          )}
          {resolved?.request_timeout_seconds !== undefined && <span className="text-xs text-accent sm:col-span-3">
            {t("cognee.requestTimeoutActive", { seconds: resolved.request_timeout_seconds })}
          </span>}
        </div>}

        <div className="mt-auto flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <Button
            variant="primary"
            type="submit"
            disabled={!hasChanges || !validTimeout}
            loading={saveMutation.isPending}
            className={cn(saved && !hasChanges && "!bg-ok")}
          >
            {saved && !hasChanges ? <Check size={14} /> : <Save size={14} />}
            {saved && !hasChanges ? ta("actions.saved") : saveMutation.isPending ? ta("actions.saving") : ta("actions.save")}
          </Button>
          <Button
            variant="secondary"
            disabled={!hasChanges || saveMutation.isPending}
            onClick={() => { setDraft(null); saveMutation.reset(); }}
          >
            <RotateCcw size={14} /> {ta("actions.reset")}
          </Button>
          {saveMutation.isError && (
            <p role="alert" className="w-full break-words text-sm text-danger">{apiErrorMessage(saveMutation.error, t("cognee.saveFailed"))}</p>
          )}
          <p className="w-full text-xs leading-relaxed text-muted">{t("cognee.hotApplyNote")}</p>
        </div>
      </form>
    </Card>
  );
}
