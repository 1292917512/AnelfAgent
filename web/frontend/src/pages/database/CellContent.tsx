import { useTranslation } from "react-i18next";
import type { CellValue } from "@/lib/types";

/**
 * 智能单元格渲染 — 与后端 services/database.py 的序列化协议对应：
 * - blob/vec：二进制与 float32 向量徽标
 * - ts：纳秒时间戳 → 可读时间
 * - json：结构化徽标 + 紧凑预览
 * - text(truncated)：截断标记
 */
export function CellContent({ value }: { value: CellValue }) {
  const { t } = useTranslation("data");

  if (value === null || value === undefined) {
    return <span className="text-muted/50 italic">NULL</span>;
  }
  if (typeof value === "number") {
    return <span className="font-mono">{String(value)}</span>;
  }
  if (typeof value === "string") {
    return <span>{value}</span>;
  }

  switch (value.__type__) {
    case "blob":
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted border border-border font-mono">
          BLOB {value.bytes}B
        </span>
      );
    case "vec":
      return (
        <span
          className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 font-mono"
          title={value.preview ? `[${value.preview.join(", ")} …]` : undefined}
        >
          VEC×{value.dims}
        </span>
      );
    case "ts":
      return (
        <span className="font-mono text-xs" title={String(value.value)}>
          {value.text}
        </span>
      );
    case "json": {
      const preview = (value.raw ?? "").replace(/\s+/g, " ").slice(0, 80);
      return (
        <span className="inline-flex items-center gap-1.5 max-w-full">
          <span className="text-[10px] px-1 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 shrink-0">
            JSON
          </span>
          <span className="font-mono text-xs truncate" title={value.raw}>
            {preview}
            {value.truncated && " …"}
          </span>
        </span>
      );
    }
    case "text":
      return (
        <span>
          {value.text}
          {value.truncated && (
            <span className="text-muted"> …({t("db.truncatedHint")})</span>
          )}
        </span>
      );
    default:
      return <span>{String(value)}</span>;
  }
}
