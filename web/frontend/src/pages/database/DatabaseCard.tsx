import { useTranslation } from "react-i18next";
import { Database, Pencil, Trash2 } from "lucide-react";
import type { DbInfo } from "@/lib/types";
import { cn } from "@/lib/utils";
import { formatSize } from "./format";

export function DatabaseCard({ database: db, active, onSelect, onEdit, onDelete }: {
  database: DbInfo;
  active: boolean;
  onSelect: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation("data");
  return (
    <div className={cn("rounded-xl border transition-colors", active
      ? "border-accent bg-accent-subtle" : "border-border bg-card hover:border-border-strong")}>
      <button onClick={onSelect} className="w-full text-left p-3" aria-pressed={active}>
        <div className="flex items-center gap-2">
          <Database size={15} className="text-accent shrink-0" />
          <span className="text-sm font-medium text-heading">{db.name}</span>
          <span className="ml-auto text-[10px] text-muted font-mono">
            {db.external ? db.engine : db.size_bytes == null ? t("db.unmeasured") : formatSize(db.size_bytes)}
          </span>
        </div>
        <p className="text-[11px] text-muted mt-1 leading-snug">{db.description}</p>
        <div className="text-[10px] text-muted mt-2 flex gap-x-2 gap-y-1 flex-wrap">
          {db.engine && <span className="font-mono">{db.engine}</span>}
          {db.table_count != null && <span>{db.table_count} {t("db.tables")}</span>}
          {db.managed_by && <span>{t("db.managedBy", { owner: db.managed_by })}</span>}
          {db.exists === false && <span>{t(db.external ? "conn.unreachable" : "db.notCreated")}</span>}
          {db.error && <span className="text-danger">{t("db.observationFailed")}</span>}
        </div>
      </button>
      {onEdit && onDelete && <div className="flex justify-end gap-1 px-2 pb-2">
        <button onClick={onEdit} className="p-1 rounded text-muted hover:text-accent" aria-label={t("common:edit")}><Pencil size={14} /></button>
        <button onClick={onDelete} className="p-1 rounded text-muted hover:text-danger" aria-label={t("common:delete")}><Trash2 size={14} /></button>
      </div>}
    </div>
  );
}
