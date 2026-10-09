import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { providersApi } from "@/lib/api";
import { Button, Input, Modal, ConfirmDialog } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import { EMPTY_PROVIDER_FIELDS, ProviderFields } from "./ProviderFields";

export function ProviderForm({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation(["models", "common"]);
  const client = useQueryClient();
  const id = useId();
  const [providerId, setProviderId] = useState("");
  const [form, setForm] = useState(EMPTY_PROVIDER_FIELDS);
  const add = useMutation({
    mutationFn: () => providersApi.create({ ...form, id: providerId.trim() }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ["providers"] }); onClose(); },
  });
  const guard = useDiscardChanges(!!providerId || JSON.stringify(form) !== JSON.stringify(EMPTY_PROVIDER_FIELDS), onClose, add.isPending);
  return <>
    <Modal open onClose={guard.requestClose} title={t("newProvider")} width="max-w-2xl" dismissible={!add.isPending} footer={<>
      <Button onClick={guard.requestClose} disabled={add.isPending}>{t("common:cancel")}</Button>
      <Button variant="primary" disabled={!providerId.trim()} loading={add.isPending} onClick={() => add.mutate()}>{t("common:create")}</Button>
    </>}>
      <fieldset disabled={add.isPending} className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor={id} className="text-xs font-medium text-muted">{t("providerFields.id")}</label>
          <Input id={id} required value={providerId} onChange={(event) => setProviderId(event.target.value)} />
        </div>
        <ProviderFields value={form} onChange={setForm} />
      </fieldset>
      {add.error && <div className="mt-4"><QueryError compact error={add.error} /></div>}
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
