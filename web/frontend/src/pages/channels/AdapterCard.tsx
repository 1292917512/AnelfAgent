import { useTranslation } from "react-i18next";
import { ChevronDown, Power, RotateCcw, Settings2, Blocks, Wifi, WifiOff } from "lucide-react";
import type { AdapterInfo, ConfigValues } from "@/lib/types";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import { ChannelWebView } from "./ChannelWebView";
import { WeixinQrLogin } from "./WeixinQrLogin";
import { ConfigField } from "./ConfigField";
import type { ConfigMeta } from "./ConfigField";

const statusToColor = (s: string): "ok" | "warn" | "danger" | "offline" => {
  switch (s) {
    case "running": return "ok";
    case "starting": case "reconnecting": return "warn";
    case "error": return "danger";
    default: return "offline";
  }
};

/** 单个频道卡片：头部状态行 + 展开后的连接状态 / WebView / 配置表单 */
export function AdapterCard({
  adapter: a,
  isOpen,
  configs,
  values,
  toggling,
  onToggleExpand,
  onToggle,
  onOpenTools,
  onUpdateVal,
  onResetDefaults,
}: {
  adapter: AdapterInfo;
  isOpen: boolean;
  configs: Array<[string, ConfigMeta]>;
  values: ConfigValues;
  toggling: boolean;
  onToggleExpand: () => void;
  onToggle: () => void;
  onOpenTools?: (channel: { key: string; name: string }) => void;
  onUpdateVal: (key: string, val: unknown) => void;
  onResetDefaults: () => void;
}) {
  const { t } = useTranslation("channels");
  const isRunning = a.status === "running";
  return (
    <div className={cn(
      "rounded-md border transition-all bg-card",
      isOpen ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
             : "border-border hover:border-border-strong",
    )}>
      {/* Header */}
      <div className="flex items-center justify-between p-4 cursor-pointer"
        onClick={onToggleExpand}>
        <div className="flex items-center gap-3">
          <ChevronDown size={16} className={cn("text-muted transition-transform", isOpen && "rotate-180")} />
          <StatusDot status={statusToColor(a.status)} />
          <div>
            <span className="text-sm font-medium text-heading">{a.name}</span>
            <span className={cn("ml-2 text-[11px] px-2 py-0.5 rounded-full border",
              a.status === "running"
                ? "bg-ok-subtle text-ok border-[rgba(34,197,94,0.3)]"
                : a.status === "error"
                  ? "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)]"
                  : "bg-secondary text-muted border-border"
            )}>{a.status_display}</span>
          </div>
          {configs.length > 0 && (
            <span className="text-[11px] text-muted flex items-center gap-1">
              <Settings2 size={12} /> {t("nConfigItems", { count: configs.length })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {a.key === "weixin" && <WeixinQrLogin compact />}
          {onOpenTools && (
            <button onClick={() => onOpenTools({ key: a.key, name: a.name })}
              title={t("tools.openTools")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-all">
              <Blocks size={14} />
              {t("tools.openTools")}
            </button>
          )}
          <button onClick={onToggle} disabled={toggling}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-all disabled:opacity-70",
              toggling
                ? "border-border text-warn bg-warn-subtle cursor-wait"
                : isRunning
                  ? "border-[rgba(239,68,68,0.3)] text-danger hover:bg-danger-subtle"
                  : "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle",
            )}>
            <Power size={14} className={toggling ? "animate-spin" : ""} />
            {toggling ? (isRunning ? t("stopping") : t("starting")) : isRunning ? t("stop") : t("start")}
          </button>
        </div>
      </div>

      {/* Expanded: status + config */}
      {isOpen && (
        <div className="border-t border-border p-4 space-y-4">
          {/* Connection status panel */}
          {a.detail && (
            <div className={cn(
              "flex items-center gap-3 px-3 py-2.5 rounded-md text-xs font-mono",
              (a.online || a.ws_connected)
                ? "bg-ok-subtle text-ok border border-[rgba(34,197,94,0.2)]"
                : a.status === "running" || a.status === "reconnecting"
                  ? "bg-warn-subtle text-warn border border-[rgba(245,158,11,0.2)]"
                  : "bg-secondary text-muted border border-border"
            )}>
              {(a.online || a.ws_connected) ? <Wifi size={14} /> : <WifiOff size={14} />}
              <span>{a.detail}</span>
              {a.self_id && (
                <span className="ml-auto text-[10px] opacity-70">bot: {a.self_id}</span>
              )}
            </div>
          )}

          {/* Embedded WebUI iframe */}
          <ChannelWebView channelKey={a.key} configs={configs} values={values} />

          {configs.length > 0 ? (
            <>
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-muted uppercase tracking-wider">
                  {t("channelConfig", { name: a.name })}
                </p>
                <button onClick={onResetDefaults}
                  className="flex items-center gap-1 px-2 py-1 text-[11px] text-muted rounded hover:bg-hover transition-colors">
                  <RotateCcw size={12} /> {t("resetDefaults")}
                </button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {configs
                  .filter(([, meta]) => {
                    if (!meta.tag) return true;
                    const modeKey = configs.find(([k]) => k.endsWith(".ws_mode"));
                    if (!modeKey) return true;
                    const currentMode = values[modeKey[0]] ?? modeKey[1].value;
                    return meta.tag === currentMode;
                  })
                  .map(([key, meta]) => (
                  <ConfigField key={key} configKey={key} meta={meta}
                    value={values[key]} onChange={(v) => onUpdateVal(key, v)} />
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted text-center py-2">
              {t("noConfig")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
