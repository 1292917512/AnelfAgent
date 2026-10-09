import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { useThinkingStore } from "@/stores/thinking-store";
import { useThinkingSessions } from "@/hooks/useThinking";
import { SessionOverview } from "@/components/thinking/SessionOverview";
import { TimelineView } from "@/components/thinking/TimelineView";
import { NodeDetail } from "@/components/thinking/NodeDetail";
import { sessionKind } from "@/components/thinking/trace-model";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui/Button";

/** Compact execution inspector backed by the same sessions and node details as the full page. */
export function TracePanel() {
  const { t } = useTranslation("thinking");
  useThinkingSessions();
  const state = useThinkingStore();
  const selected = state.activeSession?.nodes.find((node) => node.id === state.selectedNodeId);
  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 items-center gap-2 border-b border-border p-3">
      <select aria-label={t("sessionList")} value={state.activeSessionId ?? ""} onChange={(event) => void state.selectSession(event.target.value)}
        className="h-8 min-w-0 flex-1 rounded-lg border border-input bg-card px-2 text-xs">
        {!state.activeSessionId && <option value="">{t("noSessions")}</option>}
        {state.sessions.map((session) => <option key={session.id} value={session.id}>
          {t(sessionKind(session))} · {new Date(session.start_time * 1000).toLocaleString()}
        </option>)}
      </select>
      <Link to="/thinking" title={t("openFullTrace")} aria-label={t("openFullTrace")} className="rounded-lg p-2 text-accent hover:bg-hover"><ArrowUpRight size={16} /></Link>
    </div>
    {state.sessionsError != null && <QueryError compact error={state.sessionsError} retry={() => void state.refreshSessions()} />}
    {state.sessionError != null && <QueryError compact error={state.sessionError} retry={() => void state.refreshSession()} />}
    {state.activeSession ? <>
      <SessionOverview session={state.activeSession} compact onSelect={state.setSelectedNodeId} onSelectSession={(id) => void state.selectSession(id)} />
      <div className="min-h-0 flex-1"><TimelineView key={state.activeSession.id} compact session={state.activeSession}
        selectedNodeId={state.selectedNodeId} autoFollow={state.autoFollow} onSelect={state.setSelectedNodeId} /></div>
    </> : <div className="space-y-3 p-4 text-center text-xs text-muted">
      <p>{t(state.sessionLoading || state.sessionsLoading ? "loadingSession" : state.enabled ? "waitingForActivity" : "enableTracking")}</p>
      {!state.enabled && <Button size="sm" loading={state.toggling} onClick={() => void state.setTracking(true)}>{t("startTracking")}</Button>}
    </div>}
    <DialogSurface open={!!selected} onClose={() => state.setSelectedNodeId(null)} title={t("nodeDetails")} placement="right" className="max-w-lg">
      {selected && <NodeDetail key={selected.id} node={selected} onClose={() => state.setSelectedNodeId(null)} />}
    </DialogSurface>
  </div>;
}
