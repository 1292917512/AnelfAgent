import { ArrowUpRight, Check, Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ConfigSource } from "@/components/common/ConfigSource";
import { ConfigLink } from "@/components/common/ConfigLink";
import { configItemId } from "@/lib/config-navigation";
import { apiErrorMessage } from "@/lib/api/client";
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
  const { save, saving, saved, error } = useConfigSave(item.key);

  const value = item.value;
  const isDefault = JSON.stringify(value) === JSON.stringify(item.default);
  const disabled = !item.editable || saving;
  const label = item.title || item.description || item.key;

  const control = (() => {
    if (item.type === "boolean") {
      return <SwitchField label={label} value={!!value} disabled={disabled} onCommit={save} />;
    }
    if (item.type === "enum" && item.options) {
      return (
        <SelectField label={label} value={String(value ?? "")} options={item.options} disabled={disabled} onCommit={save} />
      );
    }
    if (item.type === "range" && item.min !== null && item.max !== null) {
      return (
        <RangeField label={label}
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
        <ModelField label={label}
          value={value == null ? "" : String(value)}
          allowEmpty={item.default === "" || item.default === null}
          disabled={disabled}
          onCommit={save}
        />
      );
    }
    if (item.type === "integer" || item.type === "float" || item.type === "range") {
      return (
        <NumberField label={label}
          value={Number(value ?? 0)}
          isFloat={item.type === "float"} min={item.min ?? undefined} max={item.max ?? undefined}
          unit={item.unit || undefined}
          disabled={disabled}
          onCommit={save}
        />
      );
    }
    if (item.type === "password") {
      return <PasswordField label={label} value={value == null ? "" : String(value)} disabled={disabled} onCommit={save} />;
    }
    return <TextField multiline={item.type === "text"} label={label} value={value == null ? "" : String(value)} disabled={disabled} onCommit={save} />;
  })();

  return (
    <div
      id={configItemId(item.key)}
      tabIndex={-1}
      data-config-highlight={highlight || undefined}
      data-config-type={item.type}
      className={cn(
        "config-item flex flex-wrap items-center gap-3 p-4 rounded-xl border bg-card transition-colors",
        highlight ? "border-accent/40 bg-accent-subtle" : "border-border",
      )}
    >
      <button
        type="button"
        onClick={() => onOpenDetail(item)}
        className="min-w-[180px] flex-1 text-left group"
        title={t("detail.open")}
      >
        <div className="text-sm text-heading group-hover:text-accent transition-colors">
          {label}
        </div>
        {item.title && item.description !== item.title && <p className="mt-1 text-xs leading-relaxed text-muted">{item.description}</p>}
        <div className="text-xs text-muted font-mono truncate">{item.key}</div>
        <ConfigSource item={item} />
      </button>

      {item.related_key && <ConfigLink target="_blank" rel="noreferrer" config={{ key: item.related_key }}>{t("relatedSetting")}<ArrowUpRight size={13} /></ConfigLink>}
      <div className={cn("config-item-control flex max-w-full items-start gap-2", item.type === "text" && "w-full")}>
        {control}
        <span className="flex h-9 w-4 shrink-0 items-center" role="status" aria-label={saving ? t("common:saving") : saved && !error ? t("common:saved") : undefined}>
          {saving && <Loader2 size={14} className="animate-spin text-muted" />}
          {saved && !saving && !error && <Check size={16} className="text-ok" />}
        </span>
        {item.editable && (
          <button
            title={t("resetToDefault")}
            onClick={() => save(item.default)}
            disabled={saving || isDefault}
            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors disabled:opacity-30"
          >
            <RotateCcw size={14} />
          </button>
        )}
      </div>
      {error && <p role="alert" className="w-full text-sm text-danger">{apiErrorMessage(error, t("common:error"))}</p>}
    </div>
  );
}
