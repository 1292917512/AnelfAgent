import { Check, Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useConfigSave } from "./useConfigSave";
import { NumberField, PasswordField, RangeField, SelectField, SwitchField, TextField } from "./fields";

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
      return <SwitchField value={!!value} disabled={disabled} onCommit={save} />;
    }
    if (item.type === "enum" && item.options) {
      return (
        <SelectField value={String(value ?? "")} options={item.options} disabled={disabled} onCommit={save} />
      );
    }
    if (item.type === "range" && item.min !== null && item.max !== null) {
      return (
        <RangeField
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
    if (item.type === "integer" || item.type === "float" || item.type === "range") {
      return (
        <NumberField
          value={Number(value ?? 0)}
          isFloat={item.type === "float"}
          unit={item.unit || undefined}
          disabled={disabled}
          onCommit={save}
        />
      );
    }
    if (item.type === "password") {
      return <PasswordField value={value == null ? "" : String(value)} disabled={disabled} onCommit={save} />;
    }
    return <TextField value={value == null ? "" : String(value)} disabled={disabled} onCommit={save} />;
  })();

  return (
    <div
      id={`config-item-${item.key}`}
      className={cn(
        "flex items-center gap-3 p-3 rounded-md border bg-card transition-colors",
        highlight ? "border-accent ring-1 ring-accent" : "border-border",
      )}
    >
      <button
        type="button"
        onClick={() => onOpenDetail(item)}
        className="flex-1 min-w-0 text-left group"
        title={t("detail.open")}
      >
        <div className="text-sm text-heading group-hover:text-accent transition-colors">
          {item.description}
        </div>
        <div className="text-xs text-muted font-mono truncate">{item.key}</div>
      </button>

      <div className="flex items-center gap-2 shrink-0">
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
