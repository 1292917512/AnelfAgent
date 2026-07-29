import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";
import { Eye } from "lucide-react";
import { databaseApi } from "@/lib/api";
import type { CellValue } from "@/lib/types";
import { useAppStore } from "@/stores/app-store";
import { LoadingBlock, Modal } from "@/components/ui";
import { CellContent } from "./CellContent";

/** 单行全文详情（JSON 用 CodeMirror 美化只读展示） */
export function RowDetailModal({
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
