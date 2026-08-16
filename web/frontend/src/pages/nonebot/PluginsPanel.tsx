import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Puzzle, Trash2 } from "lucide-react";
import { apiErrorMessage, nonebotApi } from "@/lib/api";
import type { NoneBotPluginInfo } from "@/lib/types";
import { Badge, Button, ConfirmDialog, EmptyState, LoadingBlock, toast } from "@/components/ui";

/** 已加载插件列表（worker 实时上报）+ 卸载 */
export function PluginsPanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();
  const [uninstalling, setUninstalling] = useState<NoneBotPluginInfo | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["nonebotPlugins"],
    queryFn: () => nonebotApi.plugins().then((r) => r.data),
    refetchInterval: 8000,
  });

  const uninstallMutation = useMutation({
    mutationFn: (moduleName: string) => nonebotApi.uninstallPlugin(moduleName),
    onSuccess: (r, module) => {
      if (r.data?.success) toast.success(t("toast.pluginUninstalled", { module }));
      else toast.error(r.data?.error || t("toast.uninstallFailed"));
      setUninstalling(null);
      queryClient.invalidateQueries({ queryKey: ["nonebotPlugins"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
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

  const plugins = data.plugins || [];

  return (
    <div className="space-y-3">
      {plugins.length === 0 ? (
        <EmptyState
          icon={Puzzle}
          title={t("plugins.empty")}
          description={t("plugins.emptyHint")}
        />
      ) : (
        <div className="grid gap-3">
          {plugins.map((plugin) => (
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
                  </div>
                  <div className="mt-0.5 font-mono text-xs text-muted">{plugin.module}</div>
                  {plugin.description && (
                    <p className="mt-1 text-xs text-muted">{plugin.description}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
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
                    onClick={() => setUninstalling(plugin)}
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
        </div>
      )}

      <ConfirmDialog
        open={!!uninstalling}
        onClose={() => setUninstalling(null)}
        onConfirm={() => uninstalling && uninstallMutation.mutate(uninstalling.module)}
        title={t("plugins.uninstallTitle")}
        message={uninstalling ? t("plugins.uninstallMessage", { name: uninstalling.module }) : ""}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={uninstallMutation.isPending}
      />
    </div>
  );
}
