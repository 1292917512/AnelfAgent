import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Download, Link, Trash2 } from "lucide-react";
import { apiErrorMessage, nonebotApi } from "@/lib/api";
import type { NoneBotAdapterInfo } from "@/lib/types";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge, Button, ConfirmDialog, EmptyState, Input, LoadingBlock, Switch, Textarea, toast } from "@/components/ui";

function difficultyVariant(difficulty: string): "ok" | "warn" | "danger" {
  if (difficulty === "easy") return "ok";
  if (difficulty === "medium") return "warn";
  return "danger";
}

/** 适配器管理：安装/卸载、启用开关、平台接入配置（setup 元数据驱动表单） */
export function AdaptersPanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();

  const [expanded, setExpanded] = useState<string | null>(null);
  const [uninstalling, setUninstalling] = useState<NoneBotAdapterInfo | null>(null);
  const [envDraft, setEnvDraft] = useState<Record<string, string>>({});

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["nonebotAdapters"] });
    queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
  };

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["nonebotAdapters"],
    queryFn: () => nonebotApi.adapters().then((r) => r.data),
    refetchInterval: 15000,
  });

  const { data: config } = useQuery({
    queryKey: ["nonebotConfig"],
    queryFn: () => nonebotApi.config().then((r) => r.data),
  });

  const { data: status } = useQuery({
    queryKey: ["nonebotStatus"],
    queryFn: () => nonebotApi.status().then((r) => r.data),
    refetchInterval: 5000,
  });

  const installMutation = useMutation({
    mutationFn: (key: string) => nonebotApi.installAdapter(key, true),
    onSuccess: (r, key) => {
      if (r.data?.success) toast.success(t("toast.adapterInstalled", { key }));
      else toast.error(r.data?.error || t("toast.installFailed"));
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.installFailed"))),
  });

  const uninstallMutation = useMutation({
    mutationFn: (key: string) => nonebotApi.uninstallAdapter(key),
    onSuccess: (r, key) => {
      if (r.data?.success) toast.success(t("toast.adapterUninstalled", { key }));
      else toast.error(r.data?.error || t("toast.uninstallFailed"));
      setUninstalling(null);
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.uninstallFailed"))),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) => {
      // 启用/禁用走 adapters 配置列表（save_config 内部热重启）
      const adapters = (config?.adapters || []).filter((a) => a !== key);
      if (enabled) adapters.push(key);
      return nonebotApi.saveConfig({ adapters });
    },
    onSuccess: () => {
      toast.success(t("toast.configSaved"));
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.saveFailed"))),
  });

  const saveEnvMutation = useMutation({
    mutationFn: (patch: Record<string, string>) => {
      const merged = { ...(config?.nonebot_env || {}), ...patch };
      Object.entries(patch).forEach(([k, v]) => {
        if (!v.trim()) delete merged[k];
      });
      return nonebotApi.saveConfig({ nonebot_env: merged });
    },
    onSuccess: () => {
      toast.success(t("toast.envSaved"));
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.saveFailed"))),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;
  if (isError) {
    return (
      <EmptyState
        icon={Link}
        title={t("loadFailed")}
        action={<Button variant="secondary" size="sm" onClick={() => refetch()}>{t("retry")}</Button>}
      />
    );
  }

  const adapters = data?.adapters || [];
  const workerBase = status?.channel_status?.worker_base_url;

  return (
    <div className="space-y-3">
      {workerBase && (
        <div className="rounded-lg border border-accent/30 bg-accent-subtle px-4 py-3 text-xs text-accent">
          {t("adapters.reverseWsHint", { base: workerBase })}
        </div>
      )}

      {adapters.length === 0 ? (
        <EmptyState icon={Link} title={t("adapters.empty")} description={t("adapters.emptyHint")} />
      ) : (
        <div className="grid gap-3">
          {adapters.map((adapter) => {
            const isOpen = expanded === adapter.key;
            return (
              <div key={adapter.key} className="rounded-lg border border-border bg-panel">
                <button
                  className="flex w-full items-center gap-2 px-4 py-3 text-left"
                  onClick={() => {
                    setExpanded(isOpen ? null : adapter.key);
                    if (!isOpen) {
                      const draft: Record<string, string> = {};
                      adapter.setup.env_keys.forEach((k) => {
                        draft[k.key] = (config?.nonebot_env || {})[k.key] || "";
                      });
                      setEnvDraft(draft);
                    }
                  }}
                >
                  {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <StatusDot status={adapter.enabled ? (adapter.installed ? "ok" : "warn") : "offline"} />
                  <span className="text-sm font-medium">{adapter.label}</span>
                  <span className="text-xs text-muted font-mono">{adapter.key}</span>
                  <div className="ml-auto flex items-center gap-1.5">
                    {adapter.version && <Badge variant="neutral">v{adapter.version}</Badge>}
                    {!adapter.builtin && <Badge variant="info">{t("adapters.community")}</Badge>}
                    <Badge variant={difficultyVariant(adapter.setup.difficulty)}>
                      {t(`adapters.difficulty.${adapter.setup.difficulty}`)}
                    </Badge>
                    {adapter.installed ? (
                      <Badge variant="ok">{t("adapters.installed")}</Badge>
                    ) : (
                      <Badge variant="warn">{t("adapters.notInstalled")}</Badge>
                    )}
                  </div>
                </button>

                {isOpen && (
                  <div className="space-y-3 border-t border-border px-4 py-3">
                    <p className="text-xs text-muted">
                      {adapter.setup.notes}
                      {adapter.setup.docs && (
                        <a
                          className="ml-1 text-accent hover:underline"
                          href={adapter.setup.docs}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {t("adapters.docs")}
                        </a>
                      )}
                    </p>

                    <div className="flex flex-wrap items-center gap-2">
                      {adapter.installed ? (
                        <>
                          <div className="flex items-center gap-2">
                            <Switch
                              checked={adapter.enabled}
                              onChange={(enabled: boolean) =>
                                toggleMutation.mutate({ key: adapter.key, enabled })
                              }
                            />
                            <span className="text-xs text-muted">{t("adapters.enableToggle")}</span>
                          </div>
                          <Button
                            variant="danger"
                            size="sm"
                            disabled={uninstallMutation.isPending}
                            onClick={() => setUninstalling(adapter)}
                          >
                            <Trash2 size={14} />
                            {t("adapters.uninstall")}
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="primary"
                          size="sm"
                          disabled={installMutation.isPending}
                          onClick={() => installMutation.mutate(adapter.key)}
                        >
                          <Download size={14} className={installMutation.isPending ? "animate-pulse" : ""} />
                          {installMutation.isPending ? t("adapters.installing") : t("adapters.install")}
                        </Button>
                      )}
                      <span className="text-xs text-muted font-mono">{adapter.package}</span>
                    </div>

                    {/* 平台接入配置表单 */}
                    {adapter.setup.env_keys.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-medium text-muted">{t("adapters.setupTitle")}</h4>
                        {adapter.setup.env_keys.map((envKey) => (
                          <div key={envKey.key}>
                            <label className="mb-1 block text-xs text-muted">
                              <code className="text-foreground">{envKey.key}</code>
                              <span className="ml-1">{envKey.label}</span>
                            </label>
                            {envKey.json_mode ? (
                              <Textarea
                                rows={3}
                                className="font-mono text-xs"
                                placeholder={envKey.placeholder}
                                value={envDraft[envKey.key] || ""}
                                onChange={(e) =>
                                  setEnvDraft((d) => ({ ...d, [envKey.key]: e.target.value }))
                                }
                              />
                            ) : (
                              <Input
                                type={envKey.secret ? "password" : "text"}
                                className="font-mono text-xs"
                                placeholder={envKey.placeholder}
                                value={envDraft[envKey.key] || ""}
                                onChange={(e) =>
                                  setEnvDraft((d) => ({ ...d, [envKey.key]: e.target.value }))
                                }
                              />
                            )}
                          </div>
                        ))}
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={saveEnvMutation.isPending}
                          onClick={() => saveEnvMutation.mutate({ ...envDraft })}
                        >
                          {t("adapters.saveEnv")}
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={!!uninstalling}
        onClose={() => setUninstalling(null)}
        onConfirm={() => uninstalling && uninstallMutation.mutate(uninstalling.key)}
        title={t("adapters.uninstallTitle")}
        message={uninstalling ? t("adapters.uninstallMessage", { name: uninstalling.label }) : ""}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={uninstallMutation.isPending}
      />
    </div>
  );
}
