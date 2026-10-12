/** 设备能力驱动的控制区，所有参数范围来自后端描述。 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Megaphone, Minus, Pause, Play, Plus, Sparkles, Square } from "lucide-react";
import { Button } from "@/components/ui";
import type { DeviceInfo } from "./types";

export type DeviceControl = (entityId: string, action: string, value?: string) => Promise<boolean>;

export function ValueSlider({ label, value, min = 0, max = 100, step = 1, unit = "%", disabled, onCommit }: {
  label: string; value: number | null; min?: number; max?: number; step?: number; unit?: string;
  disabled: boolean; onCommit: (value: number) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<{ source: number | null; value: number } | null>(null);
  const lastSent = useRef<number | null>(null);
  const shown = draft?.source === value ? draft.value : value;
  const commit = async (next: number) => {
    if (next === value || next === lastSent.current) return;
    lastSent.current = next;
    if (!await onCommit(next)) { setDraft(null); lastSent.current = null; }
  };
  return <label className="block space-y-2.5">
    <span className="flex items-center justify-between gap-2 text-xs"><span className="text-muted">{label}</span><span className="font-medium tabular-nums text-heading">{shown === null ? "—" : shown + unit}</span></span>
    <input aria-label={label} type="range" min={min} max={max} step={step} value={shown ?? min} disabled={disabled}
      className="block h-5 w-full cursor-pointer accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
      onChange={(e) => { lastSent.current = null; setDraft({ source: value, value: Number(e.target.value) }); }}
      onPointerUp={(e) => void commit(Number(e.currentTarget.value))}
      onKeyUp={(e) => { if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(e.key)) void commit(Number(e.currentTarget.value)); }} />
  </label>;
}

export function DeviceControls({ device, domainKey, disabled, onControl }: { device: DeviceInfo; domainKey: string; disabled: boolean; onControl: DeviceControl }) {
  const { t } = useTranslation("smart_home");
  const [speakText, setSpeakText] = useState("");
  const actions = device.actions;
  const attrs = device.attributes;
  const number = (key: string) => typeof attrs[key] === "number" && Number.isFinite(attrs[key]) ? attrs[key] : null;
  const control = (action: string, value = "") => onControl(device.entity_id, action, value);
  const target = number("temperature");
  const brightness = number("brightness");
  const volume = number("volume_level");
  const temperature = actions.set_temperature;
  const modes = actions.set_hvac_mode?.options ?? [];
  const speak = async () => {
    const text = speakText.trim();
    if (text && await control("speak", text)) setSpeakText("");
  };
  return <div className="space-y-4">
    {domainKey === "light" && actions.set_brightness && <ValueSlider label={t("device.brightness")} value={brightness === null ? null : Math.round(brightness / 255 * 100)} disabled={disabled} onCommit={(v) => control("set_brightness", String(v))} />}
    {domainKey === "light" && actions.set_color_temp && <ValueSlider label={t("device.colorTemp")} value={number("color_temp_kelvin")} min={actions.set_color_temp.min ?? 1500} max={actions.set_color_temp.max ?? 8000} step={actions.set_color_temp.step ?? 100} unit=" K" disabled={disabled} onCommit={(v) => control("set_color_temp", String(v))} />}
    {domainKey === "climate" && <>
      {modes.length > 0 && <select aria-label={t("device.mode")} value={modes.includes(device.state) ? device.state : ""} disabled={disabled}
        className="h-9 w-full rounded-lg border border-border bg-elevated px-3 text-xs text-foreground disabled:opacity-50"
        onChange={(e) => void control("set_hvac_mode", e.target.value)}>
        {!modes.includes(device.state) && <option value="" disabled>{t("device.chooseMode")}</option>}
        {modes.map((mode) => <option key={mode} value={mode}>{t("hvac." + mode, { defaultValue: mode })}</option>)}
      </select>}
      {temperature && target !== null && <div className="flex items-center justify-between gap-2 rounded-xl border border-border bg-elevated/50 p-2">
        <Button size="icon" variant="ghost" title={t("device.decrease")} disabled={disabled || target - (temperature.step ?? 1) < (temperature.min ?? 5)} onClick={() => void control("set_temperature", String(target - (temperature.step ?? 1)))}><Minus size={16} /></Button>
        <div className="text-center"><p className="text-[10px] text-muted">{t("device.targetTemp")}</p><p className="text-lg font-semibold tabular-nums text-heading">{target}<span className="ml-1 text-xs font-normal">{String(attrs.temperature_unit ?? "°C")}</span></p></div>
        <Button size="icon" variant="ghost" title={t("device.increase")} disabled={disabled || target + (temperature.step ?? 1) > (temperature.max ?? 40)} onClick={() => void control("set_temperature", String(target + (temperature.step ?? 1)))}><Plus size={16} /></Button>
      </div>}
    </>}
    {domainKey === "cover" && <>
      <div className="flex gap-2">
        {actions.open_cover && <Button className="flex-1" size="sm" disabled={disabled} onClick={() => void control("open_cover")}><ArrowUp size={14} />{t("cover.open")}</Button>}
        {actions.stop_cover && <Button size="icon" title={t("device.stop")} disabled={disabled} onClick={() => void control("stop_cover")}><Square size={13} /></Button>}
        {actions.close_cover && <Button className="flex-1" size="sm" disabled={disabled} onClick={() => void control("close_cover")}><ArrowDown size={14} />{t("cover.close")}</Button>}
      </div>
      {actions.set_position && <ValueSlider label={t("device.position")} value={number("current_position")} disabled={disabled} onCommit={(v) => control("set_position", String(v))} />}
    </>}
    {domainKey === "media_player" && <>
      <div className="flex gap-2">
        {actions.media_play && <Button className="flex-1" size="sm" disabled={disabled} onClick={() => void control("media_play")}><Play size={14} />{t("device.play")}</Button>}
        {actions.media_pause && <Button className="flex-1" size="sm" disabled={disabled} onClick={() => void control("media_pause")}><Pause size={14} />{t("device.pause")}</Button>}
      </div>
      {actions.set_volume && <ValueSlider label={t("device.volume")} value={volume === null ? null : Math.round(volume * 100)} disabled={disabled} onCommit={(v) => control("set_volume", String(v))} />}
      {actions.speak && <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void speak(); }}>
        <input aria-label={t("player.speak")} value={speakText} onChange={(e) => setSpeakText(e.target.value)} disabled={disabled} maxLength={2000}
          className="h-9 min-w-0 flex-1 rounded-lg border border-border bg-elevated px-3 text-xs text-foreground placeholder:text-muted disabled:opacity-50" placeholder={t("player.speakPlaceholder")} />
        <Button type="submit" size="icon" title={t("player.speak")} disabled={disabled || !speakText.trim()}><Megaphone size={15} /></Button>
      </form>}
    </>}
    {domainKey === "scene" && actions.activate && <Button className="w-full" size="sm" disabled={disabled} onClick={() => void control("activate")}><Sparkles size={14} />{t("scene.activate")}</Button>}
  </div>;
}
