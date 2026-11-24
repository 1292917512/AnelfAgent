import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { apiErrorMessage, subAgentsApi } from "@/lib/api";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Button, Input } from "@/components/ui";
import { useProfileWrite } from "./useProfileWrite";

const empty = { name: "", model_id: "", description: "" };

export function CreateAgentForm({ names, onCreated }: { names: string[]; onCreated: () => void }) {
  const { t } = useTranslation("models");
  const id = useId();
  const nameInput = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(empty);
  const [checked, setChecked] = useState(false);
  const create = useProfileWrite(subAgentsApi.create, () => {
    setDraft(empty);
    setChecked(false);
    onCreated();
    requestAnimationFrame(() => nameInput.current?.focus());
  });
  const name = draft.name.trim();
  const nameError = !/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(name) ? t("subagents.nameHint")
    : names.includes(name) || ["easy", "medium", "hard"].includes(name) ? t("subagents.nameTaken") : "";
  return <form aria-label={t("subagents.createTitle")} className="rounded-md border border-border bg-card p-3 md:p-4"
    onSubmit={(event) => {
      event.preventDefault();
      setChecked(true);
      if (nameError) { nameInput.current?.focus(); return; }
      if (!draft.model_id) { document.getElementById(id + "-model")?.focus(); return; }
      create.submit({ ...draft, name, description: draft.description.trim() });
    }}>
    <fieldset disabled={create.pending} className="min-w-0 space-y-2">
      <legend className="mb-2 text-sm font-medium text-heading">{t("subagents.createTitle")}</legend>
      <div className="flex flex-wrap items-start gap-2">
        <div className="w-48 max-w-full">
          <Input ref={nameInput} aria-label={t("subagents.namePlaceholder")} placeholder={t("subagents.namePlaceholder")}
            aria-invalid={checked && !!nameError} aria-describedby={id + "-nameHint"}
            value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </div>
        <div className="w-48 max-w-full">
          <ModelSelect id={id + "-model"} allowPin={false} label={t("subagents.selectModel")} value={draft.model_id}
            aria-invalid={checked && !draft.model_id} aria-describedby={checked && !draft.model_id ? id + "-missing" : undefined}
            showDefaultWhenEmpty={false} placeholder={t("subagents.selectModel")} onChange={(model_id) => setDraft({ ...draft, model_id })} />
          {checked && !draft.model_id && <p id={id + "-missing"} className="mt-1 text-xs text-danger">{t("subagents.modelRequired")}</p>}
        </div>
        <Input className="!w-56 max-w-full" aria-label={t("subagents.descPlaceholder")} placeholder={t("subagents.descPlaceholder")}
          value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        <Button type="submit" loading={create.pending}><Plus size={14} />{create.error ? t("common:retry") : t("subagents.create")}</Button>
      </div>
      <p id={id + "-nameHint"} className={checked && nameError ? "text-xs text-danger" : "text-xs text-muted"}>
        {checked && nameError ? nameError : t("subagents.nameHint")}
      </p>
      {create.error && <p role="alert" className="text-sm text-danger">{apiErrorMessage(create.error, t("common:requestFailed"))}</p>}
    </fieldset>
  </form>;
}
