import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Select, Switch, Textarea } from "@/components/ui";
import type { ConfigFieldSchema } from "@/lib/types/config";

interface SchemaFieldProps {
  schema: ConfigFieldSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  label?: string;
  disabled?: boolean;
  definitions?: Record<string, ConfigFieldSchema>;
  translate?: (key: string, fallback: string) => string;
  path?: string;
}

function resolve(schema: ConfigFieldSchema, definitions: Record<string, ConfigFieldSchema>): ConfigFieldSchema {
  const ref = schema.$ref?.split("/").pop();
  if (ref && definitions[ref]) return { ...definitions[ref], ...schema, $ref: undefined };
  const branch = schema.anyOf?.find(option => option.type !== "null");
  return branch ? resolve({ ...branch, ...schema, anyOf: undefined }, definitions) : schema;
}

function initial(schema: ConfigFieldSchema, definitions: Record<string, ConfigFieldSchema>): unknown {
  const field = resolve(schema, definitions);
  if (field.default !== undefined) return structuredClone(field.default);
  if (field.enum?.length) return field.enum[0];
  if (field.type === "object") return Object.fromEntries(Object.entries(field.properties ?? {}).map(([key, child]) => [key, initial(child, definitions)]));
  if (field.type === "array") return [];
  if (field.type === "boolean") return false;
  if (["integer", "number"].includes(field.type ?? "")) return field.minimum ?? 0;
  return "";
}

export function ChoiceField({ options, value, label, disabled, onChange, optionLabel }: {
  options: string[]; value: unknown; label: string; disabled?: boolean;
  onChange: (value: string[]) => void; optionLabel?: (option: string) => string;
}) {
  const { t } = useTranslation("config");
  const [query, setQuery] = useState("");
  const selected = Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
  const all = [...new Set([...options, ...selected])];
  const visible = all.filter(option => `${option} ${optionLabel?.(option) ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  return <fieldset disabled={disabled} className="min-w-0 space-y-2" aria-label={label}>
    {all.length > 12 && <Input value={query} onChange={event => setQuery(event.target.value)} aria-label={t("schema.searchOptions", { name: label })} placeholder={t("schema.search")} />}
    <div className="flex flex-wrap gap-2 text-xs">
      <Button size="sm" disabled={disabled} onClick={() => onChange([...new Set([...selected, ...visible])])}>{t("schema.selectVisible")}</Button>
      <Button size="sm" disabled={disabled} onClick={() => onChange(selected.filter(option => !visible.includes(option)))}>{t("schema.clearVisible")}</Button>
      <span className="self-center text-muted">{t("schema.selected", { count: selected.length })}</span>
    </div>
    <div className="grid max-h-72 gap-x-4 overflow-y-auto rounded-lg border border-border p-3 sm:grid-cols-2">
      {visible.map(option => <label key={option} className="flex min-w-0 cursor-pointer items-start gap-2 py-1.5 text-xs text-heading">
        <input type="checkbox" className="mt-0.5 shrink-0 accent-accent" checked={selected.includes(option)}
          onChange={event => onChange(event.target.checked ? [...selected, option] : selected.filter(entry => entry !== option))} />
        <span className="break-all">{optionLabel?.(option) ?? option}</span>
      </label>)}
      {!visible.length && <p className="text-xs text-muted">{t("schema.noOptions")}</p>}
    </div>
  </fieldset>;
}

export function JsonField({ value, label, disabled, onChange }: {
  value: unknown; label: string; disabled?: boolean; onChange: (value: unknown) => void;
}) {
  const { t } = useTranslation("config");
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState(false);
  return <div className="space-y-1">
    <Textarea aria-label={label} aria-invalid={error} disabled={disabled} rows={4}
      value={draft ?? JSON.stringify(value ?? null, null, 2)} onChange={event => {
        setDraft(event.target.value);
        setError(false);
      }} onBlur={event => {
        if (draft === null) return;
        try { const parsed: unknown = JSON.parse(draft); onChange(parsed); setDraft(null); setError(false); event.target.setCustomValidity(""); }
        catch { setError(true); event.target.setCustomValidity(t("schema.invalidJson")); }
      }} />
    {error && <p role="alert" className="text-xs text-danger">{t("schema.invalidJson")}</p>}
  </div>;
}

export function SchemaField({ schema, value, onChange, label, disabled, definitions, translate, path = "" }: SchemaFieldProps) {
  const { t } = useTranslation("config");
  const id = useId();
  const defs = definitions ?? schema.$defs ?? {};
  const field = resolve(schema, defs);
  const name = label ?? translate?.(`field.${path}`, field.title ?? path) ?? field.title ?? path;
  if (schema.anyOf?.some(option => option.type === "null")) return <div className="space-y-2">
    <Switch label={t("schema.inherit")} disabled={disabled} checked={value == null} onChange={checked => onChange(checked ? null : initial({ ...field, default: undefined }, defs))} />
    {value != null && <SchemaField schema={{ ...field, anyOf: undefined }} value={value} onChange={onChange} label={name} disabled={disabled} definitions={defs} translate={translate} path={path} />}
  </div>;
  const choices = field.options ?? field.items?.enum?.map(String);
  if (field.type === "array" && choices) return <ChoiceField label={name} options={choices} value={value} disabled={disabled} onChange={onChange}
    optionLabel={option => translate?.(`option.${option}`, option) ?? option} />;
  if (field.type === "object" && field.properties) {
    const entries = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    return <div className="grid min-w-0 gap-3 sm:grid-cols-2">{Object.entries(field.properties).map(([key, child]) => {
      const childPath = path ? `${path}.${key}` : key;
      const childField = resolve(child, defs);
      const childLabel = translate?.(`field.${childPath}`, child.description ?? child.title ?? key) ?? child.description ?? child.title ?? key;
      return <div key={key} className={`min-w-0 space-y-1 ${["array", "object"].includes(childField.type ?? "") ? "sm:col-span-2" : ""}`}>
        <p className="text-xs font-medium text-heading">{childLabel}{field.required?.includes(key) ? " *" : ""}</p>
        <SchemaField schema={child} value={Reflect.get(entries, key) ?? child.default} label={childLabel} path={childPath} translate={translate}
          definitions={defs} disabled={disabled} onChange={next => onChange({ ...entries, [key]: next })} />
      </div>;
    })}</div>;
  }
  if (field.type === "array" && field.items) {
    const values: unknown[] = Array.isArray(value) ? value : [];
    const itemSchema = field.items;
    return <div className="min-w-0 space-y-3">
      {values.map((entry, index) => <fieldset key={index} disabled={disabled} className="min-w-0 space-y-2 rounded-lg border border-border p-3">
        <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-heading">{name} {index + 1}</span>
          <Button size="sm" aria-label={t("schema.removeEntry", { name, index: index + 1 })} disabled={disabled || values.length <= (field.minItems ?? 0)}
            onClick={() => onChange(values.filter((_, position) => position !== index))}>{t("schema.remove")}</Button></div>
        <SchemaField schema={itemSchema} definitions={defs} value={entry} onChange={next => onChange(values.map((old, position) => position === index ? next : old))}
          disabled={disabled} translate={translate} path={path} label={name} />
      </fieldset>)}
      <Button size="sm" disabled={disabled || (field.maxItems !== undefined && values.length >= field.maxItems)} onClick={() => onChange([...values, initial(itemSchema, defs)])}>{t("schema.addEntry", { name })}</Button>
    </div>;
  }
  if (field.enum) return <Select id={id} aria-label={name} disabled={disabled} value={String(value ?? field.default ?? "")}
    onChange={event => onChange(field.enum?.find(option => String(option) === event.target.value) ?? event.target.value)}>{field.enum.map(option => <option key={option} value={option}>{translate?.(`option.${option}`, String(option)) ?? String(option)}</option>)}</Select>;
  if (field.type === "boolean") return <Switch label={name} checked={!!value} disabled={disabled} onChange={onChange} />;
  if (field.type === "object" || !field.type) return <JsonField value={value} label={name} disabled={disabled} onChange={onChange} />;
  return <Input id={id} aria-label={name} disabled={disabled} value={typeof value === "number" || typeof value === "string" ? value : ""}
    type={["integer", "number"].includes(field.type) ? "number" : field.format === "password" ? "password" : "text"}
    min={field.minimum} max={field.maximum} step={field.type === "integer" ? 1 : "any"}
    onChange={event => onChange(["integer", "number"].includes(field.type ?? "") && event.target.value !== "" ? Number(event.target.value) : event.target.value)} />;
}
