import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { Button, Input } from "@/components/ui";
import type { KVRow } from "./types";

export function KVEditor({
  rows,
  onChange,
  addLabel,
}: {
  rows: KVRow[];
  onChange: (rows: KVRow[]) => void;
  addLabel: string;
}) {
  const { t } = useTranslation("mcp");
  return (
    <div className="space-y-1.5">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input
            value={row.k}
            placeholder={t("form.kvKey")}
            className="flex-1 font-mono text-xs"
            onChange={(e) =>
              onChange(rows.map((r, j) => (j === i ? { ...r, k: e.target.value } : r)))
            }
          />
          <Input
            value={row.v}
            placeholder={t("form.kvValue")}
            className="flex-[2] font-mono text-xs"
            onChange={(e) =>
              onChange(rows.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))
            }
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      ))}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onChange([...rows, { k: "", v: "" }])}
      >
        <Plus size={13} />
        {addLabel}
      </Button>
    </div>
  );
}

export function Field({
  label,
  optional,
  children,
}: {
  label: string;
  optional?: boolean;
  children: React.ReactNode;
}) {
  const { t } = useTranslation("mcp");
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-muted">
        {label}
        {optional && (
          <span className="ml-1 text-[10px] text-muted/70">({t("form.optional")})</span>
        )}
      </span>
      {children}
    </label>
  );
}
