import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CheckCheck, Play } from "lucide-react";
import { workflowApi, type WorkflowRun, type WorkflowRunDetail } from "@/lib/api";
import { Modal, ConfirmDialog } from "@/components/ui/Modal";
import { Button, Input, Textarea } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";

export function WorkflowComposer({ source, onClose, onStarted }: {
  source: WorkflowRunDetail | null; onClose: () => void; onStarted: (run: WorkflowRun) => void;
}) {
  const { t } = useTranslation("workflow");
  const initial = source ? JSON.stringify(source.spec, null, 2) : "";
  const [text, setText] = useState(initial);
  const [resumeOf, setResumeOf] = useState(source?.run.run_id ?? "");
  const [validated, setValidated] = useState<{ text: string; layers: string[][] } | null>(null);
  const start = useMutation({
    mutationFn: () => workflowApi.startRun(JSON.parse(text), resumeOf.trim()).then((r) => r.data),
    onSuccess: onStarted,
  });
  const validate = useMutation({
    mutationFn: async (submitted: string) => ({ text: submitted, ...(await workflowApi.validate(JSON.parse(submitted))).data }),
    onSuccess: (result) => setValidated({ text: result.text, layers: result.layers }),
  });
  const busy = start.isPending || validate.isPending;
  const guard = useDiscardChanges(text !== initial || resumeOf !== (source?.run.run_id ?? ""), onClose, busy);
  const example = () => setText(JSON.stringify({
    name: t("start.exampleName"), steps: [
      { key: "research", kind: "ask", goal: t("start.exampleResearch") },
      { key: "summary", kind: "ask", depends_on: ["research"], goal: t("start.exampleSummary") },
    ],
  }, null, 2));
  return <>
    <Modal open onClose={guard.requestClose} title={source ? t("action.revise") : t("start.title")} width="max-w-3xl" dismissible={!busy}
      footer={<>
        <Button onClick={guard.requestClose} disabled={busy}>{t("common:cancel")}</Button>
        <Button onClick={() => validate.mutate(text)} disabled={!text.trim() || busy} loading={validate.isPending}><CheckCheck size={15} />{t("start.validate")}</Button>
        <Button variant="primary" onClick={() => start.mutate()} disabled={!text.trim() || busy} loading={start.isPending}><Play size={15} />{t("start.submit")}</Button>
      </>}>
      <div className="space-y-4">
        <p className="text-sm text-muted">{t("start.help")}</p>
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="workflow-spec" className="text-sm font-medium">{t("start.spec")}</label>
          {!text.trim() && <Button size="sm" onClick={example}>{t("start.example")}</Button>}
        </div>
        <Textarea id="workflow-spec" rows={15} value={text} onChange={(event) => { setText(event.target.value); validate.reset(); start.reset(); }}
          disabled={busy} spellCheck={false} className="font-mono text-xs leading-6" placeholder={t("start.placeholder")} />
        <label className="block space-y-2 text-sm"><span>{t("start.resumeOf")}</span>
          <Input value={resumeOf} onChange={(event) => setResumeOf(event.target.value)} disabled={busy} />
        </label>
        {validated?.text === text && <div role="status" className="rounded-lg border border-ok/30 bg-ok/5 p-3">
          <p className="mb-2 text-sm text-ok">{t("start.valid")}</p>
          <div className="flex flex-wrap items-center gap-2 text-xs">{validated.layers.map((layer, index) =>
            <div key={index} className="rounded-md border border-border bg-card px-3 py-2">
              <span className="mr-2 text-muted">{t("start.layer", { count: index + 1 })}</span>{layer.join(" · ")}
            </div>)}</div>
        </div>}
        {(start.error || validate.error) && <QueryError compact error={start.error || validate.error} />}
      </div>
    </Modal>
    <ConfirmDialog {...guard.confirmProps} />
  </>;
}
