import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Save, RotateCcw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { AsyncState } from "@/components/common/AsyncState";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { AppField, type FieldMeta } from "./AppField";
import type { ConfigValues } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { useDraft } from "@/hooks/useDraft";
import { Button } from "@/components/ui/Button";

export interface ConfigFormPanelProps {
  title: string;
  subtitle?: string;
  fields: FieldMeta[];
  queryKey: string;
  fetchFn: () => Promise<ConfigValues>;
  saveFn: (data: ConfigValues) => Promise<unknown>;
  extraInvalidateKeys?: string[];
  note?: string;
}

export function ConfigFormPanel({ title, subtitle, fields, queryKey, fetchFn, saveFn, extraInvalidateKeys, note }: ConfigFormPanelProps) {
  const { t } = useTranslation(["common", "appconfig"]);
  const client = useQueryClient();
  const query = useQuery({ queryKey: [queryKey], queryFn: fetchFn, throwOnError: false });
  const draft = useDraft(query.data ?? {});
  const [saved, triggerSaved] = useCopyFeedback(2000);
  useUnsavedChanges(draft.dirty);
  const mutation = useMutation({
    mutationFn: saveFn,
    onSuccess: (_data, submitted) => {
      client.setQueryData<ConfigValues>([queryKey], (current) => ({ ...current, ...submitted }));
      draft.acknowledge(submitted);
      void client.invalidateQueries({ queryKey: [queryKey] });
      extraInvalidateKeys?.forEach((key) => void client.invalidateQueries({ queryKey: [key] }));
      triggerSaved();
    },
  });
  return (
    <Card title={title} subtitle={subtitle}>
      <AsyncState pending={query.isPending} error={!query.data ? query.error : undefined} retry={() => void query.refetch()}>
        <form onSubmit={(event) => { event.preventDefault(); if (draft.dirty && !mutation.isPending) mutation.mutate(draft.patch); }}>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {fields.map((field) => <AppField key={field.key} meta={field} value={draft.values[field.key]} onChange={(value) => draft.update(field.key, value)} />)}
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4">
            <Button type="submit" variant="primary" disabled={!draft.dirty} loading={mutation.isPending}>
              {saved && !draft.dirty ? <Check size={15} /> : <Save size={15} />}
              {saved && !draft.dirty ? t("actions.saved", { ns: "appconfig" }) : t("save")}
            </Button>
            <Button onClick={draft.reset} disabled={!draft.dirty || mutation.isPending}><RotateCcw size={14} />{t("reset")}</Button>
            {draft.dirty && <span className="text-xs text-warn">{t("unsavedChanges")}</span>}
            {note && <p className="text-xs text-muted">{note}</p>}
          </div>
        </form>
      </AsyncState>
    </Card>
  );
}
