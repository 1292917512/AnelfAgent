import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { skillsApi } from "@/lib/api";
import type { SkillItem } from "@/lib/types";
import { Button, Input, Textarea } from "@/components/ui";
import { Modal, ConfirmDialog } from "@/components/ui/Modal";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";

export function SkillEditor({ skill, onClose, onSaved }: {
  skill: SkillItem | null; onClose: () => void; onSaved: (name: string) => void;
}) {
  const { t } = useTranslation("skills");
  const initial = { name: skill?.name ?? "", description: skill?.description ?? "", content: skill?.content ?? "", triggers: "" };
  const [draft, setDraft] = useState(initial);
  const dirty = Object.keys(initial).some((key) => draft[key as keyof typeof draft] !== initial[key as keyof typeof initial]);
  const save = useMutation({
    mutationFn: async () => {
      if (skill) await skillsApi.update(skill.name, { description: draft.description, content: draft.content });
      else return (await skillsApi.create({ name: draft.name.trim(), description: draft.description, content: draft.content,
        trigger_patterns: draft.triggers.split(",").map((value) => value.trim()).filter(Boolean) })).data.name;
      return skill.name;
    },
    onSuccess: onSaved,
  });
  const guard = useDiscardChanges(dirty, onClose, save.isPending);
  const update = (key: keyof typeof draft, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  return <>
    <Modal open onClose={guard.requestClose} title={skill ? `${t("edit")} · ${skill.name}` : t("createNew")} width="max-w-3xl" dismissible={!save.isPending}
      footer={<><Button onClick={guard.requestClose} disabled={save.isPending}>{t("cancel")}</Button>
        <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || !draft.name.trim()}>{t("save")}</Button></>}>
      <fieldset disabled={save.isPending} className="space-y-4">
        {!skill && <label className="block space-y-2 text-sm"><span>{t("newSkillName")}</span><Input value={draft.name} onChange={(event) => update("name", event.target.value)} /></label>}
        <label className="block space-y-2 text-sm"><span>{t("description")}</span><Input value={draft.description} onChange={(event) => update("description", event.target.value)} /></label>
        <label className="block space-y-2 text-sm"><span>{t("content")}</span><Textarea value={draft.content} onChange={(event) => update("content", event.target.value)} rows={16} className="font-mono text-sm leading-6" /></label>
        {!skill && <label className="block space-y-2 text-sm"><span>{t("triggerPatterns")}</span><Input value={draft.triggers} onChange={(event) => update("triggers", event.target.value)} /></label>}
      </fieldset>
      {save.error && <div className="mt-4"><QueryError compact error={save.error} /></div>}
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
