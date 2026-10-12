import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal, LoadingBlock } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useActivityStore } from "@/stores/activity-store";
import { ActivityRunCard } from "./ActivityRunCard";

export default function ActivityDetailsDialog({ ids, onClose, onReveal }: { ids: string[]; onClose: () => void; onReveal: (id: string) => void }) {
  const { t } = useTranslation("settings");
  const { runs, loading, error, load } = useActivityStore();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const selected = runs.filter((run) => ids.includes(run.id));
  const active = selected.some((run) => run.status === "running") || selected.length < ids.length;
  useEffect(() => { void load(); }, [load, ids]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 3000);
    return () => window.clearInterval(timer);
  }, [active, load]);
  return <Modal open onClose={onClose} title={t("hookFlow.executionDetails")} width="max-w-4xl" placement="right">
    {error != null && <QueryError compact error={error} retry={() => void load()} />}
    {selected.length === 0 && (loading ? <LoadingBlock /> : <p className="text-sm leading-6 text-muted">{t("hookFlow.expiredDetails")}</p>)}
    <div className="space-y-4">
      {selected.map((run) => <ActivityRunCard key={run.id} run={run} open={expanded[run.id] ?? true}
        onToggle={(id, open) => setExpanded((current) => ({ ...current, [id]: open }))} onReveal={onReveal} />)}
    </div>
  </Modal>;
}
