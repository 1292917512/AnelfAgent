import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CheckCheck, Play, Save } from "lucide-react";
import { workflowApi, type WorkflowDefinition, type WorkflowRun, type WorkflowRunDetail } from "@/lib/api";
import { Modal, ConfirmDialog } from "@/components/ui/Modal";
import { Button, Input, Textarea } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import { useDialogSession } from "@/hooks/useDialogSession";

type ComposerProps = { onClose: () => void } & (
  { mode?: "run"; source: WorkflowRunDetail | null; onStarted: (run: WorkflowRun) => void }
  | { mode: "definition"; definition: WorkflowDefinition | null; source?: WorkflowRunDetail; onSaved: (definition: WorkflowDefinition) => void }
);

export function WorkflowComposer(props: ComposerProps) {
  const { t } = useTranslation("workflow");
  const { source, onClose } = props;
  const saving = props.mode === "definition";
  const spec = saving ? props.definition?.spec ?? source?.spec : source?.spec;
  const initial = spec ? JSON.stringify(spec, null, 2) : "";
  const [text, setText] = useState(initial);
  const [resumeOf, setResumeOf] = useState(source?.run.run_id ?? "");
  const [validated, setValidated] = useState<{ text: string; layers: string[][] } | null>(null);
  const [completion, setCompletion] = useState<(() => void) | null>(null);
  const session = useDialogSession(() => { if (completion) completion(); else onClose(); });
  const start = useMutation({
    mutationFn: () => workflowApi.startRun(JSON.parse(text), resumeOf.trim()).then((r) => r.data),
    onSuccess: (run) => {
      if (props.mode !== "definition") setCompletion(() => () => props.onStarted(run));
      session.close();
    },
  });
  const save = useMutation({
    mutationFn: () => workflowApi.saveDefinition(JSON.parse(text), props.mode === "definition" ? props.definition?.workflow_id : "").then((r) => r.data),
    onSuccess: (definition) => {
      if (props.mode === "definition") setCompletion(() => () => props.onSaved(definition));
      session.close();
    },
  });
  const validate = useMutation({
    mutationFn: async (submitted: string) => ({ text: submitted, ...(await workflowApi.validate(JSON.parse(submitted))).data }),
    onSuccess: (result) => setValidated({ text: result.text, layers: result.layers }),
  });
  const busy = start.isPending || save.isPending || validate.isPending;
  const guard = useDiscardChanges(text !== initial || resumeOf !== (source?.run.run_id ?? ""), session.close, busy);
  const example = () => setText(JSON.stringify({
    name: t("start.exampleName"), steps: [
      { key: "research", kind: "ask", goal: t("start.exampleResearch") },
      { key: "summary", kind: "ask", depends_on: ["research"], goal: t("start.exampleSummary") },
    ],
  }, null, 2));
  return <>
    <Modal {...session.surfaceProps} onClose={guard.requestClose} title={saving ? t(props.definition ? "definitions.edit" : "definitions.create") : source ? t("action.revise") : t("start.title")} width="max-w-3xl" dismissible={!busy}
      footer={<>
        <Button onClick={guard.requestClose} disabled={busy}>{t("common:cancel")}</Button>
        <Button onClick={() => validate.mutate(text)} disabled={!text.trim() || busy} loading={validate.isPending}><CheckCheck size={15} />{t("start.validate")}</Button>
        <Button variant="primary" onClick={() => saving ? save.mutate() : start.mutate()} disabled={!text.trim() || busy} loading={start.isPending || save.isPending}>
          {saving ? <Save size={15} /> : <Play size={15} />}{saving ? t("common:save") : t("start.submit")}
        </Button>
      </>}>
      <div className="space-y-4">
        <p className="text-sm text-muted">{t(saving ? "definitions.help" : "start.help")}</p>
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="workflow-spec" className="text-sm font-medium">{t("start.spec")}</label>
          {!text.trim() && <Button size="sm" onClick={example}>{t("start.example")}</Button>}
        </div>
        <Textarea id="workflow-spec" rows={15} value={text} onChange={(event) => { setText(event.target.value); validate.reset(); start.reset(); save.reset(); }}
          disabled={busy} spellCheck={false} className="font-mono text-xs leading-6" placeholder={t("start.placeholder")} />
        {!saving && <label className="block space-y-2 text-sm"><span>{t("start.resumeOf")}</span>
          <Input value={resumeOf} onChange={(event) => setResumeOf(event.target.value)} disabled={busy} />
        </label>}
        {validated?.text === text && <div role="status" className="rounded-lg border border-ok/30 bg-ok/5 p-3">
          <p className="mb-2 text-sm text-ok">{t("start.valid")}</p>
          <div className="flex flex-wrap items-center gap-2 text-xs">{validated.layers.map((layer, index) =>
            <div key={index} className="rounded-md border border-border bg-card px-3 py-2">
              <span className="mr-2 text-muted">{t("start.layer", { count: index + 1 })}</span>{layer.join(" · ")}
            </div>)}</div>
        </div>}
        {(start.error || save.error || validate.error) && <QueryError compact error={start.error || save.error || validate.error} />}
      </div>
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
