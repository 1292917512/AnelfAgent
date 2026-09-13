/**
 * 设备域组件管理卡 — 启停开关、设备计数、可用控制动作与配置项编辑。
 *
 * 数据驱动自 DeviceDomain.describe（/api/entity/smart_home/domains），
 * 新增后端设备域自动出现卡片；配置项写走通用实体配置端点。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Wrench } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Input, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import type { DeviceDomainInfo, DomainConfigItem } from "./types";

interface DomainCardProps {
  domain: DeviceDomainInfo;
  onSaveConfig: (key: string, value: unknown) => Promise<void>;
}

/** 单个域配置项编辑行（布尔即时开关，数值失焦/回车保存） */
function ConfigRow({
  item,
  disabled,
  onSave,
}: {
  item: DomainConfigItem;
  disabled: boolean;
  onSave: (key: string, value: unknown) => Promise<void>;
}) {
  const { t } = useTranslation("smart_home");
  const isBool = item.value_type === "boolean";
  const isNumber = ["integer", "float", "range"].includes(item.value_type);
  const current = String(item.value ?? "");
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

  return (
    <div className="flex items-center gap-3 py-1.5">
      <div className="flex-1 min-w-0">
        <div className="text-[12px] text-foreground">{item.description || item.name}</div>
        {(item.unit || item.min !== null || item.max !== null) && (
          <div className="text-[10px] text-muted mt-0.5">
            {[
              item.min !== null || item.max !== null
                ? `${item.min ?? "-∞"} ~ ${item.max ?? "+∞"}`
                : "",
              item.unit,
            ].filter(Boolean).join(" · ")}
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
            className="w-32 h-7 pr-6 text-[12px]"
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
            {savedTick && <Check className="h-3 w-3 text-ok" />}
            {!savedTick && dirty && (
              <span className="block h-1.5 w-1.5 rounded-full bg-warn" title={t("config.unsaved")} />
            )}
          </span>
        </div>
      )}
    </div>
  );
}

export function DomainCard({ domain, onSaveConfig }: DomainCardProps) {
  const { t } = useTranslation("smart_home");
  const actionEntries = Object.entries(domain.actions);

  return (
    <Card>
      <div className="flex items-center gap-2 mb-1">
        <span className="flex-1 min-w-0">
          <span className="text-[14px] font-semibold text-heading">{domain.display_name}</span>
          <span className="ml-2 text-[11px] text-muted">
            {t("domains.deviceCount", {
              available: domain.available_count,
              total: domain.device_count,
            })}
          </span>
        </span>
        <Switch
          checked={domain.enabled}
          onChange={(v) => void onSaveConfig(`smart_home_${domain.key}_enabled`, v)}
        />
      </div>
      <p className="text-[11px] text-muted mb-2">{domain.description}</p>

      {actionEntries.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mb-2">
          <Wrench className="h-3 w-3 text-muted" />
          {actionEntries.map(([name, action]) => (
            <span
              key={name}
              className="rounded border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted"
              title={action.value_hint || action.description}
            >
              {action.description || name}
            </span>
          ))}
        </div>
      )}

      {domain.configs.length > 0 && (
        <div className="divide-y divide-border/50">
          {domain.configs.map((item) => (
            <ConfigRow
              key={item.key}
              item={item}
              disabled={!domain.enabled}
              onSave={onSaveConfig}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
