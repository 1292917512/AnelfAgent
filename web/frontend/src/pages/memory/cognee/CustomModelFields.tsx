import { useTranslation } from "react-i18next";
import { Input, Select } from "@/components/ui";
import type { CogneeChatModelConfig, CogneeEmbeddingModelConfig } from "@/lib/types";
import { ModelConfigField as Field } from "./ModelConfigField";

type ModelConfig = CogneeChatModelConfig | CogneeEmbeddingModelConfig;

export function CustomModelFields({ kind, form, providers, update }: {
  kind: "chat" | "embedding"; form: ModelConfig; providers: string[];
  update: (patch: Partial<ModelConfig>) => void;
}) {
  const { t } = useTranslation("memory");
  return (
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
            value={form.api_version}
            onChange={(e) => update({ api_version: e.target.value })}
          />
        </Field>
      )}
      {kind === "embedding" && "dimensions" in form && (
        <Field label={t("cognee.dimensions")}>
          <Input
            type="number"
            value={form.dimensions || ""}
            onChange={(e) => update({ dimensions: parseInt(e.target.value, 10) || 0 })}
          />
        </Field>
      )}
    </div>
  );
}
