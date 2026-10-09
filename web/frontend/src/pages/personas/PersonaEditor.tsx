import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save, RotateCcw } from "lucide-react";
import { personasApi } from "@/lib/api";
import type { PersonaData } from "@/lib/types";
import { useDraft } from "@/hooks/useDraft";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { AsyncState } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { Button, Input, Textarea } from "@/components/ui";

export function parsePersonality(text: string): string[] {
  const value: unknown = JSON.parse(text);
  if (!Array.isArray(value) || !value.every((item): item is string => typeof item === "string")) {
    throw new Error("Personality must be an array of strings");
  }
  return value;
}

export function PersonaEditor({ personaKey }: { personaKey: string }) {
  const query = useQuery({ queryKey: ["personaConfig", personaKey],
    queryFn: () => personasApi.get(personaKey).then((r) => r.data), throwOnError: false });
  return <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
    {query.data && <Editor personaKey={personaKey} data={query.data} />}
  </AsyncState>;
}

function Editor({ personaKey, data }: { personaKey: string; data: PersonaData }) {
  const { t } = useTranslation(["personas", "common"]);
  const client = useQueryClient();
  const draft = useDraft({
    name: data.name ?? "", description: String(data.description ?? ""),
    personality: JSON.stringify(data.personality ?? [], null, 2),
  });
  const [invalid, setInvalid] = useState(false);
  useUnsavedChanges(draft.dirty);
  const save = useMutation({
    mutationFn: (submitted: typeof draft.values) => personasApi.save(personaKey, {
      ...data, ...submitted, personality: parsePersonality(submitted.personality),
    }),
    onSuccess: (_response, submitted) => {
      client.setQueryData<PersonaData>(["personaConfig", personaKey], {
        ...data, ...submitted, personality: parsePersonality(submitted.personality),
      });
      draft.acknowledge(submitted);
      void client.invalidateQueries({ queryKey: ["personas"] });
    },
  });
  const submit = () => {
    try { parsePersonality(draft.values.personality); }
    catch { setInvalid(true); return; }
    setInvalid(false);
    save.mutate(draft.values);
  };
  return <Card title={`${t("editPrefix")} · ${personaKey}`}>
    <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); if (draft.dirty && !save.isPending) submit(); }}>
      <label className="block space-y-2 text-sm"><span className="text-muted">{t("nameLabel")}</span>
        <Input className="w-full" value={draft.values.name} onChange={(event) => draft.update("name", event.target.value)} />
      </label>
      <label className="block space-y-2 text-sm"><span className="text-muted">{t("descriptionLabel")}</span>
        <Input className="w-full" value={draft.values.description} onChange={(event) => draft.update("description", event.target.value)} />
      </label>
      <label className="block space-y-2 text-sm"><span className="text-muted">{t("personalityLabel")}</span>
        <Textarea rows={14} className="w-full font-mono" aria-invalid={invalid} value={draft.values.personality}
          onChange={(event) => { draft.update("personality", event.target.value); setInvalid(false); }} />
      </label>
      {invalid && <p role="alert" className="text-sm text-danger">{t("invalidPersonality")}</p>}
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <Button variant="primary" type="submit" disabled={!draft.dirty} loading={save.isPending}><Save size={14} />{t("common:save")}</Button>
        <Button disabled={!draft.dirty || save.isPending} onClick={() => { draft.reset(); setInvalid(false); }}><RotateCcw size={14} />{t("common:reset")}</Button>
        <span role="status" className="text-xs text-muted">{draft.dirty ? t("common:unsavedChanges") : save.isSuccess ? t("common:saved") : ""}</span>
      </div>
    </form>
  </Card>;
}
