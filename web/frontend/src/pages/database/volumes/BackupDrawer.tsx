import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { volumeApi } from "@/lib/api";
import type { VolumeBackupInfo, VolumeInfo } from "@/lib/types";
import { Button, ConfirmDialog, Modal, toast } from "@/components/ui";
import { Download, RotateCcw, Trash2 } from "lucide-react";
import { formatSize } from "../format";

interface Props {
  open: boolean;
  onClose: () => void;
  volume: VolumeInfo;
  onChanged: () => void;
}

/** 备份管理抽屉：清单 + 恢复（重启落盘） / 下载 / 删除 */
export function BackupDrawer({ open, onClose, volume, onChanged }: Props) {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<VolumeBackupInfo | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<VolumeBackupInfo | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["volumeBackups", volume.volume_id],
    queryFn: () => volumeApi.backups(volume.volume_id).then((r) => r.data),
    enabled: open,
  });

  const errText = (e: unknown) =>
    (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
    t("volumes.backupDrawer.failed");

  const restoreMut = useMutation({
    mutationFn: (backupId: string) => volumeApi.restore(volume.volume_id, backupId),
    onSuccess: () => {
      toast.success(t("volumes.backupDrawer.restoreDone"));
      queryClient.invalidateQueries({ queryKey: ["volumeBackups", volume.volume_id] });
      onChanged();
      setConfirm(null);
    },
    onError: (e: unknown) => toast.error(errText(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (backupId: string) => volumeApi.deleteBackup(volume.volume_id, backupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["volumeBackups", volume.volume_id] });
      onChanged();
      setConfirmDelete(null);
    },
    onError: (e: unknown) => toast.error(errText(e)),
  });

  const download = async (backup: VolumeBackupInfo) => {
    try {
      const resp = await volumeApi.downloadBackup(volume.volume_id, backup.backup_id);
      const url = URL.createObjectURL(resp.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${volume.volume_id}-${backup.backup_id}-${backup.artifact}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const backups = data?.items ?? [];

  return (
    <>
      <Modal open={open} onClose={onClose} title={t("volumes.backupDrawer.title", { name: volume.name })}>
        <div className="space-y-2 min-w-[420px] max-h-[60vh] overflow-y-auto">
          {isLoading && <p className="text-xs text-muted">{t("common:loading")}</p>}
          {!isLoading && backups.length === 0 && (
            <p className="text-xs text-muted py-4 text-center">{t("volumes.backupDrawer.empty")}</p>
          )}
          {backups.map((backup) => (
            <div
              key={backup.backup_id}
              className="flex items-center gap-3 rounded border border-border bg-elevated px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="text-xs font-mono text-heading">{backup.backup_id}</p>
                <p className="text-[11px] text-muted">
                  {formatSize(backup.size_bytes)}
                  {backup.table_count > 0 && ` · ${backup.table_count} tables`}
                  {backup.file_count > 0 && ` · ${backup.file_count} files`}
                </p>
              </div>
              <Button size="sm" variant="secondary" onClick={() => setConfirm(backup)}>
                <RotateCcw size={12} /> {t("volumes.backupDrawer.restore")}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => download(backup)}>
                <Download size={12} />
              </Button>
              <Button size="sm" variant="danger" onClick={() => setConfirmDelete(backup)}>
                <Trash2 size={12} />
              </Button>
            </div>
          ))}
        </div>
      </Modal>

      <ConfirmDialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={t("volumes.backupDrawer.restoreConfirmTitle")}
        message={t("volumes.backupDrawer.restoreConfirmBody")}
        confirmText={t("volumes.backupDrawer.restore")}
        danger
        loading={restoreMut.isPending}
        onConfirm={() => confirm && restoreMut.mutate(confirm.backup_id)}
      />
      <ConfirmDialog
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        title={t("volumes.backupDrawer.deleteConfirmTitle")}
        message={confirmDelete?.backup_id ?? ""}
        confirmText={t("volumes.backupDrawer.delete")}
        danger
        loading={deleteMut.isPending}
        onConfirm={() => confirmDelete && deleteMut.mutate(confirmDelete.backup_id)}
      />
    </>
  );
}
