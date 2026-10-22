import { useDialogSession } from "@/hooks/useDialogSession";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Globe, Terminal, Radio, Settings2 } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import { Button, ConfirmDialog, Input, Modal, Switch, Textarea, toast } from "@/components/ui";
import { TabBar } from "@/components/common/TabBar";
import { useDraft } from "@/hooks/useDraft";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import { serverDraft, serverPatch, type EditingServer } from "./types";
import { Field, KVEditor } from "./formFields";
import type { MCPTransport } from "@/lib/types";

export function ServerFormModal({ onClose, editing }: { onClose: () => void; editing: EditingServer | null }) {
  const session = useDialogSession(onClose);
  const { t } = useTranslation("mcp");
  const client = useQueryClient();
  const id = useId();
  const [initial] = useState(() => serverDraft(editing));
  const draft = useDraft(initial);
  const value = draft.values;
  const [error, setError] = useState("");
  const save = useMutation({ mutationFn: async () => {
    const patch = serverPatch(value, editing);
    return editing ? mcpApi.update(editing.name, patch) : mcpApi.add(value.name.trim(), patch);
  }, onSuccess: () => {
    void client.invalidateQueries({ queryKey: ["mcpServers"] });
    void client.invalidateQueries({ queryKey: ["mcpConfig"] });
    toast.success(t(editing ? "toast.serverUpdated" : "toast.serverAdded", { name: value.name.trim() }));
    session.close();
  }, onError: (failure) => setError(apiErrorMessage(failure, t("toast.requestFailed"))) });
  const guard = useDiscardChanges(draft.dirty, session.close, save.isPending);
  const submit = () => {
    try { serverPatch(value, editing); setError(""); save.mutate(); }
    catch (failure) { setError(t(failure instanceof Error ? failure.message : "toast.requestFailed")); }
  };
  const transports: { key: MCPTransport; label: string; icon: typeof Globe }[] = [
    { key: "streamable_http", label: t("form.transportHttp"), icon: Globe },
    { key: "stdio", label: t("form.transportStdio"), icon: Terminal },
    { key: "sse", label: t("form.transportSse"), icon: Radio },
  ];
  return <>
    <Modal {...session.surfaceProps} onClose={guard.requestClose} dismissible={!save.isPending} title={editing ? `${t("editServer")} · ${editing.name}` : t("addServer")} width="max-w-2xl"
      footer={<><Button variant="secondary" size="sm" disabled={save.isPending} onClick={guard.requestClose}>{t("common:cancel")}</Button>
        <Button type="submit" form={id} variant="primary" size="sm" disabled={!draft.dirty} loading={save.isPending}>{t("common:save")}</Button></>}>
      <form id={id} onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <fieldset disabled={save.isPending} className="min-w-0 space-y-5">
          <Field label={t("form.name")} htmlFor={`${id}-name`}>
            <Input id={`${id}-name`} value={value.name} disabled={!!editing} placeholder={t("form.namePlaceholder")} onChange={(event) => draft.update("name", event.target.value)} />
          </Field>
          <div className="space-y-2"><p className="text-xs font-medium text-muted">{t("form.transport")}</p>
            <TabBar tabs={transports} activeTab={value.transport} onChange={(transport) => draft.update("transport", transport)} />
          </div>
          {value.transport === "stdio" ? <>
            <Field label={t("form.command")} htmlFor={`${id}-command`}><Input id={`${id}-command`} className="font-mono text-xs" value={value.command} placeholder={t("form.commandPlaceholder")} onChange={(event) => draft.update("command", event.target.value)} /></Field>
            <Field label={t("form.args")} htmlFor={`${id}-args`} optional><Textarea id={`${id}-args`} value={value.argsText} placeholder={t("form.argsPlaceholder")} rows={3} className="font-mono text-xs" onChange={(event) => draft.update("argsText", event.target.value)} /></Field>
            <KVEditor rows={value.envRows} onChange={(rows) => draft.update("envRows", rows)} label={t("form.env")} />
          </> : <>
            <Field label={t("form.url")} htmlFor={`${id}-url`}><Input id={`${id}-url`} value={value.url} className="font-mono text-xs" placeholder={t("form.urlPlaceholder")} onChange={(event) => draft.update("url", event.target.value)} /></Field>
            <KVEditor rows={value.headerRows} onChange={(rows) => draft.update("headerRows", rows)} label={t("form.headers")} />
          </>}
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm text-heading"><Settings2 size={14} className="mr-2 inline" />{t("form.advanced")}</summary>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {(["timeout", "sseReadTimeout", "callTimeout"] as const).filter((key) => key !== "sseReadTimeout" || value.transport !== "stdio").map((key) =>
                <Field key={key} label={t(`form.${key}`)} htmlFor={`${id}-${key}`} optional><Input id={`${id}-${key}`} value={value[key]} type="number" min={0.001} step="any" placeholder={t("form.defaultTimeout")} onChange={(event) => draft.update(key, event.target.value)} /></Field>)}
            </div>
          </details>
          {!editing && <div className="flex items-center justify-between gap-3 rounded-lg bg-secondary/50 p-3 text-sm"><span>{t("form.enabledLabel")}</span><Switch label={t("form.enabledLabel")} checked={value.enabled} onChange={(enabled) => draft.update("enabled", enabled)} /></div>}
          {error && <p role="alert" className="break-words text-xs text-danger">{error}</p>}
        </fieldset>
      </form>
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
