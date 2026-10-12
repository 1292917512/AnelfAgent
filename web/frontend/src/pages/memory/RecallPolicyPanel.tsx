import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Brain } from "lucide-react";
import { configMetaApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Button, LoadingBlock, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { cn } from "@/lib/utils";

const LEVELS = ["off", "tags", "fast", "deep"] as const;

export function RecallPolicyPanel() {
  const { t } = useTranslation("settings");
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["config-meta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });
  const items = query.data?.groups.flatMap((group) => group.items) ?? [];
  const level =
    items.find((item) => item.key === "memory_recall_level")?.value ?? "deep";
  const background =
    items.find((item) => item.key === "memory_recall_background")?.value ??
    true;
  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) =>
      configMetaApi.save(key, value),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["config-meta"] });
      toast.success(t("common:saved"));
    },
    onError: () => toast.error(t("hooks.saveFailed")),
  });
  if (query.isError)
    return <Button onClick={() => query.refetch()}>{t("common:retry")}</Button>;
  if (query.isPending) return <LoadingBlock />;
  return (
    <Card title={t("recallPolicy.title")} subtitle={t("recallPolicy.subtitle")}>
      <div
        role="group"
        aria-label={t("recallPolicy.title")}
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        {LEVELS.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={level === value}
            disabled={save.isPending}
            onClick={() => save.mutate({ key: "memory_recall_level", value })}
            className={cn(
              "rounded-xl border p-4 text-left transition-colors disabled:opacity-60",
              level === value
                ? "border-accent/50 bg-accent/10"
                : "border-border bg-elevated/30 hover:border-accent/30",
            )}
          >
            <span className="flex items-center justify-between text-sm font-semibold text-heading">
              {t(`recallPolicy.levels.${value}.title`)}
              {level === value ? (
                <Check size={16} className="text-accent" />
              ) : (
                <Brain size={16} className="text-muted" />
              )}
            </span>
            <span className="mt-3 block text-xs leading-6 text-muted">
              {t(`recallPolicy.levels.${value}.body`)}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-5 flex items-start gap-3 rounded-xl bg-elevated/40 p-4">
        <Switch
          checked={background === true}
          disabled={save.isPending}
          label={t("recallPolicy.background")}
          onChange={(value) =>
            save.mutate({ key: "memory_recall_background", value })
          }
        />
        <div>
          <p className="text-sm text-heading">{t("recallPolicy.background")}</p>
          <p className="mt-1 text-xs leading-6 text-muted">
            {t("recallPolicy.backgroundHint")}
          </p>
        </div>
      </div>
      <p className="mt-4 text-xs leading-6 text-muted">
        {t("recallPolicy.scope")}
      </p>
    </Card>
  );
}
