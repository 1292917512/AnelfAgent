import { Switch } from "@/components/ui/Switch";
import { ModelSelect } from "@/components/models/ModelSelect";

export type FieldType = "int" | "float" | "bool" | "string" | "password" | "model";

export interface FieldMeta {
  key: string;
  label: string;
  type: FieldType;
  desc?: string;
  /** type === "model" 时的模型类型（chat / embedding / vision ...），默认 chat */
  modelType?: string;
  /** type === "model" 时是否提供「跟随默认」空选项 */
  allowEmpty?: boolean;
}

interface AppFieldProps {
  meta: FieldMeta;
  value: unknown;
  onChange: (v: unknown) => void;
}

export function AppField({ meta, value, onChange }: AppFieldProps) {
  const base =
    "w-full text-sm bg-elevated border border-border rounded-md px-2.5 py-1.5 text-heading focus:outline-none focus:border-accent transition-colors";

  const renderInput = () => {
    if (meta.type === "bool") {
      return <Switch checked={!!value} onChange={onChange} label={meta.label} />;
    }
    if (meta.type === "int") {
      return (
        <input aria-label={meta.label}
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
        <input aria-label={meta.label}
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
        <input aria-label={meta.label}
          type="password"
          autoComplete="off"
          className={base}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    }
    if (meta.type === "model") {
      return (
        <ModelSelect
          label={meta.label}
          modelType={meta.modelType || "chat"}
          value={typeof value === "string" ? value : ""}
          onChange={(id) => onChange(id)}
          allowEmpty={meta.allowEmpty ?? true}
          allowPin={false}
          showDefaultWhenEmpty={false}
        />
      );
    }
    return (
      <input aria-label={meta.label}
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
        <label className="text-xs text-muted font-medium">{meta.label}</label>
        {meta.type === "bool" && renderInput()}
      </div>
      {meta.desc && (
        <p className="text-[11px] text-muted opacity-70 line-clamp-2" title={meta.desc}>
          {meta.desc}
        </p>
      )}
      {meta.type !== "bool" && renderInput()}
    </div>
  );
}
