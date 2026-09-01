import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, GitBranch, Search, Store } from "lucide-react";
import { apiErrorMessage } from "@/lib/api";
import { nonebotApi } from "../api";
import type { NoneBotStorePlugin } from "../types";
import { Badge, Button, ConfirmDialog, EmptyState, Input, LoadingBlock, Switch, toast } from "@/components/ui";
import { InstallProgressBanner } from "./InstallProgressBanner";

/** 插件商店浏览（registry.nonebot.dev 代理）：搜索 / 徽标 / 一键安装 / Git·本地路径高级安装 */
export function StorePanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();

  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [advOpen, setAdvOpen] = useState(false);
  const [advModule, setAdvModule] = useState("");
  const [advSource, setAdvSource] = useState("");
  const [advEditable, setAdvEditable] = useState(false);
  const [limit, setLimit] = useState(60);
  const [confirmInstall, setConfirmInstall] = useState<NoneBotStorePlugin | null>(null);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["nonebotStore", submitted, limit],
    queryFn: () => nonebotApi.storePlugins(submitted, limit).then((r) => r.data),
  });

  const { data: config } = useQuery({
    queryKey: ["nonebotConfig"],
    queryFn: () => nonebotApi.config().then((r) => r.data),
  });

  const installMutation = useMutation({
    mutationFn: (moduleName: string) => nonebotApi.installPlugin(moduleName),
    onMutate: () => setConfirmInstall(null),
    onSuccess: (r, module) => {
      if (r.data?.success) toast.success(t("toast.pluginInstalled", { module }));
      else toast.error(r.data?.error || t("toast.installFailed"));
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotPlugins"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.installFailed"))),
  });

  const advInstallMutation = useMutation({
    mutationFn: () =>
      nonebotApi.installPlugin(advModule.trim(), advSource.trim(), advEditable),
    onSuccess: (r) => {
      if (r.data?.success) {
        toast.success(t("toast.pluginInstalled", { module: advModule.trim() }));
        setAdvModule("");
        setAdvSource("");
        setAdvEditable(false);
      } else {
        toast.error(r.data?.error || t("toast.installFailed"));
      }
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotPlugins"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.installFailed"))),
  });

  const installedModules = new Set(config?.plugins || []);

  const shortAdapter = (module: string) =>
    module.replace("nonebot.adapters.", "").replace("~", "");

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;
  if (isError) {
    return (
      <EmptyState
        icon={Store}
        title={t("store.loadFailed")}
        action={<Button variant="secondary" size="sm" onClick={() => refetch()}>{t("retry")}</Button>}
      />
    );
  }

  const plugins = data?.plugins || [];

  return (
    <div className="space-y-3">
      <InstallProgressBanner />

      {/* 搜索栏 */}
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(query.trim());
        }}
      >
        <Input
          value={query}
          placeholder={t("store.searchPlaceholder")}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button variant="primary" size="sm" type="submit" disabled={isFetching}>
          <Search size={14} />
          {t("store.search")}
        </Button>
        {submitted && (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            onClick={() => {
              setQuery("");
              setSubmitted("");
            }}
          >
            {t("store.clear")}
          </Button>
        )}
      </form>

      <p className="text-xs text-muted">
        {t("store.sourceHint", { count: data?.count ?? 0 })}
      </p>

      {/* 高级安装：git 源 / 本地路径 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <button
          className="flex w-full items-center gap-2 text-left text-sm font-medium"
          onClick={() => setAdvOpen((v) => !v)}
        >
          <GitBranch size={15} className="text-accent" />
          {t("store.advancedInstall")}
          <span className="text-xs font-normal text-muted">{t("store.advancedInstallHint")}</span>
        </button>
        {advOpen && (
          <form
            className="mt-3 space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (advModule.trim() && advSource.trim()) advInstallMutation.mutate();
            }}
          >
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                className="sm:w-64"
                placeholder={t("store.advModulePlaceholder")}
                value={advModule}
                onChange={(e) => setAdvModule(e.target.value)}
              />
              <Input
                className="min-w-0 flex-1 font-mono text-xs"
                placeholder={t("store.advSourcePlaceholder")}
                value={advSource}
                onChange={(e) => setAdvSource(e.target.value)}
              />
            </div>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Switch checked={advEditable} onChange={setAdvEditable} />
                <span className="text-xs text-muted">{t("store.editable")}</span>
              </div>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                disabled={
                  advInstallMutation.isPending || !advModule.trim() || !advSource.trim()
                }
              >
                <Download size={14} />
                {advInstallMutation.isPending ? t("store.installing") : t("store.install")}
              </Button>
            </div>
          </form>
        )}
      </div>

      {plugins.length === 0 ? (
        <EmptyState icon={Store} title={t("store.noResults")} description={t("store.noResultsHint")} />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {plugins.map((plugin: NoneBotStorePlugin) => {
            const installed = installedModules.has(plugin.module_name);
            return (
              <div key={plugin.module_name} className="flex flex-col rounded-lg border border-border bg-panel p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm font-medium truncate">{plugin.name}</span>
                      {plugin.is_official && <Badge variant="ok">{t("store.official")}</Badge>}
                      {plugin.valid ? (
                        <Badge variant="accent">{t("store.validated")}</Badge>
                      ) : (
                        <Badge variant="warn">{t("store.unvalidated")}</Badge>
                      )}
                      {plugin.version && <Badge variant="neutral">v{plugin.version}</Badge>}
                    </div>
                    <div className="mt-0.5 font-mono text-xs text-muted">{plugin.module_name}</div>
                  </div>
                  {plugin.homepage && (
                    <a href={plugin.homepage} target="_blank" rel="noreferrer" className="shrink-0">
                      <Button variant="ghost" size="sm">
                        <ExternalLink size={14} />
                      </Button>
                    </a>
                  )}
                </div>

                {plugin.desc && <p className="mt-1.5 line-clamp-2 text-xs text-muted">{plugin.desc}</p>}

                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {(plugin.tags || []).slice(0, 4).map((tag) => (
                    <Badge key={tag.label} variant="neutral">{tag.label}</Badge>
                  ))}
                  {plugin.supported_adapters && (
                    <span className="text-[10px] text-muted" title={plugin.supported_adapters.join(", ")}>
                      {t("store.supports")}: {plugin.supported_adapters.slice(0, 3).map(shortAdapter).join(" / ")}
                      {plugin.supported_adapters.length > 3 ? " …" : ""}
                    </span>
                  )}
                </div>

                <div className="mt-3 flex items-center justify-between gap-2">
                  <span className="text-xs text-muted">
                    {t("store.author")}: {plugin.author}
                  </span>
                  {installed ? (
                    <Badge variant="ok">{t("store.installed")}</Badge>
                  ) : (
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={installMutation.isPending}
                      onClick={() =>
                        plugin.valid
                          ? installMutation.mutate(plugin.module_name)
                          : setConfirmInstall(plugin)
                      }
                    >
                      <Download size={14} className={installMutation.isPending ? "animate-pulse" : ""} />
                      {installMutation.isPending ? t("store.installing") : t("store.install")}
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 加载更多 */}
      {plugins.length >= limit && !submitted && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={() => setLimit((n) => n + 60)}>
            {t("store.loadMore")}
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={!!confirmInstall}
        onClose={() => setConfirmInstall(null)}
        onConfirm={() =>
          confirmInstall && installMutation.mutate(confirmInstall.module_name)
        }
        title={t("store.unvalidatedTitle")}
        message={
          confirmInstall
            ? t("store.unvalidatedMessage", { name: confirmInstall.module_name })
            : ""
        }
        confirmText={t("store.install")}
        cancelText={t("common:cancel")}
        danger
        loading={installMutation.isPending}
      />
    </div>
  );
}
