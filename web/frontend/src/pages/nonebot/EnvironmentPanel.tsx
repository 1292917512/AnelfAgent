import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, FolderGit2, PackageCheck, RefreshCw, Trash2 } from "lucide-react";
import { apiErrorMessage, nonebotApi } from "@/lib/api";
import { StatusDot } from "@/components/common/StatusDot";
import {
  Badge, Button, ConfirmDialog, EmptyState, Input, LoadingBlock, toast,
} from "@/components/ui";

/** 运行环境管理：uv / venv 状态、初始化、升级（NoneBot 本体）、重建、包列表 */
export function EnvironmentPanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();
  const [rebuildOpen, setRebuildOpen] = useState(false);
  const [upgradeTarget, setUpgradeTarget] = useState("");
  const [pipIndex, setPipIndex] = useState("");
  const [pipProxy, setPipProxy] = useState("");
  const [sourcesOpen, setSourcesOpen] = useState(true);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["nonebotEnv"] });
    queryClient.invalidateQueries({ queryKey: ["nonebotPackages"] });
    queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
  };

  const { data: env, isLoading } = useQuery({
    queryKey: ["nonebotEnv"],
    queryFn: () => nonebotApi.envStatus().then((r) => r.data),
    refetchInterval: 8000,
  });

  const { data: packages } = useQuery({
    queryKey: ["nonebotPackages"],
    queryFn: () => nonebotApi.envPackages().then((r) => r.data),
    enabled: !!env?.venv_ready,
    refetchInterval: 30000,
  });

  const { data: config } = useQuery({
    queryKey: ["nonebotConfig"],
    queryFn: () => nonebotApi.config().then((r) => r.data),
  });

  const { data: sources } = useQuery({
    queryKey: ["nonebotSources"],
    queryFn: () => nonebotApi.envSources().then((r) => r.data),
    refetchInterval: 15000,
  });

  useEffect(() => {
    if (config) {
      setPipIndex(config.pip_index_url || "");
      setPipProxy(config.pip_proxy || "");
    }
  }, [config]);

  const saveSourceConfigMutation = useMutation({
    mutationFn: () =>
      nonebotApi.saveConfig({ pip_index_url: pipIndex.trim(), pip_proxy: pipProxy.trim() }),
    onSuccess: () => {
      toast.success(t("toast.configSaved"));
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.saveFailed"))),
  });

  const resyncMutation = useMutation({
    mutationFn: () => nonebotApi.envResync(),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(t("runtime.resyncDone", { count: Number(r.data.updated ?? 0) }));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["nonebotSources"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  const bootstrapMutation = useMutation({
    mutationFn: () => nonebotApi.envBootstrap(),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(r.data.message || t("runtime.bootstrapped"));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  const upgradeMutation = useMutation({
    mutationFn: (targets?: string[]) => nonebotApi.envUpgrade(targets),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(t("runtime.upgradeDone"));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });
  const rebuildMutation = useMutation({
    mutationFn: () => nonebotApi.envRebuild(),
    onSuccess: (r) => {
      if (r.data?.success) toast.success(r.data.message || t("runtime.rebuilt"));
      else toast.error(r.data?.error || t("toast.requestFailed"));
      setRebuildOpen(false);
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;
  if (!env) return <EmptyState icon={Boxes} title={t("loadFailed")} />;

  const installing = env.install?.running;
  const packageList = packages?.packages || [];

  return (
    <div className="space-y-3">
      {/* 安装源与代理 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <h3 className="text-sm font-medium">{t("runtime.sourceTitle")}</h3>
        <p className="mt-0.5 mb-2 text-xs text-muted">{t("runtime.sourceDesc")}</p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            className="min-w-0 flex-1 font-mono text-xs"
            placeholder={t("env.indexPlaceholder")}
            value={pipIndex}
            onChange={(e) => setPipIndex(e.target.value)}
          />
          <Input
            className="min-w-0 flex-1 font-mono text-xs"
            placeholder={t("env.proxyPlaceholder")}
            value={pipProxy}
            onChange={(e) => setPipProxy(e.target.value)}
          />
          <Button
            variant="secondary"
            size="sm"
            disabled={saveSourceConfigMutation.isPending}
            onClick={() => saveSourceConfigMutation.mutate()}
          >
            {t("common:save")}
          </Button>
        </div>
      </div>

      {/* 源码仓库（git 源本地检出） */}
      {(sources?.items?.length || 0) > 0 && (
        <div className="rounded-lg border border-border bg-panel p-4">
          <button
            className="flex w-full items-center gap-2 text-left text-sm font-medium"
            onClick={() => setSourcesOpen((v) => !v)}
          >
            <FolderGit2 size={15} className="text-accent" />
            {t("runtime.sourcesTitle")}
            <span className="text-xs font-normal text-muted">
              {t("runtime.sourcesHint", { dir: sources?.sources_dir || "" })}
            </span>
            <span className="ml-auto">
              <Button
                variant="secondary"
                size="sm"
                disabled={resyncMutation.isPending}
                onClick={(e) => {
                  e.stopPropagation();
                  resyncMutation.mutate();
                }}
              >
                <RefreshCw size={14} className={resyncMutation.isPending ? "animate-spin" : ""} />
                {t("runtime.resync")}
              </Button>
            </span>
          </button>
          {sourcesOpen && (
            <div className="mt-2 space-y-1.5">
              {sources!.items.map((item) => (
                <div
                  key={item.key}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-elevated px-3 py-2 text-xs"
                >
                  <Badge variant={item.kind === "git" ? "accent2" : "neutral"}>{item.kind}</Badge>
                  <span className="font-mono">{item.key}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-muted" title={item.spec}>
                    {item.spec}
                  </span>
                  <span className="truncate text-muted" title={item.repo_path}>
                    {item.repo_exists ? "📁" : "⚠️"} {item.repo_path}
                  </span>
                </div>
              ))}
              <p className="text-[11px] text-muted">{t("runtime.sourcesNote")}</p>
            </div>
          )}
        </div>
      )}

      {/* 环境状态卡 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <StatusDot status={env.venv_ready ? "ok" : "offline"} />
            <span className="text-sm font-medium">{t("runtime.title")}</span>
            {env.venv_ready ? (
              <Badge variant="ok">{t("runtime.ready")}</Badge>
            ) : (
              <Badge variant="warn">{t("runtime.notReady")}</Badge>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!env.venv_ready ? (
              <Button
                variant="primary"
                size="sm"
                disabled={bootstrapMutation.isPending || installing}
                onClick={() => bootstrapMutation.mutate()}
              >
                <Boxes size={14} />
                {t("runtime.bootstrap")}
              </Button>
            ) : (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={upgradeMutation.isPending || installing}
                  onClick={() => upgradeMutation.mutate(undefined)}
                  title={env.baseline.join(", ")}
                >
                  <RefreshCw size={14} className={upgradeMutation.isPending ? "animate-spin" : ""} />
                  {t("runtime.upgradeBaseline")}
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  disabled={rebuildMutation.isPending || installing}
                  onClick={() => setRebuildOpen(true)}
                >
                  <Trash2 size={14} />
                  {t("runtime.rebuild")}
                </Button>
              </>
            )}
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            { label: "uv", value: env.uv_version || (env.uv_found ? t("runtime.found") : t("runtime.uvMissing")) },
            { label: "Python", value: env.python_version || "—" },
            { label: t("runtime.pythonPkgs"), value: String(packages?.count ?? "—") },
            { label: t("runtime.venvPath"), value: env.venv_path, mono: true },
          ].map((item) => (
            <div key={item.label} className="rounded-md border border-border bg-elevated px-3 py-2">
              <div className="text-[11px] text-muted">{item.label}</div>
              <div className={`truncate text-xs ${item.mono ? "font-mono" : "font-medium"}`} title={item.value}>
                {item.value}
              </div>
            </div>
          ))}
        </div>

        {!env.uv_found && (
          <p className="mt-2 text-xs text-warn">{t("runtime.uvMissingHint")}</p>
        )}
      </div>

      {/* 安装进度 */}
      {installing && (
        <div className="rounded-lg border border-warn/30 bg-warn-subtle p-4">
          <div className="flex items-center gap-2 text-sm text-warn">
            <RefreshCw size={14} className="animate-spin" />
            {t("runtime.installing", { packages: (env.install?.packages || []).join(", ") })}
          </div>
          {(env.install?.logs || []).slice(-8).map((line, i) => (
            <div key={i} className="mt-1 truncate font-mono text-[11px] text-muted">{line}</div>
          ))}
        </div>
      )}

      {/* 包管理 */}
      {env.venv_ready && (
        <div className="rounded-lg border border-border bg-panel p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-medium">{t("runtime.packages")}</h3>
              <p className="mt-0.5 text-xs text-muted">{t("runtime.packagesHint")}</p>
            </div>
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const target = upgradeTarget.trim();
                if (target) upgradeMutation.mutate([target]);
                setUpgradeTarget("");
              }}
            >
              <Input
                className="w-52 font-mono text-xs"
                placeholder={t("runtime.upgradePlaceholder")}
                value={upgradeTarget}
                onChange={(e) => setUpgradeTarget(e.target.value)}
              />
              <Button variant="secondary" size="sm" type="submit" disabled={upgradeMutation.isPending || installing}>
                <PackageCheck size={14} />
                {t("runtime.upgradeOne")}
              </Button>
            </form>
          </div>

          {packageList.length === 0 ? (
            <p className="text-xs text-muted">{t("runtime.noPackages")}</p>
          ) : (
            <div className="max-h-[45vh] overflow-auto rounded-md border border-border">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-elevated text-muted">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">{t("runtime.pkgName")}</th>
                    <th className="px-3 py-2 text-left font-medium">{t("runtime.pkgVersion")}</th>
                    <th className="px-3 py-2 text-right font-medium">{t("runtime.pkgActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {packageList.map((pkg) => (
                    <tr key={pkg.name} className="border-t border-border">
                      <td className="px-3 py-1.5 font-mono">{pkg.name}</td>
                      <td className="px-3 py-1.5 font-mono text-muted">{pkg.version}</td>
                      <td className="px-3 py-1.5 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={upgradeMutation.isPending || installing}
                          onClick={() => upgradeMutation.mutate([pkg.name])}
                        >
                          <RefreshCw size={12} />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={rebuildOpen}
        onClose={() => setRebuildOpen(false)}
        onConfirm={() => rebuildMutation.mutate()}
        title={t("runtime.rebuildTitle")}
        message={t("runtime.rebuildMessage")}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={rebuildMutation.isPending}
      />
    </div>
  );
}
