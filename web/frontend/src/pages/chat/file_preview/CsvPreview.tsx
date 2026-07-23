import { useMemo } from "react";
import { useTranslation } from "react-i18next";

/** 预览渲染的最大行数（超出部分省略，避免大文件卡死渲染） */
const MAX_ROWS = 1000;

/** 解析 CSV/TSV 文本为二维数组（RFC 4180 引号转义状态机） */
export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

interface CsvPreviewProps {
  /** CSV/TSV 源文本 */
  text: string;
  /** 字段分隔符，默认逗号 */
  delimiter?: string;
}

/** CSV/TSV 预览：首行作为表头渲染为可滚动表格 */
export function CsvPreview({ text, delimiter = "," }: CsvPreviewProps) {
  const { t } = useTranslation("workbench");
  const rows = useMemo(() => parseDelimited(text, delimiter), [text, delimiter]);

  if (rows.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">{t("editor.emptyPreview")}</p>;
  }

  const header = rows[0] ?? [];
  const body = rows.slice(1, MAX_ROWS + 1);
  const omitted = rows.length - 1 - body.length;

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-1">
      <div className="flex-1 min-h-0 overflow-auto rounded-md border border-border">
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-hover">
            <tr>
              {header.map((cell, i) => (
                <th key={i} className="px-2 py-1.5 text-left font-medium text-foreground border border-border whitespace-nowrap">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, i) => (
              <tr key={i} className="odd:bg-panel even:bg-transparent">
                {row.map((cell, j) => (
                  <td key={j} className="px-2 py-1 text-foreground border border-border whitespace-pre-wrap break-all">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {omitted > 0 && (
        <p className="text-[11px] text-muted shrink-0">{t("editor.csvTruncated", { count: MAX_ROWS })}</p>
      )}
    </div>
  );
}
