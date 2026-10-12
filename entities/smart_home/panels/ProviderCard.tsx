/** 供应商连接卡片 — 每家外部接入平台一个组件模块。
 *
 * 结构：标识行（图标/名称/状态徽章）→ 状态行（设备数/错误/引导）→
 * 接入详情（地址/账号/令牌到期/连接时刻）→ 连接配置（收纳式表单，保存后重连）→
 * 操作行（重连，无操作时不渲染）。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronRight,
  Home,
  Mic,
  RefreshCw,
  Server,
  Settings2,
  Speaker,
  Unplug,
} from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { smartHomeApi } from "./api";
import { ProviderConfigForm } from "./ProviderConfigForm";
import type { EntityConfigItem, ProviderStatus } from "./types";

/** 平台标识图标（未知平台回落服务器图标） */
const PROVIDER_ICONS: Record<string, typeof Server> = {
  ha: Home,
  xiaodu: Speaker,
  xiaomi: Mic,
};

interface ProviderCardProps {
  provider: ProviderStatus;
  configItems: EntityConfigItem[];
  onChanged: () => void;
}

export function ProviderCard({ provider, configItems, onChanged }: ProviderCardProps) {
  const { t } = useTranslation("smart_home");
  const [reconnecting, setReconnecting] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const Icon = PROVIDER_ICONS[provider.provider] ?? Server;

  const cfgValue = (suffix: string) =>
    String(
      configItems.find((i) => i.key === `smart_home_${provider.provider}_${suffix}`)
        ?.value ?? "",
    ).trim();

  const reconnect = async () => {
    setReconnecting(true);
    try {
      await smartHomeApi.reconnect(provider.provider);
      onChanged();
    } catch {
      toast.error(t("messages.reconnectFailed"));
    } finally {
      setReconnecting(false);
    }
  };

  // 接入详情（按平台展示关键连接信息）
  const details: Array<[string, string]> = [];
  if (provider.provider === "ha" && cfgValue("url")) {
    details.push([t("providers.detailUrl"), cfgValue("url")]);
  }
  if (provider.provider === "xiaomi" && cfgValue("username")) {
    details.push([t("providers.detailAccount"), cfgValue("username")]);
  }
  if (provider.provider === "xiaodu") {
    details.push([t("providers.mcpServers"), "xiaodu-iot / xiaodu"]);
    if (cfgValue("token_expires_at")) {
      details.push([t("providers.detailExpires"), cfgValue("token_expires_at")]);
    }
  }
  if (provider.connected && provider.connected_at) {
    details.push([
      t("providers.connectedAt"),
      new Date(provider.connected_at * 1000).toLocaleString(),
    ]);
  }

  const statusBadge = provider.connected ? (
    <Badge variant="ok">
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {t("providers.statusConnected")}
    </Badge>
  ) : provider.configured ? (
    <Badge variant="warn">
      <Unplug className="h-2.5 w-2.5" />
      {t("providers.disconnected")}
    </Badge>
  ) : (
    <Badge variant="neutral">{t("providers.unconfigured")}</Badge>
  );

  return (
    <div className="flex h-full flex-col gap-4 rounded-2xl border border-border bg-card p-5">
      {/* 标识行 */}
      <div className="flex items-center gap-2.5">
        <span
          className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border ${
            provider.connected
              ? "border-ok/30 bg-ok-subtle text-ok"
              : "border-border bg-elevated text-muted"
          }`}
        >
          <Icon className="h-4 w-4" />
        </span>
        <span className="flex-1 min-w-0 text-sm font-semibold text-heading">
          {provider.provider_name}
        </span>
        {statusBadge}
      </div>

      {/* 状态行 */}
      <div className="space-y-2 text-xs leading-relaxed">
        {provider.configured && (
          <div className="text-muted">
            {t("providers.connected", { count: provider.device_count })}
          </div>
        )}
        {provider.last_error && (
          <div
            className="break-words rounded-md border border-danger/30 bg-danger-subtle px-2 py-1 text-danger"
            title={provider.last_error}
          >
            {provider.last_error}
          </div>
        )}
        {!provider.configured && (
          <div className="text-muted">
            {t(`providers.configHint_${provider.provider}`, {
              defaultValue: t("providers.configHint"),
            })}
          </div>
        )}
      </div>

      {/* 接入详情 */}
      {details.length > 0 && (
        <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 rounded-xl bg-elevated/60 p-3 text-xs">
          {details.map(([label, value]) => (
            <div key={label} className="contents">
              <span className="text-muted">{label}</span>
              <span className="truncate text-foreground" title={value}>
                {value}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 连接配置（收纳式） */}
      {configItems.some((item) => item.editable) && (
        <div className="border-t border-border/60 pt-2">
          <button
            type="button"
            onClick={() => setConfigOpen((v) => !v)}
            aria-expanded={configOpen}
            className="flex w-full items-center gap-2 py-1 text-xs font-medium text-muted hover:text-foreground"
          >
            <Settings2 className="h-3 w-3" />
            {t("providers.config")}
            {configOpen ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
          {configOpen && (
            <ProviderConfigForm
              items={configItems}
              onSaved={onChanged}
            />
          )}
        </div>
      )}

      {/* 操作行（无可用操作时不渲染空条） */}
      {provider.configured && (
        <div className="mt-auto flex items-center justify-end gap-1.5 border-t border-border/60 pt-2.5">
          <Button
            variant="ghost" size="sm"
            loading={reconnecting}
            onClick={() => void reconnect()}
            title={t("providers.reconnect")}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1 ${reconnecting ? "animate-spin" : ""}`} />
            {t("providers.reconnect")}
          </Button>
        </div>
      )}
    </div>
  );
}
