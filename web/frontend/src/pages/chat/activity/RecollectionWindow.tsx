import { Brain, Loader2 } from "lucide-react";
import { useContext, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Modal } from "@/components/ui";
import { ActivityInspection } from "@/components/common/ActivityInspection";
import type { ActivityRun } from "@/lib/types/activity";
import { ActivityEntryView } from "./ActivityEntryView";
import { ActivitySourceLabel } from "./ActivityReferences";

/** 按当前执行页的会话归属查看回忆，不另开订阅或渲染隐藏的详情。 */
export function RecollectionWindow({ runs }: { runs: ActivityRun[] }) {
  const { t } = useTranslation("workbench");
  const [open, setOpen] = useState(false);
  const inspect = useContext(ActivityInspection);
  const groups = useMemo(() => runs.map((run) => ({ run, entries: run.entries.filter((entry) =>
    entry.kind === "context" || (entry.kind === "hook" && (entry.name.startsWith("memory.") || entry.name.startsWith("skills."))),
  ) })).filter((group) => group.entries.length), [runs]);
  const pending = groups.some(({ entries }) => entries.some((entry) => "status" in entry && entry.status === "running"));
  return <>
    <Button variant="ghost" size="sm" aria-label={t("activity.recollectionTitle")} title={t("activity.recollectionTitle")} aria-haspopup="dialog" onClick={() => setOpen(true)}>
      {pending ? <Loader2 size={13} className="animate-spin motion-reduce:animate-none" /> : <Brain size={13} />}
      <span className="activity-control-label">{t("activity.recollectionTitle")}</span>
    </Button>
    {open && <Modal open onClose={() => setOpen(false)} title={t("activity.recollectionTitle")} width="max-w-3xl">
      <p className="mb-4 text-xs leading-relaxed text-muted">{t("activity.recollectionHint")}</p>
      {!groups.length && <p className="py-6 text-center text-sm text-muted">{t("activity.recollectionEmpty")}</p>}
      <div className="space-y-6">{groups.map(({ run, entries }) => <section key={run.id} className="min-w-0 space-y-2">
        <div className="mb-3 space-y-1"><ActivitySourceLabel source={run.source} /><h3 className="break-words text-sm font-medium text-heading">{run.label}</h3></div>
        {entries.map((entry) => <ActivityEntryView key={entry.id} entry={entry} live={run.status === "running"} current={false} onReveal={(id) => inspect?.([id])} />)}
      </section>)}</div>
    </Modal>}
  </>;
}
