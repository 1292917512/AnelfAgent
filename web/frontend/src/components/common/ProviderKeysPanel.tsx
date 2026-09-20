import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { localModelsApi, providerKeysApi } from "@/lib/api";
import type { ProviderKeyEntry } from "@/lib/types";
import { Badge, Button, Input, toast } from "@/components/ui";
import { KeyRound, Package } from "lucide-react";

/** 通用组件凭据面板：组件外部凭据集中配置（脱敏展示，就地编辑）。
 *  domain 缺省展示全部域；本地模型资产摘要并入同一外部依赖管理面。 */
export function ProviderKeysPanel({ domain }: { domain?: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const { data, isLoading } = useQuery({
    queryKey: ["providerKeys", domain ?? "all"],
    queryFn: () => providerKeysApi.list(domain).then((r) => r.data),
  });
  const { data: localModels } = useQuery({
    queryKey: ["localModels"],
    queryFn: () => localModelsApi.list().then((r) => r.data),
  });

  const saveMut = useMutation({
    mutationFn: ({ provider, field, value }: { provider: string; field: string; value: string }) =>
      providerKeysApi.set(provider, field, value),
    onSuccess: (_r, vars) => {
      setDrafts((d) => {
        const next = { ...d };
        delete next[`${vars.provider}.${vars.field}`];
        return next;
      });
      toast.success(t("providerKeys.saved"));
      queryClient.invalidateQueries({ queryKey: ["providerKeys", domain ?? "all"] });
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("providerKeys.saveFailed"));
    },
  });

  if (isLoading) return null;
  const providers = data?.providers ?? [];
  if (providers.length === 0) {
    return (
      <div className="rounded-md border border-border bg-card p-4 flex items-center gap-2 text-xs text-muted">
        <KeyRound size={14} />
        {t("providerKeys.empty")}
      </div>
    );
  }

  const renderField = (p: ProviderKeyEntry, field: ProviderKeyEntry["fields"][number]) => {
    const draftKey = `${p.name}.${field.key}`;
    const draft = drafts[draftKey];
    return (
      <div key={draftKey} className="flex items-center gap-2">
        <Input
          className="flex-1 font-mono text-xs"
          type={field.secret ? "password" : "text"}
          placeholder={field.label || field.key}
          value={draft ?? ""}
          onChange={(e) => setDrafts((d) => ({ ...d, [draftKey]: e.target.value }))}
        />
        <span className={`text-[11px] whitespace-nowrap ${field.configured ? "text-muted" : "text-warn"}`}>
          {field.configured ? (field.value || t("providerKeys.configured")) : t("providerKeys.notConfigured")}
        </span>
        <Button
          size="sm"
          disabled={draft === undefined || draft === "" || saveMut.isPending}
          loading={saveMut.isPending && saveMut.variables?.provider === p.name}
          onClick={() => draft !== undefined && saveMut.mutate({ provider: p.name, field: field.key, value: draft })}
        >
          {t("providerKeys.save")}
        </Button>
      </div>
    );
  };

  return (
    <div className="rounded-md border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <KeyRound size={14} className="text-accent" />
        <span className="text-sm font-semibold text-heading">{t("providerKeys.title")}</span>
        <span className="text-xs text-muted">{t("providerKeys.hint")}</span>
      </div>
      {providers.map((p) => (
        <div key={`${p.name}.${p.domain}`} className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-foreground">{p.title}</span>
            <Badge variant="neutral">{t(`providerKeys.domain.${p.domain}`, { defaultValue: p.domain || "-" })}</Badge>
            {p.fields.some((f) => f.configured) ? (
              <Badge variant="ok">{t("providerKeys.configured")}</Badge>
            ) : (
              <Badge variant="neutral">{t("providerKeys.notConfigured")}</Badge>
            )}
            <span className="text-[11px] text-muted">{p.description}</span>
          </div>
          {p.fields.map((f) => renderField(p, f))}
        </div>
      ))}

      {localModels && (
        <div className="pt-2 border-t border-border/60 flex items-center gap-2 flex-wrap">
          <Package size={13} className="text-accent" />
          <span className="text-xs font-medium text-foreground">{t("providerKeys.localModels")}</span>
          <span className="text-xs text-muted">
            {t("providerKeys.localModelsSummary", {
              ready: localModels.models.filter((m) => m.status === "ready").length,
              total: localModels.models.length,
            })}
          </span>
          <a href="/webui/settings" className="ml-auto text-xs text-accent hover:underline">
            {t("providerKeys.localModelsLink")}
          </a>
        </div>
      )}
    </div>
  );
}
