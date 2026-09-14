/**
 * 供应商连接卡片 — 每家平台一张（状态/设备数/错误/重连/发现设备）。
 *
 * 小度支持局域网一键发现（discover 按钮 + 手动 IP 输入）；
 * 未配置的供应商展示配置引导（配置项在「配置」页签脱敏填写）。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PlugZap, Radar, RefreshCw, Unplug } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, Input } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { smartHomeApi } from "./api";
import type { ProviderStatus } from "./types";

interface ProviderCardProps {
  provider: ProviderStatus;
  onChanged: () => void;
}

export function ProviderCard({ provider, onChanged }: ProviderCardProps) {
  const { t } = useTranslation("smart_home");
  const [reconnecting, setReconnecting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [manualHost, setManualHost] = useState("");

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

  const discover = async () => {
    setDiscovering(true);
    try {
      const resp = await smartHomeApi.discover(provider.provider);
      if (resp.data.count > 0) {
        toast.success(t("messages.discoverFound", { count: resp.data.count }));
      } else {
        toast.error(t("messages.discoverEmpty"));
      }
      onChanged();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail;
      toast.error(detail || t("messages.discoverFailed"));
    } finally {
      setDiscovering(false);
    }
  };

  const addManualHost = async () => {
    const host = manualHost.trim();
    if (!host) return;
    const key = `smart_home_${provider.provider}_hosts`;
    try {
      const config = await smartHomeApi.getConfig();
      const existing = String(config.data.values[key] ?? "")
        .split(",")
        .map((h) => h.trim())
        .filter(Boolean);
      if (!existing.includes(host)) {
        existing.push(host);
      }
      await smartHomeApi.updateConfig(key, existing.join(","));
      setManualHost("");
      onChanged();
      toast.success(t("messages.hostAdded"));
    } catch {
      toast.error(t("messages.hostAddFailed"));
    }
  };

  return (
    <Card>
      <div className="flex items-center gap-2.5">
        {provider.connected ? (
          <PlugZap className="h-4 w-4 text-ok flex-shrink-0" />
        ) : (
          <Unplug className="h-4 w-4 text-muted flex-shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-medium text-heading">
            {provider.provider_name}
            <span className="ml-2 text-[11px] text-muted">
              {provider.connected
                ? t("providers.connected", { count: provider.device_count })
                : provider.configured
                  ? t("providers.disconnected")
                  : t("providers.unconfigured")}
            </span>
          </div>
          {provider.last_error && (
            <div className="text-[11px] text-danger truncate" title={provider.last_error}>
              {provider.last_error}
            </div>
          )}
          {!provider.configured && (
            <div className="text-[11px] text-muted">
              {t(`providers.configHint_${provider.provider}`, {
                defaultValue: t("providers.configHint"),
              })}
            </div>
          )}
        </div>
        {provider.discovery && (
          <Button
            variant="ghost" size="sm"
            disabled={discovering || !provider.connected}
            onClick={() => void discover()}
            title={t("providers.discover")}
          >
            <Radar className={`h-3.5 w-3.5 mr-1 ${discovering ? "animate-pulse" : ""}`} />
            {discovering ? t("providers.discovering") : t("providers.discover")}
          </Button>
        )}
        {provider.configured && (
          <Button
            variant="ghost" size="icon"
            disabled={reconnecting}
            onClick={() => void reconnect()}
            title={t("providers.reconnect")}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${reconnecting ? "animate-spin" : ""}`} />
          </Button>
        )}
      </div>

      {/* 小度：手动 IP 接入（跨网段/发现失败时） */}
      {provider.discovery && provider.connected && (
        <div className="flex items-center gap-1.5 mt-2.5">
          <Input
            className="h-7 flex-1 text-[12px]"
            placeholder={t("providers.manualHostPlaceholder")}
            value={manualHost}
            onChange={(e) => setManualHost(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void addManualHost();
            }}
          />
          <Button
            variant="ghost" size="sm"
            disabled={!manualHost.trim()}
            onClick={() => void addManualHost()}
          >
            {t("providers.addHost")}
          </Button>
        </div>
      )}
    </Card>
  );
}
