/** 房间导航、设备筛选与能力驱动的控制卡片。 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Home, Search } from "lucide-react";
import { Button, EmptyState } from "@/components/ui";
import { DeviceFilters, EMPTY_FILTERS } from "./DeviceFilters";
import { DeviceGroup } from "./DeviceGroup";
import type { DeviceControl } from "./DeviceControls";
import type { CommandFeedback, DeviceDomainInfo, DeviceInfo } from "./types";

export function DevicesPanel({ devices, domains, domainByHa, connected, pendingIds, feedback, onControl, onConnect }: {
  devices: DeviceInfo[]; domains: DeviceDomainInfo[]; domainByHa: Map<string, DeviceDomainInfo>; connected: boolean;
  pendingIds: Set<string>; feedback: Record<string, CommandFeedback>; onControl: DeviceControl; onConnect: () => void;
}) {
  const { t, i18n } = useTranslation("smart_home");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [room, setRoom] = useState("");
  const rooms = [...new Set(devices.map((device) => device.area).filter(Boolean))].sort((a, b) => a.localeCompare(b, i18n.language));
  const filtered = useMemo(() => {
    const query = filters.query.trim().toLocaleLowerCase();
    return devices.filter((device) => (!room || device.area === room)
      && (!filters.hideUnavailable || device.available)
      && (!filters.domainKey || domainByHa.get(device.domain)?.key === filters.domainKey)
      && (!query || [device.name, device.entity_id, device.area].some((value) => value.toLocaleLowerCase().includes(query))));
  }, [devices, filters, domainByHa, room]);
  const presentDomains = domains.filter((domain) => devices.some((device) => domain.ha_domains.includes(device.domain)));
  const grouped = new Map<string, DeviceInfo[]>();
  for (const device of filtered) {
    const domain = domainByHa.get(device.domain);
    const key = filters.view === "room" ? device.area || t("devices.ungrouped") : t("domainNames." + domain?.key, { defaultValue: domain?.display_name ?? device.domain });
    grouped.set(key, [...(grouped.get(key) ?? []), device]);
  }
  const unavailable = devices.filter((device) => !device.available).length;
  if (!devices.length) return <div className="rounded-2xl border border-dashed border-border bg-card px-6 py-12">
    <EmptyState icon={Home} title={t(connected ? "devices.empty" : "devices.emptyOffline")} action={<Button onClick={onConnect}>{t("devices.connect")}</Button>} />
  </div>;
  return <div className="space-y-5">
    <div className="flex gap-2 overflow-x-auto pb-1" aria-label={t("home.rooms")}>
      {[{ key: "", label: t("devices.allRooms"), count: devices.length }, ...rooms.map((key) => ({ key, label: key, count: devices.filter((device) => device.area === key).length }))].map((item) =>
        <button key={item.key} aria-pressed={room === item.key} onClick={() => setRoom(item.key)} className={"flex shrink-0 items-center gap-2 rounded-xl border px-4 py-2.5 text-xs transition-colors " + (room === item.key ? "border-accent/30 bg-accent-subtle text-accent" : "border-border bg-card text-muted hover:text-heading")}>
          {item.key === "" && <Home size={14} />}{item.label}<span className="text-[10px] tabular-nums opacity-70">{item.count}</span>
        </button>)}
    </div>
    <DeviceFilters value={filters} domains={presentDomains} onChange={setFilters} />
    {unavailable > 0 && <p className="flex items-start gap-2 rounded-xl bg-elevated px-4 py-3 text-xs leading-relaxed text-muted"><AlertCircle size={15} className="shrink-0 text-warn" />{t("devices.unavailableHint", { count: unavailable })}</p>}
    {!filtered.length ? <EmptyState icon={Search} title={t("devices.noMatch")} action={<Button size="sm" onClick={() => { setFilters(EMPTY_FILTERS); setRoom(""); }}>{t("devices.clearFilter")}</Button>} />
      : [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b, i18n.language)).map(([title, list]) =>
        <DeviceGroup key={title} title={title} devices={[...list].sort((a, b) => a.name.localeCompare(b.name, i18n.language))} domainByHa={domainByHa} pendingIds={pendingIds} feedback={feedback} onControl={onControl} />)}
  </div>;
}
