/** 设备页签 — 过滤/双视图/可折叠分组的设备总览与实时控制（SSE 就地刷新）。 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Home, Search } from "lucide-react";
import { Card } from "@/components/common/Card";
import { Button, EmptyState } from "@/components/ui";
import { DeviceFilters, EMPTY_FILTERS } from "./DeviceFilters";
import { DeviceGroup } from "./DeviceGroup";
import type { DeviceDomainInfo, DeviceInfo } from "./types";

interface DevicesPanelProps {
  devices: DeviceInfo[];
  domains: DeviceDomainInfo[];
  domainByHa: Map<string, DeviceDomainInfo>;
  connected: boolean;
  pendingIds: Set<string>;
  onControl: (entityId: string, action: string, value?: string) => void;
}

export function DevicesPanel({
  devices,
  domains,
  domainByHa,
  connected,
  pendingIds,
  onControl,
}: DevicesPanelProps) {
  const { t } = useTranslation("smart_home");
  const [filters, setFilters] = useState(EMPTY_FILTERS);

  // 仅展示有域组件的设备；过滤条件：搜索/域/隐藏不可用
  const filtered = useMemo(() => {
    const query = filters.query.trim().toLowerCase();
    return devices.filter((device) => {
      if (!domainByHa.has(device.domain)) return false;
      if (filters.hideUnavailable && !device.available) return false;
      if (filters.domainKey && domainByHa.get(device.domain)?.key !== filters.domainKey) {
        return false;
      }
      if (
        query &&
        !device.name.toLowerCase().includes(query) &&
        !device.entity_id.toLowerCase().includes(query)
      ) {
        return false;
      }
      return true;
    });
  }, [devices, filters, domainByHa]);

  // 过滤芯片只列当前有设备的域
  const presentDomains = useMemo(() => {
    const present = new Set(
      devices
        .map((d) => domainByHa.get(d.domain)?.key)
        .filter((k): k is string => Boolean(k)),
    );
    return domains.filter((d) => present.has(d.key));
  }, [devices, domains, domainByHa]);

  // 分组：房间视图（未分组排尾）/ 类型视图（按域展示名）；设备量级小，直接计算
  const ungrouped = t("devices.ungrouped");
  const groups = new Map<string, DeviceInfo[]>();
  for (const device of filtered) {
    const domain = domainByHa.get(device.domain);
    const key =
      filters.view === "room"
        ? device.area || ungrouped
        : domain?.display_name ?? device.domain;
    const list = groups.get(key) ?? [];
    list.push(device);
    groups.set(key, list);
  }
  const grouped = [...groups.entries()]
    .sort(([a], [b]) =>
      filters.view === "room" && a === ungrouped
        ? 1
        : filters.view === "room" && b === ungrouped
          ? -1
          : a.localeCompare(b, "zh"),
    )
    .map(([title, list]) => [
      title,
      [...list].sort((a, b) => a.name.localeCompare(b.name, "zh")),
    ] as [string, DeviceInfo[]]);

  return (
    <Card
      title={t("devices.title", { count: devices.length })}
      subtitle={t("devices.hint", { count: devices.length })}
    >
      {devices.length === 0 ? (
        <EmptyState
          icon={Home}
          title={connected ? t("devices.empty") : t("devices.emptyOffline")}
        />
      ) : (
        <>
          <DeviceFilters
            value={filters}
            domains={presentDomains}
            onChange={setFilters}
          />
          {grouped.length === 0 ? (
            <EmptyState
              icon={Search}
              title={t("devices.noMatch")}
              action={
                <Button size="sm" onClick={() => setFilters(EMPTY_FILTERS)}>
                  {t("devices.clearFilter")}
                </Button>
              }
            />
          ) : (
            <div className="space-y-4">
              {grouped.map(([title, list]) => (
                <DeviceGroup
                  key={title}
                  title={title}
                  devices={list}
                  domainByHa={domainByHa}
                  pendingIds={pendingIds}
                  onControl={onControl}
                />
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
