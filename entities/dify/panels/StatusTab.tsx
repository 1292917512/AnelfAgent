import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ExternalLink, Link2, RefreshCw } from "lucide-react";
import { difyApi } from "./api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Button, Input, Modal, toast } from "@/components/ui";
import { apiErrorMessage } from "@/lib/api";

export function StatusTab() {
  const { t } = useTranslation("dify");
  const queryClient = useQueryClient();
  const [baseUrl, setBaseUrl] = useState("");
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");

  const { data: status, refetch } = useQuery({
    queryKey: ["dify-status"],
    queryFn: () => difyApi.status().then((r) => r.data),
    refetchInterval: 15000,
  });

  const { data: config } = useQuery({
    queryKey: ["dify-config"],
    queryFn: () => difyApi.config().then((r) => r.data),
  });

  useEffect(() => {
    if (config && baseUrl === "") {
      setBaseUrl(config.settings.base_url);
    }
    // 仅在首次载入配置时回填，避免覆盖用户正在编辑的内容
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["dify-status"] });
    queryClient.invalidateQueries({ queryKey: ["dify-config"] });
  };

  const saveUrlMutation = useMutation({
    mutationFn: () => difyApi.saveBaseUrl(baseUrl.trim()),
    onSuccess: () => {
      toast.success(t("messages.urlSaved"));
      invalidate();
    },
    onError: (err: unknown) => toast.error(apiErrorMessage(err, t("messages.actionFailed"))),
  });

  const connectMutation = useMutation({
    mutationFn: () => difyApi.connect(),
    onSuccess: (resp) => {
      toast.success(resp.data?.message || t("messages.actionDone"));
      invalidate();
    },
    onError: (err: unknown) => toast.error(apiErrorMessage(err, t("messages.connectFailed"))),
    onSettled: invalidate,
  });

  const adminMutation = useMutation({
    mutationFn: () => difyApi.saveAdmin(adminEmail.trim(), adminPassword),
    onSuccess: () => {
      toast.success(t("messages.adminSaved"));
      setAdminOpen(false);
      setAdminPassword("");
      invalidate();
    },
    onError: (err: unknown) => toast.error(apiErrorMessage(err, t("messages.adminFailed"))),
  });

  const openAdminModal = () => {
    setAdminEmail(config?.admin?.email || status?.admin_email || "");
    setAdminPassword("********");
    setAdminOpen(true);
  };

  const connected = !!(status?.reachable && status?.admin_configured);
  const savedUrl = config?.settings.base_url ?? "";
  const urlDirty = baseUrl.trim() !== savedUrl;

  return (
    <div className="space-y-4 max-w-3xl">
      <Card
        title={t("connect.card")}
        subtitle={t("connect.desc")}
        actions={
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            <RefreshCw size={14} /> {t("actions.refresh")}
          </button>
        }
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-64">
            <label className="block text-xs text-muted mb-1">{t("connect.baseUrl")}</label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://127.0.0.1:8899"
            />
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => saveUrlMutation.mutate()}
            loading={saveUrlMutation.isPending}
            disabled={!urlDirty}
          >
            {t("actions.save")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => connectMutation.mutate()}
            loading={connectMutation.isPending}
            disabled={!savedUrl || urlDirty}
          >
            <Link2 size={14} /> {t("connect.connect")}
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted">{t("connect.hint")}</p>

        <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="rounded-md border border-border bg-elevated p-3">
            <div className="text-xs text-muted">{t("connect.reachability")}</div>
            <div className="mt-1 flex items-center gap-1.5 text-sm">
              <StatusDot status={status?.reachable ? "ok" : "offline"} />
              {status?.reachable ? t("connect.reachable") : t("connect.unreachable")}
            </div>
          </div>
          <div className="rounded-md border border-border bg-elevated p-3">
            <div className="text-xs text-muted">{t("connect.version")}</div>
            <div className="mt-1 text-sm text-heading">
              {status?.version ? `v${status.version}` : "-"}
            </div>
          </div>
          <div className="rounded-md border border-border bg-elevated p-3">
            <div className="text-xs text-muted">{t("connect.setup")}</div>
            <div className="mt-1 flex items-center gap-1.5 text-sm">
              <StatusDot status={status?.setup_step === "finished" ? "ok" : "warn"} />
              {status?.setup_step === "finished"
                ? t("connect.setupDone")
                : status?.setup_step === "not_started"
                  ? t("connect.setupPending")
                  : "-"}
            </div>
          </div>
          <div className="rounded-md border border-border bg-elevated p-3">
            <div className="text-xs text-muted">{t("connect.appsTracked")}</div>
            <div className="mt-1 text-sm text-heading">{status?.apps_tracked ?? 0}</div>
          </div>
        </div>

        {connected && status?.base_url && (
          <a
            href={status.base_url}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-accent hover:bg-hover transition-all"
          >
            <ExternalLink size={14} /> {t("connect.openConsole")}
          </a>
        )}
      </Card>

      <Card title={t("connect.admin")} subtitle={t("connect.adminDesc")}>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-foreground">
            {config?.admin?.email || status?.admin_email || "-"}
          </span>
          <span className="text-xs text-muted">
            {status?.admin_configured ? t("connect.adminReady") : t("connect.adminMissing")}
          </span>
          <Button variant="secondary" size="sm" onClick={openAdminModal}>
            {t("connect.editAdmin")}
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted">{t("connect.adminHint")}</p>
      </Card>

      <Modal
        open={adminOpen}
        onClose={() => setAdminOpen(false)}
        title={t("admin.modalTitle")}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setAdminOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => adminMutation.mutate()}
              loading={adminMutation.isPending}
              disabled={!adminEmail.trim() || !adminPassword}
            >
              {t("actions.save")}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-muted">{t("admin.modalDesc")}</p>
          <div>
            <label className="block text-xs text-muted mb-1">{t("admin.email")}</label>
            <Input value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">{t("admin.password")}</label>
            <Input
              type="text"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              placeholder={t("admin.passwordPlaceholder")}
            />
            <p className="mt-1 text-[11px] text-muted">{t("admin.passwordKeepHint")}</p>
          </div>
        </div>
      </Modal>
    </div>
  );
}
