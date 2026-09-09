import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, MapPin, Plus, Search, Trash2 } from "lucide-react";
import { Button, Input, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { aiDesktopApi } from "./api";
import type {
  DesktopModuleInfo,
  GeocodeCandidate,
  LocationWeather,
} from "./types";

/** 配置中落库的地区条目（含检索确认时写入的坐标） */
interface StoredLocation {
  name: string;
  label?: string;
  latitude?: number;
  longitude?: number;
  enabled: boolean;
}

interface WeatherLocationsProps {
  mod: DesktopModuleInfo;
  onSave: (key: string, value: unknown) => Promise<void>;
  onRefresh: (mod: DesktopModuleInfo) => void;
}

function formatTime(epoch?: number): string {
  if (!epoch) return "-";
  return new Date(epoch * 1000).toLocaleTimeString();
}

/** 单个地区的数据微格 */
function MiniCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-card border border-border/50 px-2 py-1">
      <div className="text-[9px] text-muted">{label}</div>
      <div className="text-xs font-medium text-foreground">{value}</div>
    </div>
  );
}

/**
 * 天气地区管理器 — 检索确认添加（带坐标落库，杜绝错配城市）+
 * 逐地区启停/移除 + 逐地区实时数据展示。保存即触发后端即时重采集。
 */
export function WeatherLocations({ mod, onSave, onRefresh }: WeatherLocationsProps) {
  const { t } = useTranslation("ai_desktop");
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<GeocodeCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [busy, setBusy] = useState(false);
  const searchSeq = useRef(0);

  const configItem = mod.configs.find((c) => c.name === "locations");
  const statusByName = new Map(
    (mod.detail.locations ?? []).map((l: LocationWeather) => [l.name, l]),
  );

  // 配置中的地区列表（含坐标的完整落库条目）
  const stored: StoredLocation[] = (() => {
    try {
      const raw = typeof configItem?.value === "string"
        ? JSON.parse(configItem.value)
        : configItem?.value;
      if (!Array.isArray(raw)) return [];
      return raw
        .filter((l) => l && typeof l === "object" && l.name)
        .map((l) => ({
          name: String(l.name),
          label: l.label ? String(l.label) : undefined,
          latitude: typeof l.latitude === "number" ? l.latitude : undefined,
          longitude: typeof l.longitude === "number" ? l.longitude : undefined,
          enabled: l.enabled !== false,
        }));
    } catch {
      return [];
    }
  })();

  // 检索防抖（300ms；只采纳最新一次查询的结果）
  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setCandidates([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const seq = ++searchSeq.current;
    const timer = setTimeout(() => {
      aiDesktopApi.geocode(text)
        .then((r) => {
          if (searchSeq.current === seq) {
            setCandidates(r.data.candidates);
            setShowDropdown(true);
          }
        })
        .catch(() => {
          if (searchSeq.current === seq) {
            setCandidates([]);
            toast.error(t("weather.searchFailed"));
          }
        })
        .finally(() => {
          if (searchSeq.current === seq) setSearching(false);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query, t]);

  if (!configItem) return null;

  const persist = async (next: StoredLocation[]) => {
    setBusy(true);
    try {
      await onSave(configItem.key, JSON.stringify(next));
      onRefresh(mod);
    } catch {
      toast.error(t("config.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const addCandidate = (c: GeocodeCandidate) => {
    if (stored.some((l) => l.name === c.name)) {
      toast.error(t("weather.duplicate"));
      return;
    }
    void persist([...stored, {
      name: c.name,
      label: c.label,
      latitude: c.latitude,
      longitude: c.longitude,
      enabled: true,
    }]);
    setQuery("");
    setCandidates([]);
    setShowDropdown(false);
  };

  const toggleLocation = (name: string, enabled: boolean) => {
    void persist(stored.map((l) => (l.name === name ? { ...l, enabled } : l)));
  };

  const removeLocation = (name: string) => {
    void persist(stored.filter((l) => l.name !== name));
  };

  return (
    <div className="space-y-2">
      {stored.map((loc) => {
        const status = statusByName.get(loc.name);
        return (
          <div
            key={loc.name}
            className={`rounded-md border border-border/60 bg-elevated px-3 py-2 ${loc.enabled ? "" : "opacity-55"}`}
          >
            <div className="flex items-center gap-2">
              <MapPin className="h-3.5 w-3.5 text-accent flex-shrink-0" />
              <span className="text-[13px] font-medium text-foreground">{loc.name}</span>
              {(loc.label || status?.configured_label) && (
                <span className="text-[10px] text-muted truncate">
                  {loc.label || status?.configured_label}
                </span>
              )}
              <span className="flex-1" />
              <Switch
                checked={loc.enabled}
                disabled={busy || !mod.enabled}
                onChange={(v) => toggleLocation(loc.name, v)}
              />
              <Button
                variant="ghost"
                size="icon"
                disabled={busy}
                onClick={() => removeLocation(loc.name)}
                title={t("weather.remove")}
              >
                <Trash2 className="h-3.5 w-3.5 text-danger" />
              </Button>
            </div>
            {loc.enabled && status?.error && (
              <p className="mt-1.5 text-[11px] text-danger">{status.error}</p>
            )}
            {loc.enabled && !status?.error && !status?.has_data && (
              <p className="mt-1.5 text-[11px] text-muted">{t("weather.pending")}</p>
            )}
            {loc.enabled && status?.has_data && (
              <div className="mt-2 grid grid-cols-3 lg:grid-cols-6 gap-1.5">
                <MiniCell label={t("detail.condition")} value={status.condition || "-"} />
                <MiniCell
                  label={t("detail.temp")}
                  value={status.temp != null
                    ? `${status.temp.toFixed(0)}°C${status.feels != null ? ` (${status.feels.toFixed(0)}°C)` : ""}`
                    : "-"}
                />
                <MiniCell
                  label={t("detail.humidity")}
                  value={status.humidity != null ? `${status.humidity.toFixed(0)}%` : "-"}
                />
                <MiniCell
                  label={t("detail.wind")}
                  value={status.wind_speed != null
                    ? `${status.wind_direction || ""} ${status.wind_speed.toFixed(0)}km/h`.trim()
                    : "-"}
                />
                <MiniCell
                  label={t("detail.todayRange")}
                  value={status.today_min != null && status.today_max != null
                    ? `${status.today_min.toFixed(0)}~${status.today_max.toFixed(0)}°C`
                    : "-"}
                />
                <MiniCell label={t("detail.fetchedAt")} value={formatTime(status.fetched_at)} />
              </div>
            )}
          </div>
        );
      })}

      {/* 检索确认添加地区 */}
      <div className="relative">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted" />
            <Input
              className="h-8 pl-8"
              value={query}
              disabled={busy || !mod.enabled}
              placeholder={t("weather.addPlaceholder")}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => candidates.length > 0 && setShowDropdown(true)}
              onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
            />
            {searching && (
              <Loader2 className="absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted animate-spin" />
            )}
          </div>
        </div>
        {showDropdown && query.trim() && (
          <div className="absolute z-10 mt-1 w-full rounded-md border border-border bg-card shadow-lg max-h-56 overflow-auto">
            {candidates.length === 0 && !searching ? (
              <p className="px-3 py-2 text-xs text-muted">{t("weather.noResults")}</p>
            ) : (
              candidates.map((c, i) => (
                <button
                  key={`${c.name}-${c.latitude}-${i}`}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => addCandidate(c)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-hover transition-colors"
                >
                  <Plus className="h-3 w-3 text-accent flex-shrink-0" />
                  <span className="text-[13px] text-foreground">{c.name}</span>
                  <span className="text-[10px] text-muted truncate">{c.label}</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
      <p className="text-[10px] text-muted">{t("weather.sourceHint")}</p>
    </div>
  );
}
