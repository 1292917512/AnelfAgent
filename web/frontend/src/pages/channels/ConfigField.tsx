import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/types";
import { Input, Select, Switch, Textarea } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";
import { ConfigSource } from "@/components/common/ConfigSource";

export function ConfigField({ meta, prefix, value, onChange }: {
  meta: ConfigMetaItem; prefix?: string; value: unknown; onChange: (value: unknown) => void;
}) {
  const { t } = useTranslation("common");
  const id = useId();
  const shortKey = prefix && meta.key.startsWith(prefix) ? meta.key.slice(prefix.length) : meta.key;
  const label = meta.description || shortKey;
  const disabled = !meta.editable;
  const field = { id, "aria-label": label, disabled, className: "w-full" };
  const text = String(value ?? "");
  return <div className="min-w-0 space-y-2">
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <label htmlFor={id} className="text-xs font-medium text-heading">{label}</label>
      <span className="text-[10px] font-mono text-muted">{shortKey}</span>
    </div>
    <ConfigSource item={meta} />
    {meta.type === "boolean" ? <Switch label={label} checked={!!value} disabled={disabled} onChange={onChange} />
      : meta.type === "text" ? <Textarea {...field} value={text} rows={3} onChange={(event) => onChange(event.target.value)} />
      : meta.type === "model" ? <ModelSelect id={id} label={label} value={text} allowEmpty allowPin={false} disabled={disabled} onChange={onChange} />
      : meta.type === "enum" ? <Select {...field} value={text} onChange={(event) => onChange(event.target.value)}>
        {(meta.options ?? []).map((option) => <option key={option} value={option}>{option || t("empty")}</option>)}
      </Select>
      : <Input {...field}
        type={meta.type === "password" ? "password" : ["integer", "float", "range"].includes(meta.type) ? "number" : "text"}
        autoComplete={meta.type === "password" ? "new-password" : "off"}
        min={meta.min ?? undefined} max={meta.max ?? undefined} step={meta.step ?? (meta.type === "integer" ? 1 : "any")}
        value={text} onChange={(event) => {
          const raw = event.target.value;
          onChange(["integer", "float", "range"].includes(meta.type) && raw !== "" ? Number(raw) : raw);
        }} />}
  </div>;
}
