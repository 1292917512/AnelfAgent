import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { memoryApi } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api/client";
import type { EntityProfile } from "@/lib/types";

export function ProfileEditor({ entity, onDeleted }: { entity: EntityProfile; onDeleted: () => void }) {
  const { t } = useTranslation("memory");
  const client = useQueryClient();
  const [draft, setDraft] = useState<{ content: string; revision: string } | null>(null);
  const [pendingSync, setPendingSync] = useState(false);
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["entities"] });
    client.invalidateQueries({ queryKey: ["profileTimeline"] });
    client.invalidateQueries({ queryKey: ["ltmPaginated"] });
    client.invalidateQueries({ queryKey: ["ltmSearch"] });
    client.invalidateQueries({ queryKey: ["memoryTimeline"] });
  };
  const save = useMutation({
    mutationFn: (value: { content: string; revision: string }) => memoryApi.entities.save(entity.scope_type, entity.scope_id, value.content, value.revision),
    onSuccess: ({ data }) => { setDraft(null); setPendingSync(!data.synced); refresh(); },
    onError: refresh,
  });
  const remove = useMutation({
    mutationFn: () => memoryApi.entities.delete(entity.scope_type, entity.scope_id, entity.revision),
    onSuccess: () => { refresh(); onDeleted(); },
  });
  return <div className="space-y-3">
    <div className="flex justify-end gap-3 text-xs">
      <button className="text-accent" disabled={save.isPending || remove.isPending} onClick={() => {
        save.reset(); remove.reset(); setDraft(draft ? null : { content: entity.personality, revision: entity.revision });
      }}>{t(draft ? "common:cancel" : "common:edit")}</button>
      <button className="text-danger" disabled={save.isPending || remove.isPending} onClick={() => remove.mutate()}>{t("common:delete")}</button>
    </div>
    {pendingSync && <p role="status" className="text-sm text-warning">{t("profileSavedPending")}</p>}
    {(save.error || remove.error) && <p role="alert" className="text-sm text-danger">{apiErrorMessage(save.error || remove.error, t("profileSaveFailed"))}</p>}
    {draft ? <>
      {draft.revision !== entity.revision && <p className="text-sm text-warning">{t("profileDraftStale")}</p>}
      <textarea aria-label={t("entityProfile")} value={draft.content} rows={14}
        onChange={event => setDraft({ ...draft, content: event.target.value })}
        className="w-full bg-card border border-input rounded-md px-3 py-2 text-sm outline-none focus:border-ring resize-y font-mono" />
      <button className="px-3 py-1.5 text-xs rounded-md bg-accent text-primary-foreground disabled:opacity-40"
        disabled={save.isPending || !draft.content.trim() || draft.revision !== entity.revision}
        onClick={() => save.mutate(draft)}>{t("common:save")}</button>
    </> : <pre className="max-h-96 overflow-y-auto text-sm whitespace-pre-wrap break-words font-sans leading-relaxed">{entity.personality || t("noEntityProfile")}</pre>}
  </div>;
}
