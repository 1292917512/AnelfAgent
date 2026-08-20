import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { devopsApi, volumeApi } from "@/lib/api";
import { LoadingBlock, toast } from "@/components/ui";
import { AlertTriangle, Layers, RotateCw } from "lucide-react";
import { VolumeCard } from "./VolumeCard";

/** 存储卷：所有持久化数据的模块化管理（备份 / 恢复 / 迁移 / 外部 SQL） */
export function VolumePanel() {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["dbVolumes"],
    queryFn: () => volumeApi.list().then((r) => r.data),
    refetchInterval: 30000,
  });

  const restartMut = useMutation({
    mutationFn: () => devopsApi.restart(),
    onSuccess: () => toast.success(t("volumes.restarting")),
    onError: () => toast.error(t("volumes.restartFailed")),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["dbVolumes"] });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  const volumes = data?.items ?? [];
  const pending = data?.pending_restore;

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted flex items-center gap-1.5">
        <Layers size={13} className="text-accent" />
        {t("volumes.subtitle")}
      </p>

      {pending && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          <span className="flex items-start gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {t("volumes.pendingBanner", { volumes: pending })}
          </span>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 rounded border border-warning/50 px-2 py-1 hover:bg-warning/20"
            onClick={() => restartMut.mutate()}
            disabled={restartMut.isPending}
          >
            <RotateCw size={12} /> {t("volumes.restartNow")}
          </button>
        </div>
      )}

      {volumes.length === 0 ? (
        <p className="text-xs text-muted">{t("volumes.noVolumes")}</p>
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {volumes.map((volume) => (
            <VolumeCard key={volume.volume_id} volume={volume} onChanged={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}
