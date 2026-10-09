import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Pencil, Save, TestTube } from "lucide-react";
import type { ProviderConfig, UpdateProviderConfig } from "@/lib/types";
import { modelsApi, providersApi } from "@/lib/api";
import { Button, ConfirmDialog, Modal } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import { ProviderFields, providerFields, type ProviderFieldsValue } from "./ProviderFields";

function ProviderEditor({ provider, onClose }: { provider: ProviderConfig; onClose: () => void }) {
  const { t } = useTranslation(["models", "common"]);
  const client = useQueryClient();
  const [initial] = useState(() => providerFields(provider));
  const [draft, setDraft] = useState<ProviderFieldsValue>(initial);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const save = useMutation({
    mutationFn: (data: UpdateProviderConfig) => providersApi.update(provider.id, data),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: ["providers"] }), client.invalidateQueries({ queryKey: ["priorities"] })]);
      onClose();
    },
  });
  const test = useMutation({ mutationFn: () => modelsApi.test(draft.base_url, draft.api_key, provider.id, draft.api_type).then((response) => response.data.result) });
  const busy = save.isPending || test.isPending;
  const guard = useDiscardChanges(dirty, onClose, busy);
  return <>
    <Modal open onClose={guard.requestClose} title={t("providerConfig")} width="max-w-2xl" dismissible={!busy} footer={<>
      <Button onClick={guard.requestClose} disabled={busy}>{t("common:cancel")}</Button>
      <Button loading={test.isPending} disabled={save.isPending} onClick={() => test.mutate()}><TestTube size={14} />{t("common:test")}</Button>
      <Button variant="primary" loading={save.isPending} disabled={!dirty || test.isPending} onClick={() => save.mutate(draft)}><Save size={14} />{t("common:save")}</Button>
    </>}>
      <fieldset disabled={busy}><ProviderFields value={draft} onChange={setDraft} /></fieldset>
      {test.data && <p role="status" className="mt-4 break-words rounded-lg border border-border bg-elevated p-3 text-sm">{test.data}</p>}
      {(save.error || test.error) && <div className="mt-4"><QueryError compact error={save.error || test.error} /></div>}
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}

export function ProviderConfigEditor({ provider }: { provider: ProviderConfig }) {
  const { t } = useTranslation(["models", "common"]);
  const [editing, setEditing] = useState(false);
  return <section className="rounded-lg border border-border bg-elevated p-4">
    <div className="flex items-center justify-between gap-3">
      <h4 className="text-sm font-medium text-heading">{t("providerConfig")}</h4>
      <Button size="sm" onClick={() => setEditing(true)}><Pencil size={13} />{t("common:edit")}</Button>
    </div>
    <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
      {(["name", "base_url", "api_type", "proxy_url", "media_protocol"] as const).map((key) => <div key={key} className="min-w-0">
        <dt className="text-muted">{t(`providerFields.${key}`)}</dt>
        <dd className="mt-1 break-words text-foreground">{provider[key] || "—"}</dd>
      </div>)}
    </dl>
    {editing && <ProviderEditor provider={provider} onClose={() => setEditing(false)} />}
  </section>;
}
