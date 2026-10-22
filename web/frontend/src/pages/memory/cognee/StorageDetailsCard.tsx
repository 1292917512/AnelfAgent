import { useTranslation } from "react-i18next";
import { Card } from "@/components/common/Card";
import { formatSize } from "@/lib/utils";
import type { CogneeStatus } from "@/lib/types";

export function StorageDetailsCard({ status }: { status?: CogneeStatus }) {
  const { t } = useTranslation("memory");
  const storage = status?.storage;
  const retirement = status?.retirement;
  if (!storage) return null;
  const categories = [
    ["storageGraph", storage.graph_bytes], ["storageLance", storage.lance_bytes],
    ["storageMetadata", storage.metadata_bytes], ["storageData", storage.data_bytes], ["storageOther", storage.other_bytes],
  ] as const;
  return <Card title={t("cognee.storageDetails")} subtitle={t("cognee.storageScope")}>
    <p className="mb-3 text-lg font-semibold text-heading">{formatSize(storage.total_bytes)}</p>
    <div className="space-y-3">
      {categories.map(([label, bytes]) => <div key={label}>
        <div className="mb-1 flex justify-between gap-3 text-sm text-muted">
          <span>{t(`cognee.${label}`)}</span>
          <span>{formatSize(bytes)} · {storage.total_bytes ? (bytes / storage.total_bytes * 100).toFixed(1) : "0"}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-elevated"><div className="h-full rounded-full bg-accent"
          style={{ width: `${storage.total_bytes ? bytes / storage.total_bytes * 100 : 0}%` }} /></div>
      </div>)}
      <p className="text-xs text-muted">{storage.computed_at
        ? t("cognee.storageMeasured", { time: new Date(storage.computed_at * 1000).toLocaleString() })
        : t("cognee.storagePending")}</p>
      {retirement?.stage && <div className="rounded-lg border border-border p-3 space-y-2 text-sm text-muted">
        <p className="break-all text-heading">{t("cognee.retirementTarget", { name: retirement.dataset_name })}</p>
        <p role="status">{t(`cognee.retirementStages.${retirement.stage}`, { defaultValue: retirement.stage })}
          {` · ${t("cognee.retirementPreserved", { count: retirement.preserved, total: retirement.documents })}`}</p>
        {retirement.backup_path && <p className="break-all text-xs">{t("cognee.projectionBackup")}: {retirement.backup_path}</p>}
        {retirement.error && <p role="alert" className="text-danger break-words">{retirement.error}</p>}
      </div>}
    </div>
  </Card>;
}
