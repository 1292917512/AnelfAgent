import { Check, Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ConfigSource } from "@/components/common/ConfigSource";
import { useConfigSave } from "./useConfigSave";
import { NumberField, ModelField, PasswordField, RangeField, SelectField, SwitchField, TextField } from "./fields";

interface ConfigItemRowProps {
  item: ConfigMetaItem;
  /** 深链/搜索定位高亮 */
  highlight?: boolean;
  onOpenDetail: (item: ConfigMetaItem) => void;
}

/** 通用配置行：描述/key 信息区（点击开详情抽屉）+ 类型分发控件 + 保存反馈 + 重置。 */
export function ConfigItemRow({ item, highlight, onOpenDetail }: ConfigItemRowProps) {
  const { t } = useTranslation("config");
  const { save, saving, saved } = useConfigSave(item.key);

  const value = item.value;
  const isDefault = JSON.stringify(value) === JSON.stringify(item.default);
  const disabled = !item.editable || saving;

  const control = (() => {
    if (item.type === "boolean") {
      return <SwitchField label={item.description || item.key} value={!!value} disabled={disabled} onCommit={save} />;
    }
    if (item.type === "enum" && item.options) {
      return (
        <SelectField label={item.description || item.key} value={String(value ?? "")} options={item.options} disabled={disabled} onCommit={save} />
      );
    }
    if (item.type === "range" && item.min !== null && item.max !== null) {
      return (
        <RangeField label={item.description || item.key}
          value={Number(value ?? item.default ?? 0)}
          min={item.min}
          max={item.max}
          step={item.step ?? 1}
          unit={item.unit || undefined}
          disabled={disabled}
          onCommit={save}
        />
      );
    }
    if (item.type === "model") {
      return (
        <ModelField label={item.description || item.key}
          value={value == null ? "" : String(value)}
          allowEmpty={item.default === "" || item.default === null}
          disabled={disabled}
          onCommit={save}
        />
      );
    }
    if (item.type === "integer" || item.type === "float" || item.type === "range") {
      return (
        <NumberField label={item.description || item.key}
          value={Number(value ?? 0)}
          isFloat={item.type === "float"} min={item.min ?? undefined} max={item.max ?? undefined}
          unit={item.unit || undefined}
          disabled={disabled}
          onCommit={save}
        />
      );
    }
    if (item.type === "password") {
      return <PasswordField label={item.description || item.key} value={value == null ? "" : String(value)} disabled={disabled} onCommit={save} />;
    }
    return <TextField multiline={item.type === "text"} label={item.description || item.key} value={value == null ? "" : String(value)} disabled={disabled} onCommit={save} />;
  })();

  return (
    <div
      id={`config-item-${item.key}`}
      className={cn(
        "flex flex-wrap items-center gap-3 p-3 rounded-md border bg-card transition-colors",
        highlight ? "border-accent ring-1 ring-accent" : "border-border",
      )}
    >
      <button
        type="button"
        onClick={() => onOpenDetail(item)}
        className="min-w-[180px] flex-1 text-left group"
        title={t("detail.open")}
      >
        <div className="text-sm text-heading group-hover:text-accent transition-colors">
          {item.description}
        </div>
        <div className="text-xs text-muted font-mono truncate">{item.key}</div>
        <ConfigSource item={item} />
      </button>

      <div className="flex max-w-full items-center gap-2">
        {control}
        {saving && <Loader2 size={14} className="animate-spin text-muted" />}
        {saved && <Check size={16} className="text-ok" />}
        {!isDefault && item.editable && (
          <button
            title={t("resetToDefault")}
            onClick={() => save(item.default)}
            disabled={saving}
            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors disabled:opacity-50"
          >
            <RotateCcw size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
