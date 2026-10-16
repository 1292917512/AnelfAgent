import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { Button, Input } from "@/components/ui";
import type { KVRow } from "./types";

export function KVEditor({ rows, onChange, label }: { rows: KVRow[]; onChange: (rows: KVRow[]) => void; label: string }) {
  const { t } = useTranslation("mcp");
  return <fieldset className="space-y-2">
    <legend className="mb-2 text-xs font-medium text-muted">{label}</legend>
    {rows.map((row, index) => <div key={index} className="flex items-start gap-2 rounded-lg bg-secondary/50 p-2">
      <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-2">
        <Input value={row.k} aria-label={`${label} ${index + 1} ${t("form.kvKey")}`} placeholder={t("form.kvKey")} className="font-mono text-xs"
          onChange={(event) => onChange(rows.map((value, i) => i === index ? { ...value, k: event.target.value } : value))} />
        <Input value={row.v} aria-label={`${label} ${index + 1} ${t("form.kvValue")}`} placeholder={t("form.kvValue")} autoComplete="off" className="font-mono text-xs"
          onChange={(event) => onChange(rows.map((value, i) => i === index ? { ...value, v: event.target.value } : value))} />
      </div>
      <Button type="button" variant="ghost" size="icon" aria-label={`${t("common:delete")} ${label} ${index + 1}`}
        onClick={() => onChange(rows.filter((_, i) => i !== index))}><Trash2 size={14} /></Button>
    </div>)}
    <Button type="button" variant="ghost" size="sm" onClick={() => onChange([...rows, { k: "", v: "" }])}><Plus size={13} />{t("form.addRow")}</Button>
    {!!rows.length && <p className="text-[11px] text-muted">{t("form.secretHint")}</p>}
  </fieldset>;
}

export function Field({ label, htmlFor, optional, children }: { label: string; htmlFor: string; optional?: boolean; children: ReactNode }) {
  const { t } = useTranslation("mcp");
  return <div className="space-y-1.5">
    <label htmlFor={htmlFor} className="block text-xs font-medium text-muted">{label}{optional && <span className="ml-1 font-normal">({t("form.optional")})</span>}</label>
    {children}
  </div>;
}
