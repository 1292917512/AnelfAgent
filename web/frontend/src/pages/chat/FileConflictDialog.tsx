import { useTranslation } from "react-i18next";
import { Modal, Button } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useFileEditorStore } from "@/stores/file-editor-store";
import type { useFileSession } from "./useFileSession";

export function FileConflictDialog({ session }: { session: ReturnType<typeof useFileSession> }) {
  const { t } = useTranslation("workbench");
  const conflict = session.conflict;
  const draft = useFileEditorStore((state) => conflict ? state.tabs.get(conflict.id)?.draft : undefined);
  return <Modal open={!!conflict} onClose={session.closeConflict} title={t("conflict.title")} width="max-w-4xl" dismissible={!session.saving}>
    {conflict && <div className="space-y-4">
      <p className="break-all text-sm text-muted">{t("conflict.description", { path: conflict.ref.path })}</p>
      {conflict.error != null ? <QueryError error={conflict.error} retry={session.retryConflict} />
        : conflict.remote === undefined ? <p role="status">{t("editor.loading")}</p>
        : <div className="grid min-h-0 gap-3 sm:grid-cols-2">
          <section className="min-w-0"><h3 className="mb-2 text-sm font-medium">{t("conflict.local")}</h3>
            <pre className="max-h-[40dvh] overflow-auto rounded-lg border border-border bg-elevated p-3 text-xs whitespace-pre-wrap break-words">{draft}</pre></section>
          <section className="min-w-0"><h3 className="mb-2 text-sm font-medium">{t("conflict.remote")}</h3>
            <pre className="max-h-[40dvh] overflow-auto rounded-lg border border-border bg-elevated p-3 text-xs whitespace-pre-wrap break-words">{conflict.remote === null ? t("conflict.deleted") : conflict.remote.binary || conflict.remote.truncated ? t("conflict.notEditable") : conflict.remote.content}</pre></section>
        </div>}
      {session.saveError != null && <QueryError compact error={session.saveError} />}
      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={session.closeConflict} disabled={session.saving}>{t("conflict.keepEditing")}</Button>
        {conflict.remote && <Button onClick={session.useRemote} disabled={session.saving}>{t("conflict.useRemote")}</Button>}
        <Button variant="danger" loading={session.saving} onClick={session.overwrite}
          disabled={conflict.remote === undefined || draft === undefined || !!conflict.remote?.binary || !!conflict.remote?.truncated}>{t("conflict.overwrite")}</Button>
      </div>
    </div>}
  </Modal>;
}
