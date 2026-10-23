import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Layers } from "lucide-react";
import type { DbInfo } from "@/lib/types";
import { formatSize } from "./format";

/** 受管组件的只读观测与所属管理入口；不复制同步或恢复状态机。 */
export function ManagedDatabaseCard({ database: db }: { database: DbInfo }) {
  const { t } = useTranslation("data");
  const managementUrl = db.management_url?.startsWith("/") && !db.management_url.startsWith("//")
    ? db.management_url : null;
  return (
    <section aria-label={db.name} className="rounded-xl border border-border bg-card p-4 space-y-4">
      <div className="flex items-center gap-2 text-heading font-medium"><Layers size={18} />{db.name}</div>
      <p className="text-sm text-muted">{t("db.managedDescription", { owner: db.managed_by })}</p>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
        <div><dt className="text-muted text-xs">{t("db.engine")}</dt><dd>{db.engine}</dd></div>
        <div><dt className="text-muted text-xs">{t("db.componentSize")}</dt><dd>{db.size_bytes == null ? t("db.unmeasured") : formatSize(db.size_bytes)}</dd></div>
        <div><dt className="text-muted text-xs">{t("db.volume")}</dt><dd>{db.volume_id}</dd></div>
        <div><dt className="text-muted text-xs">{t("db.measuredAt")}</dt><dd>{db.measured_at ? new Date(db.measured_at * 1000).toLocaleString() : t("db.unmeasured")}</dd></div>
        <div className="sm:col-span-2 min-w-0"><dt className="text-muted text-xs">{t("db.path")}</dt><dd className="font-mono text-xs break-all">{db.path}</dd></div>
      </dl>
      <p className="text-xs text-muted">{t("db.snapshotNote")}</p>
      {db.needs_restart && <p className="text-xs text-warning">{t("db.pathPending")}</p>}
      {db.error && <p role="alert" className="text-xs text-danger">{t("db.observationFailed")}: {db.error}</p>}
      <div className="flex flex-wrap gap-3 text-sm">
        {managementUrl && <Link className="text-accent hover:underline" to={managementUrl}>{t("db.openManagement", { owner: db.managed_by })}</Link>}
        {db.volume_id && <Link className="text-accent hover:underline" to={`/data?tab=volumes&volume=${encodeURIComponent(db.volume_id)}`}>{t("db.openVolume")}</Link>}
      </div>
    </section>
  );
}
