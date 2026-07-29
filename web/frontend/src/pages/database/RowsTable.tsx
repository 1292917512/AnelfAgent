import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Eye, Pencil, Trash2 } from "lucide-react";
import type { DbColumnInfo, DbRow } from "@/lib/types";
import { CellContent } from "./CellContent";

/** 数据行表格：排序表头 + 行操作（查看/编辑/删除） */
export function RowsTable({
  db,
  columns,
  rows,
  sort,
  order,
  readonly,
  onToggleSort,
  onView,
  onEdit,
  onDelete,
}: {
  db: string;
  columns: DbColumnInfo[];
  rows: DbRow[];
  sort: string | undefined;
  order: "asc" | "desc";
  readonly: boolean;
  onToggleSort: (col: string) => void;
  onView: (rowid: number) => void;
  onEdit: (row: DbRow) => void;
  onDelete: (row: DbRow) => void;
}) {
  const { t } = useTranslation("data");
  return (
    <div className="rounded-md border border-border overflow-auto max-h-[60vh]">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-panel z-10">
          <tr className="border-b border-border">
            {columns.map((c) => (
              <th
                key={c.name}
                onClick={() => onToggleSort(c.name)}
                className="px-2.5 py-2 text-left font-medium text-muted cursor-pointer hover:text-foreground select-none whitespace-nowrap"
              >
                <span className="inline-flex items-center gap-1">
                  {c.name}
                  {c.pk && <span className="text-[9px] px-1 rounded bg-accent/10 text-accent">PK</span>}
                  {sort === c.name && (order === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
                </span>
              </th>
            ))}
            <th className="px-2.5 py-2 w-24" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.__rowid__} className="border-b border-border/50 hover:bg-hover/50">
              {columns.map((c) => (
                <td key={c.name} className="px-2.5 py-1.5 max-w-[220px] truncate align-top">
                  <CellContent value={row.values[c.name] ?? null} />
                </td>
              ))}
              <td className="px-2.5 py-1.5 whitespace-nowrap text-right">
                {!db.startsWith("ext:") && (
                  <button
                    onClick={() => onView(row.__rowid__)}
                    className="p-1 rounded text-muted hover:text-accent transition-colors"
                    title={t("db.viewRow")}
                  >
                    <Eye size={13} />
                  </button>
                )}
                {!readonly && (
                  <>
                    <button
                      onClick={() => onEdit(row)}
                      className="p-1 rounded text-muted hover:text-accent transition-colors"
                      title={t("common:edit")}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      onClick={() => onDelete(row)}
                      className="p-1 rounded text-muted hover:text-danger transition-colors"
                      title={t("common:delete")}
                    >
                      <Trash2 size={13} />
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
