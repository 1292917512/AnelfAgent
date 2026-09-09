import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link2, Link2Off, RefreshCw } from "lucide-react";
import { difyApi } from "./api";
import type { DifyApp } from "./types";
import { Card } from "@/components/common/Card";
import { ConfirmDialog, toast } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api";

export function McpTab() {
  const { t } = useTranslation("dify");
  const queryClient = useQueryClient();
  const [disableTarget, setDisableTarget] = useState<DifyApp | null>(null);
  const [urlByApp, setUrlByApp] = useState<Record<string, string>>({});

  const { data, refetch } = useQuery({
    queryKey: ["dify-apps"],
    queryFn: () => difyApi.listApps().then((r) => r.data),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["dify-apps"] });

  const enableMutation = useMutation({
    mutationFn: (app: DifyApp) => difyApi.mcpEnable(app.id),
    onSuccess: (resp, app) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      const url = (resp.data as Record<string, unknown> | undefined)?.mcp_url;
      if (typeof url === "string" && url) {
        setUrlByApp((prev) => ({ ...prev, [app.id]: url }));
      }
      invalidate();
    },
    onError: (err: unknown) => toast.error(apiErrorMessage(err, t("messages.actionFailed"))),
  });

  const disableMutation = useMutation({
    mutationFn: (app: DifyApp) => difyApi.mcpDisable(app.id),
    onSuccess: () => {
      toast.success(t("messages.actionDone"));
      setDisableTarget(null);
      invalidate();
    },
    onError: (err: unknown) => toast.error(apiErrorMessage(err, t("messages.actionFailed"))),
  });

  const apps = data?.apps ?? [];

  return (
    <div className="space-y-4 max-w-3xl">
      <Card
        title={t("mcp.title")}
        subtitle={t("mcp.desc")}
        actions={
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            <RefreshCw size={14} /> {t("actions.refresh")}
          </button>
        }
      >
        {apps.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted">{t("apps.empty")}</div>
        ) : (
          <div className="space-y-2">
            {apps.map((app) => (
              <div
                key={app.id}
                className="p-3 rounded-lg border border-border bg-elevated hover:border-border-strong transition-all"
              >
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-heading truncate">{app.name}</div>
                    <div className="text-xs text-muted truncate mt-0.5">
                      {app.mode} · {app.id}
                    </div>
                  </div>
                  {app.mcp_server_code ? (
                    <button
                      onClick={() => setDisableTarget(app)}
                      title={t("mcp.disable")}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border text-danger hover:bg-hover transition-all"
                    >
                      <Link2Off size={14} /> {t("mcp.disable")}
                    </button>
                  ) : (
                    <button
                      onClick={() => enableMutation.mutate(app)}
                      title={t("mcp.enable")}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all"
                    >
                      <Link2 size={14} /> {t("mcp.enable")}
                    </button>
                  )}
                </div>
                {app.mcp_server_code && (
                  <div className="mt-2 text-xs font-mono text-accent break-all">
                    {urlByApp[app.id] || t("mcp.bridgedHint")}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={disableTarget !== null}
        onClose={() => setDisableTarget(null)}
        onConfirm={() => disableTarget && disableMutation.mutate(disableTarget)}
        title={t("confirm.mcpDisableTitle")}
        message={t("confirm.mcpDisableMsg", { name: disableTarget?.name ?? "" })}
        danger
        loading={disableMutation.isPending}
      />
    </div>
  );
}
