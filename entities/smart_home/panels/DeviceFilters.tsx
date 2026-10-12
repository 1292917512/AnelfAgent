/** 设备过滤条（展示组件，状态归父级）— 搜索 / 设备域芯片 / 房间|类型视图 / 隐藏不可用。 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Boxes, Home, Search } from "lucide-react";
import { Input, Switch } from "@/components/ui";
import type { DeviceDomainInfo } from "./types";

export interface DeviceFilterState {
  query: string;
  domainKey: string;
  view: "room" | "domain";
  hideUnavailable: boolean;
}

export const EMPTY_FILTERS: DeviceFilterState = {
  query: "",
  domainKey: "",
  view: "room",
  hideUnavailable: false,
};

interface DeviceFiltersProps {
  value: DeviceFilterState;
  domains: DeviceDomainInfo[];
  onChange: (next: DeviceFilterState) => void;
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-lg border px-3 py-2 text-xs transition-colors ${
        active
          ? "border-accent/40 bg-accent-subtle text-accent"
          : "border-border text-muted hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

export function DeviceFilters({ value, domains, onChange }: DeviceFiltersProps) {
  const { t } = useTranslation("smart_home");
  const set = (patch: Partial<DeviceFilterState>) => onChange({ ...value, ...patch });
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative w-full sm:w-auto">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
        <Input
          className="h-9 w-full pl-8 text-xs sm:w-56"
          aria-label={t("devices.search")}
          placeholder={t("devices.search")}
          value={value.query}
          onChange={(e) => set({ query: e.target.value })}
        />
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <FilterChip active={value.domainKey === ""} onClick={() => set({ domainKey: "" })}>
          {t("devices.allDomains")}
        </FilterChip>
        {domains.map((domain) => (
          <FilterChip
            key={domain.key}
            active={value.domainKey === domain.key}
            onClick={() =>
              set({ domainKey: value.domainKey === domain.key ? "" : domain.key })
            }
          >
            {t("domainNames." + domain.key, { defaultValue: domain.display_name })}
          </FilterChip>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-3">
        <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted">
          <Switch
            checked={value.hideUnavailable}
            label={t("devices.hideUnavailable")}
            onChange={(v) => set({ hideUnavailable: v })}
          />
          {t("devices.hideUnavailable")}
        </label>
        <div className="flex overflow-hidden rounded-md border border-border">
          <button
            type="button"
            title={t("devices.viewRoom")}
            aria-pressed={value.view === "room"}
            onClick={() => set({ view: "room" })}
            className={`px-2 py-1 ${value.view === "room" ? "bg-accent-subtle text-accent" : "text-muted hover:text-foreground"}`}
          >
            <Home className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            title={t("devices.viewDomain")}
            aria-pressed={value.view === "domain"}
            onClick={() => set({ view: "domain" })}
            className={`px-2 py-1 ${value.view === "domain" ? "bg-accent-subtle text-accent" : "text-muted hover:text-foreground"}`}
          >
            <Boxes className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
