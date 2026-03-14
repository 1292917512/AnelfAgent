import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { introspectionApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Check, Save, RotateCcw, RefreshCw } from "lucide-react";
import { AppField, type FieldMeta } from "@/pages/config/AppField";

export function IntrospectionConfigPanel() {
  const { t } = useTranslation("appconfig");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState(false);
  const [triggerState, setTriggerState] = useState<"idle" | "pending" | "ok" | "error">("idle");

  const fields: FieldMeta[] = [
    { key: "enabled", label: t("introspection.enabled"), type: "bool" },
    { key: "reflect_min_hours", label: t("introspection.reflectMinHours"), type: "float", desc: t("introspection.reflectMinHoursDesc") },
    { key: "reflect_max_hours", label: t("introspection.reflectMaxHours"), type: "float", desc: t("introspection.reflectMaxHoursDesc") },
    { key: "analysis_temperature", label: t("introspection.analysisTemperature"), type: "float", desc: t("introspection.analysisTemperatureDesc") },
    { key: "min_conversations_for_analysis", label: t("introspection.minConversations"), type: "int", desc: t("introspection.minConversationsDesc") },
  ];

  const { data, isLoading } = useQuery({
    queryKey: ["introspectionConfig"],
    queryFn: () => introspectionApi.get().then((r) => r.data),
  });

  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const mutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => introspectionApi.save(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["introspectionConfig"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const handleTrigger = async () => {
    setTriggerState("pending");
    try {
      await introspectionApi.trigger();
      setTriggerState("ok");
      setTimeout(() => setTriggerState("idle"), 3000);
    } catch {
      setTriggerState("error");
      setTimeout(() => setTriggerState("idle"), 3000);
    }
  };

  const handleChange = (key: string, value: unknown) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  if (isLoading) {
    return (
      <Card title={t("introspection.title")}>
        <p className="text-sm text-[var(--muted)]">{tc("loading")}</p>
      </Card>
    );
  }

  return (
    <Card title={t("introspection.title")} subtitle={t("introspection.subtitle")}>
      <div className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {fields.map((field) => (
            <AppField key={field.key} meta={field} value={form[field.key]} onChange={(v) => handleChange(field.key, v)} />
          ))}
        </div>

        <div className="flex items-center gap-3 pt-1 flex-wrap">
          <button
            onClick={() => mutation.mutate(form)}
            disabled={mutation.isPending}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-[var(--radius-md)] transition-all",
              saved
                ? "bg-[var(--ok)] text-white border border-[var(--ok)]"
                : "bg-[var(--accent)] text-white border border-[var(--accent)] hover:opacity-90",
            )}
          >
            {saved ? <Check size={14} /> : <Save size={14} />}
            {saved ? t("actions.saved") : mutation.isPending ? t("actions.saving") : t("actions.saveConfig")}
          </button>
          <button
            onClick={() => {
              if (data) setForm(data);
            }}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
          >
            <RotateCcw size={14} /> {t("actions.reset")}
          </button>

          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={handleTrigger}
              disabled={triggerState === "pending"}
              className={cn(
                "flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-[var(--radius-md)] border transition-all",
                triggerState === "ok"
                  ? "border-[var(--ok)] bg-[var(--ok-subtle)] text-[var(--ok)]"
                  : triggerState === "error"
                    ? "border-[var(--error)] bg-[var(--error-subtle,#fee2e2)] text-[var(--error)]"
                    : triggerState === "pending"
                      ? "border-[var(--border)] bg-[var(--secondary)] text-[var(--muted)] cursor-not-allowed"
                      : "border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white",
              )}
            >
              {triggerState === "pending" ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : triggerState === "ok" ? (
                <Check size={14} />
              ) : (
                <RefreshCw size={14} />
              )}
              {triggerState === "pending"
                ? t("introspection.reflecting")
                : triggerState === "ok"
                  ? t("introspection.triggered")
                  : triggerState === "error"
                    ? t("introspection.triggerFailed")
                    : t("introspection.triggerNow")}
            </button>
            {triggerState === "idle" && <p className="text-xs text-[var(--muted)]">{t("introspection.skipIntervalHint")}</p>}
          </div>
        </div>
      </div>
    </Card>
  );
}
