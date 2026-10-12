/** 设备卡片展示真实状态与能力；过期快照保留可读并禁止控制。 */
import { useTranslation } from "react-i18next";
import { Blinds, Clock3, Lightbulb, Loader2, Power, Radio, Sparkles, Speaker, Thermometer, ToggleLeft } from "lucide-react";
import { Button } from "@/components/ui";
import { DeviceControls, type DeviceControl } from "./DeviceControls";
import { formatIsoTimestamp } from "./format";
import type { CommandFeedback, DeviceDomainInfo, DeviceInfo } from "./types";

const ICONS = { light: Lightbulb, switch: ToggleLeft, climate: Thermometer, sensor: Radio, cover: Blinds, media_player: Speaker, scene: Sparkles };
const ACTIVE_STATES = ["on", "playing", "open", "opening", "cool", "heat", "auto", "dry", "fan_only"];

export function DeviceCard({ device, domainKey, domain, pending, feedback, onControl }: {
  device: DeviceInfo; domainKey: string; domain?: DeviceDomainInfo; pending: boolean;
  feedback?: CommandFeedback; onControl: DeviceControl;
}) {
  const { t, i18n } = useTranslation("smart_home");
  const Icon = ICONS[domainKey as keyof typeof ICONS] ?? ToggleLeft;
  const disabled = pending || !device.available || !domain?.enabled;
  const active = device.available && ACTIVE_STATES.includes(device.state);
  const powerAction = device.state === "off" ? "turn_on" : "turn_off";
  const canPower = Boolean(device.actions[powerAction]) && ["light", "switch", "climate", "media_player"].includes(domainKey);
  const stateText = (() => {
    if (device.stale) return t("device.stale");
    if (!device.available) return t("device.unavailable");
    if (domainKey === "scene") return t("scene.ready");
    if (device.domain === "binary_sensor") return t(device.state === "on" ? "state.triggered" : "state.normal");
    if (domainKey === "sensor") return formatIsoTimestamp(device.state, i18n.language) ?? device.state + String(device.attributes.unit_of_measurement ?? "");
    const namespace = domainKey === "climate" ? "hvac" : domainKey === "cover" ? "cover" : domainKey === "media_player" ? "player" : "state";
    return t(namespace + "." + device.state, { defaultValue: device.state });
  })();
  const currentTemp = device.attributes.current_temperature;
  const mediaTitle = device.attributes.media_title;
  const showFeedback = feedback && (feedback.kind === "error" || device.updated_at * 1000 <= feedback.at);
  return <article className={"flex min-w-0 flex-col rounded-2xl border bg-card p-4 transition-colors sm:p-5 " + (active ? "border-accent/30" : "border-border")} aria-label={device.name}>
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className={"grid h-10 w-10 place-items-center rounded-xl " + (active ? "bg-accent-subtle text-accent" : "bg-elevated text-muted")}><Icon size={20} strokeWidth={1.7} /></div>
      {pending ? <span role="status" className="flex h-9 items-center gap-1.5 text-xs text-muted"><Loader2 size={14} className="animate-spin" />{t("device.sending")}</span>
        : canPower ? <Button size="icon" variant={active ? "primary" : "secondary"} title={t(powerAction === "turn_on" ? "device.turnOn" : "device.turnOff", { name: device.name })} disabled={disabled} onClick={() => void onControl(device.entity_id, powerAction)}><Power size={16} /></Button>
        : <span className="rounded-full bg-elevated px-2.5 py-1 text-[10px] text-muted">{t("domainNames." + domainKey, { defaultValue: domain?.display_name ?? domainKey })}</span>}
    </div>
    <h3 className="truncate text-sm font-semibold text-heading" title={device.name}>{device.name}</h3>
    <p className="mt-1 truncate text-[11px] text-muted">{device.area || t("devices.ungrouped")}<span className="mx-1.5 opacity-50">/</span>{t("providerNames." + device.provider, { defaultValue: device.provider })}</p>
    <div className="mb-4 mt-4">
      <p className={"break-words text-xl font-medium tracking-tight " + (device.available ? "text-heading" : "text-muted")}>{stateText}</p>
      {typeof currentTemp === "number" && device.available && <p className="mt-1 text-xs text-muted">{t("device.currentTemp", { value: currentTemp, unit: String(device.attributes.temperature_unit ?? "°C") })}</p>}
      {typeof mediaTitle === "string" && mediaTitle && device.available && <p className="mt-1 truncate text-xs text-muted" title={mediaTitle}>{mediaTitle}</p>}
    </div>
    <div className="mt-auto"><DeviceControls device={device} domainKey={domainKey} disabled={disabled} onControl={onControl} /></div>
    {!domain?.enabled && <p className="mt-3 text-xs text-muted">{t("device.domainDisabled")}</p>}
    {showFeedback && <p role={feedback.kind === "error" ? "alert" : "status"} className={"mt-3 rounded-lg px-2.5 py-2 text-xs leading-relaxed " + (feedback.kind === "error" ? "bg-danger-subtle text-danger" : "bg-accent-subtle text-accent")}>{feedback.message}</p>}
    <details className="mt-4 border-t border-border/60 pt-3 text-[10px] text-muted">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 hover:text-foreground"><Clock3 size={11} />{device.updated_at > 0 ? t("device.updatedAt", { time: new Date(device.updated_at * 1000).toLocaleTimeString(i18n.language, { hour: "2-digit", minute: "2-digit" }) }) : t("device.noSnapshot")}</summary>
      <p className="mt-2 break-all font-mono leading-relaxed">{device.entity_id}</p>
    </details>
  </article>;
}
