import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { databaseApi } from "@/lib/api";
import { Button, toast } from "@/components/ui";
import { Activity, Download, Wrench } from "lucide-react";
import { formatSize } from "./format";

/** 库健康概览卡：体积 / WAL / 碎片化 / 表规模 + 一键维护建议 + 备份下载 */
export function HealthCard({ db }: { db: string }) {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();

  const { data: health } = useQuery({
    queryKey: ["dbHealth", db],
    queryFn: () => databaseApi.health(db).then((r) => r.data),
  });

  const optimizeMut = useMutation({
    mutationFn: (actions: string[]) => databaseApi.optimize(db, actions),
    onSuccess: () => {
      toast.success(t("health.optimizeOk"));
      queryClient.invalidateQueries({ queryKey: ["dbHealth", db] });
      queryClient.invalidateQueries({ queryKey: ["dbDatabases"] });
    },
    onError: () => toast.error(t("health.optimizeFailed")),
  });

  const backupMut = useMutation({
    mutationFn: async () => {
      const res = await databaseApi.backup(db);
      const dispo = res.headers["content-disposition"] as string | undefined;
      const match = dispo?.match(/filename="?([^";]+)"?/);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = match?.[1] ?? `${db}-backup.sqlite3`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onSuccess: () => toast.success(t("health.backupOk")),
    onError: () => toast.error(t("health.backupFailed")),
  });

  if (!health) return null;

  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-3 text-xs text-muted flex-wrap">
        <span className="flex items-center gap-1.5 font-semibold text-heading">
          <Activity size={13} className="text-accent" /> {t("health.title")}
        </span>
        <span>
          {t("health.size")} <b className="text-foreground">{formatSize(health.size_bytes)}</b>
        </span>
        <span>
          {t("health.wal")} <b className="text-foreground">{formatSize(health.wal_bytes)}</b>
        </span>
        <span>
          {t("health.fragmentation")}{" "}
          <b className="text-foreground">{(health.fragmentation * 100).toFixed(1)}%</b>
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          loading={backupMut.isPending}
          onClick={() => backupMut.mutate()}
        >
          <Download size={13} /> {t("health.backup")}
        </Button>
      </div>

      {health.top_tables.length > 0 && (
        <div className="flex items-center gap-2 text-[11px] text-muted flex-wrap">
          <span>{t("health.topTables")}</span>
          {health.top_tables.map((tb) => (
            <span key={tb.name} className="rounded bg-elevated px-1.5 py-0.5 font-mono">
              {tb.name} <span className="text-foreground">{tb.row_count}</span>
            </span>
          ))}
        </div>
      )}

      {health.suggestions.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {health.suggestions.map((s) => (
            <button
              key={s.id}
              disabled={optimizeMut.isPending}
              onClick={() => optimizeMut.mutate([s.action])}
              className={
                s.level === "warn"
                  ? "flex items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2.5 py-1 text-[11px] text-warning hover:bg-warning/20 transition-colors disabled:opacity-50"
                  : "flex items-center gap-1 rounded-full border border-border bg-elevated px-2.5 py-1 text-[11px] text-muted hover:text-foreground transition-colors disabled:opacity-50"
              }
            >
              <Wrench size={11} />
              {t(`health.suggest.${s.id}`)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
