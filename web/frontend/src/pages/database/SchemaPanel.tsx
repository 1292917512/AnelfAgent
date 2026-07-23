import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { databaseApi } from "@/lib/api";
import { LoadingBlock } from "@/components/ui";
import { Lock } from "lucide-react";

export function SchemaPanel({ db, table }: { db: string; table: string }) {
  const { t } = useTranslation("data");
  const { data, isLoading } = useQuery({
    queryKey: ["dbSchema", db, table],
    queryFn: () => databaseApi.schema(db, table).then((r) => r.data),
  });

  if (isLoading || !data) return <LoadingBlock label={t("common:loading")} />;

  return (
    <div className="space-y-4">
      {/* 列定义 */}
      <div className="rounded-md border border-border overflow-auto">
        <table className="w-full text-xs">
          <thead className="bg-panel">
            <tr className="border-b border-border">
              <th className="px-3 py-2 text-left font-medium text-muted">{t("db.colName")}</th>
              <th className="px-3 py-2 text-left font-medium text-muted">{t("db.colType")}</th>
              <th className="px-3 py-2 text-left font-medium text-muted">{t("db.colNotnull")}</th>
              <th className="px-3 py-2 text-left font-medium text-muted">{t("db.colDefault")}</th>
              <th className="px-3 py-2 text-left font-medium text-muted">PK</th>
            </tr>
          </thead>
          <tbody>
            {data.columns.map((c) => (
              <tr key={c.name} className="border-b border-border/50">
                <td className="px-3 py-1.5 font-mono">{c.name}</td>
                <td className="px-3 py-1.5 text-muted">{c.type || "ANY"}</td>
                <td className="px-3 py-1.5 text-muted">{c.notnull ? "NOT NULL" : ""}</td>
                <td className="px-3 py-1.5 text-muted font-mono">{c.default ?? ""}</td>
                <td className="px-3 py-1.5">
                  {c.pk && <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">PK</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 索引 */}
      {data.indexes.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">
            {t("db.indexes")}
          </p>
          <div className="flex flex-wrap gap-2">
            {data.indexes.map((idx) => (
              <span
                key={idx.name}
                className="text-[11px] px-2 py-1 rounded-md bg-secondary border border-border font-mono"
                title={idx.name}
              >
                {idx.unique ? "UNIQUE " : ""}({idx.columns.join(", ")})
              </span>
            ))}
          </div>
        </div>
      )}

      {/* DDL */}
      {data.ddl && (
        <div>
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2 flex items-center gap-2">
            DDL
            {data.readonly && (
              <span className="flex items-center gap-1 text-[10px] font-normal normal-case text-muted">
                <Lock size={11} /> {t("db.readonly")}
              </span>
            )}
          </p>
          <pre className="text-xs font-mono whitespace-pre-wrap break-all bg-secondary rounded-md p-3 border border-border">
            {data.ddl}
          </pre>
        </div>
      )}
    </div>
  );
}
