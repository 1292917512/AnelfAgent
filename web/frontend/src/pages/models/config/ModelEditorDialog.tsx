import { useTranslation } from "react-i18next";
import { FlaskConical, Save } from "lucide-react";
import type { ModelConfig, ProviderConfig } from "@/lib/types";
import { Button, ConfirmDialog, Modal } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import { useModelEditor } from "./useModelEditor";
import { ModelEditorFields } from "./ModelEditorFields";
import { ModelTestResult } from "./ModelTestResult";

export function ModelEditorDialog({ provider, model, onClose }: { provider: ProviderConfig; model: ModelConfig; onClose: () => void }) {
  const { t } = useTranslation(["models", "common"]);
  const editor = useModelEditor(provider, model, onClose);
  const guard = useDiscardChanges(editor.dirty, onClose, editor.busy);
  return <>
    <Modal open onClose={guard.requestClose} dismissible={!editor.busy} width="max-w-2xl"
      title={<span>{t("editModel")} <span className="text-xs font-normal text-muted">· {model.id}</span></span>}
      footer={<>
        <Button size="sm" disabled={editor.busy} onClick={guard.requestClose}>{t("common:cancel")}</Button>
        <Button size="sm" disabled={editor.busy} onClick={() => void editor.save(true)}><FlaskConical size={14} />{t("saveAndTest")}</Button>
        <Button size="sm" variant="primary" loading={editor.busy} onClick={() => void editor.save(false)}><Save size={14} />{t("common:save")}</Button>
      </>}>
      <fieldset disabled={editor.busy}><ModelEditorFields provider={provider} model={model} editor={editor} /></fieldset>
      {editor.remoteQuery.error && <div className="mt-4"><QueryError compact error={editor.remoteQuery.error} retry={() => void editor.remoteQuery.refetch()} /></div>}
      {editor.error && <div className="mt-4"><QueryError compact error={editor.error} /></div>}
      <div className="mt-4"><ModelTestResult test={editor.test} stale={editor.stale} /></div>
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
