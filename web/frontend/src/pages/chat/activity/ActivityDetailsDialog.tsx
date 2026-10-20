import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { Modal, LoadingBlock, Button } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { ActivityRunCard } from "./ActivityRunCard";
import { ActivityExecutionControl } from "./ActivityExecutionControl";
import { useShallow } from "zustand/react/shallow";
import { hasActiveWork } from "@/lib/chat-activity";

export default function ActivityDetailsDialog({ ids, onClose, onReveal }: { ids: string[]; onClose: () => void; onReveal: (id: string) => void }) {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const selected = useActivityStore(useShallow((state) => state.runs.filter((run) => ids.includes(run.id))));
  const loading = useActivityStore((state) => state.loading);
  const error = useActivityStore((state) => state.error);
  const load = useActivityStore((state) => state.load);
  const connected = useChatStore((state) => state.streamStatus === "connected");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [checkedIds, setCheckedIds] = useState<string[] | null>(null);
  const active = selected.some(hasActiveWork);
  const pending = checkedIds !== ids;
  useEffect(() => {
    let cancelled = false;
    void load().then(() => { if (!cancelled) setCheckedIds(ids); });
    return () => { cancelled = true; };
  }, [load, ids]);
  useEffect(() => {
    if (connected || !active) return;
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 3000);
    return () => window.clearInterval(timer);
  }, [active, connected, load]);
  return <Modal open onClose={onClose} title={t("hookFlow.executionDetails")} width="max-w-4xl" placement="right">
    <div className="mb-3 flex justify-end"><Button variant="ghost" size="sm" disabled={loading} onClick={() => void load()}><RefreshCw size={14} />{tc("refresh")}</Button></div>
    {error != null && <QueryError compact error={error} retry={() => void load()} />}
    {selected.length === 0 && (pending || loading) && <LoadingBlock />}
    {!pending && !loading && error == null && selected.length < ids.length && <p className="mb-3 text-sm leading-6 text-muted" role="status">{t("hookFlow.expiredDetails")}</p>}
    <div className="space-y-4">
      {selected.map((run) => <section key={run.id}>
        <div className="flex justify-end"><ActivityExecutionControl run={run} /></div>
        <ActivityRunCard run={run} open={expanded[run.id] ?? true}
          onToggle={(id, open) => setExpanded((current) => ({ ...current, [id]: open }))} onReveal={onReveal} />
      </section>)}
    </div>
  </Modal>;
}
