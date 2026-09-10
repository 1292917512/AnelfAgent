/**
 * AI 桌面实体面板 — 上下文注入组件的完整管理页。
 *
 * 统计卡（组件/启用/注入/异常）+ 整体注入预览 + 组件卡片
 * （实时状态详情、实际注入文本、配置编辑、启停与手动刷新）。
 * 数据驱动自 /api/entity/ai_desktop/modules，新增后端组件自动出现卡片。
 *
 * i18n：locales/{zh,en}.json 由 lib/entity-plugin-locales.ts 启动时 eager
 * 注册（命名空间 ai_desktop），无需在本文件自行 registerPluginI18n；
 * _registry.groups/configSections 声明工具页与配置中心的分组名。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Eye, Puzzle, RefreshCw, ToggleRight, Zap } from "lucide-react";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { Button, LoadingBlock } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { aiDesktopApi } from "./ai_desktop/api";
import { ModuleCard } from "./ai_desktop/ModuleCard";
import type { DesktopModuleInfo } from "./ai_desktop/types";

export default function AiDesktopPanel() {
  const { t } = useTranslation("ai_desktop");
  const queryClient = useQueryClient();
  const [refreshingKey, setRefreshingKey] = useState<string | null>(null);

  const modulesQuery = useQuery({
    queryKey: ["ai-desktop-modules"],
    queryFn: () => aiDesktopApi.modules().then((r) => r.data),
    refetchInterval: 30000,
  });
  const modulesData = modulesQuery.data;

  const { data: preview, refetch: refetchPreview } = useQuery({
    queryKey: ["ai-desktop-preview"],
    queryFn: () => aiDesktopApi.preview().then((r) => r.data),
    refetchInterval: 15000,
  });

  const reloadModules = () =>
    queryClient.invalidateQueries({ queryKey: ["ai-desktop-modules"] });

  const saveConfig = async (key: string, value: unknown) => {
    await aiDesktopApi.updateConfig(key, value);
    await reloadModules();
    await refetchPreview();
  };

  const toggleModule = (mod: DesktopModuleInfo, enabled: boolean) => {
    saveConfig(`ai_desktop_${mod.key}_enabled`, enabled)
      .then(() => toast.success(t("messages.toggleSuccess", { name: mod.display_name })))
      .catch(() => toast.error(t("messages.toggleFailed")));
  };

  const refreshModule = async (mod: DesktopModuleInfo) => {
    setRefreshingKey(mod.key);
    try {
      const resp = await aiDesktopApi.refresh(mod.key);
      if (!resp.data.success) {
        toast.error(resp.data.last_error || t("messages.refreshFailed"));
      } else {
        toast.success(t("messages.refreshSuccess", { name: mod.display_name }));
      }
      await reloadModules();
      await refetchPreview();
    } catch {
      toast.error(t("messages.refreshFailed"));
    } finally {
      setRefreshingKey(null);
    }
  };

  if (modulesQuery.isError) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <AlertTriangle className="h-6 w-6 text-danger" />
          <p className="text-sm text-muted">{t("loadFailed")}</p>
          <Button size="sm" onClick={() => void modulesQuery.refetch()}>
            {t("retry")}
          </Button>
        </div>
      </Card>
    );
  }

  if (modulesQuery.isLoading || !modulesData) {
    return <LoadingBlock />;
  }

  const modules = modulesData.modules;
  const enabledCount = modules.filter((m) => m.enabled).length;
  const injectingCount = modules.filter((m) => m.enabled && m.current_content).length;
  const errorCount = modules.filter((m) => m.last_error).length;

  return (
    <div className="space-y-4">
      {/* 统计概览 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label={t("stats.total")} value={modulesData.count} />
        <StatCard
          label={t("stats.enabled")}
          value={enabledCount}
          variant={enabledCount > 0 ? "ok" : "default"}
        />
        <StatCard
          label={t("stats.injecting")}
          value={injectingCount}
          variant={injectingCount > 0 ? "ok" : "default"}
        />
        <StatCard
          label={t("stats.errors")}
          value={errorCount}
          variant={errorCount > 0 ? "danger" : "default"}
        />
      </div>

      {/* 整体注入预览 */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold tracking-tight text-heading">
            <Eye className="h-4 w-4 text-accent" />
            {t("preview.title")}
          </h3>
          <Button variant="ghost" size="icon" onClick={() => void refetchPreview()} title={t("preview.refresh")}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
        {preview?.injecting ? (
          <pre className="whitespace-pre-wrap break-all rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-[13px] leading-relaxed text-foreground">
            {preview.content}
          </pre>
        ) : (
          <div className="text-[13px] text-muted">{t("preview.empty")}</div>
        )}
        <p className="mt-2 text-[10px] text-muted">{t("preview.hint")}</p>
      </Card>

      {/* 组件卡片 */}
      <div className="flex items-center gap-2 text-sm text-muted">
        <Puzzle className="h-4 w-4" />
        {t("modules.hint", { count: modulesData.count })}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {modules.map((mod) => (
          <ModuleCard
            key={mod.key}
            mod={mod}
            refreshing={refreshingKey === mod.key}
            onToggle={toggleModule}
            onSaveConfig={saveConfig}
            onRefresh={(m) => void refreshModule(m)}
          />
        ))}
      </div>

      {modules.length === 0 && (
        <Card>
          <div className="flex flex-col items-center gap-2 py-8 text-center text-muted">
            <Zap className="h-6 w-6" />
            <p className="text-sm">{t("modules.empty")}</p>
          </div>
        </Card>
      )}

      <p className="text-[10px] text-muted flex items-center gap-1.5">
        <ToggleRight className="h-3 w-3" />
        {t("modules.footerHint")}
      </p>
    </div>
  );
}
