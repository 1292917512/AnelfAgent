import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, RotateCcw, Save, Trash2 } from "lucide-react";
import { apiErrorMessage, providerKeysApi } from "@/lib/api";
import { Button, ConfirmDialog, Input, toast } from "@/components/ui";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import type { CredentialEditorProps } from "@/lib/ui-contributions";
import { CredentialDialog } from "./CredentialCard";

export function CredentialFields({ credential, onClose }: CredentialEditorProps) {
  const { t } = useTranslation("common");
  const client = useQueryClient();
  const formId = useId();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const dirty = Object.keys(draft).length > 0;
  const reset = () => { setDraft({}); setVisible({}); };
  const save = useMutation({
    mutationFn: () => providerKeysApi.set(credential.name, draft),
    onSuccess: async () => {
      reset();
      await client.invalidateQueries({ queryKey: ["providerKeys"] });
      toast.success(t("providerKeys.saved"));
    },
  });
  const update = (key: string, value: string, remove = false) => setDraft((current) => {
    const next = { ...current };
    const field = credential.fields.find((item) => item.key === key);
    if (!remove && (field?.secret ? !value : value === field?.value)) delete next[key];
    else next[key] = value;
    return next;
  });
  const guard = useDiscardChanges(dirty, onClose, save.isPending);
  return <><CredentialDialog credential={credential} onClose={guard.requestClose} busy={save.isPending}
    footer={<>
      <Button type="button" size="sm" disabled={save.isPending} onClick={guard.requestClose}>{t("close")}</Button>
      {dirty && <Button type="button" size="sm" variant="ghost" disabled={save.isPending} onClick={reset}>{t("reset")}</Button>}
      <Button type="submit" form={formId} size="sm" variant="primary" disabled={!dirty} loading={save.isPending}><Save size={14} />{t("providerKeys.save")}</Button>
    </>}>
    <form id={formId} className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (dirty && !save.isPending) save.mutate(); }}>
      {credential.fields.map((field) => {
        const id = `credential-${credential.name}-${field.key}`;
        const clearing = draft[field.key] === "" && field.configured;
        const value = draft[field.key] ?? (field.secret ? "" : field.value);
        return <div key={field.key} className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label htmlFor={id} className="text-xs font-medium text-foreground">{field.label || field.key}</label>
            <span className={`text-[11px] ${clearing ? "text-warn" : "text-muted"}`}>
              {t(clearing ? "providerKeys.pendingClear" : field.configured ? "providerKeys.configured" : field.required ? "providerKeys.notConfigured" : "providerKeys.optional")}
            </span>
          </div>
          <div className="flex items-start gap-2">
            {field.multiline ? <textarea id={id} rows={3} value={value} disabled={save.isPending} autoComplete="off"
              placeholder={field.configured ? field.value || "••••••••" : field.label}
              className="min-w-0 flex-1 resize-y rounded-lg border border-input bg-card p-3 font-mono text-xs"
              onChange={(event) => update(field.key, event.target.value)} />
              : <div className="relative min-w-0 flex-1">
                <Input id={id} type={field.secret && !visible[field.key] ? "password" : "text"} value={value}
                  autoComplete={field.secret ? "new-password" : "off"} disabled={save.isPending} spellCheck={false}
                  className={field.secret ? "pr-10 font-mono text-xs" : "font-mono text-xs"}
                  placeholder={field.secret && field.configured ? field.value || "••••••••" : field.label || field.key}
                  onChange={(event) => update(field.key, event.target.value)} />
                {field.secret && value && <button type="button" className="absolute right-2 top-2 text-muted"
                  aria-label={t(visible[field.key] ? "providerKeys.hide" : "providerKeys.reveal")}
                  onClick={() => setVisible((current) => ({ ...current, [field.key]: !current[field.key] }))}>
                  {visible[field.key] ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>}
              </div>}
            {field.configured && <Button type="button" variant="ghost" size="icon" disabled={save.isPending}
              aria-label={t(clearing ? "providerKeys.undoClear" : "providerKeys.clearField", { field: field.label || field.key })}
              onClick={() => clearing ? setDraft((current) => { const next = { ...current }; delete next[field.key]; return next; }) : update(field.key, "", true)}>
              {clearing ? <RotateCcw size={15} /> : <Trash2 size={15} />}
            </Button>}
          </div>
          {field.secret && field.configured && !clearing && <p className="break-all text-[11px] text-muted">
            {t("providerKeys.keepSecret")}
          </p>}
        </div>;
      })}
      {save.isError && <p role="alert" className="text-xs text-danger">{apiErrorMessage(save.error, t("providerKeys.saveFailed"))}</p>}
    </form>
  </CredentialDialog><ConfirmDialog {...guard.confirmProps} /></>;
}
