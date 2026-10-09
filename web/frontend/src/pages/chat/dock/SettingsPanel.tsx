import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Moon, Sun } from "lucide-react";
import { heartbeatApi } from "@/lib/api";
import { useThinkingStore } from "@/stores/thinking-store";
import { useAppStore } from "@/stores/app-store";
import { useThinkingBootstrap } from "@/hooks/useThinking";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Switch } from "@/components/ui";

/** 快捷设置面板：聊天模型 / 思维链开关 / 主题 */
export function SettingsPanel() {
  const { t } = useTranslation("workbench");
  useThinkingBootstrap();

  const theme = useAppStore((s) => s.theme);
  const toggleTheme = useAppStore((s) => s.toggleTheme);

  const enabled = useThinkingStore((s) => s.enabled);
  const setTracking = useThinkingStore((s) => s.setTracking);
  const toggling = useThinkingStore((s) => s.toggling);

  const { data: heartbeatStatus } = useQuery({
    queryKey: ["heartbeatStatus"],
    queryFn: () => heartbeatApi.getStatus().then((r) => r.data),
    refetchInterval: 30000,
  });

  return (
    <div className="p-3 space-y-4">
      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-heading">{t("settings.chatModel")}</h4>
        <ModelSelect modelType="chat" compact />
      </section>

      <section className="flex items-center justify-between">
        <div>
          <div className="text-xs font-medium text-heading">{t("settings.thinkingTrace")}</div>
          <div className="text-[10px] text-muted">{t("settings.thinkingTraceHint")}</div>
        </div>
        <Switch checked={enabled} disabled={toggling} onChange={(next) => void setTracking(next)} aria-label={t("settings.thinkingTrace")} />
      </section>

      <section className="flex items-center justify-between">
        <div>
          <div className="text-xs font-medium text-heading">{t("settings.theme")}</div>
          <div className="text-[10px] text-muted">{theme === "dark" ? t("settings.dark") : t("settings.light")}</div>
        </div>
        <button
          onClick={toggleTheme}
          className="p-2 rounded-md border border-border bg-elevated text-muted hover:text-foreground transition-colors"
        >
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </section>

      {heartbeatStatus && (
        <section className="rounded-md border border-border bg-elevated px-3 py-2">
          <div className="text-xs font-medium text-heading">{t("settings.heartbeat")}</div>
          <div className="text-[10px] text-muted mt-0.5">
            {t("settings.heartbeatInfo", {
              count: heartbeatStatus.total_ticks ?? 0,
              tasks: heartbeatStatus.schedule_count ?? 0,
            })}
          </div>
        </section>
      )}
    </div>
  );
}
