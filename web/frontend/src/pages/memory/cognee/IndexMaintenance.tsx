import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench } from "lucide-react";
import { Button, ConfirmDialog, Modal, toast } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { memoryApi } from "@/lib/api";
import type { CogneeMaintenanceAction, CogneeStatus } from "@/lib/types";

const actions: CogneeMaintenanceAction[] = ["cleanup", "compact", "backfill", "rebuild", "freshRebuild"];

export function IndexMaintenance({ status, stale, onChanged }: {
  status?: CogneeStatus; stale: boolean; onChanged: () => void;
}) {
  const { t } = useTranslation("memory");
  const descriptionId = useId();
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<CogneeMaintenanceAction | null>(null);
  const mutation = useMutation({
    mutationFn: async (action: CogneeMaintenanceAction): Promise<void> => {
      if (action === "cleanup" || action === "compact") {
        const { data } = await memoryApi.cognee.compact(action === "cleanup");
        if (!data.ok) throw new Error(data.error || t("cognee.compactFailed"));
      } else if (action === "backfill") {
        const { data } = await memoryApi.cognee.backfill(0, false);
        if (data?.error) throw new Error(data.error);
      } else {
        await memoryApi.cognee.rebuild(action === "freshRebuild");
      }
    },
    onSuccess: (_response, action) => {
      onChanged();
      if (action === "cleanup" || action === "compact") toast.success(t("cognee.compactScheduled"));
    },
  });
  const disabled = (action: CogneeMaintenanceAction) => {
    if (stale || !status?.availability.ready || mutation.isPending || status.sync.maintenance
      || status.sync.configuration_pending || status.retirement?.running) return true;
    if (action === "rebuild" || action === "freshRebuild") {
      if (status.projection?.rebuild_requested) return true;
      return action === "rebuild" && ["requested", "scanning", "building", "verifying"].includes(status.projection?.phase ?? "");
    }
    return false;
  };
  const danger = confirmation === "rebuild" || confirmation === "freshRebuild";
  return <>
    <Button variant="secondary" size="sm" title={t("cognee.maintenanceOpenHint")} onClick={() => setOpen(true)}>
      <Wrench size={14} />{t("cognee.maintenanceTitle")}
    </Button>
    <Modal open={open} onClose={() => setOpen(false)} title={t("cognee.maintenanceTitle")} width="max-w-2xl">
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-muted">{t("cognee.maintenanceHint")}</p>
        {status?.sync.maintenance && <p role="status" className="text-sm text-accent">
          {t(`cognee.maintenance${status.sync.maintenance.phase === "queued" ? "Queued" : "Running"}`, {
            action: t(`cognee.${status.sync.maintenance.mode}`),
          })}
        </p>}
        {stale && <p role="alert" className="text-sm text-danger">{t("cognee.maintenanceStale")}</p>}
        {mutation.error && <QueryError error={mutation.error} compact />}
        <div className="space-y-3">
          {actions.map(action => <div key={action} title={t(`cognee.${action}Hint`)}
            className="flex flex-col gap-3 rounded-xl border border-border p-4 sm:flex-row sm:items-center sm:justify-between">
            <p id={`${descriptionId}-${action}`} className="text-sm leading-relaxed text-muted">{t(`cognee.${action}Hint`)}</p>
            <Button variant={action === "rebuild" || action === "freshRebuild" ? "danger" : "secondary"}
              className="shrink-0" title={t(`cognee.${action}Hint`)} aria-describedby={`${descriptionId}-${action}`}
              disabled={disabled(action)} onClick={() => setConfirmation(action)}>{t(`cognee.${action}`)}</Button>
          </div>)}
        </div>
      </div>
    </Modal>
    <ConfirmDialog open={confirmation !== null} danger={danger}
      title={t(`cognee.${confirmation ?? "cleanup"}`)} message={t(`cognee.${confirmation ?? "cleanup"}Confirm`)}
      onClose={() => setConfirmation(null)} onConfirm={() => {
        if (!confirmation || disabled(confirmation)) { setConfirmation(null); return; }
        mutation.mutate(confirmation);
        setConfirmation(null);
      }} />
  </>;
}
