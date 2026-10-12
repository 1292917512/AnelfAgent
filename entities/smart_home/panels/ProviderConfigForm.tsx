/** 元数据驱动的连接表单，批量保存；空密码保持现值。 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Switch } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api";
import { toast } from "@/stores/toast-store";
import { smartHomeApi } from "./api";
import type { EntityConfigItem } from "./types";

const NUMERIC_TYPES = ["integer", "float", "range"];

export function ProviderConfigForm({ items, onSaved }: { items: EntityConfigItem[]; onSaved: () => void }) {
  const { t } = useTranslation("smart_home");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const editable = items.filter((item) => item.editable);
  const current = (item: EntityConfigItem) => drafts[item.key] ?? (item.type === "password" ? "" : String(item.value ?? ""));
  const changed = (item: EntityConfigItem) => item.type === "password" ? Boolean(current(item)) : current(item) !== String(item.value ?? "");
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const updates: Record<string, unknown> = {};
      for (const item of editable.filter(changed)) {
        const next = current(item);
        if (item.type === "boolean") updates[item.key] = next === "true";
        else if (NUMERIC_TYPES.includes(item.type)) {
          const value = Number(next);
          if (!next.trim() || !Number.isFinite(value) || (item.type === "integer" && !Number.isInteger(value))) throw new Error(t("config.invalidNumber"));
          updates[item.key] = value;
        } else updates[item.key] = next;
      }
      if (!Object.keys(updates).length) return;
      const response = await smartHomeApi.updateConfigs(updates);
      if (response.data.updated !== Object.keys(updates).length) throw new Error(t("config.partialSave"));
      setDrafts({});
      onSaved();
      toast.success(t("providers.configSaved"));
    } catch (cause) {
      setError(apiErrorMessage(cause, t("config.saveFailed")));
    } finally { setSaving(false); }
  };
  return <form className="mt-4 space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
    {editable.map((item) => <div key={item.key} className="space-y-2">
      <label htmlFor={item.key} className="block text-xs leading-relaxed text-muted">{item.description}</label>
      {item.type === "boolean" ? <Switch label={item.description} checked={current(item) === "true"} disabled={saving} onChange={(v) => setDrafts((old) => ({ ...old, [item.key]: String(v) }))} />
        : item.enum_options?.length ? <select id={item.key} value={current(item)} disabled={saving} onChange={(event) => setDrafts((old) => ({ ...old, [item.key]: event.target.value }))} className="h-9 w-full rounded-lg border border-border bg-elevated px-3 text-xs text-foreground">
          {item.enum_options.map((value) => <option key={value} value={value}>{t("configValues." + value, { defaultValue: value })}</option>)}
        </select>
        : <Input id={item.key} className="h-9 w-full text-xs" type={item.type === "password" ? "password" : NUMERIC_TYPES.includes(item.type) ? "number" : "text"}
          autoComplete={item.type === "password" ? "new-password" : "off"}
          min={item.min ?? undefined} max={item.max ?? undefined} step={item.type === "integer" ? 1 : "any"}
          placeholder={item.type === "password" ? t("providers.secretPlaceholder") : String(item.default ?? "")}
          value={current(item)} disabled={saving} onChange={(event) => setDrafts((old) => ({ ...old, [item.key]: event.target.value }))} />}
    </div>)}
    {error && <p role="alert" className="rounded-lg bg-danger-subtle p-3 text-xs leading-relaxed text-danger">{error}</p>}
    <Button type="submit" className="w-full" variant="primary" size="sm" disabled={!editable.some(changed)} loading={saving}>{t("providers.configSave")}</Button>
  </form>;
}
