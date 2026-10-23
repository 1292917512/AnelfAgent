import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { workflowApi } from "@/lib/api";
import { Button, Input } from "@/components/ui";
import { Modal } from "@/components/ui/Modal";
import { QueryError } from "@/components/common/AsyncState";
import { useDialogSession } from "@/hooks/useDialogSession";

export function CleanupDialog({ onClose, onCleaned }: { onClose: () => void; onCleaned: (count: number) => void }) {
  const { t } = useTranslation("workflow");
  const [days, setDays] = useState("30");
  const [includeStopped, setIncludeStopped] = useState(false);
  const [deleted, setDeleted] = useState<number | null>(null);
  const session = useDialogSession(() => { if (deleted !== null) onCleaned(deleted); else onClose(); });
  const validDays = days.trim() !== "" && Number.isInteger(Number(days)) && Number(days) >= 0;
  const cleanup = useMutation({ mutationFn: () => workflowApi.cleanupRuns(Number(days), includeStopped),
    onSuccess: (result) => { setDeleted(result.data.deleted); session.close(); } });
  return <Modal {...session.surfaceProps} title={t("cleanup.title")} onClose={session.close} dismissible={!cleanup.isPending}
    footer={<>
      <Button onClick={session.close} disabled={cleanup.isPending}>{t("common:cancel")}</Button>
      <Button variant="danger" disabled={!validDays} loading={cleanup.isPending} onClick={() => cleanup.mutate()}>{t("cleanup.submit")}</Button>
    </>}>
    <div className="space-y-4">
      <p className="text-sm text-muted">{t("cleanup.help")}</p>
      <label className="block space-y-2 text-sm"><span>{t("cleanup.days")}</span>
        <Input type="number" min={0} step={1} value={days} disabled={cleanup.isPending} onChange={(event) => setDays(event.target.value)} />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={includeStopped} disabled={cleanup.isPending} onChange={(event) => setIncludeStopped(event.target.checked)} />
        {t("cleanup.includeStopped")}
      </label>
      {cleanup.error && <QueryError compact error={cleanup.error} />}
    </div>
  </Modal>;
}
