import { useTranslation } from "react-i18next";
import { Camera, CameraOff, Download, Repeat, Trash2 } from "lucide-react";
import { useContextSnapshot } from "@/hooks/useContextSnapshot";
import { SnapshotDetail } from "@/components/context/SnapshotDetail";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";
import { Button, Switch } from "@/components/ui";
import { downloadJson } from "./downloadJson";

export function MonitorTab() {
  const { t } = useTranslation("context");
  const query = useContextSnapshot();
  const { action } = query;
  const snapshot = query.data?.snapshot;
  const armed = query.data?.status.armed ?? false;
  const continuous = query.data?.status.continuous ?? false;
  if (query.isPending) return <PageSkeleton />;
  return <div className="space-y-5">
    {(query.error || action.error) && <QueryError compact error={action.error ?? query.error} retry={() => { action.reset(); void query.refetch(); }} />}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-3">
      <div className="flex items-center gap-2 text-xs text-muted">
        <Repeat size={15} /><Switch label={t("monitor.continuous")} checked={continuous} onChange={(value) => action.mutate(value)} disabled={action.isPending} />
        <span>{t("monitor.continuous")}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" loading={action.isPending} onClick={() => action.mutate(armed ? "disarm" : "arm")}>
          {armed ? <CameraOff size={14} /> : <Camera size={14} />}{t(armed ? "monitor.cancel" : "monitor.arm")}
        </Button>
        {snapshot && <>
          <Button size="icon" variant="ghost" title={t("monitor.export")} onClick={() => downloadJson(snapshot, `context-${snapshot.captured_at}.json`)}><Download size={15} /></Button>
          <Button size="icon" variant="ghost" title={t("monitor.discard")} disabled={action.isPending} onClick={() => action.mutate("clear")}><Trash2 size={15} /></Button>
        </>}
      </div>
    </div>
    {armed && <p role="status" className="rounded-lg bg-accent-subtle p-3 text-sm text-accent">{t("monitor.waiting")} · {t("monitor.waitingDesc")}</p>}
    {snapshot ? <SnapshotDetail snapshot={snapshot} /> : <div className="mx-auto flex max-w-lg flex-col items-center gap-4 py-12 text-center">
      <Camera size={32} className="text-accent" />
      <h2 className="text-base font-semibold text-heading">{t("monitor.ready")}</h2>
      <p className="text-sm leading-relaxed text-muted">{t("monitor.readyDesc")}</p>
      <p className="text-xs leading-relaxed text-muted">{t("monitor.continuousDesc")}</p>
    </div>}
  </div>;
}
