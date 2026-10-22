import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { volumeApi } from "@/lib/api";
import { InlineContributions } from "@/components/extensions/ContributionSlot";
import type { VolumeInfo } from "@/lib/types";
import { Badge, Button, toast } from "@/components/ui";
import {
  Archive,
  Database,
  FileText,
  FolderInput,
  HardDrive,
  UploadCloud,
} from "lucide-react";
import { formatSize } from "../format";
import { BackupDrawer } from "./BackupDrawer";
import { RelocateDialog } from "./RelocateDialog";
import { SqlTransferDialog } from "./SqlTransferDialog";
import { useVolumeOperation } from "./useVolumeOperation";

const KIND_ICON = { sqlite: Database, cognee_tree: HardDrive, notes_tree: FileText } as const;

interface Props {
  volume: VolumeInfo;
  onChanged: () => void;
}

const has = (volume: VolumeInfo, cap: string) => volume.capabilities.includes(cap);

/** 单个存储卷卡片：状态概览 + 操作入口 + 进行中任务进度 */
export function VolumeCard({ volume, onChanged }: Props) {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const [drawer, setDrawer] = useState(false);
  const [relocate, setRelocate] = useState(false);
  const [sqlMode, setSqlMode] = useState<"export" | "import" | null>(null);

  const refreshOp = () =>
    queryClient.invalidateQueries({ queryKey: ["volumeOp", volume.volume_id] });

  const { data: op, isError: statusError } = useVolumeOperation(volume.volume_id);

  const backupMut = useMutation({
    mutationFn: () => volumeApi.backup(volume.volume_id),
    onSuccess: ({ data }) => {
      queryClient.setQueryData(["volumeOp", volume.volume_id], data);
      void refreshOp();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("volumes.backupDrawer.failed"));
    },
  });

  const cancelMut = useMutation({
    mutationFn: () => volumeApi.cancel(volume.volume_id),
    onSuccess: ({ data }) => queryClient.setQueryData(["volumeOp", volume.volume_id], data),
    onError: () => { toast.error(t("volumes.cancelFailed")); void refreshOp(); },
  });

  const Icon = KIND_ICON[volume.kind] ?? Database;
  const running = op?.state === "running";
  const blocked = running || volume.pending_restore || volume.needs_restart;
  const done = op?.state === "done" && op.result?.needs_restart;
  const failed = op?.state === "error";
  const progress = op && (op.total ?? 0) > 0 ? Math.min(1, (op.done ?? 0) / (op.total ?? 1)) : 0;

  return (
    <div className="rounded-md border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Icon size={15} className="text-accent shrink-0" />
        <span className="text-sm font-semibold text-heading">{volume.name}</span>
        <Badge variant="info">{t(`volumes.kind.${volume.kind}`)}</Badge>
        <Badge variant={volume.location_source === "default" ? "neutral" : "accent"}>
          {t(`volumes.source.${volume.location_source}`)}
        </Badge>
        <span className="ml-auto text-xs text-muted">{formatSize(volume.size_bytes)}</span>
      </div>

      <p className="text-xs text-muted">{volume.description}</p>
      <p className="text-[11px] font-mono text-foreground break-all">{volume.path}</p>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        <span>
          {t("volumes.backupCount", { count: volume.backup_count })}
          {volume.last_backup_at > 0 &&
            ` · ${t("volumes.lastBackup", {
              time: new Date(volume.last_backup_at * 1000).toLocaleString(),
            })}`}
        </span>
      </div>

      {(volume.needs_restart || volume.pending_restore || done) && (
        <div className="flex items-center justify-between gap-2 rounded border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-[11px] text-warning">
          <span>
            {volume.pending_restore
              ? t("volumes.pendingOne")
              : t("volumes.needsRestart")}
          </span>
          <InlineContributions slot="system.restart" componentProps={{}} />
        </div>
      )}

      {running && op && (
        <div className="space-y-1">
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
            <div
              className="h-full bg-accent transition-[width]"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
          <p className="text-[11px] text-muted font-mono truncate">
            {t("volumes.opRunning", { op: t(`volumes.op.${op.op ?? ""}`) })}
            {op.total ? ` · ${op.unit === "bytes" ? formatSize(op.done ?? 0) : op.done ?? 0}/${op.unit === "bytes" ? formatSize(op.total) : op.total}` : ""}
            {op.phase ? ` · ${t(`volumes.phase.${op.phase}`, { defaultValue: op.phase })}` : ""}
          </p>
          {op.current_file && <p className="text-[11px] text-muted truncate" title={op.current_file}>{op.current_file}</p>}
          {op.cancellable && (
            <Button size="sm" variant="secondary" disabled={op.cancel_requested} loading={cancelMut.isPending}
              onClick={() => cancelMut.mutate()}>
              {t(op.cancel_requested ? "volumes.cancelling" : "volumes.cancel")}
            </Button>
          )}
        </div>
      )}
      {statusError && <p role="alert" className="text-xs text-warning">{t("volumes.statusFailed")}</p>}
      {op?.state === "cancelled" && <p className="text-xs text-muted">{t("volumes.cancelled")}</p>}
      {op?.state === "done" && op.op === "backup" && <p className="text-xs text-success">{t("volumes.backupDone")}</p>}
      {failed && op?.error && (
        <p className="text-[11px] text-danger">{t("volumes.opFailed", { error: op.error })}</p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        {has(volume, "backup") && (
          <Button size="sm" disabled={blocked} loading={backupMut.isPending || running} onClick={() => backupMut.mutate()}>
            <Archive size={13} /> {t("volumes.ops.backup")}
          </Button>
        )}
        {has(volume, "restore") && (
          <Button size="sm" variant="secondary" onClick={() => setDrawer(true)}>
            <Archive size={13} /> {t("volumes.ops.backups")}
          </Button>
        )}
        {has(volume, "relocate") && (
          <Button size="sm" variant="secondary" onClick={() => setRelocate(true)} disabled={blocked}>
            <FolderInput size={13} /> {t("volumes.ops.relocate")}
          </Button>
        )}
        {has(volume, "export_sql") && (
          <Button size="sm" variant="secondary" onClick={() => setSqlMode("export")} disabled={blocked}>
            <UploadCloud size={13} /> {t("volumes.ops.export")}
          </Button>
        )}
        {has(volume, "import_sql") && (
          <Button size="sm" variant="secondary" onClick={() => setSqlMode("import")} disabled={blocked}>
            <UploadCloud size={13} className="rotate-180" /> {t("volumes.ops.import")}
          </Button>
        )}
      </div>

      <BackupDrawer
        open={drawer}
        onClose={() => setDrawer(false)}
        volume={volume}
        busy={Boolean(blocked)}
        onChanged={() => {
          onChanged();
          refreshOp();
        }}
      />
      <RelocateDialog
        open={relocate}
        onClose={() => setRelocate(false)}
        volume={volume}
        onChanged={() => {
          onChanged();
          refreshOp();
        }}
      />
      {sqlMode && (
        <SqlTransferDialog
          mode={sqlMode}
          onClose={() => setSqlMode(null)}
          volume={volume}
          onChanged={() => {
            onChanged();
            refreshOp();
          }}
        />
      )}
    </div>
  );
}
