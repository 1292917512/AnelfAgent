import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Copy, KeyRound, RefreshCw, Shield, Trash2 } from "lucide-react";
import { authApi, configApi, type ApiKeyCreated, type ApiKeyInfo, type WebToolsConfig } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Badge, Button, Input } from "@/components/ui";
import { type FieldMeta } from "@/pages/config/AppField";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import { LiteLLMCostMapCard } from "@/pages/config/LiteLLMCostMapCard";
import type { ConfigValues } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";

/** 系统配置面板：密码 / API Keys / 成本表 / Web 工具（网络与下载配置已收编进配置中心） */
export function SysConfigPanel() {
  const { t } = useTranslation("appconfig");

  const { data } = useQuery({
    queryKey: ["appConfig"],
    queryFn: () => configApi.getApp().then((r) => r.data),
  });

  const proxyUrl = typeof data?.["https_proxy"] === "string" ? (data["https_proxy"] as string) : "";

  const webToolsFields: FieldMeta[] = [
    { key: "proxy", label: t("fields.web_proxy"), type: "string", desc: t("descs.web_proxy") },
  ];

  return (
    <div className="space-y-4">
      <PasswordCard />
      <ApiKeysCard />
      <LiteLLMCostMapCard defaultProxy={proxyUrl} />
      <ConfigFormPanel
        title={t("sections.webTools")}
        subtitle={t("sections.webToolsSubtitle")}
        fields={webToolsFields}
        queryKey="webToolsConfig"
        fetchFn={() => configApi.getWebTools().then((r) => r.data as unknown as ConfigValues)}
        saveFn={(values) => configApi.saveWebTools(values as unknown as Partial<WebToolsConfig>)}
      />
      <Card title={t("sections.network")} subtitle={t("sections.networkMoved")}>
        <Link
          to="/config?key=proxy_enabled"
          className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
        >
          {t("sections.networkMovedAction")}
          <ArrowRight size={14} />
        </Link>
      </Card>
    </div>
  );
}

function PasswordCard() {
  const { t } = useTranslation("appconfig");
  const { data: authStatus } = useQuery({
    queryKey: ["authCheck"],
    queryFn: () => authApi.check().then((r) => r.data),
  });
  const [newPwd, setNewPwd] = useState("");
  const [saved, triggerSaved] = useCopyFeedback(2000);

  const mutation = useMutation({
    mutationFn: (pwd: string) => authApi.updatePassword(pwd),
    onSuccess: () => {
      triggerSaved();
      setNewPwd("");
    },
  });

  const hasPassword = authStatus?.required ?? false;

  return (
    <Card
      title={t("auth.title")}
      subtitle={t("auth.subtitle")}
      actions={
        <Badge variant={hasPassword ? "ok" : "neutral"}>
          {hasPassword ? t("auth.enabled") : t("auth.disabled")}
        </Badge>
      }
    >
      <div className="flex items-end gap-3 flex-wrap">
        <div className="flex-1 min-w-48 flex flex-col gap-1">
          <label className="text-xs text-muted font-medium">{t("auth.newPassword")}</label>
          <Input
            type="password"
            autoComplete="new-password"
            placeholder={t("auth.placeholder")}
            value={newPwd}
            onChange={(e) => setNewPwd(e.target.value)}
          />
          <p className="text-[11px] text-muted opacity-70">{t("auth.hint")}</p>
        </div>
        <Button
          variant="primary"
          onClick={() => mutation.mutate(newPwd)}
          loading={mutation.isPending}
          className={cn(saved && "!bg-ok")}
        >
          <Shield size={14} />
          {saved ? t("actions.saved") : newPwd ? t("auth.setPassword") : t("auth.removePassword")}
        </Button>
      </div>
    </Card>
  );
}

function ApiKeysCard() {
  const { t } = useTranslation("appconfig");
  const qc = useQueryClient();
  const [name, setName] = useState("default");
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null);
  const [copied, triggerCopied, resetCopied] = useCopyFeedback(1500);

  const { data } = useQuery({
    queryKey: ["apiKeys"],
    queryFn: () => authApi.listApiKeys().then((r) => r.data),
  });
  const keys: ApiKeyInfo[] = data?.keys ?? [];

  const refresh = async (created?: ApiKeyCreated) => {
    if (created) {
      setRevealed(created);
      resetCopied();
    }
    await qc.invalidateQueries({ queryKey: ["apiKeys"] });
  };

  const createMut = useMutation({
    mutationFn: () => authApi.createApiKey(name || "default").then((r) => r.data),
    onSuccess: (created) => { void refresh(created); },
  });
  const rotateMut = useMutation({
    mutationFn: (keyId: string) => authApi.rotateApiKey(keyId).then((r) => r.data),
    onSuccess: (created) => { void refresh(created); },
  });
  const deleteMut = useMutation({
    mutationFn: (keyId: string) => authApi.deleteApiKey(keyId),
    onSuccess: () => {
      setRevealed(null);
      void refresh();
    },
  });

  return (
    <Card title={t("apiKeys.title")} subtitle={t("apiKeys.subtitle")}>
      <div className="space-y-3">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-48 flex flex-col gap-1">
            <label className="text-xs text-muted font-medium">{t("apiKeys.name")}</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("apiKeys.namePlaceholder")}
            />
          </div>
          <Button variant="primary" onClick={() => createMut.mutate()} loading={createMut.isPending}>
            <KeyRound size={14} />
            {t("apiKeys.create")}
          </Button>
        </div>

        {revealed?.api_key && (
          <div className="rounded-md border border-accent bg-accent-subtle p-3 space-y-2">
            <p className="text-xs text-accent font-medium">{t("apiKeys.createdOnce")}</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs break-all text-heading">{revealed.api_key}</code>
              <Button
                variant="secondary"
                size="sm"
                onClick={async () => {
                  await navigator.clipboard.writeText(revealed.api_key);
                  triggerCopied();
                }}
              >
                <Copy size={12} />
                {copied ? t("apiKeys.copied") : t("apiKeys.copy")}
              </Button>
            </div>
          </div>
        )}

        {keys.length === 0 ? (
          <p className="text-sm text-muted">{t("apiKeys.empty")}</p>
        ) : (
          <div className="space-y-2">
            {keys.map((key) => (
              <div
                key={key.id}
                className="flex items-center justify-between gap-3 rounded-md border border-border bg-elevated px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-heading truncate">{key.name}</p>
                  <p className="text-[11px] text-muted">
                    {t("apiKeys.prefix")}: {key.masked_key || key.key_prefix}
                    {" · "}
                    {t("apiKeys.createdAt")}: {key.created_at ? new Date(key.created_at * 1000).toLocaleString() : "-"}
                    {key.last_used_at
                      ? ` · ${t("apiKeys.lastUsed")}: ${new Date(key.last_used_at * 1000).toLocaleString()}`
                      : ""}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button variant="secondary" size="sm" onClick={() => rotateMut.mutate(key.id)}>
                    <RefreshCw size={12} /> {t("apiKeys.rotate")}
                  </Button>
                  <Button variant="secondary" size="sm" onClick={() => deleteMut.mutate(key.id)}
                    className="text-danger">
                    <Trash2 size={12} /> {t("apiKeys.delete")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
