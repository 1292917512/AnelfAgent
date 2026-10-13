import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import type { DecisionProvider } from "@/lib/types";
import { judgmentApi } from "@/lib/api";
import { Button, Input, Modal, ConfirmDialog } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";

interface Props { id?: string; provider?: DecisionProvider; onClose: () => void }

export function JudgmentProviderEditor({ id = "", provider, onClose }: Props) {
  const { t } = useTranslation(["models", "common"]);
  const cache = useQueryClient();
  const [providerId, setProviderId] = useState(id);
  const [name, setName] = useState(provider?.name ?? "");
  const [url, setUrl] = useState(provider?.base_url ?? "");
  const [key, setKey] = useState(provider?.api_key ?? "");
  const [models, setModels] = useState(() => Object.entries(provider?.models ?? {}).map(([mid, model]) => ({ row: crypto.randomUUID(), id: mid, ...model })));
  const ids = models.map((model) => model.id.trim());
  const duplicate = new Set(ids).size !== ids.length;
  const valid = providerId.trim() && name.trim() && url.trim() && !duplicate && models.every((model) => model.id.trim() && model.model.trim());
  const payload: DecisionProvider = { name, base_url: url, api_key: key, models: Object.fromEntries(models.map((model) => [model.id.trim(), { name: model.name, model: model.model }])) };
  const save = useMutation({
    mutationFn: () => judgmentApi.saveProvider(providerId.trim(), payload),
    onSuccess: async () => { await cache.invalidateQueries({ queryKey: ["judgmentCatalog"] }); onClose(); },
  });
  const dirty = providerId !== id || JSON.stringify(payload) !== JSON.stringify(provider ?? { name: "", base_url: "", api_key: "", models: {} });
  const guard = useDiscardChanges(dirty, onClose, save.isPending);
  return <>
    <Modal open onClose={guard.requestClose} title={t(id ? "judgment.editProvider" : "judgment.addProvider")} width="max-w-2xl" dismissible={!save.isPending} footer={<>
      <Button onClick={guard.requestClose} disabled={save.isPending}>{t("common:cancel")}</Button>
      <Button variant="primary" disabled={!valid} loading={save.isPending} onClick={() => save.mutate()}>{t("common:save")}</Button>
    </>}>
      <fieldset disabled={save.isPending} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-1.5 text-xs text-muted">{t("judgment.providerId")}<Input value={providerId} disabled={!!id} onChange={(e) => setProviderId(e.target.value)} /></label>
          <label className="space-y-1.5 text-xs text-muted">{t("judgment.providerName")}<Input value={name} onChange={(e) => setName(e.target.value)} /></label>
        </div>
        <label className="block space-y-1.5 text-xs text-muted">{t("judgment.serviceUrl")}<Input placeholder="http://127.0.0.1:8090" value={url} onChange={(e) => setUrl(e.target.value)} /></label>
        <label className="block space-y-1.5 text-xs text-muted">{t("judgment.apiKey")}<Input type="password" autoComplete="new-password" value={key} onChange={(e) => setKey(e.target.value)} /></label>
        <p className="text-xs text-muted">{t("judgment.keyHint")}</p>
        <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-medium">{t("judgment.models")}</h3><Button size="sm" disabled={models.length >= 64} onClick={() => setModels([...models, { row: crypto.randomUUID(), id: "", name: "", model: "" }])}><Plus size={14} />{t("judgment.addModel")}</Button></div>
        <div className="space-y-3">
          {models.map((model, index) => <div key={model.row} className="rounded-xl border border-border p-3 space-y-3">
            <div className="flex items-center justify-between"><span className="text-xs text-muted">{t("judgment.modelNumber", { count: index + 1 })}</span><Button size="icon" variant="ghost" aria-label={t("judgment.removeModel", { name: model.name || model.id || index + 1 })} onClick={() => setModels(models.filter((entry) => entry.row !== model.row))}><Trash2 size={14} /></Button></div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-xs text-muted">{t("judgment.modelId")}<Input value={model.id} onChange={(e) => setModels(models.map((entry) => entry.row === model.row ? { ...entry, id: e.target.value } : entry))} /></label>
              <label className="space-y-1 text-xs text-muted">{t("judgment.modelName")}<Input value={model.name} onChange={(e) => setModels(models.map((entry) => entry.row === model.row ? { ...entry, name: e.target.value } : entry))} /></label>
            </div>
            <label className="block space-y-1 text-xs text-muted">{t("judgment.apiModel")}<Input placeholder="StartLux-Decision-27B" value={model.model} onChange={(e) => setModels(models.map((entry) => entry.row === model.row ? { ...entry, model: e.target.value } : entry))} /></label>
          </div>)}
          {duplicate && <p role="alert" className="text-sm text-danger">{t("judgment.duplicateId")}</p>}
        </div>
        <p className="text-xs text-muted">{t("judgment.defaultRemovalHint")}</p>
      </fieldset>
      {save.error && <div className="mt-3"><QueryError compact error={save.error} /></div>}
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
