/** 供应商连接配置表单 — 数据驱动自实体配置项（前缀过滤由父级完成）。
 *
 * 密码项留空 = 保持现值；保存后自动重连该供应商使新配置生效。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { smartHomeApi } from "./api";
import type { EntityConfigItem } from "./types";

interface ProviderConfigFormProps {
  providerKey: string;
  items: EntityConfigItem[];
  onSaved: () => void;
}

const NUMERIC_TYPES = ["integer", "float", "range"];

export function ProviderConfigForm({
  providerKey,
  items,
  onSaved,
}: ProviderConfigFormProps) {
  const { t } = useTranslation("smart_home");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const editable = items.filter((item) => item.editable);

  const current = (item: EntityConfigItem) =>
    drafts[item.key] ?? String(item.value ?? "");
  const dirty = editable.some(
    (item) => current(item) !== String(item.value ?? ""),
  );
  const set = (key: string, value: string) =>
    setDrafts((prev) => ({ ...prev, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      for (const item of editable) {
        const next = current(item);
        if (next === String(item.value ?? "")) continue;
        if (item.type === "password" && !next) continue;
        let value: unknown = next;
        if (item.type === "boolean") value = next === "true";
        else if (NUMERIC_TYPES.includes(item.type)) value = Number(next);
        await smartHomeApi.updateConfig(item.key, value);
      }
      setDrafts({});
      toast.success(t("providers.configSaved"));
      await smartHomeApi.reconnect(providerKey).catch(() => undefined);
      onSaved();
    } catch {
      toast.error(t("config.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-2 space-y-2">
      {editable.map((item) => (
        <div key={item.key} className="flex items-center gap-2">
          <span
            className="flex-1 min-w-0 truncate text-[11px] text-muted"
            title={item.description}
          >
            {item.description}
          </span>
          {item.type === "boolean" ? (
            <Switch
              checked={current(item) === "true"}
              disabled={saving}
              onChange={(v) => set(item.key, String(v))}
            />
          ) : (
            <Input
              className="h-7 w-44 text-[12px]"
              type={item.type === "password" ? "password" : NUMERIC_TYPES.includes(item.type) ? "number" : "text"}
              min={NUMERIC_TYPES.includes(item.type) && item.min != null ? item.min : undefined}
              max={NUMERIC_TYPES.includes(item.type) && item.max != null ? item.max : undefined}
              placeholder={
                item.type === "password"
                  ? t("providers.secretPlaceholder")
                  : String(item.default ?? "")
              }
              value={current(item)}
              disabled={saving}
              onChange={(e) => set(item.key, e.target.value)}
            />
          )}
        </div>
      ))}
      <div className="flex justify-end">
        <Button size="sm" disabled={!dirty || saving} onClick={() => void save()}>
          {t("providers.configSave")}
        </Button>
      </div>
    </div>
  );
}
