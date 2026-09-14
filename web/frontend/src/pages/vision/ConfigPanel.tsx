import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { configMetaApi } from "@/lib/api";
import { Button, Input, LoadingBlock, toast } from "@/components/ui";
import { SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";

interface ConfigRow {
  key: string;
  label: string;
  value: number | boolean;
  kind: "number" | "boolean";
}

/** 视觉配置：vision_* 键的热编辑（经 configMetaApi，保存即生效） */
export function VisionConfigPanel() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<ConfigRow[]>([]);

  const { data: meta, isLoading } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });

  useEffect(() => {
    if (!meta) return;
    const items = (meta.groups ?? [])
      .filter((g) => g.group === "vision")
      .flatMap((g) => g.items);
    const mapped: ConfigRow[] = items
      .filter((i) => i.key.startsWith("vision_"))
      .map((i) => ({
        key: i.key,
        label: i.description || i.key,
        value: i.value as number | boolean,
        kind: i.type === "boolean" || typeof i.value === "boolean" ? "boolean" : "number",
      }));
    setRows(mapped);
  }, [meta]);

  const saveMut = useMutation({
    mutationFn: ({ key, value }: { key: string; value: number | boolean }) =>
      configMetaApi.save(key, value),
    onSuccess: () => {
      toast.success(t("saved"));
      queryClient.invalidateQueries({ queryKey: ["configMeta"] });
    },
    onError: () => toast.error(t("saveFailed")),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  return (
    <div className="space-y-3 max-w-2xl">
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <SlidersHorizontal size={15} className="text-accent" />
          <span className="text-sm font-semibold text-heading">{t("configTitle")}</span>
        </div>
        {rows.map((row) => (
          <div key={row.key} className="flex items-center gap-3">
            <span className="flex-1 text-xs text-muted">{row.label}</span>
            {row.kind === "boolean" ? (
              <input
                type="checkbox"
                className="accent-accent"
                checked={Boolean(row.value)}
                onChange={(e) => {
                  const v = e.target.checked;
                  setRows((rs) => rs.map((r) => (r.key === row.key ? { ...r, value: v } : r)));
                  saveMut.mutate({ key: row.key, value: v });
                }}
              />
            ) : (
              <Input
                type="number"
                className="w-28"
                value={Number(row.value)}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setRows((rs) => rs.map((r) => (r.key === row.key ? { ...r, value: v } : r)));
                }}
                onBlur={() => saveMut.mutate({ key: row.key, value: Number(row.value) })}
              />
            )}
          </div>
        ))}
        {rows.length === 0 && (
          <p className="text-xs text-muted">{t("noConfig")}</p>
        )}
        <div className="pt-1 text-[11px] text-muted">{t("hotHint")}</div>
      </div>
      <div className="text-right">
        <Button size="sm" variant="ghost" disabled>
          {t("autoSaveOn")}
        </Button>
      </div>
    </div>
  );
}
