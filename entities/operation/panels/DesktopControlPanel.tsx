import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { Button, toast } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api/client";
import { operationApi } from "./api";
import type { DesktopControl, DesktopReceipt, HistoryEntry } from "./types";

export function DesktopReceiptView({ receipt }: { receipt: DesktopReceipt }) {
  const { t } = useTranslation("operation");
  return <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
    <div><dt className="text-muted">{t("control.result")}</dt><dd>{t(`control.states.${receipt.status ?? "unknown"}`)}</dd></div>
    <div><dt className="text-muted">{t("control.progress")}</dt><dd>{receipt.completed ?? 0} / {receipt.total ?? 0}</dd></div>
    <div><dt className="text-muted">{t("control.process")}</dt><dd>{t(receipt.process_stopped ? "control.confirmed" : "control.unknown")}</dd></div>
    <div><dt className="text-muted">{t("control.released")}</dt><dd>{t(receipt.input_released ? "control.confirmed" : "control.unknown")}</dd></div>
    <div><dt className="text-muted">{t("control.verification")}</dt><dd>{t(`control.verify.${receipt.verification ?? "skipped"}`)}</dd></div>
  </dl>;
}

/** 桌面控制与收束回执使用同一服务状态，不在前端推定动作已经停止。 */
export function DesktopControlPanel({ control, history }: { control?: DesktopControl; history: HistoryEntry[] }) {
  const { t } = useTranslation("operation");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (action: "stop" | "takeover" | "release") => operationApi.control(action, control?.revision ?? -1),
    onSuccess: ({ data }) => { if (!data.ok) toast.error(String(data.error || t("control.failed"))); },
    onError: (error) => toast.error(apiErrorMessage(error, t("control.failed"))),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["operationStatus"] }),
  });
  const recent = history.filter((item) => item.kind === "desktop" && item.receipt?.status).slice(0, 5);
  return <Card title={t("control.title")} subtitle={t("control.subtitle")}>
    <div className="space-y-3 min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm" role="status">{t(`control.states.${control?.state ?? "unknown"}`)}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="danger" disabled={!control?.active || mutation.isPending || control.state === "stopping"}
            onClick={() => mutation.mutate("stop")}>{t("control.stop")}</Button>
          <Button size="sm" disabled={!control || control.human || mutation.isPending}
            onClick={() => mutation.mutate("takeover")}>{t("control.takeover")}</Button>
          <Button size="sm" disabled={!control?.human || !!control.active || mutation.isPending}
            onClick={() => mutation.mutate("release")}>{t("control.release")}</Button>
        </div>
      </div>
      {control?.active && <p className="break-all text-xs text-muted">{control.active.scope} · {t("control.progress")} {control.active.completed} / {control.active.total}</p>}
      <p className="text-xs text-muted">{t(control?.automatic_takeover ? "control.automatic" : "control.manual")}</p>
      <p className="text-xs text-muted">{t("control.evidence")}</p>
      {recent.map((entry) => <details key={`${entry.ts}-${entry.op}`} className="rounded-md border border-border p-3">
        <summary className="cursor-pointer break-words text-xs">{entry.op} · {t(`control.states.${entry.receipt?.status}`)}</summary>
        <div className="mt-2 space-y-2">{entry.receipt && <DesktopReceiptView receipt={entry.receipt} />}<p className="text-xs break-words">{entry.detail}</p></div>
      </details>)}
    </div>
  </Card>;
}
