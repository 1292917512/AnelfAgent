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
  const [draft, setDraft] = useState(current);
  const [saving, setSaving] = useState(false);
  const [savedTick, setSavedTick] = useState(false);
  const dirty = draft !== current;

  const commit = async (raw: string) => {
    const text = raw.trim();
    if (text === current) return;
    let value: unknown = text;
    if (isNumber && text !== "") {
      value = item.value_type === "integer" ? parseInt(text, 10) : parseFloat(text);
      if (Number.isNaN(value)) {
        setDraft(current);
        toast.error(t("config.invalidNumber"));
        return;
      }
    }
    setSaving(true);
    try {
      await onSave(item.key, value);
      setSavedTick(true);
      setTimeout(() => setSavedTick(false), 1200);
    } catch {
      setDraft(current);
      toast.error(t("config.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const rangeHint =
    isNumber && (item.min !== null || item.max !== null)
      ? `${item.min ?? "-∞"} ~ ${item.max ?? "+∞"}`
      : "";

  return (
    <div className="flex items-center gap-3 py-2">
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
          disabled={disabled || saving}
          onChange={(v) => void onSave(item.key, v).catch(() => toast.error(t("config.saveFailed")))}
        />
      ) : (
        <div className="relative flex-shrink-0">
          <Input
            className="w-48 h-8 pr-7"
            type={isNumber ? "number" : "text"}
            value={draft}
            min={item.min ?? undefined}
            max={item.max ?? undefined}
            disabled={disabled || saving}
            placeholder={String(item.default ?? "")}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void commit(draft)}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
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
