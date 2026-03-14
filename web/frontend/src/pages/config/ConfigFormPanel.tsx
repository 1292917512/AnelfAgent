import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Check, Save, RotateCcw } from "lucide-react";
import { AppField, type FieldMeta } from "@/pages/config/AppField";

export interface ConfigFormPanelProps {
  title: string;
  subtitle?: string;
  fields: FieldMeta[];
  queryKey: string;
  fetchFn: () => Promise<Record<string, unknown>>;
  saveFn: (data: Record<string, unknown>) => Promise<unknown>;
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
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [hasChanges, setHasChanges] = useState(false);
  const [saved, setSaved] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: [queryKey],
    queryFn: fetchFn,
  });

  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => saveFn(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [queryKey] });
      extraInvalidateKeys?.forEach((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      );
      setHasChanges(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const handleChange = (key: string, value: unknown) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setHasChanges(true);
  };

  const handleSave = () => {
    if (!hasChanges) return;
    saveMutation.mutate(form);
  };

  const handleReset = () => {
    if (data) {
      setForm(data);
      setHasChanges(false);
    }
  };

  if (isLoading) return <Card title={title}><p className="text-sm text-[var(--muted)]">{tc("loading")}</p></Card>;

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
          onClick={handleReset}
          className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
        >
          <RotateCcw size={14} /> {ta("actions.reset")}
        </button>
        {note && <p className="text-xs text-[var(--muted)]">{note}</p>}
      </div>
    </Card>
  );
}
