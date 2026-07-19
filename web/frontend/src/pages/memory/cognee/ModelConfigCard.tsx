import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Save, RotateCcw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { memoryApi, modelsApi } from "@/lib/api";
import type {
  CogneeChatModelConfig,
  CogneeEmbeddingModelConfig,
  CogneeModelSource,
} from "@/lib/types";

type ModelKind = "chat" | "embedding";
type KindConfig = CogneeChatModelConfig | CogneeEmbeddingModelConfig;

const CHAT_PROVIDERS = ["openai", "anthropic", "gemini", "ollama", "custom", "azure", "mistral", "bedrock"];
const EMBED_PROVIDERS = ["openai", "ollama", "azure", "fastembed"];
const INSTRUCTOR_MODES = ["json_mode", "json_schema_mode", "tools", "anthropic_tools", "mistral_tools"];

const inputCls =
  "w-full text-sm bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-[var(--text-strong)] focus:outline-none focus:border-[var(--accent)] transition-colors";

function Field({ label, desc, children }: { label: string; desc?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-[var(--muted)]">{label}</span>
      {children}
      {desc && <span className="block text-xs text-[var(--muted)] opacity-70">{desc}</span>}
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
  const { data: priorities } = useQuery({
    queryKey: ["modelPriorities"],
    queryFn: () => modelsApi.priorities().then((r) => r.data),
  });

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
        <p className="text-sm text-[var(--muted)]">{t("common:loading")}</p>
      </Card>
    );
  }

  const update = (patch: Partial<KindConfig>) => {
    setForm((prev) => (prev ? { ...prev, ...patch } : prev));
    setHasChanges(true);
  };

  const modelOptions = (priorities?.[kind] || []).map((item) => ({
    id: item.id,
    label: `${item.id}（${item.provider_name || item.provider_id} · ${item.api_type} · ${item.model}）`,
  }));
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
          <div className="flex gap-1.5">
            {(["auto", "model", "custom"] as CogneeModelSource[]).map((s) => (
              <button
                key={s}
                onClick={() => update({ source: s })}
                className={cn(
                  "px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border transition-all",
                  form.source === s
                    ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                    : "bg-[var(--bg-elevated)] text-[var(--muted)] border-[var(--border)] hover:bg-[var(--bg-hover)]",
                )}
              >
                {t(`cognee.source_${s}`)}
              </button>
            ))}
          </div>
          <span className="block text-xs text-[var(--muted)] opacity-70 pt-1">{sourceDesc[form.source]}</span>
        </Field>

        {form.source === "model" && (
          <Field label={t("cognee.selectModel")}>
            <select
              className={inputCls}
              value={form.model_id}
              onChange={(e) => update({ model_id: e.target.value })}
            >
              <option value="">{t("cognee.selectModelPlaceholder")}</option>
              {modelOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>{opt.label}</option>
              ))}
            </select>
          </Field>
        )}

        {form.source === "custom" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t("cognee.provider")}>
              <select
                className={inputCls}
                value={form.provider || providers[0]}
                onChange={(e) => update({ provider: e.target.value })}
              >
                {providers.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </Field>
            <Field label={t("cognee.model")} desc={t("cognee.modelDesc")}>
              <input
                className={inputCls}
                value={form.model}
                placeholder={kind === "chat" ? "openai/gpt-4o-mini" : "openai/text-embedding-3-large"}
                onChange={(e) => update({ model: e.target.value })}
              />
            </Field>
            <Field label={t("cognee.endpoint")}>
              <input
                className={inputCls}
                value={form.endpoint}
                placeholder="https://api.example.com/v1"
                onChange={(e) => update({ endpoint: e.target.value })}
              />
            </Field>
            <Field label={t("cognee.apiKey")}>
              <input
                type="password"
                className={inputCls}
                value={form.api_key}
                onChange={(e) => update({ api_key: e.target.value })}
              />
            </Field>
            {kind === "chat" && "api_version" in form && (
              <Field label={t("cognee.apiVersion")}>
                <input
                  className={inputCls}
                  value={(form as CogneeChatModelConfig).api_version}
                  onChange={(e) => update({ api_version: e.target.value })}
                />
              </Field>
            )}
            {kind === "embedding" && "dimensions" in form && (
              <Field label={t("cognee.dimensions")}>
                <input
                  type="number"
                  className={inputCls}
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
              <select
                className={inputCls}
                value={(form as CogneeChatModelConfig).instructor_mode}
                onChange={(e) => update({ instructor_mode: e.target.value })}
              >
                <option value="">{t("cognee.instructorModeAuto")}</option>
                {INSTRUCTOR_MODES.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label={t("cognee.maxCompletionTokens")}>
              <input
                type="number"
                className={inputCls}
                value={(form as CogneeChatModelConfig).max_completion_tokens || ""}
                onChange={(e) => update({ max_completion_tokens: parseInt(e.target.value, 10) || 0 })}
              />
            </Field>
          </div>
        )}

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={() => hasChanges && saveMutation.mutate(form)}
            disabled={!hasChanges || saveMutation.isPending}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-[var(--radius-md)] transition-all",
              saved
                ? "bg-[var(--ok)] text-white border border-[var(--ok)]"
                : "bg-[var(--accent)] text-white border border-[var(--accent)] hover:opacity-90",
              (!hasChanges || saveMutation.isPending) && "opacity-50 cursor-not-allowed",
            )}
          >
            {saved ? <Check size={14} /> : <Save size={14} />}
            {saved ? ta("actions.saved") : saveMutation.isPending ? ta("actions.saving") : ta("actions.save")}
          </button>
          <button
            onClick={() => { if (config) { setForm(config[kind] as KindConfig); setHasChanges(false); } }}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
          >
            <RotateCcw size={14} /> {ta("actions.reset")}
          </button>
          {saveMutation.isError && (
            <p className="text-xs text-[var(--danger)]">{t("cognee.saveFailed")}</p>
          )}
          <p className="text-xs text-[var(--muted)]">{t("cognee.hotApplyNote")}</p>
        </div>
      </div>
    </Card>
  );
}
