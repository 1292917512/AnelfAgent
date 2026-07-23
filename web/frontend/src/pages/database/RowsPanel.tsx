import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";
import { databaseApi } from "@/lib/api";
import type { CellValue, DbRow } from "@/lib/types";
import { useAppStore } from "@/stores/app-store";
import { Button, ConfirmDialog, Input, LoadingBlock, Modal, Select, toast } from "@/components/ui";
import {
  ArrowDown,
  ArrowUp,
  Eye,
  Lock,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { CellContent } from "./CellContent";
import { RowEditModal } from "./RowEditModal";

const PAGE_SIZE = 50;

/** 单行全文详情（JSON 用 CodeMirror 美化只读展示） */
function RowDetailModal({
  db,
  table,
  rowid,
  onClose,
}: {
  db: string;
  table: string;
  rowid: number;
  onClose: () => void;
}) {
  const { t } = useTranslation("data");
  const theme = useAppStore((s) => s.theme);
  const { data: row, isLoading } = useQuery({
    queryKey: ["dbRow", db, table, rowid],
    queryFn: () => databaseApi.row(db, table, rowid).then((r) => r.data),
  });

  const renderValue = (value: CellValue) => {
    if (value !== null && typeof value === "object" && !Array.isArray(value) && "__type__" in value) {
      if (value.__type__ === "json") {
        return (
          <CodeMirror
            value={JSON.stringify(value.value, null, 2)}
            extensions={[json()]}
            theme={theme}
            height="auto"
            editable={false}
            style={{ fontSize: 12, borderRadius: 6, overflow: "hidden" }}
            basicSetup={{ lineNumbers: true, foldGutter: true }}
          />
        );
      }
      if (value.__type__ === "text") {
        return (
          <pre className="text-xs whitespace-pre-wrap break-all bg-secondary rounded-md p-2 border border-border max-h-60 overflow-y-auto">
            {value.text}
          </pre>
        );
      }
    }
    if (typeof value === "string" && value.length > 120) {
      return (
        <pre className="text-xs whitespace-pre-wrap break-all bg-secondary rounded-md p-2 border border-border max-h-60 overflow-y-auto">
          {value}
        </pre>
      );
    }
    return <CellContent value={value} />;
  };

  return (
    <Modal
      open
      onClose={onClose}
      width="max-w-2xl"
      title={
        <span className="flex items-center gap-2">
          <Eye size={18} className="text-accent" />
          {t("db.rowDetail", { rowid })}
          <span className="text-xs text-muted font-normal">{table}</span>
        </span>
      }
    >
      {isLoading || !row ? (
        <LoadingBlock label={t("common:loading")} />
      ) : (
        <div className="space-y-3 max-h-[65vh] overflow-y-auto pr-1">
          {Object.entries(row.values).map(([col, value]) => (
            <div key={col}>
              <p className="text-[11px] font-medium text-muted mb-1 font-mono">{col}</p>
              {renderValue(value)}
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

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
        <div className="rounded-md border border-border overflow-auto max-h-[60vh]">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-panel z-10">
              <tr className="border-b border-border">
                {columns.map((c) => (
                  <th
                    key={c.name}
                    onClick={() => toggleSort(c.name)}
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
              {data.items.map((row) => (
                <tr key={row.__rowid__} className="border-b border-border/50 hover:bg-hover/50">
                  {columns.map((c) => (
                    <td key={c.name} className="px-2.5 py-1.5 max-w-[220px] truncate align-top">
                      <CellContent value={row.values[c.name] ?? null} />
                    </td>
                  ))}
                  <td className="px-2.5 py-1.5 whitespace-nowrap text-right">
                    <button
                      onClick={() => setViewRowid(row.__rowid__)}
                      className="p-1 rounded text-muted hover:text-accent transition-colors"
                      title={t("db.viewRow")}
                    >
                      <Eye size={13} />
                    </button>
                    {!readonly && (
                      <>
                        <button
                          onClick={() => setEditRow(row)}
                          className="p-1 rounded text-muted hover:text-accent transition-colors"
                          title={t("common:edit")}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          onClick={() => setDeleteTarget(row)}
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
