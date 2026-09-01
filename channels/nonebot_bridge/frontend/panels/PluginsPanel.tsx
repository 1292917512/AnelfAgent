import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Puzzle, Trash2 } from "lucide-react";
import { apiErrorMessage } from "@/lib/api";
import { nonebotApi } from "../api";
import { Badge, Button, ConfirmDialog, EmptyState, LoadingBlock, Switch, toast } from "@/components/ui";

/** 已加载插件列表（worker 实时）+ 启用/停用（配置态）+ 卸载 */
export function PluginsPanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();
  const [uninstalling, setUninstalling] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["nonebotPlugins"],
    queryFn: () => nonebotApi.plugins().then((r) => r.data),
    refetchInterval: 8000,
  });

  const { data: config } = useQuery({
    queryKey: ["nonebotConfig"],
    queryFn: () => nonebotApi.config().then((r) => r.data),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["nonebotPlugins"] });
    queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
    queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
  };

  const enableMutation = useMutation({
    mutationFn: ({ module, enabled }: { module: string; enabled: boolean }) =>
      nonebotApi.enablePlugin(module, enabled),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(t("toast.done"));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  const uninstallMutation = useMutation({
    mutationFn: (moduleName: string) => nonebotApi.uninstallPlugin(moduleName),
    onSuccess: (r, module) => {
      if (r.data?.success) toast.success(t("toast.pluginUninstalled", { module }));
      else toast.error(r.data?.error || t("toast.uninstallFailed"));
      setUninstalling(null);
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.uninstallFailed"))),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  if (!data?.success) {
    return (
      <EmptyState
        icon={Puzzle}
        title={t("plugins.bridgeDisabled")}
        description={data?.error || t("plugins.bridgeDisabledHint")}
      />
    );
  }

  const loadedPlugins = data.plugins || [];
  const enabledModules = new Set(config?.plugins || []);
  // 已启用但 worker 未上报的插件（启用列表为准，含停用失败/未运行场景）
  const enabledOnly = [...enabledModules].filter(
    (m) => !loadedPlugins.some((p) => p.module === m),
  );

  return (
    <div className="space-y-3">
      {loadedPlugins.length === 0 && enabledOnly.length === 0 ? (
        <EmptyState
          icon={Puzzle}
          title={t("plugins.empty")}
          description={t("plugins.emptyHint")}
        />
      ) : (
        <div className="grid gap-3">
          {loadedPlugins.map((plugin) => (
            <div key={plugin.module} className="rounded-lg border border-border bg-panel p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">
                      {plugin.name || plugin.module}
                    </span>
                    {plugin.type && <Badge variant="info">{plugin.type}</Badge>}
                    {plugin.matcher_count > 0 && (
                      <Badge variant="neutral">
                        {t("plugins.matchers", { count: plugin.matcher_count })}
                      </Badge>
                    )}
                    <Badge variant="ok">{t("plugins.loaded")}</Badge>
                  </div>
                  <div className="mt-0.5 font-mono text-xs text-muted">{plugin.module}</div>
                  {plugin.description && (
                    <p className="mt-1 text-xs text-muted">{plugin.description}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <div className="flex items-center gap-1.5 px-1" title={t("plugins.enableToggle")}>
                    <Switch
                      checked={enabledModules.has(plugin.module)}
                      onChange={(enabled: boolean) =>
                        enableMutation.mutate({ module: plugin.module, enabled })
                      }
                    />
                  </div>
                  {plugin.homepage && (
                    <a href={plugin.homepage} target="_blank" rel="noreferrer">
                      <Button variant="ghost" size="sm">
                        <ExternalLink size={14} />
                      </Button>
                    </a>
                  )}
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={uninstallMutation.isPending}
                    onClick={() => setUninstalling(plugin.module)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
              {plugin.usage && (
                <pre className="mt-2 max-h-32 overflow-auto rounded-md border border-border bg-elevated p-2 text-xs whitespace-pre-wrap text-muted">
                  {plugin.usage}
                </pre>
              )}
            </div>
          ))}

          {enabledOnly.map((module) => (
            <div key={module} className="rounded-lg border border-dashed border-border bg-panel p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm">{module}</span>
                    <Badge variant="warn">{t("plugins.notLoaded")}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted">{t("plugins.notLoadedHint")}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Switch
                    checked
                    onChange={(enabled: boolean) => enableMutation.mutate({ module, enabled })}
                  />
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={uninstallMutation.isPending}
                    onClick={() => setUninstalling(module)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!uninstalling}
        onClose={() => setUninstalling(null)}
        onConfirm={() => uninstalling && uninstallMutation.mutate(uninstalling)}
        title={t("plugins.uninstallTitle")}
        message={uninstalling ? t("plugins.uninstallMessage", { name: uninstalling }) : ""}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={uninstallMutation.isPending}
      />
    </div>
  );
}
