import { useTranslation } from "react-i18next";
import { Box, CalendarDays, Clock, CloudSun, CreditCard, RefreshCw } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, Switch } from "@/components/ui";
import { ConfigField } from "./ConfigField";
import { ExtraTimezones } from "./ExtraTimezones";
import { ModuleDetailView } from "./ModuleDetailView";
import { WeatherLocations } from "./WeatherLocations";
import type { DesktopModuleInfo } from "./types";

const MODULE_ICONS: Record<string, typeof Box> = {
  datetime: Clock,
  weather: CloudSun,
  subscription: CreditCard,
  calendar: CalendarDays,
};

/** 由专属编辑器接管的配置项（不进通用编辑列表） */
const MANAGED_CONFIGS: Record<string, string[]> = {
  weather: ["locations"],
  datetime: ["extra_timezones"],
};

interface ModuleCardProps {
  mod: DesktopModuleInfo;
  refreshing: boolean;
  onToggle: (mod: DesktopModuleInfo, enabled: boolean) => void;
  onSaveConfig: (key: string, value: unknown) => Promise<void>;
  onRefresh: (mod: DesktopModuleInfo) => void;
}

/** 单个桌面组件卡片：实时状态 + 地区/时区管理 + 注入文本 + 配置编辑 + 启停/刷新 */
export function ModuleCard({
  mod,
  refreshing,
  onToggle,
  onSaveConfig,
  onRefresh,
}: ModuleCardProps) {
  const { t } = useTranslation("ai_desktop");
  const Icon = MODULE_ICONS[mod.key] ?? Box;
  const pollable = mod.refresh_interval > 0;
  const managed = MANAGED_CONFIGS[mod.key] ?? [];
  const genericConfigs = mod.configs.filter((c) => !managed.includes(c.name));

  return (
    <Card>
      {/* 头部：图标 + 名称 + 操作 */}
      <div className="flex items-center justify-between mb-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold tracking-tight text-heading">
            <Icon className="h-4 w-4 text-accent" />
            {mod.display_name}
            <span className="text-xs font-normal text-muted">{mod.key}</span>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded ${
                mod.enabled ? "bg-ok-subtle text-ok" : "bg-border/40 text-muted"
              }`}
            >
              {mod.enabled ? t("module.enabled") : t("module.disabled")}
            </span>
          </h3>
          <p className="text-[13px] text-muted mt-1">{mod.description}</p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {pollable && (
            <Button
              variant="ghost"
              size="icon"
              disabled={!mod.enabled || refreshing}
              onClick={() => onRefresh(mod)}
              title={t("module.refresh")}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            </Button>
          )}
          <Switch checked={mod.enabled} onChange={(v) => onToggle(mod, v)} />
        </div>
      </div>

      {mod.last_error && (
        <div className="mb-3 rounded-md border border-danger/30 bg-danger-subtle px-3 py-1.5 text-xs text-danger">
          {t("module.lastError")}: {mod.last_error}
        </div>
      )}

      {/* 当前状态（组件特化详情） */}
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
        {t("module.status")}
        {pollable && mod.last_refresh && (
          <span className="ml-2 normal-case font-normal">
            {t("module.lastRefresh")}: {new Date(mod.last_refresh * 1000).toLocaleTimeString()}
          </span>
        )}
      </div>
      <ModuleDetailView mod={mod} />

      {/* 天气：地区管理器（逐地区数据 + 增删启停） */}
      {mod.key === "weather" && mod.enabled && (
        <WeatherLocations mod={mod} onSave={onSaveConfig} onRefresh={onRefresh} />
      )}

      {/* 实际注入 AI 的文本 */}
      {mod.enabled && mod.current_content && (
        <div className="mt-3">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
            {t("module.injectedText")}
          </div>
          <pre className="whitespace-pre-wrap break-all rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-xs leading-relaxed text-foreground">
            {mod.current_content}
          </pre>
        </div>
      )}

      {/* 设置：通用配置项 + 组件专属编辑器 */}
      {(genericConfigs.length > 0 || mod.key === "datetime") && mod.enabled && (
        <div className="mt-3">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">
            {t("module.settings")}
          </div>
          <div className="divide-y divide-border/60">
            {genericConfigs.map((item) => (
              <ConfigField
                key={item.key}
                item={item}
                disabled={!mod.enabled}
                onSave={onSaveConfig}
              />
            ))}
            {mod.key === "datetime" && (
              <ExtraTimezones mod={mod} onSave={onSaveConfig} />
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
