import { lazy, Suspense, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FlaskConical, Settings2, Trash2 } from "lucide-react";
import { apiErrorMessage, retrievalApi } from "@/lib/api";
import type { RetrievalProviderInfo } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Badge, Button, Input, Modal, Select, Switch } from "@/components/ui";

const RegisteredConfigPanel = lazy(() => import("../config/RegisteredConfigPanel").then((module) => ({ default: module.RegisteredConfigPanel })));

export function RetrievalProviderCard({ provider, onChanged }: { provider: RetrievalProviderInfo; onChanged: () => void }) {
  const { t } = useTranslation("retrieval");
  const [apiKey, setApiKey] = useState("");
  const [configOpen, setConfigOpen] = useState(false);
  const [testCap, setTestCap] = useState(provider.capabilities[0] ?? "");
  const [testInput, setTestInput] = useState("");
  const toggle = useMutation({ mutationFn: () => retrievalApi.setEnabled(provider.name, !provider.enabled), onSuccess: onChanged });
  const credential = useMutation({
    mutationFn: (key: string) => retrievalApi.setCredential(provider.name, key),
    onSuccess: () => { setApiKey(""); onChanged(); },
  });
  const test = useMutation({ mutationFn: () => retrievalApi.test(provider.name, testCap, testInput).then((r) => r.data) });
  const busy = toggle.isPending || credential.isPending || test.isPending;
  const usable = provider.enabled && provider.configured;
  const error = toggle.error || credential.error || test.error;
  return <Card title={provider.display_name} subtitle={provider.description}
    actions={<Switch checked={provider.enabled} label={t("provider.toggle", { name: provider.display_name })} disabled={busy} onChange={() => toggle.mutate()} />}>
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={!provider.enabled ? "neutral" : usable ? "ok" : "warn"}>{t(!provider.enabled ? "provider.disabledBadge" : provider.configured ? "provider.configured" : "provider.notConfigured")}</Badge>
      {provider.capabilities.map((cap) => <span key={cap} className="rounded-md bg-secondary px-2 py-1 text-xs text-muted">{t(`caps.${cap}`)}</span>)}
    </div>
    <details className="group mt-4 border-t border-border pt-3">
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 text-xs font-medium text-muted hover:text-heading"><Settings2 size={14} />{t("provider.manage")}</summary>
      <div className="mt-3 space-y-4">
        {provider.config_group && <Button size="sm" onClick={() => setConfigOpen(true)}><Settings2 size={14} />{t("provider.serviceSettings")}</Button>}
        {provider.requires_credential && !provider.config_group && <div className="space-y-2">
          <label className="block text-xs text-muted" htmlFor={`key-${provider.name}`}>{t("provider.credential")}</label>
          <div className="flex flex-wrap gap-2">
            <Input id={`key-${provider.name}`} type="password" autoComplete="new-password" value={apiKey} disabled={busy}
              onChange={(event) => setApiKey(event.target.value)} className="min-w-0 flex-1 font-mono text-xs"
              placeholder={t(provider.credential_source === "config" ? "provider.keyPlaceholderSet" : "provider.keyPlaceholderEmpty")} />
            <Button size="sm" disabled={!apiKey.trim() || busy} onClick={() => credential.mutate(apiKey.trim())}>{t("provider.saveKey")}</Button>
            {provider.credential_source === "config" && <Button size="icon" variant="ghost" title={t("provider.clearKey")} disabled={busy} onClick={() => credential.mutate("")}><Trash2 size={14} /></Button>}
          </div>
          {provider.credential_source && <p className="text-xs text-muted">{t("provider.sourcePrefix")}: {t(`provider.source.${provider.credential_source}`, { defaultValue: provider.credential_source })}</p>}
        </div>}
        {!!provider.capabilities.length && <div className="space-y-2">
          <div className="flex items-center gap-2"><FlaskConical size={14} className="text-muted" /><span className="text-xs text-muted">{t("test.title")}</span></div>
          <div className="flex flex-wrap gap-2">
            <Select value={testCap} aria-label={t("test.capability")} disabled={busy} onChange={(event) => { setTestCap(event.target.value); test.reset(); }}>
              {provider.capabilities.map((cap) => <option key={cap} value={cap}>{t(`caps.${cap}`)}</option>)}
            </Select>
            <Input className="min-w-0 flex-1 basis-40 text-xs" aria-label={t("test.input")} value={testInput} disabled={busy}
              placeholder={t(`test.inputPlaceholder.${testCap}`)} onChange={(event) => { setTestInput(event.target.value); test.reset(); }} />
            <Button size="sm" disabled={!usable || busy} loading={test.isPending} onClick={() => { test.reset(); test.mutate(); }}>{t("test.run")}</Button>
          </div>
        </div>}
      </div>
    </details>
    <Modal open={configOpen} onClose={() => { setConfigOpen(false); test.reset(); onChanged(); }} title={provider.display_name} width="max-w-3xl">
      <Suspense fallback={<p className="text-sm text-muted">{t("provider.loadingSettings")}</p>}><RegisteredConfigPanel group={provider.config_group} /></Suspense>
    </Modal>
    {error && <p role="alert" className="mt-3 break-words text-xs text-danger">{apiErrorMessage(error, t("common:requestFailed"))}</p>}
    {test.data && <div role="status" className={`mt-3 rounded-lg border p-3 text-xs ${test.data.ok ? "border-ok/25 bg-ok-subtle" : "border-danger/25 bg-danger-subtle"}`}>
      <div className="flex flex-wrap justify-between gap-2"><span className="font-medium">{test.data.ok ? test.data.summary : t("test.failed")}</span><span className="tabular-nums text-muted">{test.data.latency_ms ?? 0} ms</span></div>
      {test.data.error && <p className="mt-2 break-words text-danger">{test.data.error}</p>}
      {test.data.excerpt && <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words font-sans leading-relaxed text-muted">{test.data.excerpt}</pre>}
    </div>}
  </Card>;
}
