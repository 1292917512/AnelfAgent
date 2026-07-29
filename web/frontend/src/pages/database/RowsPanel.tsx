import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { databaseApi } from "@/lib/api";
import type { DbRow } from "@/lib/types";
import { Button, ConfirmDialog, Input, LoadingBlock, Select, toast } from "@/components/ui";
import { Lock, Plus, Search } from "lucide-react";
import { RowEditModal } from "./RowEditModal";
import { RowDetailModal } from "./RowDetailModal";
import { RowsTable } from "./RowsTable";

const PAGE_SIZE = 50;

export function RowsPanel({ db, table }: { db: string; table: string }) {
  const { t } = useTranslation("data");
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<string | undefined>();
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [filterCol, setFilterCol] = useState("");
  const [filterText, setFilterText] = useState("");
  const [appliedFilter, setAppliedFilter] = useState<{ col: string; text: string } | null>(null);
  const [viewRowid, setViewRowid] = useState<number | null>(null);
  const [editRow, setEditRow] = useState<DbRow | null>(null);
  const [inserting, setInserting] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DbRow | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["dbRows", db, table, page, sort, order, appliedFilter],
    queryFn: () =>
      databaseApi
        .rows(db, table, {
          page,
          page_size: PAGE_SIZE,
          sort,
          order,
          filter_col: appliedFilter?.col || undefined,
          filter_text: appliedFilter?.text || undefined,
        })
        .then((r) => r.data),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["dbRows", db, table] });

  const deleteMut = useMutation({
    mutationFn: (rowid: number) => databaseApi.deleteRow(db, table, rowid),
    onSuccess: () => {
      toast.success(t("db.deleteOk"));
      setDeleteTarget(null);
      invalidate();
    },
    onError: (e) => toast.error(`${t("db.deleteFailed")}: ${e}`),
  });

  const toggleSort = (col: string) => {
    if (sort !== col) {
      setSort(col);
      setOrder("asc");
    } else if (order === "asc") {
      setOrder("desc");
    } else {
      setSort(undefined);
    }
    setPage(1);
  };

  const applyFilter = () => {
    setAppliedFilter(filterCol && filterText ? { col: filterCol, text: filterText } : null);
    setPage(1);
  };

  const columns = data?.columns ?? [];
  const readonly = data?.readonly ?? true;

  return (
    <div className="space-y-3">
      {/* 筛选 + 新增 */}
      <div className="flex items-center gap-2 flex-wrap">
        <Select
          value={filterCol}
          onChange={(e) => setFilterCol(e.target.value)}
          className="w-40"
        >
          <option value="">{t("db.filterColumn")}</option>
          {columns.map((c) => (
            <option key={c.name} value={c.name}>{c.name}</option>
          ))}
        </Select>
        <div className="relative flex-1 min-w-[160px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilter()}
            placeholder={t("db.filterPlaceholder")}
            className="pl-8"
          />
        </div>
        <Button variant="secondary" size="sm" onClick={applyFilter}>
          {t("db.filterApply")}
        </Button>
        {appliedFilter && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setAppliedFilter(null); setFilterText(""); setPage(1); }}
          >
            {t("db.filterClear")}
          </Button>
        )}
        <div className="ml-auto flex items-center gap-2">
          {readonly && (
            <span className="flex items-center gap-1 text-[11px] text-muted">
              <Lock size={12} /> {t("db.readonly")}
            </span>
          )}
          {!readonly && (
            <Button variant="primary" size="sm" onClick={() => setInserting(true)}>
              <Plus size={14} /> {t("db.insertRow")}
            </Button>
          )}
        </div>
      </div>

      {/* 数据表格 */}
      {isLoading ? (
        <LoadingBlock label={t("common:loading")} />
      ) : !data || data.items.length === 0 ? (
        <p className="text-sm text-muted text-center py-10">{t("db.noRows")}</p>
      ) : (
        <RowsTable
          db={db}
          columns={columns}
          rows={data.items}
          sort={sort}
          order={order}
          readonly={readonly}
          onToggleSort={toggleSort}
          onView={setViewRowid}
          onEdit={setEditRow}
          onDelete={setDeleteTarget}
        />
      )}

      {/* 分页 */}
      {data && data.total > 0 && (
        <div className="flex items-center justify-center gap-3">
          <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            {t("common:prev")}
          </Button>
          <span className="text-xs text-muted">
            {page} / {Math.max(1, data.pages)}（{t("db.totalRows", { count: data.total })}）
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= Math.max(1, data.pages)}
            onClick={() => setPage(page + 1)}
          >
            {t("common:next")}
          </Button>
        </div>
      )}

      {/* 弹窗 */}
      {viewRowid !== null && (
        <RowDetailModal db={db} table={table} rowid={viewRowid} onClose={() => setViewRowid(null)} />
      )}
      {(editRow || inserting) && (
        <RowEditModal
          db={db}
          table={table}
          columns={columns}
          row={inserting ? null : editRow}
          onClose={() => { setEditRow(null); setInserting(false); }}
          onSaved={invalidate}
        />
      )}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.__rowid__)}
        title={t("common:delete")}
        message={t("db.deleteConfirm", { rowid: deleteTarget?.__rowid__ })}
        confirmText={deleteMut.isPending ? t("common:saving") : t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={deleteMut.isPending}
      />
    </div>
  );
}
