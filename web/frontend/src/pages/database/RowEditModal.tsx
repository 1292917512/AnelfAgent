import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";
import { databaseApi } from "@/lib/api";
import type { CellValue, DbColumnInfo, DbRow } from "@/lib/types";
import { useAppStore } from "@/stores/app-store";
import { Button, Input, Modal, Textarea, toast } from "@/components/ui";
import { Pencil, Plus } from "lucide-react";

/** 从智能序列化值还原可编辑的字符串形态 */
function toEditString(value: CellValue): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  switch (value.__type__) {
    case "json":
      return value.raw ?? JSON.stringify(value.value, null, 2);
    case "ts":
      return String(value.value ?? "");
    case "text":
      return value.text ?? "";
    default:
      return "";
  }
}

function isNumericColumn(type: string): boolean {
  return /INT|REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL/.test(type);
}

function looksJsonColumn(name: string, initial: string): boolean {
  if (name.toLowerCase().endsWith("_json")) return true;
  const s = initial.trim();
  return s.startsWith("{") || s.startsWith("[");
}

interface FieldState {
  text: string;
  isNull: boolean;
}

export function RowEditModal({
  db,
  table,
  columns,
  row,
  onClose,
  onSaved,
}: {
  db: string;
  table: string;
  columns: DbColumnInfo[];
  /** null = 插入新行 */
  row: DbRow | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation("data");
  const theme = useAppStore((s) => s.theme);
  const isInsert = row === null;

  // BLOB/向量列不可编辑（二进制安全考虑）
  const editableColumns = useMemo(
    () =>
      columns.map((col) => {
        const raw = row?.values[col.name];
        const isBlob =
          typeof raw === "object" && raw !== null && "__type__" in raw &&
          (raw.__type__ === "blob" || raw.__type__ === "vec");
        return { col, readonly: isBlob };
      }),
    [columns, row],
  );

  const [fields, setFields] = useState<Record<string, FieldState>>(() => {
    const init: Record<string, FieldState> = {};
    for (const col of columns) {
      const value = row?.values[col.name];
      init[col.name] = {
        text: value == null ? "" : toEditString(value),
        isNull: isInsert ? false : value == null,
      };
    }
    return init;
  });

  useEffect(() => {
    // 插入模式下自增主键留空（由 SQLite 生成）
    if (!isInsert) return;
    setFields((prev) => {
      const next = { ...prev };
      for (const col of columns) {
        if (col.pk && /INT/.test(col.type)) next[col.name] = { text: "", isNull: false };
      }
      return next;
    });
  }, [isInsert, columns]);

  const saveMut = useMutation({
    mutationFn: async () => {
      const values: Record<string, unknown> = {};
      for (const col of columns) {
        const field = fields[col.name];
        if (!field) continue;
        if (field.isNull) {
          values[col.name] = null;
          continue;
        }
        const text = field.text;
        if (isInsert && col.pk && /INT/.test(col.type) && text.trim() === "") {
          continue; // 自增主键留空
        }
        if (isNumericColumn(col.type)) {
          if (text.trim() === "") {
            values[col.name] = null;
          } else {
            const num = Number(text);
            if (Number.isNaN(num)) throw new Error(t("db.invalidNumber", { col: col.name }));
            values[col.name] = num;
          }
        } else {
          values[col.name] = text;
        }
      }
      if (isInsert) {
        await databaseApi.insertRow(db, table, values);
      } else {
        await databaseApi.updateRow(db, table, row!.__rowid__, values);
      }
    },
    onSuccess: () => {
      toast.success(t("db.saveOk"));
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(`${t("db.saveFailed")}: ${e}`),
  });

  return (
    <Modal
      open
      onClose={onClose}
      width="max-w-2xl"
      title={
        <span className="flex items-center gap-2">
          {isInsert ? <Plus size={18} className="text-accent" /> : <Pencil size={18} className="text-accent" />}
          {isInsert ? t("db.insertRow") : t("db.editRow", { rowid: row?.__rowid__ })}
          <span className="text-xs text-muted font-normal">{table}</span>
        </span>
      }
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={() => saveMut.mutate()} loading={saveMut.isPending}>
            {saveMut.isPending ? t("common:saving") : t("common:save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
        {editableColumns.map(({ col, readonly }) => {
          const field = fields[col.name] ?? { text: "", isNull: false };
          const jsonLike = looksJsonColumn(col.name, field.text);
          return (
            <div key={col.name}>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-medium text-muted">
                  <span className="font-mono text-foreground">{col.name}</span>
                  <span className="ml-1.5 text-[10px] text-muted/70">{col.type || "ANY"}</span>
                  {col.pk && (
                    <span className="ml-1 text-[10px] px-1 rounded bg-accent/10 text-accent">PK</span>
                  )}
                </label>
                {!col.notnull && !readonly && (
                  <label className="flex items-center gap-1 text-[11px] text-muted cursor-pointer">
                    <input
                      type="checkbox"
                      checked={field.isNull}
                      onChange={(e) =>
                        setFields((prev) => ({
                          ...prev,
                          [col.name]: { ...(prev[col.name] ?? { text: "", isNull: false }), isNull: e.target.checked },
                        }))
                      }
                      className="accent-[var(--accent)]"
                    />
                    NULL
                  </label>
                )}
              </div>
              {readonly ? (
                <p className="text-[11px] text-muted italic px-2 py-1.5 rounded bg-secondary border border-border">
                  {t("db.blobReadonly")}
                </p>
              ) : field.isNull ? (
                <p className="text-[11px] text-muted/50 italic px-2 py-1.5 rounded bg-secondary border border-border">
                  NULL
                </p>
              ) : jsonLike ? (
                <CodeMirror
                  value={field.text}
                  onChange={(v) =>
                    setFields((prev) => ({ ...prev, [col.name]: { ...(prev[col.name] ?? { text: "", isNull: false }), text: v } }))
                  }
                  extensions={[json()]}
                  theme={theme}
                  height="140px"
                  style={{ fontSize: 12, borderRadius: 6, overflow: "hidden" }}
                  basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
                />
              ) : isNumericColumn(col.type) ? (
                <Input
                  type="number"
                  value={field.text}
                  onChange={(e) =>
                    setFields((prev) => ({ ...prev, [col.name]: { ...(prev[col.name] ?? { text: "", isNull: false }), text: e.target.value } }))
                  }
                />
              ) : field.text.length > 80 || field.text.includes("\n") ? (
                <Textarea
                  value={field.text}
                  onChange={(e) =>
                    setFields((prev) => ({ ...prev, [col.name]: { ...(prev[col.name] ?? { text: "", isNull: false }), text: e.target.value } }))
                  }
                  rows={4}
                />
              ) : (
                <Input
                  value={field.text}
                  onChange={(e) =>
                    setFields((prev) => ({ ...prev, [col.name]: { ...(prev[col.name] ?? { text: "", isNull: false }), text: e.target.value } }))
                  }
                />
              )}
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
