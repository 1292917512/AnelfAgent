/**
 * 智能家居实体面板 — 设备实时总览与控制的完整管理页。
 *
 * 连接状态条 + 注入预览 + 按房间分组的设备卡片（域特化控制件）+
 * 设备域组件管理（启停/配置）。实时性经 /api/entity/smart_home/stream
 * SSE 推送（state 事件就地更新缓存，sync/connection 事件整表刷新）。
 *
 * i18n：locales/{zh,en}.json 由 lib/entity-plugin-locales.ts 启动时 eager
 * 注册（命名空间 smart_home）；_registry.groups/configSections 声明
 * 工具页与配置中心的分组名。
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Eye,
  Home,
  PlugZap,
  Puzzle,
  RefreshCw,
  Unplug,
} from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, LoadingBlock } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { smartHomeApi } from "./panels/api";
import { DeviceCard } from "./panels/DeviceCard";
import { DomainCard } from "./panels/DomainCard";
import type {
  ConnectionStatus,
  DeviceInfo,
  DevicesResponse,
  StateEventPayload,
} from "./panels/types";

export default function SmartHomePanel() {
  const { t } = useTranslation("smart_home");
  const queryClient = useQueryClient();
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const statusQuery = useQuery({
    queryKey: ["smart-home-status"],
    queryFn: () => smartHomeApi.status().then((r) => r.data),
    refetchInterval: 30000,
  });
  const devicesQuery = useQuery({
    queryKey: ["smart-home-devices"],
    queryFn: () => smartHomeApi.devices().then((r) => r.data),
    refetchInterval: 60000,
  });
  const domainsQuery = useQuery({
    queryKey: ["smart-home-domains"],
    queryFn: () => smartHomeApi.domains().then((r) => r.data),
  });
  const previewQuery = useQuery({
    queryKey: ["smart-home-preview"],
    queryFn: () => smartHomeApi.preview().then((r) => r.data),
    refetchInterval: 30000,
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["smart-home-status"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-devices"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-domains"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-preview"] });
  };

  // SSE 实时推送：state 就地更新缓存，sync/connection 整表刷新（EventSource 自动重连）
  useEffect(() => {
    const es = new EventSource("/api/entity/smart_home/stream");
    es.addEventListener("state", (raw) => {
      try {
        const payload = JSON.parse((raw as MessageEvent).data) as StateEventPayload;
        if (!payload.device) return;
        queryClient.setQueryData<DevicesResponse>(["smart-home-devices"], (old) => {
          if (!old) return old;
          const idx = old.devices.findIndex(
            (d) => d.entity_id === payload.device!.entity_id,
          );
          if (idx < 0) return old;
          const devices = [...old.devices];
          devices[idx] = payload.device!;
          return { ...old, devices };
        });
      } catch {
        /* 忽略畸形事件 */
      }
    });
    es.addEventListener("sync", refreshAll);
    es.addEventListener("connection", refreshAll);
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient]);

  const status: ConnectionStatus | undefined = statusQuery.data;
  const devices = useMemo(() => devicesQuery.data?.devices ?? [], [devicesQuery.data]);
  const domains = useMemo(() => domainsQuery.data?.domains ?? [], [domainsQuery.data]);

  // ha domain → 设备域组件（面板按域特化渲染控制件）
  const domainByHa = useMemo(() => {
    const map = new Map<string, (typeof domains)[number]>();
    for (const domain of domains) {
      for (const ha of domain.ha_domains) {
        if (!map.has(ha)) map.set(ha, domain);
      }
    }
    return map;
  }, [domains]);

  // 按房间分组（未分组排尾），组内按名称排序
  const grouped = useMemo(() => {
    const known = new Set(domainByHa.keys());
    const groups = new Map<string, DeviceInfo[]>();
    for (const device of devices) {
      if (!known.has(device.domain)) continue;
      const area = device.area || t("devices.ungrouped");
      const list = groups.get(area) ?? [];
      list.push(device);
      groups.set(area, list);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "zh"))
      .map(([area, list]) => [
        area,
        [...list].sort((a, b) => a.name.localeCompare(b.name, "zh")),
      ] as [string, DeviceInfo[]]);
  }, [devices, domainByHa, t]);

  const saveConfig = async (key: string, value: unknown) => {
    await smartHomeApi.updateConfig(key, value);
    await domainsQuery.refetch();
    await previewQuery.refetch();
  };

  const control = (entityId: string, action: string, value = "") => {
    setPendingIds((prev) => new Set(prev).add(entityId));
    smartHomeApi
      .control(entityId, action, value)
      .catch((err: { response?: { data?: { detail?: string } } }) => {
        toast.error(err.response?.data?.detail || t("messages.controlFailed"));
      })
      .finally(() => {
        setPendingIds((prev) => {
          const next = new Set(prev);
          next.delete(entityId);
          return next;
        });
      });
  };

  if (devicesQuery.isError || statusQuery.isError) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <AlertTriangle className="h-6 w-6 text-danger" />
          <p className="text-sm text-muted">{t("loadFailed")}</p>
          <Button size="sm" onClick={refreshAll}>
            {t("retry")}
          </Button>
        </div>
      </Card>
    );
  }

  if (devicesQuery.isLoading || !status) {
    return <LoadingBlock />;
  }

  return (
    <div className="space-y-4">
      {/* 连接状态条 */}
      <Card>
        <div className="flex items-center gap-3">
          {status.connected ? (
            <PlugZap className="h-4 w-4 text-ok flex-shrink-0" />
          ) : (
            <Unplug className="h-4 w-4 text-muted flex-shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <div className="text-[13px] font-medium text-heading">
              {status.configured
                ? status.connected
                  ? t("status.connected", {
                      name: status.provider_name,
                      count: status.device_count,
                    })
                  : t("status.disconnected", { name: status.provider_name })
                : t("status.unconfigured")}
            </div>
            {status.last_error && (
              <div className="text-[11px] text-danger truncate">{status.last_error}</div>
            )}
            {!status.configured && (
              <div className="text-[11px] text-muted">{t("status.unconfiguredHint")}</div>
            )}
          </div>
          <Button variant="ghost" size="icon" onClick={refreshAll} title={t("status.refresh")}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </Card>

      {/* 注入预览 */}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <h3 className="flex items-center gap-2 text-[14px] font-semibold tracking-tight text-heading">
            <Eye className="h-4 w-4 text-accent" />
            {t("preview.title")}
          </h3>
          <Button
            variant="ghost" size="icon"
            onClick={() => void previewQuery.refetch()}
            title={t("preview.refresh")}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
        {previewQuery.data?.injecting ? (
          <pre className="whitespace-pre-wrap break-all rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-[12px] leading-relaxed text-foreground">
            {previewQuery.data.content}
          </pre>
        ) : (
          <div className="text-[12px] text-muted">{t("preview.empty")}</div>
        )}
      </Card>

      {/* 设备（按房间分组） */}
      <div className="flex items-center gap-2 text-sm text-muted">
        <Home className="h-4 w-4" />
        {t("devices.hint", { count: devices.length })}
      </div>

      {grouped.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-2 py-8 text-center text-muted">
            <Home className="h-6 w-6" />
            <p className="text-sm">
              {status.connected ? t("devices.empty") : t("devices.emptyOffline")}
            </p>
          </div>
        </Card>
      ) : (
        grouped.map(([area, list]) => (
          <div key={area}>
            <div className="mb-2 text-[12px] font-medium text-muted">
              {area}
              <span className="ml-1.5 text-[10px]">{list.length}</span>
            </div>
            <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
              {list.map((device) => {
                const domain = domainByHa.get(device.domain);
                return (
                  <DeviceCard
                    key={device.entity_id}
                    device={device}
                    domainKey={domain?.key ?? device.domain}
                    domain={domain}
                    pending={pendingIds.has(device.entity_id)}
                    onControl={control}
                  />
                );
              })}
            </div>
          </div>
        ))
      )}

      {/* 设备域组件管理 */}
      <div className="flex items-center gap-2 text-sm text-muted pt-2">
        <Puzzle className="h-4 w-4" />
        {t("domains.hint", { count: domains.length })}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {domains.map((domain) => (
          <DomainCard key={domain.key} domain={domain} onSaveConfig={saveConfig} />
        ))}
      </div>
      <p className="text-[10px] text-muted">{t("domains.footerHint")}</p>
    </div>
  );
}
