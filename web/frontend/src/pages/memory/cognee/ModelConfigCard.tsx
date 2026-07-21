import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Save, RotateCcw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { memoryApi } from "@/lib/api";
import type {
  CogneeChatModelConfig,
  CogneeEmbeddingModelConfig,
  CogneeModelSource,
  CogneeReasoningEffort,
} from "@/lib/types";
import { Button, Input, Select } from "@/components/ui";
import { ModelSelect, usePriorities } from "@/components/models/ModelSelect";

type ModelKind = "chat" | "embedding";
type KindConfig = CogneeChatModelConfig | CogneeEmbeddingModelConfig;

const CHAT_PROVIDERS = ["openai", "anthropic", "gemini", "ollama", "custom", "azure", "mistral", "bedrock"];
const EMBED_PROVIDERS = ["openai", "ollama", "azure", "fastembed"];
const INSTRUCTOR_MODES = ["json_mode", "json_schema_mode", "tools", "anthropic_tools", "mistral_tools"];
const REASONING_EFFORTS: CogneeReasoningEffort[] = ["", "off", "low", "medium", "high", "max"];

function Field({ label, desc, children }: { label: string; desc?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {desc && <span className="block text-xs text-muted opacity-70">{desc}</span>}
    </label>
  );
}

export function ModelConfigCard({ kind }: { kind: ModelKind }) {
  const { t } = useTranslation("memory");
  const { t: ta } = useTranslation("appconfig");
  const queryClient = useQueryClient();
  const [form, setForm] = useState<KindConfig | null>(null);
  const [hasChanges, setHasChanges] = useState(false);
  const [saved, setSaved] = useState(false);

  const { data: config } = useQuery({
    queryKey: ["cogneeConfig"],
    queryFn: () => memoryApi.cognee.getConfig().then((r) => r.data),
  });
  const { data: priorities = {} } = usePriorities();

  useEffect(() => {
    if (config) setForm(config[kind] as KindConfig);
  }, [config, kind]);

  const saveMutation = useMutation({
    mutationFn: (values: KindConfig) =>
      memoryApi.cognee.saveConfig({ [kind]: values } as Parameters<typeof memoryApi.cognee.saveConfig>[0]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cogneeConfig"] });
      queryClient.invalidateQueries({ queryKey: ["cogneeStatus"] });
      queryClient.invalidateQueries({ queryKey: ["memoryHealth"] });
      setHasChanges(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  if (!form) {
    return (
      <Card title={t(`cognee.${kind}ConfigTitle`)}>
        <p className="text-sm text-muted">{t("common:loading")}</p>
      </Card>
    );
  }

  const update = (patch: Partial<KindConfig>) => {
    setForm((prev) => (prev ? { ...prev, ...patch } : prev));
    setHasChanges(true);
  };

  const selectedModel = (priorities[kind] || []).find((item) => item.id === form.model_id);
  const providers = kind === "chat" ? CHAT_PROVIDERS : EMBED_PROVIDERS;
  const sourceDesc: Record<CogneeModelSource, string> = {
    auto: t("cognee.sourceAutoDesc"),
    model: t("cognee.sourceModelDesc"),
    custom: t("cognee.sourceCustomDesc"),
  };

  return (
    <Card
      title={t(`cognee.${kind}ConfigTitle`)}
      subtitle={t(`cognee.${kind}ConfigSubtitle`)}
    >
      <div className="space-y-3">
        <Field label={t("cognee.source")}>
          <div className="flex gap-1.5 flex-wrap">
            {(["auto", "model", "custom"] as CogneeModelSource[]).map((s) => (
              <button
                key={s}
                onClick={() => update({ source: s })}
                className={cn(
                  "px-3 py-1.5 text-xs font-medium rounded-md border transition-all",
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
        </Field>

        {form.source === "model" && (
          <Field label={t("cognee.selectModel")}>
            <ModelSelect
              modelType={kind}
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

        {kind === "chat" && "instructor_mode" in form && form.source !== "auto" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t("cognee.instructorModeLabel")} desc={t("cognee.instructorModeHint")}>
              <Select
                className="w-full"
                value={(form as CogneeChatModelConfig).instructor_mode}
                onChange={(e) => update({ instructor_mode: e.target.value })}
              >
                <option value="">{t("cognee.instructorModeAuto")}</option>
                {INSTRUCTOR_MODES.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </Select>
            </Field>
            <Field label={t("cognee.maxCompletionTokens")}>
              <Input
                type="number"
                value={(form as CogneeChatModelConfig).max_completion_tokens || ""}
                onChange={(e) => update({ max_completion_tokens: parseInt(e.target.value, 10) || 0 })}
              />
            </Field>
            <Field label={t("cognee.reasoningEffort")} desc={t("cognee.reasoningEffortHint")}>
              <Select
                className="w-full"
                value={(form as CogneeChatModelConfig).reasoning_effort ?? ""}
                onChange={(e) => update({ reasoning_effort: e.target.value as CogneeReasoningEffort })}
              >
                {REASONING_EFFORTS.map((effort) => (
                  <option key={effort || "auto"} value={effort}>
                    {t(`cognee.reasoning_${effort || "auto"}`)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        )}

        <div className="flex items-center gap-3 pt-1 flex-wrap">
          <Button
            variant="primary"
            onClick={() => hasChanges && saveMutation.mutate(form)}
            disabled={!hasChanges}
            loading={saveMutation.isPending}
            className={cn(saved && "!bg-ok")}
          >
            {saved ? <Check size={14} /> : <Save size={14} />}
            {saved ? ta("actions.saved") : saveMutation.isPending ? ta("actions.saving") : ta("actions.save")}
          </Button>
          <Button
            variant="secondary"
            onClick={() => { if (config) { setForm(config[kind] as KindConfig); setHasChanges(false); } }}
          >
            <RotateCcw size={14} /> {ta("actions.reset")}
          </Button>
          {saveMutation.isError && (
            <p className="text-xs text-danger">{t("cognee.saveFailed")}</p>
          )}
          <p className="text-xs text-muted">{t("cognee.hotApplyNote")}</p>
        </div>
      </div>
    </Card>
  );
}
