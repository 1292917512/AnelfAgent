import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlignLeft, RotateCcw, Save, ShieldAlert } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { AsyncState } from "@/components/common/AsyncState";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { Button, Textarea, toast } from "@/components/ui";

export function JsonConfigPanel() {
  const { t } = useTranslation("mcp");
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["mcpConfig"], queryFn: () => mcpApi.config().then((r) => r.data.content), throwOnError: false });
  const [draft, setDraft] = useState<string>();
  const [error, setError] = useState("");
  const content = draft ?? query.data ?? "";
  const dirty = draft !== undefined && draft !== query.data;
  useUnsavedChanges(dirty);
  const save = useMutation({ mutationFn: (text: string) => mcpApi.saveConfig(text), onSuccess: (_result, text) => {
    client.setQueryData(["mcpConfig"], text);
    setDraft(undefined);
    void client.invalidateQueries({ queryKey: ["mcpServers"] });
    void client.invalidateQueries({ queryKey: ["mcpConfig"] });
    toast.success(t("toast.configSaved"));
  }, onError: (failure) => setError(apiErrorMessage(failure, t("toast.requestFailed"))) });
  const parse = (): unknown => {
    const value: unknown = JSON.parse(content);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(t("jsonObjectRequired"));
    return value;
  };
  const act = (format: boolean) => {
    try {
      const value = parse();
      setError("");
      if (format) setDraft(JSON.stringify(value, null, 2)); else save.mutate(content);
    } catch (failure) { setError(t("invalidJson", { message: failure instanceof Error ? failure.message : String(failure) })); }
  };
  return <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
    <Card title={t("jsonConfig")} subtitle={t("jsonHint")} actions={<div className="flex flex-wrap gap-2">
      <Button variant="ghost" size="sm" disabled={!dirty || save.isPending} onClick={() => { setDraft(undefined); setError(""); }}><RotateCcw size={14} />{t("common:reset")}</Button>
      <Button variant="secondary" size="sm" disabled={save.isPending} onClick={() => act(true)}><AlignLeft size={14} />{t("format")}</Button>
      <Button variant="primary" size="sm" disabled={!dirty} loading={save.isPending} onClick={() => act(false)}><Save size={14} />{t("common:save")}</Button>
    </div>}>
      <div className="space-y-3">
        <p className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-subtle px-3 py-2 text-xs text-warn"><ShieldAlert size={14} className="shrink-0" />{t("jsonSecretWarning")}</p>
        <Textarea aria-label={t("jsonConfig")} value={content} disabled={save.isPending} onChange={(event) => { setDraft(event.target.value); setError(""); }} rows={22} className="font-mono text-xs leading-relaxed" spellCheck={false} />
        {error && <p role="alert" className="break-words text-xs text-danger">{error}</p>}
      </div>
    </Card>
  </AsyncState>;
}
