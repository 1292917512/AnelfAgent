import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Save, RotateCcw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { AsyncState } from "@/components/common/AsyncState";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { AppField, type FieldMeta } from "./AppField";
import type { ConfigValues } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { apiErrorMessage } from "@/lib/api/client";
import { useDraft } from "@/hooks/useDraft";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Modal";

export interface ConfigFormPanelProps {
  title: string;
  subtitle?: string;
  fields: FieldMeta[];
  queryKey: string;
  fetchFn: () => Promise<ConfigValues>;
  saveFn: (data: ConfigValues) => Promise<unknown>;
  extraInvalidateKeys?: string[];
  note?: string;
  advancedLabel?: string;
  confirmBooleanChanges?: boolean;
}

export function ConfigFormPanel({ title, subtitle, fields, queryKey, fetchFn, saveFn, extraInvalidateKeys, note, advancedLabel, confirmBooleanChanges = false }: ConfigFormPanelProps) {
  const { t } = useTranslation(["common", "appconfig"]);
  const client = useQueryClient();
  const query = useQuery({ queryKey: [queryKey], queryFn: fetchFn, throwOnError: false });
  const draft = useDraft(query.data ?? {});
  const [saved, triggerSaved] = useCopyFeedback(2000);
  const [pendingToggle, setPendingToggle] = useState<{ field: FieldMeta; value: boolean } | null>(null);
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
  const changeField = (field: FieldMeta, value: unknown) => {
    if (confirmBooleanChanges && field.type === "bool" && typeof value === "boolean") {
      setPendingToggle({ field, value });
    } else draft.update(field.key, value);
  };
  return (
    <Card title={title} subtitle={subtitle}>
      <AsyncState pending={query.isPending} error={!query.data ? query.error : undefined} retry={() => void query.refetch()}>
        <form onSubmit={(event) => { event.preventDefault(); if (draft.dirty && !mutation.isPending) mutation.mutate(draft.patch); }}>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {fields.filter((field) => !field.advanced).map((field) => <AppField key={field.key} meta={field} value={draft.values[field.key]} onChange={(value) => changeField(field, value)} />)}
          </div>
          {fields.some((field) => field.advanced) && <details className="mt-4 rounded-xl border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium text-heading">{advancedLabel}</summary>
            <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
              {fields.filter((field) => field.advanced).map((field) => <AppField key={field.key} meta={field} value={draft.values[field.key]} onChange={(value) => changeField(field, value)} />)}
            </div>
          </details>}
          <div className="sticky bottom-0 z-10 mt-5 flex flex-wrap items-center gap-3 border-t border-border bg-card py-4">
            <Button type="submit" variant="primary" disabled={!draft.dirty} loading={mutation.isPending}>
              {saved && !draft.dirty ? <Check size={15} /> : <Save size={15} />}
              {saved && !draft.dirty ? t("actions.saved", { ns: "appconfig" }) : t("save")}
            </Button>
            <Button onClick={draft.reset} disabled={!draft.dirty || mutation.isPending}><RotateCcw size={14} />{t("reset")}</Button>
            {draft.dirty && <span className="text-xs text-warn">{t("unsavedChanges")}</span>}
            {mutation.error && <p role="alert" className="w-full text-sm text-danger">{apiErrorMessage(mutation.error, t("requestFailed"))}</p>}
            {note && <p className="text-xs text-muted">{note}</p>}
          </div>
        </form>
      </AsyncState>
      <ConfirmDialog open={pendingToggle !== null} onClose={() => setPendingToggle(null)}
        title={t(pendingToggle?.value ? "toggleConfirm.enable" : "toggleConfirm.disable", { ns: "appconfig", name: pendingToggle?.field.label })}
        message={<div className="space-y-3">
          {pendingToggle?.field.desc && <p>{pendingToggle.field.desc}</p>}
          <p className="text-muted">{t("toggleConfirm.draftNote", { ns: "appconfig" })}</p>
          {note && <p className="text-muted">{note}</p>}
        </div>}
        onConfirm={() => {
          if (pendingToggle && fields.some((field) => field.key === pendingToggle.field.key && !field.read_only)) {
            draft.update(pendingToggle.field.key, pendingToggle.value);
          }
          setPendingToggle(null);
        }} />
    </Card>
  );
}
