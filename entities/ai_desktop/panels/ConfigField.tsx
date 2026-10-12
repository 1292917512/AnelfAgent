import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check } from "lucide-react";
import { Input, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import type { ModuleConfigItem } from "./types";

interface ConfigFieldProps {
  item: ModuleConfigItem;
  disabled: boolean;
  onSave: (key: string, value: unknown) => Promise<void>;
}

/** 单个组件配置项编辑器（布尔即时开关，其余失焦/回车保存，带保存反馈） */
export function ConfigField({ item, disabled, onSave }: ConfigFieldProps) {
  const { t } = useTranslation("ai_desktop");
  const isBool = item.value_type === "boolean";
  const isNumber = ["integer", "float", "range"].includes(item.value_type);
  const current = typeof item.value === "object" && item.value !== null
    ? JSON.stringify(item.value)
    : String(item.value ?? "");
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedTick, setSavedTick] = useState(false);
  const dirty = draft !== null && draft !== current;

  const save = async (value: unknown) => {
    if (saving || disabled) return;
    setSaving(true);
    try {
      await onSave(item.key, value);
      setDraft(null);
      setSavedTick(true);
    } catch {
      toast.error(t("config.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const commit = async (raw: string) => {
    const text = raw.trim();
    if (text === current) return;
    let value: unknown = text;
    if (isNumber) {
      const number = Number(text);
      if (!text || !Number.isFinite(number) || (item.value_type === "integer" && !Number.isInteger(number))
        || (item.min !== null && number < item.min) || (item.max !== null && number > item.max)) {
        toast.error(t("config.invalidNumber"));
        return;
      }
      value = number;
    }
    await save(value);
  };

  const rangeHint =
    isNumber && (item.min !== null || item.max !== null)
      ? `${item.min ?? "-∞"} ~ ${item.max ?? "+∞"}`
      : "";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-2">
      <div className="flex-1 min-w-0">
        <div className="text-[13px] text-foreground">{item.description || item.name}</div>
        {(item.unit || rangeHint) && (
          <div className="text-[10px] text-muted mt-0.5">
            {[rangeHint, item.unit].filter(Boolean).join(" · ")}
          </div>
        )}
      </div>
      {isBool ? (
        <Switch
          checked={Boolean(item.value)}
          label={item.description || item.name}
          disabled={disabled || saving}
          onChange={(v) => void save(v)}
        />
      ) : (
        <div className="relative w-48 max-w-full">
          <Input
            className="w-full h-8 pr-7"
            aria-label={item.description || item.name}
            type={isNumber ? "number" : "text"}
            value={draft ?? current}
            min={item.min ?? undefined}
            max={item.max ?? undefined}
            disabled={disabled || saving}
            placeholder={String(item.default ?? "")}
            onChange={(e) => { setDraft(e.target.value); setSavedTick(false); }}
            onBlur={() => void commit(draft ?? current)}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
          />
          <span className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">
            {savedTick && <Check className="h-3.5 w-3.5 text-ok" />}
            {!savedTick && dirty && (
              <span className="block h-1.5 w-1.5 rounded-full bg-warn" title={t("config.unsaved")} />
            )}
          </span>
        </div>
      )}
    </div>
  );
}
