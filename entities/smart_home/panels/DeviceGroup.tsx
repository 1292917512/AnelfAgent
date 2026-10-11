/** 设备分组（可折叠）— 组头（计数徽章）+ 设备卡网格。 */
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui";
import { DeviceCard } from "./DeviceCard";
import type { DeviceDomainInfo, DeviceInfo } from "./types";

interface DeviceGroupProps {
  title: string;
  devices: DeviceInfo[];
  domainByHa: Map<string, DeviceDomainInfo>;
  pendingIds: Set<string>;
  onControl: (entityId: string, action: string, value?: string) => void;
}

export function DeviceGroup({
  title,
  devices,
  domainByHa,
  pendingIds,
  onControl,
}: DeviceGroupProps) {
  const [open, setOpen] = useState(true);
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold tracking-wide text-muted-strong hover:text-foreground"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        {title}
        <Badge variant="neutral">{devices.length}</Badge>
      </button>
      {open && (
        <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
          {devices.map((device) => {
            const domain = domainByHa.get(device.domain);
            return (
              <DeviceCard
                key={device.entity_id}
                device={device}
                domainKey={domain?.key ?? device.domain}
                domain={domain}
                pending={pendingIds.has(device.entity_id)}
                onControl={onControl}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}
