import { useTranslation } from "react-i18next";
import {
  Crosshair, Database, List, ListTree, PanelLeftClose, PanelLeftOpen, Power, PowerOff, Workflow, Wrench,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type ThinkingViewMode = "flow" | "timeline";

/** 思维链页面工具栏：开关 / 连接状态 / 视图切换 / 面板与跟随开关 */
export function ThinkingToolbar({
  isMobile,
  onShowSessions,
  enabled,
  onToggle,
  connected,
  view,
  onViewChange,
  showTools,
  onToggleTools,
  showProviders,
  onToggleProviders,
  autoFollow,
  onToggleAutoFollow,
  nodeCount,
}: {
  isMobile: boolean;
  onShowSessions: () => void;
  enabled: boolean;
  onToggle: () => void;
  connected: boolean;
  view: ThinkingViewMode;
  onViewChange: (view: ThinkingViewMode) => void;
  showTools: boolean;
  onToggleTools: () => void;
  showProviders: boolean;
  onToggleProviders: () => void;
  autoFollow: boolean;
  onToggleAutoFollow: () => void;
  nodeCount: number | undefined;
}) {
  const { t } = useTranslation("thinking");
  const { t: tc } = useTranslation("common");
  return (
    <div className="flex items-center gap-2 px-3 md:px-4 py-2 border-b border-border bg-panel">
      {isMobile && (
        <button
          onClick={onShowSessions}
          className="flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium text-muted hover:text-foreground transition-all"
          title={t("sessionList")}
          aria-label={t("sessionList")}
        >
          <List size={14} />
        </button>
      )}
      <button
        onClick={onToggle}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all",
          enabled
            ? "bg-ok-subtle text-ok border border-ok"
            : "bg-hover text-muted border border-border hover:border-border-strong",
        )}
      >
        {enabled ? <Power size={12} /> : <PowerOff size={12} />}
        <span className="hidden sm:inline">{enabled ? t("tracking") : t("disabled")}</span>
      </button>

      <div className="flex items-center gap-1 text-[10px] text-muted">
        <div
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            connected ? "bg-ok" : "bg-danger",
          )}
        />
        <span className="hidden md:inline">{connected ? tc("connected") : tc("disconnected")}</span>
      </div>

      {/* 视图切换 */}
      <div className="flex items-center rounded-md border border-border overflow-hidden">
        {(["flow", "timeline"] as const).map((v) => (
          <button
            key={v}
            onClick={() => onViewChange(v)}
            className={cn(
              "flex items-center gap-1 px-2 py-1 text-[10px] font-medium transition-all",
              view === v
                ? "bg-accent-subtle text-accent"
                : "text-muted hover:text-foreground",
            )}
            title={t(`views.${v}`)}
            aria-label={t(`views.${v}`)}
          >
            {v === "flow" ? <Workflow size={11} /> : <ListTree size={11} />}
            <span className="hidden sm:inline">{t(`views.${v}`)}</span>
          </button>
        ))}
      </div>

      <div className="flex-1" />

      <button
        onClick={onToggleTools}
        className={cn(
          "flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
          showTools
            ? "bg-accent-subtle text-accent"
            : "text-muted hover:text-foreground",
        )}
        title={t("toolsPanel")}
        aria-label={t("toolsPanel")}
      >
        {isMobile
          ? <Wrench size={11} />
          : showTools ? <PanelLeftClose size={11} /> : <PanelLeftOpen size={11} />}
        <span className="hidden md:inline">{t("toolsPanel")}</span>
      </button>

      <button
        onClick={onToggleProviders}
        className={cn(
          "flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
          showProviders
            ? "bg-accent-subtle text-accent"
            : "text-muted hover:text-foreground",
        )}
        title={t("contextProviders.title")}
        aria-label={t("contextProviders.title")}
      >
        <Database size={11} />
        <span className="hidden md:inline">{t("contextProviders.title")}</span>
      </button>

      <button
        onClick={onToggleAutoFollow}
        className={cn(
          "flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
          autoFollow
            ? "bg-accent-subtle text-accent"
            : "text-muted hover:text-foreground",
        )}
        title={t("autoFollow")}
        aria-label={t("autoFollow")}
      >
        <Crosshair size={11} />
        <span className="hidden sm:inline">{t("autoFollow")}</span>
      </button>

      {nodeCount !== undefined && (
        <div className="text-[10px] text-muted font-mono">
          {t("nNodes", { count: nodeCount })}
        </div>
      )}
    </div>
  );
}
