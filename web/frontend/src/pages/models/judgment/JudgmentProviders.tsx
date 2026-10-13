import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FlaskConical, Pencil, Plus, Server, Trash2 } from "lucide-react";
import type { JudgmentCatalog, JudgmentHealth } from "@/lib/types";
import { judgmentApi } from "@/lib/api";
import { Badge, Button, ConfirmDialog, Modal } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { JudgmentPriority } from "./JudgmentPriority";
import { JudgmentProviderEditor } from "./JudgmentProviderEditor";
import { JudgmentTestResultCard } from "./JudgmentTestResultCard";

function ModelHealth({ health }: { health?: JudgmentHealth }) {
  const { t } = useTranslation("models");
  const status = health?.status ?? "unknown";
  return <div className="mt-2 space-y-1 text-xs">
    <Badge variant={status === "ready" ? "ok" : status === "offline" ? "danger" : "neutral"}>{t(`judgment.health.${status}`)}</Badge>
    {health?.checked_at ? <span className="ml-2 text-muted">{t("judgment.lastCheck", { time: new Date(health.checked_at * 1000).toLocaleTimeString() })}</span> : null}
    {status === "ready" && health?.check_kind === "connectivity" && <p className="text-muted">{t("judgment.reachableHint")}</p>}
    {health?.error && <p className="break-words text-danger">{health.error}</p>}
    {health?.retry_at ? <p className="text-muted">{t("judgment.nextCheck", { time: new Date(health.retry_at * 1000).toLocaleTimeString() })}</p> : null}
  </div>;
}

function ModelTest({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation("models");
  const cache = useQueryClient();
  const test = useMutation({ mutationFn: (inference: boolean) => judgmentApi.test(id, inference).then((r) => r.data), onSettled: () => cache.invalidateQueries({ queryKey: ["judgmentCatalog"] }) });
  return <Modal open onClose={onClose} title={`${t("judgment.testChannel")} · ${id}`} footer={<><Button disabled={test.isPending} onClick={() => test.mutate(true)}><FlaskConical size={14} />{t("judgment.testInference")}</Button><Button variant="primary" loading={test.isPending} onClick={() => test.mutate(false)}>{t("judgment.testChannel")}</Button></>}>
    <p className="mb-3 text-xs text-muted">{t("judgment.testHint")}</p>
    {test.data && <JudgmentTestResultCard result={test.data} />}
    {test.error && <QueryError compact error={test.error} />}
  </Modal>;
}

export function JudgmentProviders({ catalog, error, revision }: { catalog?: JudgmentCatalog; error: Error | null; revision: string }) {
  const { t } = useTranslation(["models", "common"]);
  const cache = useQueryClient();
  const [editor, setEditor] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const refresh = () => cache.invalidateQueries({ queryKey: ["judgmentCatalog"] });
  const remove = useMutation({ mutationFn: judgmentApi.deleteProvider, onSuccess: async () => { await refresh(); setRemoving(null); } });
  const providers = Object.entries(catalog?.providers ?? {});
  return <section aria-label={t("judgment.connection")} className="space-y-3">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1"><h3 className="text-sm font-medium text-heading">{t("judgment.connection")}</h3><p className="mt-1 text-xs leading-relaxed text-muted">{t("judgment.connectionHint")}</p></div>
      <Button size="sm" onClick={() => setEditor("")} disabled={!catalog}><Plus size={14} />{t("judgment.addProvider")}</Button>
    </div>
    <a className="text-xs text-accent hover:underline" href="https://github.com/StartLuxLabs/StartLux-Decision/blob/main/docs/inference.md#gguf-and-llamacpp" target="_blank" rel="noreferrer">{t("judgment.localGuide")}</a>
    {error && <QueryError compact error={error} />}
    <JudgmentPriority catalog={catalog} />
    {!providers.length && catalog && <p className="rounded-xl border border-dashed border-border p-6 text-sm text-muted">{t("judgment.emptyProviders")}</p>}
    <div className="grid items-start gap-3 xl:grid-cols-2">
      {providers.map(([pid, provider]) => <article key={pid} className="min-w-0 rounded-xl border border-border bg-card" aria-label={provider.name}>
        <div className="flex items-start gap-3 p-4"><Server size={18} className="mt-0.5 shrink-0 text-accent" /><div className="min-w-0 flex-1"><h4 className="font-medium text-heading">{provider.name}</h4><p className="mt-1 break-all text-xs text-muted">{provider.base_url}</p><span className="text-xs text-muted">{t(provider.api_key ? "judgment.keyConfigured" : "judgment.noKey")}</span></div>
          <Button size="icon" variant="ghost" aria-label={t("judgment.editNamed", { name: provider.name })} onClick={() => setEditor(pid)}><Pencil size={14} /></Button>
          <Button size="icon" variant="ghost" aria-label={t("judgment.deleteNamed", { name: provider.name })} onClick={() => setRemoving(pid)}><Trash2 size={14} /></Button>
        </div>
        <div className="divide-y divide-border border-t border-border">{Object.entries(provider.models).map(([mid, model]) => <div key={mid} className="flex flex-wrap items-center gap-3 px-4 py-3"><div className="min-w-0 flex-1"><p className="break-words text-sm text-heading">{model.name || model.model}</p><p className="break-all text-xs font-mono text-muted">{mid} · {model.model}</p><ModelHealth health={catalog?.health?.[mid]} /></div>{catalog?.default_model === mid && <Badge variant="info">{t("judgment.defaultBadge")}</Badge>}<Button size="sm" onClick={() => setTesting(mid)}>{t("judgment.testModel")}</Button></div>)}</div>
      </article>)}
    </div>
    {editor !== null && <JudgmentProviderEditor id={editor} provider={catalog?.providers[editor]} onClose={() => setEditor(null)} />}
    {testing && <ModelTest key={`${revision}:${testing}`} id={testing} onClose={() => setTesting(null)} />}
    <ConfirmDialog open={removing !== null} onClose={() => setRemoving(null)} onConfirm={() => { if (removing) remove.mutate(removing); }} title={t("judgment.deleteProvider")} message={t("judgment.deleteProviderHint")} danger loading={remove.isPending} />
    {remove.error && <QueryError compact error={remove.error} />}
  </section>;
}
