import { useTranslation } from "react-i18next";
import { ConfigSource } from "@/components/common/ConfigSource";
import { Switch } from "@/components/ui";
import type { ConfigMetaItem } from "@/lib/types";
import { useConfigSave } from "@/pages/config/useConfigSave";

export function FallbackPolicyRow({ item }: { item: ConfigMetaItem }) {
  const { t } = useTranslation("models");
  const { save, saving } = useConfigSave(item.key);
  const enabled = item.value === true;
  const label = t("fallbackLabel");
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-card px-4 py-3">
      <div>
        <p className="text-sm font-medium text-heading">{label}</p>
        <p className="mt-1 text-xs text-muted">{t(enabled ? "fallbackOn" : "fallbackOff")}</p>
        <ConfigSource item={item} />
      </div>
      <Switch checked={enabled} label={label} disabled={!item.editable || saving} onChange={save} />
    </div>
  );
}
