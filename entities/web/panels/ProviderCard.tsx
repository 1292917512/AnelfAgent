/** 单个提供者卡片：启停开关、能力徽章、凭据配置、按能力连通性测试。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FlaskConical, KeyRound, Loader2, Power, Trash2 } from "lucide-react";
import { webEntityApi } from "./api";
import type { WebProviderInfo, WebProviderTestResult } from "./types";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";

export function ProviderCard({
  provider,
  onChanged,
}: {
  provider: WebProviderInfo;
  onChanged: () => void;
}) {
  const { t } = useTranslation("web");
  const [apiKey, setApiKey] = useState("");
  const [testCap, setTestCap] = useState(provider.capabilities[0] ?? "");
  const [testInput, setTestInput] = useState("");
  const [testResult, setTestResult] = useState<WebProviderTestResult | null>(null);

  const toggleMutation = useMutation({
    mutationFn: () => webEntityApi.setEnabled(provider.name, !provider.enabled),
    onSuccess: onChanged,
  });
  const keyMutation = useMutation({
    mutationFn: (key: string) => webEntityApi.setKey(provider.name, key),
    onSuccess: () => {
      setApiKey("");
      onChanged();
    },
  });
  const testMutation = useMutation({
    mutationFn: () => webEntityApi.test(provider.name, testCap, testInput).then((r) => r.data),
    onSuccess: setTestResult,
  });

  const sourceLabel = provider.credential_source
    ? t(`provider.source.${provider.credential_source}`, { defaultValue: provider.credential_source })
    : "";
  const usable = provider.enabled && provider.configured;

  return (
    <Card
      title={provider.display_name}
      subtitle={provider.description}
      className={cn(!provider.enabled && "opacity-60")}
      actions={
        <button
          onClick={() => toggleMutation.mutate()}
          disabled={toggleMutation.isPending}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs transition-colors disabled:opacity-50",
            provider.enabled
              ? "border-border text-muted hover:text-danger hover:border-danger"
              : "border-accent text-accent hover:bg-accent/10",
          )}
        >
          <Power size={12} />
          {provider.enabled ? t("provider.disable") : t("provider.enable")}
        </button>
      }
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <StatusDot status={!provider.enabled ? "offline" : usable ? "ok" : "warn"} />
          <span className="text-foreground">
            {!provider.enabled
              ? t("provider.disabledBadge")
              : provider.requires_credential
                ? provider.configured
                  ? t("provider.configured")
                  : t("provider.notConfigured")
                : t("provider.noCredential")}
          </span>
          {sourceLabel && (
            <span className="text-muted">· {t("provider.sourcePrefix")}: {sourceLabel}</span>
          )}
          <span className="flex gap-1 ml-auto">
            {(["search", "reader", "repo"] as const).map((cap) => (
              <span
                key={cap}
                className={cn(
                  "px-1.5 py-0.5 rounded text-[10px] border",
                  provider.capabilities.includes(cap)
                    ? "border-accent/40 text-accent"
                    : "border-border text-muted/50",
                )}
              >
                {t(`caps.${cap}`)}
                {!provider.capabilities.includes(cap) && ` · ${t("provider.unsupported")}`}
              </span>
            ))}
          </span>
        </div>

        {provider.requires_credential && (
          <div className="flex items-center gap-2">
            <KeyRound size={13} className="text-muted shrink-0" />
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t(provider.credential_source === "config" ? "provider.keyPlaceholderSet" : "provider.keyPlaceholderEmpty")}
              className="flex-1 px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
            />
            <button
              onClick={() => keyMutation.mutate(apiKey)}
              disabled={!apiKey.trim() || keyMutation.isPending}
              className="px-2.5 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
            >
              {t("provider.saveKey")}
            </button>
            {provider.credential_source === "config" && (
              <button
                onClick={() => keyMutation.mutate("")}
                disabled={keyMutation.isPending}
                title={t("provider.clearKey")}
                className="p-1.5 rounded-md border border-border text-muted hover:text-danger hover:border-danger disabled:opacity-50"
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
        )}

        {provider.capabilities.length > 0 && (
          <div className="flex items-center gap-2">
            <select
              value={testCap}
              onChange={(e) => setTestCap(e.target.value)}
              className="px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground"
            >
              {provider.capabilities.map((cap) => (
                <option key={cap} value={cap}>{t(`caps.${cap}`)}</option>
              ))}
            </select>
            <input
              type="text"
              value={testInput}
              onChange={(e) => setTestInput(e.target.value)}
              placeholder={t(`test.inputPlaceholder.${testCap}`, { defaultValue: "" })}
              className="flex-1 px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground"
            />
            <button
              onClick={() => testMutation.mutate()}
              disabled={!usable || testMutation.isPending}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border text-xs text-foreground hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {testMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <FlaskConical size={12} />}
              {testMutation.isPending ? t("test.testing") : t("test.run")}
            </button>
          </div>
        )}

        {testResult && (
          <div
            className={cn(
              "rounded-md border px-3 py-2 text-xs",
              testResult.ok ? "border-ok/40 bg-ok/5" : "border-danger/40 bg-danger/5",
            )}
          >
            {testResult.ok ? (
              <div className="space-y-1">
                <div className="text-foreground font-medium">
                  {testResult.latency_ms} ms · {testResult.summary}
                </div>
                {testResult.excerpt && (
                  <pre className="text-muted whitespace-pre-wrap break-all font-mono text-[11px] max-h-32 overflow-y-auto">
                    {testResult.excerpt}
                  </pre>
                )}
              </div>
            ) : (
              <div className="text-danger">{t("test.failed")}: {testResult.error}</div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
