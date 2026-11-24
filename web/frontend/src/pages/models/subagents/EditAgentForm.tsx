import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { apiErrorMessage, subAgentsApi } from "@/lib/api";
import type { JsonObject, SubAgentProfile } from "@/lib/types";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Button, Input, Textarea } from "@/components/ui";
import { useProfileWrite } from "./useProfileWrite";

const tagsOf = (value: string) => [...new Set(value.trim().split(/[,，\s]+/).filter(Boolean))];
const isObject = (value: unknown): value is JsonObject => value !== null && typeof value === "object" && !Array.isArray(value);

export function EditAgentForm({ profile, onSaved, onCancel }: {
  profile: SubAgentProfile; onSaved: () => void; onCancel: () => void;
}) {
  const { t } = useTranslation("models");
  const id = useId();
  const form = useRef<HTMLFormElement>(null);
  const [base] = useState(profile);
  const [draft, setDraft] = useState(() => ({
    model_id: base.models[0] ?? "", description: base.description, instructions: base.instructions,
    tool_tags: base.tool_tags.join(", "), blocked_tools: base.blocked_tools.join(", "),
    schema: base.output_schema ? JSON.stringify(base.output_schema, null, 2) : "",
  }));
  const [open, setOpen] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<keyof typeof draft, string>>>({});
  type Update = Parameters<typeof subAgentsApi.update>[1];
  const save = useProfileWrite((update: Update) => subAgentsApi.update(base.name, update), onSaved);
  function change(key: keyof typeof draft, value: string) {
    setDraft((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: undefined }));
  }
  function submit() {
    const invalid: typeof errors = {};
    const tags = tagsOf(draft.tool_tags);
    const blocked = tagsOf(draft.blocked_tools);
    if ([...draft.instructions.trim()].length > 4000) invalid.instructions = t("subagents.fieldLimit", { count: 4000 });
    if (tags.length > 12) invalid.tool_tags = t("subagents.tagLimit", { count: 12 });
    if (blocked.length > 24) invalid.blocked_tools = t("subagents.tagLimit", { count: 24 });
    let schema: JsonObject | null = null;
    if (draft.schema.trim()) {
      try {
        const parsed: unknown = JSON.parse(draft.schema);
        if (isObject(parsed)) {
          schema = parsed;
          if ([...JSON.stringify(parsed)].length > 4000) invalid.schema = t("subagents.fieldLimit", { count: 4000 });
        } else invalid.schema = t("subagents.schemaInvalid");
      } catch { invalid.schema = t("subagents.schemaInvalid"); }
    }
    setErrors(invalid);
    if (Object.keys(invalid).length) {
      setOpen(true);
      requestAnimationFrame(() => form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    const update: Update = {};
    if (draft.model_id && draft.model_id !== base.models[0]) update.model_id = draft.model_id;
    if (draft.description !== base.description) update.description = draft.description;
    if (draft.instructions !== base.instructions) update.instructions = draft.instructions;
    if (JSON.stringify(tags) !== JSON.stringify(base.tool_tags)) update.tool_tags = tags;
    if (JSON.stringify(blocked) !== JSON.stringify(base.blocked_tools)) update.blocked_tools = blocked;
    if (JSON.stringify(schema) !== JSON.stringify(base.output_schema)) update.output_schema = schema;
    if (!Object.keys(update).length) { onCancel(); return; }
    save.submit(update);
  }
  function field(key: keyof typeof draft, labelKey: string) {
    const label = t("subagents." + labelKey);
    return { value: draft[key], "aria-label": label, placeholder: label, "aria-invalid": !!errors[key],
      "aria-describedby": errors[key] ? id + key : undefined,
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => change(key, e.target.value) };
  }
  function error(key: keyof typeof draft) {
    return errors[key] && <p id={id + key} className="text-xs text-danger">{errors[key]}</p>;
  }
  return <form ref={form} aria-label={t("subagents.edit") + " " + base.name}
    onSubmit={(e) => { e.preventDefault(); submit(); }}>
    <fieldset disabled={save.pending} className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <ModelSelect allowPin={false} value={draft.model_id} onChange={(value) => change("model_id", value)} className="w-48" />
        <Input className="!w-56 max-w-full" {...field("description", "descPlaceholder")} />
        <Button size="sm" aria-expanded={open} onClick={() => setOpen(!open)}><ChevronDown size={14} />{t("subagents.facetsToggle")}</Button>
      </div>
      {open && <div className="space-y-2 rounded-md border border-border bg-secondary/30 p-2.5">
        <p className="text-xs text-muted">{t("subagents.facetsHint")}</p>
        <Textarea rows={3} {...field("instructions", "instructionsPlaceholder")} />{error("instructions")}
        <Input {...field("tool_tags", "toolTagsPlaceholder")} />{error("tool_tags")}
        <Input {...field("blocked_tools", "blockedToolsPlaceholder")} />{error("blocked_tools")}
        <Textarea rows={4} className="font-mono" {...field("schema", "schemaPlaceholder")} />{error("schema")}
        <p className="text-xs text-muted">{t("subagents.schemaHint")}</p>
      </div>}
      {save.error && <p role="alert" className="text-sm text-danger">{apiErrorMessage(save.error, t("common:requestFailed"))}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={onCancel}>{t("common:cancel")}</Button>
        <Button size="sm" type="submit" variant="primary" loading={save.pending}>{save.error ? t("common:retry") : t("subagents.save")}</Button>
      </div>
    </fieldset>
  </form>;
}
