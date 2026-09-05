import { Suspense } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Power, Settings2 } from "lucide-react";
import type { ConfigMetaItem, ConfigValues } from "@/lib/types";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import { ConfigField } from "./ConfigField";
import { CHANNEL_LOGIN_COMPONENTS } from "@/lib/channel-plugins";

/** 未注册为频道的配置组卡片（可启用 + 配置表单） */
export function UnmatchedGroupCard({
  channelKey,
  configs,
  values,
  isOpen,
  toggling,
  onToggleExpand,
  onStart,
  onUpdateVal,
}: {
  channelKey: string;
  configs: ConfigMetaItem[];
  values: ConfigValues;
  isOpen: boolean;
  toggling: boolean;
  onToggleExpand: () => void;
  onStart: () => void;
  onUpdateVal: (key: string, val: unknown) => void;
}) {
  const { t } = useTranslation("channels");
  // 未注册频道同样可携带插件登录入口（如扫码登录在启用前可用）
  const PluginLogin = CHANNEL_LOGIN_COMPONENTS[channelKey];
  return (
    <div className={cn(
      "rounded-md border transition-all bg-card",
      isOpen ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
             : "border-border hover:border-border-strong",
    )}>
      <div className="flex items-center justify-between p-4 cursor-pointer"
        onClick={onToggleExpand}>
        <div className="flex items-center gap-3">
          <ChevronDown size={16} className={cn("text-muted transition-transform", isOpen && "rotate-180")} />
          <StatusDot status="offline" />
          <div>
            <span className="text-sm font-medium text-heading">{channelKey}</span>
            <span className="ml-2 text-[11px] px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("notEnabled")}
            </span>
          </div>
          <span className="text-[11px] text-muted flex items-center gap-1">
            <Settings2 size={12} /> {t("nConfigItems", { count: configs.length })}
          </span>
        </div>
        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {PluginLogin && (
            <Suspense fallback={null}>
              <PluginLogin compact />
            </Suspense>
          )}
          <button
            onClick={onStart}
            disabled={toggling}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-all disabled:opacity-70",
              toggling
                ? "border-border text-warn bg-warn-subtle cursor-wait"
                : "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle",
            )}
          >
            <Power size={14} className={toggling ? "animate-spin" : ""} />
            {toggling ? t("starting") : t("start")}
          </button>
        </div>
      </div>
      {isOpen && (
        <div className="border-t border-border p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {configs.map((meta) => (
              <ConfigField key={meta.key} meta={meta} prefix={`${channelKey}_`}
                value={values[meta.key]} onChange={(v) => onUpdateVal(meta.key, v)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
