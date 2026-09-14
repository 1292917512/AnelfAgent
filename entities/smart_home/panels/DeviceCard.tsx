/**
 * 设备卡片 — 按设备域特化渲染状态与控制件。
 *
 * 控制动作经后端设备域校验，卡片只按域类型组织交互：
 * 灯（开关+亮度）/ 开关（通断）/ 空调（模式+温度）/ 传感器（只读数值）/
 * 窗帘（开停关+位置）/ 播放器（播放暂停+音量）/ 场景（激活）。
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Blinds,
  Lightbulb,
  Megaphone,
  Minus,
  Pause,
  Play,
  Plus,
  Sparkles,
  Speaker,
  Square,
  Thermometer,
  ToggleLeft,
  Waves,
} from "lucide-react";
import { Button, Switch } from "@/components/ui";
import type { DeviceDomainInfo, DeviceInfo } from "./types";

/** 设备域图标（未知域回落开关图标） */
const DOMAIN_ICONS: Record<string, typeof Lightbulb> = {
  light: Lightbulb,
  switch: ToggleLeft,
  climate: Thermometer,
  sensor: Waves,
  cover: Blinds,
  media_player: Speaker,
  scene: Sparkles,
};

const HVAC_MODES = ["off", "cool", "heat", "auto", "dry", "fan_only"];

interface DeviceCardProps {
  device: DeviceInfo;
  domainKey: string;
  domain?: DeviceDomainInfo;
  pending: boolean;
  onControl: (entityId: string, action: string, value?: string) => void;
}

/** 数值滑条（拖动中本地预览，释放时提交） */
function CommitSlider({
  value,
  onCommit,
  disabled,
}: {
  value: number;
  onCommit: (v: number) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="flex items-center gap-2 flex-1">
      <input
        type="range"
        min={0}
        max={100}
        value={draft}
        disabled={disabled}
        className="flex-1 h-1.5 accent-[var(--accent)] cursor-pointer disabled:opacity-40"
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={() => draft !== value && onCommit(draft)}
        onKeyUp={(e) => (e.key === "ArrowLeft" || e.key === "ArrowRight") && draft !== value && onCommit(draft)}
      />
      <span className="w-10 text-right text-[12px] tabular-nums text-muted">{draft}%</span>
    </div>
  );
}

export function DeviceCard({ device, domainKey, domain, pending, onControl }: DeviceCardProps) {
  const { t } = useTranslation("smart_home");
  const Icon = DOMAIN_ICONS[domainKey] ?? ToggleLeft;
  const attrs = device.attributes as Record<string, number | string | undefined>;
  const disabled = pending || !device.available || !domain?.enabled;
  const [speakText, setSpeakText] = useState("");
  const control = (action: string, value = "") =>
    onControl(device.entity_id, action, value);

  const speak = () => {
    const text = speakText.trim();
    if (!text) return;
    control("speak", text);
    setSpeakText("");
  };

  const brightnessPct =
    typeof attrs.brightness === "number" ? Math.round((attrs.brightness / 255) * 100) : null;
  const volumePct =
    typeof attrs.volume_level === "number" ? Math.round(attrs.volume_level * 100) : null;
  const position = typeof attrs.current_position === "number" ? attrs.current_position : null;
  const targetTemp = typeof attrs.temperature === "number" ? attrs.temperature : null;

  const stateText = (() => {
    if (!device.available) return t("device.unavailable");
    switch (domainKey) {
      case "light":
      case "switch":
        return device.state === "on" ? t("state.on") : t("state.off");
      case "climate":
        return t(`hvac.${device.state}`, { defaultValue: device.state });
      case "sensor":
        return device.domain === "binary_sensor"
          ? device.state === "on" ? t("state.triggered") : t("state.normal")
          : `${device.state}${attrs.unit_of_measurement ?? ""}`;
      case "cover":
        return t(`cover.${device.state}`, { defaultValue: device.state });
      case "media_player":
        return t(`player.${device.state}`, { defaultValue: device.state });
      case "scene":
        return t("scene.ready");
      default:
        return device.state;
    }
  })();

  return (
    <div
      className={`rounded-lg border border-border bg-surface px-3 py-2.5 space-y-2 ${
        device.available ? "" : "opacity-55"
      }`}
    >
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 flex-shrink-0 ${device.state === "on" || device.state === "playing" || device.state === "open" ? "text-accent" : "text-muted"}`} />
        <span className="flex-1 min-w-0 truncate text-[13px] font-medium text-heading" title={device.entity_id}>
          {device.name}
        </span>
        <span className="text-[12px] text-muted flex-shrink-0">{stateText}</span>
        {(domainKey === "light" || domainKey === "switch") && (
          <Switch
            checked={device.state === "on"}
            disabled={disabled}
            onChange={(v) => control(v ? "turn_on" : "turn_off")}
          />
        )}
      </div>

      {/* 灯：亮度 */}
      {domainKey === "light" && device.state === "on" && brightnessPct !== null && domain?.actions.set_brightness && (
        <CommitSlider
          value={brightnessPct}
          disabled={disabled}
          onCommit={(v) => control("set_brightness", String(v))}
        />
      )}

      {/* 空调：模式 + 温度 */}
      {domainKey === "climate" && (
        <div className="flex items-center gap-2">
          <select
            className="h-7 rounded-md border border-border bg-background px-1.5 text-[12px] text-foreground disabled:opacity-40"
            value={HVAC_MODES.includes(device.state) ? device.state : "off"}
            disabled={disabled}
            onChange={(e) =>
              e.target.value === "off"
                ? control("turn_off")
                : control("set_hvac_mode", e.target.value)
            }
          >
            {HVAC_MODES.map((mode) => (
              <option key={mode} value={mode}>{t(`hvac.${mode}`)}</option>
            ))}
          </select>
          {device.state !== "off" && targetTemp !== null && (
            <div className="flex items-center gap-1 ml-auto">
              <Button
                variant="ghost" size="icon" className="h-6 w-6"
                disabled={disabled}
                onClick={() => control("set_temperature", String(targetTemp - 1))}
              >
                <Minus className="h-3 w-3" />
              </Button>
              <span className="w-12 text-center text-[13px] tabular-nums text-heading">{targetTemp}°C</span>
              <Button
                variant="ghost" size="icon" className="h-6 w-6"
                disabled={disabled}
                onClick={() => control("set_temperature", String(targetTemp + 1))}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 窗帘：开 / 停 / 关 + 位置 */}
      {domainKey === "cover" && (
        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="sm" disabled={disabled} onClick={() => control("open_cover")}>
            {t("cover.open")}
          </Button>
          <Button variant="ghost" size="sm" disabled={disabled} onClick={() => control("stop_cover")}>
            <Square className="h-3 w-3" />
          </Button>
          <Button variant="ghost" size="sm" disabled={disabled} onClick={() => control("close_cover")}>
            {t("cover.close")}
          </Button>
          {position !== null && (
            <CommitSlider
              value={position}
              disabled={disabled}
              onCommit={(v) => control("set_position", String(v))}
            />
          )}
        </div>
      )}

      {/* 播放器：播放 / 暂停 + 音量 */}
      {domainKey === "media_player" && device.state !== "off" && (
        <div className="flex items-center gap-1.5">
          {device.state === "playing" ? (
            <Button variant="ghost" size="icon" className="h-7 w-7" disabled={disabled} onClick={() => control("media_pause")}>
              <Pause className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <Button variant="ghost" size="icon" className="h-7 w-7" disabled={disabled} onClick={() => control("media_play")}>
              <Play className="h-3.5 w-3.5" />
            </Button>
          )}
          {typeof attrs.media_title === "string" && attrs.media_title && (
            <span className="flex-1 min-w-0 truncate text-[12px] text-muted">{attrs.media_title}</span>
          )}
          {volumePct !== null && (
            <CommitSlider
              value={volumePct}
              disabled={disabled}
              onCommit={(v) => control("set_volume", String(v))}
            />
          )}
        </div>
      )}

      {/* 播放器：语音播报（域动作含 speak 时展示） */}
      {domainKey === "media_player" && device.state !== "off" && domain?.actions.speak && (
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            className="flex-1 min-w-0 h-7 rounded-md border border-border bg-background px-2 text-[12px] text-foreground placeholder:text-muted disabled:opacity-40"
            placeholder={t("player.speakPlaceholder")}
            value={speakText}
            disabled={disabled}
            onChange={(e) => setSpeakText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") speak();
            }}
          />
          <Button
            variant="ghost" size="icon" className="h-7 w-7 flex-shrink-0"
            disabled={disabled || !speakText.trim()}
            title={t("player.speak")}
            onClick={speak}
          >
            <Megaphone className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {/* 场景：激活 */}
      {domainKey === "scene" && (
        <Button variant="ghost" size="sm" disabled={disabled || !domain?.enabled} onClick={() => control("activate")}>
          <Sparkles className="h-3.5 w-3.5 mr-1" />
          {t("scene.activate")}
        </Button>
      )}
    </div>
  );
}
