import { cn } from "@/lib/utils";

export type FieldType = "int" | "float" | "bool" | "string" | "password";

export interface FieldMeta {
  key: string;
  label: string;
  type: FieldType;
  desc?: string;
}

interface AppFieldProps {
  meta: FieldMeta;
  value: unknown;
  onChange: (v: unknown) => void;
}

export function AppField({ meta, value, onChange }: AppFieldProps) {
  const base =
    "w-full text-sm bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] px-2.5 py-1.5 text-[var(--text-strong)] focus:outline-none focus:border-[var(--accent)] transition-colors";

  const renderInput = () => {
    if (meta.type === "bool") {
      return (
        <button
          onClick={() => onChange(!value)}
          className={cn(
            "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
            value ? "bg-[var(--accent)]" : "bg-[var(--border)]",
          )}
        >
          <span
            className={cn(
              "inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform",
              value ? "translate-x-4" : "translate-x-1",
            )}
          />
        </button>
      );
    }
    if (meta.type === "int") {
      return (
        <input
          type="number"
          step="1"
          className={base}
          value={typeof value === "number" ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? null : parseInt(e.target.value, 10))}
        />
      );
    }
    if (meta.type === "float") {
      return (
        <input
          type="number"
          step="any"
          className={base}
          value={typeof value === "number" ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? null : parseFloat(e.target.value))}
        />
      );
    }
    if (meta.type === "password") {
      return (
        <input
          type="password"
          autoComplete="off"
          className={base}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    }
    return (
      <input
        type="text"
        className={base}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <label className="text-xs text-[var(--muted)] font-medium">{meta.label}</label>
        {meta.type === "bool" && renderInput()}
      </div>
      {meta.desc && <p className="text-[11px] text-[var(--muted)] opacity-70">{meta.desc}</p>}
      {meta.type !== "bool" && renderInput()}
    </div>
  );
}
