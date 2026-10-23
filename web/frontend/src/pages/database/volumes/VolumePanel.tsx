import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { volumeApi } from "@/lib/api";
import { InlineContributions } from "@/components/extensions/ContributionSlot";
import { Button, LoadingBlock } from "@/components/ui";
import { AlertTriangle, Layers } from "lucide-react";
import { VolumeCard } from "./VolumeCard";

/** 存储卷：所有持久化数据的模块化管理（备份 / 恢复 / 迁移 / 外部 SQL） */
export function VolumePanel() {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("volume");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["dbVolumes"],
    queryFn: () => volumeApi.list().then((r) => r.data),
    refetchInterval: 30000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["dbVolumes"] });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  const volumes = data?.items ?? [];
  const pending = data?.pending_restore;
  const visibleVolumes = selected ? volumes.filter((volume) => volume.volume_id === selected) : volumes;

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted flex items-center gap-1.5">
        <Layers size={13} className="text-accent" />
        {t("volumes.subtitle")}
        <Button size="sm" variant="secondary" onClick={() => void refresh()}>{t("common:refresh")}</Button>
      </p>
      {isError && <p role="alert" className="text-xs text-danger">{t("volumes.statusFailed")}</p>}

      {selected && <Button size="sm" variant="secondary" onClick={() => setParams((current) => {
        const next = new URLSearchParams(current); next.delete("volume"); return next;
      })}>{t("volumes.showAll")}</Button>}
      {pending && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          <span className="flex items-start gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {t("volumes.pendingBanner", { volumes: pending })}
          </span>
          <InlineContributions slot="system.restart" componentProps={{}} />
        </div>
      )}

      {visibleVolumes.length === 0 && !isError ? (
        <p className="text-xs text-muted">{t("volumes.noVolumes")}</p>
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {visibleVolumes.map((volume) => (
            <VolumeCard key={volume.volume_id} volume={volume} onChanged={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}
