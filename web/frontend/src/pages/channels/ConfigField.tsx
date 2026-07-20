import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

export interface ConfigMeta {
  description: string;
  default: unknown;
  value: unknown;
  group: string;
  value_type_str: string;
  enum_options?: string[];
  tag?: string;
}

export function ConfigField({
  configKey,
  meta,
  value,
  onChange,
}: {
  configKey: string;
  meta: ConfigMeta;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const { t } = useTranslation("common");
  const shortKey = configKey.split(".").pop() || configKey;
  const vtype = meta.value_type_str;

  const fieldCls =
    "w-full px-3 py-1.5 text-sm rounded-md " +
    "border border-border bg-elevated text-foreground " +
    "placeholder:text-muted focus:outline-none focus:border-accent font-mono";

  const label = meta.description && meta.description !== shortKey ? meta.description : shortKey;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label className="text-xs font-semibold text-heading">
          {label}
        </label>
        <span className="text-[10px] text-muted font-mono px-1.5 py-0.5 rounded bg-secondary">
          {shortKey}
        </span>
      </div>

      {vtype === "boolean" ? (
        <div className="flex items-center gap-2">
          <button
            onClick={() => onChange(!value)}
            className={cn(
              "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
              value ? "bg-accent" : "bg-secondary border border-border",
            )}
          >
            <span
              className={cn(
                "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform",
                value ? "translate-x-[18px]" : "translate-x-[3px]",
              )}
            />
          </button>
          <span className="text-xs text-muted">
            {value ? t("enabled") : t("disabled")}
          </span>
        </div>
      ) : vtype === "text" ? (
        <textarea
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          placeholder={`${meta.description}（默认: ${meta.default || "空"}）`}
          className={fieldCls + " resize-y"}
        />
      ) : vtype === "password" ? (
        <input
          type="password"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          placeholder="••••••"
          autoComplete="new-password"
          className={fieldCls}
        />
      ) : vtype === "integer" || vtype === "float" || vtype === "range" ? (
        <input
          type="number"
          value={value !== undefined && value !== null ? Number(value) : ""}
          onChange={(e) => {
            const n =
              vtype === "integer"
                ? parseInt(e.target.value)
                : parseFloat(e.target.value);
            onChange(isNaN(n) ? meta.default : n);
          }}
          placeholder={`默认: ${meta.default}`}
          className={fieldCls}
        />
      ) : vtype === "enum" && meta.enum_options ? (
        <select
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className={fieldCls}
        >
          {meta.enum_options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <input
          type="text"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          placeholder={`默认: ${meta.default || "空"}`}
          autoComplete="off"
          className={fieldCls}
        />
      )}
    </div>
  );
}
