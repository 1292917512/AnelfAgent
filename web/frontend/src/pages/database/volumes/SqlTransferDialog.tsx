import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery } from "@tanstack/react-query";
import { connectionApi, volumeApi } from "@/lib/api";
import type { VolumeInfo } from "@/lib/types";
import { Button, Input, Modal, Select, Switch, toast } from "@/components/ui";
import { Database, UploadCloud } from "lucide-react";

interface Props {
  mode: "export" | "import";
  onClose: () => void;
  volume: VolumeInfo;
  onChanged: () => void;
}

/** 外部 SQL 传输对话框：导出（快照覆写 + 清单登记）/ 导入（清单定位 + 重启落盘） */
export function SqlTransferDialog({ mode, onClose, volume, onChanged }: Props) {
  const { t } = useTranslation("data");
  const [connectionId, setConnectionId] = useState("");
  const [prefix, setPrefix] = useState("");
  const [drop, setDrop] = useState(true);

  const { data: connections } = useQuery({
    queryKey: ["dbConnections"],
    queryFn: () => connectionApi.list().then((r) => r.data),
  });

  const { data: op } = useQuery({
    queryKey: ["volumeOp", volume.volume_id],
    queryFn: () => volumeApi.operation(volume.volume_id).then((r) => r.data),
    refetchInterval: (query) => (query.state.data?.state === "running" ? 1000 : false),
  });

  const transferMut = useMutation({
    mutationFn: () =>
      mode === "export"
        ? volumeApi.exportSql(volume.volume_id, {
            connection_id: connectionId,
            table_prefix: prefix.trim(),
            drop_existing: drop,
          })
        : volumeApi.importSql(volume.volume_id, connectionId),
    onSuccess: () => {
      onChanged();
      toast.success(t("volumes.sql.started"));
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("volumes.sql.failed"));
    },
  });

  const items = connections?.items ?? [];
  const running = op?.op === mode && op.state === "running";
  const done = op?.op === mode && op.state === "done";
  const failed = op?.op === mode && op.state === "error";

  return (
    <Modal
      open
      onClose={onClose}
      title={t(mode === "export" ? "volumes.sql.exportTitle" : "volumes.sql.importTitle", {
        name: volume.name,
      })}
    >
      <div className="space-y-3 min-w-[440px]">
        <p className="text-xs text-muted">
          {t(mode === "export" ? "volumes.sql.exportHint" : "volumes.sql.importHint")}
        </p>

        {items.length === 0 ? (
          <p className="text-xs text-warning">{t("volumes.sql.noConnections")}</p>
        ) : (
          <>
            <label className="flex items-center gap-2 text-xs text-heading">
              <Database size={13} className="text-accent" />
              {t("volumes.sql.connection")}
              <Select
                value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}
                className="flex-1"
              >
                <option value="">{t("volumes.sql.pickConnection")}</option>
                {items.map((conn) => (
                  <option key={conn.id} value={conn.id}>
                    {conn.name} · {conn.engine} / {conn.database}
                  </option>
                ))}
              </Select>
            </label>

            {mode === "export" && (
              <>
                <Input
                  value={prefix}
                  onChange={(e) => setPrefix(e.target.value)}
                  placeholder={t("volumes.sql.prefix")}
                  className="font-mono"
                />
                <label className="flex items-center gap-2 text-xs text-heading">
                  <Switch checked={drop} onChange={setDrop} />
                  <span>
                    {t("volumes.sql.drop")}
                    <span className="ml-1 text-muted">{t("volumes.sql.dropHint")}</span>
                  </span>
                </label>
              </>
            )}
          </>
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
              {t("volumes.opRunning", { op: t(`volumes.op.${mode}`) })}
              {op.total ? ` · ${op.done ?? 0}/${op.total}` : ""} {op.phase ?? ""}
            </p>
          </div>
        )}
        {done && op.result?.needs_restart && (
          <p className="text-xs text-warning">{t("volumes.needsRestart")}</p>
        )}
        {failed && op?.error && (
          <p className="text-xs text-danger">{t("volumes.opFailed", { error: op.error })}</p>
        )}

        <Button
          disabled={!connectionId || running || items.length === 0}
          loading={transferMut.isPending}
          onClick={() => transferMut.mutate()}
        >
          <UploadCloud size={14} className={mode === "import" ? "rotate-180" : ""} />
          {t("volumes.sql.run")}
        </Button>
      </div>
    </Modal>
  );
}
