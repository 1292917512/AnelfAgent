import { useTranslation } from "react-i18next";
import { RotateCcw } from "lucide-react";
import type { ConfigMetaItem } from "@/lib/api";
import { Drawer } from "@/components/common/Drawer";
import { Badge } from "@/components/ui";
import { useConfigSave } from "./useConfigSave";

interface ConfigDetailDrawerProps {
  item: ConfigMetaItem | null;
  /** 所属分组（完整 group key） */
  group?: string;
  onClose: () => void;
}

function ValueBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="text-xs text-muted mb-1">{label}</div>
      <div className="px-3 py-2 rounded-md bg-bg border border-border text-sm font-mono text-foreground break-all">
        {value === null || value === undefined || value === "" ? "—" : String(value)}
      </div>
    </div>
  );
}

/** 配置项详情抽屉：完整描述、key、默认/当前值、来源、取值范围与重置。 */
export function ConfigDetailDrawer({ item, group, onClose }: ConfigDetailDrawerProps) {
  const { t } = useTranslation("config");
  const { save, saving } = useConfigSave(item?.key ?? "");

  if (!item) return null;
  const isDefault = JSON.stringify(item.value) === JSON.stringify(item.default);

  return (
    <Drawer open onClose={onClose} title={item.description} width="max-w-sm">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {group && (
            <Badge variant="neutral">
              {t(`sections.${group}`, { defaultValue: group })}
            </Badge>
          )}
          {item.advanced && <Badge variant="neutral">{t("detail.advancedBadge")}</Badge>}
          <Badge variant="neutral">
            {item.source === "mind" ? t("detail.sourceMind") : t("detail.sourceConfig")}
          </Badge>
          {!item.editable && <Badge variant="neutral">{t("detail.readonly")}</Badge>}
        </div>

        <div>
          <div className="text-xs text-muted mb-1">{t("detail.key")}</div>
          <div className="px-3 py-2 rounded-md bg-bg border border-border text-sm font-mono text-foreground break-all">
            {item.key}
          </div>
        </div>

        <ValueBlock label={t("detail.value")} value={item.value} />
        <ValueBlock label={t("detail.default")} value={item.default} />

        {item.type === "range" && item.min !== null && item.max !== null && (
          <div className="text-xs text-muted">
            {t("detail.range", { min: item.min, max: item.max, step: item.step ?? 1 })}
          </div>
        )}

        {!isDefault && item.editable && (
          <button
            type="button"
            disabled={saving}
            onClick={() => save(item.default)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-colors disabled:opacity-50"
          >
            <RotateCcw size={14} />
            {t("resetToDefault")}
          </button>
        )}
      </div>
    </Drawer>
  );
}
