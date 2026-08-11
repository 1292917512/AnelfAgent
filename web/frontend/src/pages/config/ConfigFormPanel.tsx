import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Check, Save, RotateCcw } from "lucide-react";
import { AppField, type FieldMeta } from "@/pages/config/AppField";
import type { ConfigValues } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";

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

export function ConfigFormPanel({
  title,
  subtitle,
  fields,
  queryKey,
  fetchFn,
  saveFn,
  extraInvalidateKeys,
  note,
}: ConfigFormPanelProps) {
  const { t: tc } = useTranslation("common");
  const { t: ta } = useTranslation("appconfig");
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ConfigValues>({});
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [saved, triggerSaved] = useCopyFeedback(2000);

  const { data, isLoading } = useQuery({
    queryKey: [queryKey],
    queryFn: fetchFn,
  });

  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const saveMutation = useMutation({
    // 仅提交变更过的字段，避免全量回写
    mutationFn: (values: ConfigValues) =>
      saveFn(Object.fromEntries([...dirtyKeys].map((k) => [k, values[k]]))),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [queryKey] });
      extraInvalidateKeys?.forEach((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      );
      setDirtyKeys(new Set());
      triggerSaved();
    },
  });

  const handleChange = (key: string, value: unknown) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirtyKeys((prev) => new Set(prev).add(key));
  };

  const handleSave = () => {
    if (dirtyKeys.size === 0) return;
    saveMutation.mutate(form);
  };

  const handleReset = () => {
    if (data) {
      setForm(data);
      setDirtyKeys(new Set());
    }
  };

  if (isLoading) return <Card title={title}><p className="text-sm text-muted">{tc("loading")}</p></Card>;

  return (
    <Card title={title} subtitle={subtitle}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {fields.map((field) => (
          <AppField key={field.key} meta={field} value={form[field.key]} onChange={(v) => handleChange(field.key, v)} />
        ))}
      </div>
      <div className="flex items-center gap-3 pt-3">
        <button
          onClick={handleSave}
          disabled={dirtyKeys.size === 0 || saveMutation.isPending}
          className={cn(
            "flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-md transition-all",
            saved
              ? "bg-ok text-white border border-[var(--ok)]"
              : "bg-accent text-white border border-accent hover:opacity-90",
            (dirtyKeys.size === 0 || saveMutation.isPending) && "opacity-50 cursor-not-allowed",
          )}
        >
          {saved ? <Check size={14} /> : <Save size={14} />}
          {saved ? ta("actions.saved") : saveMutation.isPending ? ta("actions.saving") : ta("actions.save")}
        </button>
        <button
          onClick={handleReset}
          className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
        >
          <RotateCcw size={14} /> {ta("actions.reset")}
        </button>
        {note && <p className="text-xs text-muted">{note}</p>}
      </div>
    </Card>
  );
}
