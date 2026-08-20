import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { devopsApi, volumeApi } from "@/lib/api";
import type { VolumeInfo } from "@/lib/types";
import { Badge, Button, toast } from "@/components/ui";
import {
  Archive,
  Database,
  FileText,
  FolderInput,
  HardDrive,
  RotateCw,
  UploadCloud,
} from "lucide-react";
import { formatSize } from "../format";
import { BackupDrawer } from "./BackupDrawer";
import { RelocateDialog } from "./RelocateDialog";
import { SqlTransferDialog } from "./SqlTransferDialog";

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

  const { data: op } = useQuery({
    queryKey: ["volumeOp", volume.volume_id],
    queryFn: () => volumeApi.operation(volume.volume_id).then((r) => r.data),
    refetchInterval: (query) => (query.state.data?.state === "running" ? 1000 : false),
  });

  const backupMut = useMutation({
    mutationFn: () => volumeApi.backup(volume.volume_id),
    onSuccess: refreshOp,
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("volumes.backupDrawer.failed"));
    },
  });

  const restartMut = useMutation({
    mutationFn: () => devopsApi.restart(),
    onSuccess: () => toast.success(t("volumes.restarting")),
  });

  const Icon = KIND_ICON[volume.kind] ?? Database;
  const running = op?.state === "running";
  const done = op?.state === "done" && op.result?.needs_restart;
  const failed = op?.state === "error";
  const progress = op && (op.total ?? 0) > 0 ? (op.done ?? 0) / (op.total ?? 1) : 0;

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
          <button
            type="button"
            className="flex items-center gap-1 rounded border border-warning/50 px-1.5 py-0.5 hover:bg-warning/20"
            onClick={() => restartMut.mutate()}
            disabled={restartMut.isPending}
          >
            <RotateCw size={11} /> {t("volumes.restartNow")}
          </button>
        </div>
      )}

      {running && op && (
        <div className="space-y-1">
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
          <p className="text-[11px] text-muted font-mono truncate">
            {t("volumes.opRunning", { op: t(`volumes.op.${op.op ?? ""}`) })}
            {op.total ? ` · ${op.done ?? 0}/${op.total}` : ""}
            {op.phase ? ` · ${op.phase}` : ""}
          </p>
        </div>
      )}
      {failed && op?.error && (
        <p className="text-[11px] text-danger">{t("volumes.opFailed", { error: op.error })}</p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        {has(volume, "backup") && (
          <Button size="sm" loading={backupMut.isPending || running} onClick={() => backupMut.mutate()}>
            <Archive size={13} /> {t("volumes.ops.backup")}
          </Button>
        )}
        {has(volume, "restore") && (
          <Button size="sm" variant="secondary" onClick={() => setDrawer(true)}>
            <Archive size={13} /> {t("volumes.ops.backups")}
          </Button>
        )}
        {has(volume, "relocate") && (
          <Button size="sm" variant="secondary" onClick={() => setRelocate(true)} disabled={running}>
            <FolderInput size={13} /> {t("volumes.ops.relocate")}
          </Button>
        )}
        {has(volume, "export_sql") && (
          <Button size="sm" variant="secondary" onClick={() => setSqlMode("export")} disabled={running}>
            <UploadCloud size={13} /> {t("volumes.ops.export")}
          </Button>
        )}
        {has(volume, "import_sql") && (
          <Button size="sm" variant="secondary" onClick={() => setSqlMode("import")} disabled={running}>
            <UploadCloud size={13} className="rotate-180" /> {t("volumes.ops.import")}
          </Button>
        )}
      </div>

      <BackupDrawer
        open={drawer}
        onClose={() => setDrawer(false)}
        volume={volume}
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
