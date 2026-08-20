import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery } from "@tanstack/react-query";
import { volumeApi } from "@/lib/api";
import type { VolumeInfo } from "@/lib/types";
import { Button, Input, Modal, toast } from "@/components/ui";
import { AlertTriangle, CheckCircle2, FolderSearch, FolderInput, XCircle } from "lucide-react";
import { formatSize } from "../format";

interface Props {
  open: boolean;
  onClose: () => void;
  volume: VolumeInfo;
  onChanged: () => void;
}

/** 卷迁移对话框：目标校验 → 在线拷贝 → 指派切换（重启生效） */
export function RelocateDialog({ open, onClose, volume, onChanged }: Props) {
  const { t } = useTranslation("data");
  const [target, setTarget] = useState("");

  const { data: op } = useQuery({
    queryKey: ["volumeOp", volume.volume_id],
    queryFn: () => volumeApi.operation(volume.volume_id).then((r) => r.data),
    refetchInterval: (query) => (query.state.data?.state === "running" ? 1000 : false),
    enabled: open,
  });

  const checkMut = useMutation({
    mutationFn: (value: string) =>
      volumeApi.checkRelocate(volume.volume_id, value).then((r) => r.data),
  });

  const relocateMut = useMutation({
    mutationFn: (value: string) => volumeApi.relocate(volume.volume_id, value),
    onSuccess: () => {
      onChanged();
      toast.success(t("volumes.relocate.started"));
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("volumes.relocate.failed"));
    },
  });

  if (!open) return null;

  const check = checkMut.data;
  const running = op?.op === "relocate" && op.state === "running";
  const done = op?.op === "relocate" && op.state === "done";
  const failed = op?.op === "relocate" && op.state === "error";
  const problemText = (token: string) =>
    token === "target_file_exists"
      ? t("volumes.relocate.problem.target_file_exists")
      : t(`storage.problem.${token}`);

  return (
    <Modal open={open} onClose={onClose} title={t("volumes.relocate.title", { name: volume.name })}>
      <div className="space-y-3 min-w-[440px]">
        <p className="text-xs text-muted">{t("volumes.relocate.hint")}</p>
        <p className="text-[11px] font-mono text-foreground break-all rounded bg-elevated px-2 py-1.5">
          {volume.path}
        </p>

        <div className="flex gap-2">
          <Input
            value={target}
            disabled={running}
            onChange={(e) => {
              setTarget(e.target.value);
              checkMut.reset();
            }}
            placeholder={t("storage.targetPlaceholder")}
            className="flex-1 font-mono"
          />
          <Button
            variant="secondary"
            disabled={!target.trim() || running}
            loading={checkMut.isPending}
            onClick={() => checkMut.mutate(target.trim())}
          >
            <FolderSearch size={14} /> {t("storage.check")}
          </Button>
        </div>

        {check && (
          <div className="space-y-1.5 text-xs">
            {check.ok ? (
              <p className="flex items-center gap-1.5 text-success">
                <CheckCircle2 size={13} />
                {t("storage.checkOk", { size: formatSize(check.required_bytes) })}
              </p>
            ) : (
              check.problems.map((p) => (
                <p key={p} className="flex items-center gap-1.5 text-danger">
                  <XCircle size={13} /> {problemText(p)}
                </p>
              ))
            )}
            {check.warnings.map((w) => (
              <p key={w} className="flex items-center gap-1.5 text-warning">
                <AlertTriangle size={13} /> {t(`storage.warning.${w}`)}
              </p>
            ))}
          </div>
        )}

        {running && op && (
          <div className="space-y-1">
            <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
              <div
                className="h-full bg-accent transition-all"
                style={{
                  width: `${Math.round(
                    ((op.total ?? 0) > 0 ? (op.done ?? 0) / (op.total ?? 1) : 0) * 100,
                  )}%`,
                }}
              />
            </div>
            <p className="text-[11px] text-muted font-mono">
              {t("volumes.opRunning", { op: t("volumes.op.relocate") })}
              {op.total ? ` · ${op.done ?? 0}/${op.total}` : ""} {op.phase ?? ""}
            </p>
          </div>
        )}
        {done && (
          <p className="flex items-center gap-1.5 text-xs text-warning">
            <AlertTriangle size={13} /> {t("volumes.needsRestart")}
          </p>
        )}
        {failed && op?.error && (
          <p className="text-xs text-danger">{t("volumes.opFailed", { error: op.error })}</p>
        )}

        <Button
          disabled={!check?.ok || running}
          loading={relocateMut.isPending}
          onClick={() => relocateMut.mutate(target.trim())}
        >
          <FolderInput size={14} /> {t("volumes.relocate.run")}
        </Button>
      </div>
    </Modal>
  );
}
