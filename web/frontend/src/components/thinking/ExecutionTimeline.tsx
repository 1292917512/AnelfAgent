import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Activity } from "lucide-react";
import { useThinkingStore } from "@/stores/thinking-store";
import { useThinkingSessions } from "@/hooks/useThinking";
import { useRouteTab } from "@/hooks/useRouteTab";
import { useIsMobile } from "@/lib/use-media-query";
import { NodeDetail } from "@/components/thinking/NodeDetail";
import { ToolsPanel } from "@/components/thinking/ToolsPanel";
import { ProvidersPanel } from "@/components/context/ProvidersPanel";
import { FlowView } from "@/components/thinking/FlowView";
import { TimelineView } from "@/components/thinking/TimelineView";
import { SessionOverview } from "@/components/thinking/SessionOverview";
import { ThinkingSessionsPanel } from "@/components/thinking/ThinkingSessionsPanel";
import { ThinkingToolbar } from "@/components/thinking/ThinkingToolbar";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";
import { Drawer } from "@/components/common/Drawer";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { Button } from "@/components/ui/Button";
import { useTracePlanNodes } from "@/components/thinking/trace-plans";
import { useElementSize } from "@/hooks/useElementSize";

function ThinkingFlow({ compact }: { compact: boolean }) {
  const { t } = useTranslation("thinking");
  useThinkingSessions();
  const state = useThinkingStore();
  const [view, setView] = useRouteTab(["timeline", "flow"] as const, "timeline", "view");
  const [panel, setPanel] = useState<"tools" | "providers" | null>(null);
  const [showSessions, setShowSessions] = useState(false);
  const mobileViewport = useIsMobile();
  const { ref, size } = useElementSize<HTMLDivElement>();
  const isMobile = mobileViewport || (size?.width ?? 0) < 640;
  const sessionOverlay = (size?.width ?? 0) < 960;
  const detailOverlay = (size?.width ?? 0) < 1240;
  const planNodes = useTracePlanNodes(state.activeSession);
  const selected = [...(state.activeSession?.nodes ?? []), ...planNodes].find((node) => node.id === state.selectedNodeId);
  const selectNode = (id: string) => { state.setSelectedNodeId(id); };
  return <div ref={ref} className="flex h-full min-h-0">
    <ThinkingSessionsPanel sessions={state.sessions} activeId={state.activeSessionId}
      onSelect={(id) => void state.selectSession(id)} onRefresh={() => void state.refreshSessions()}
      loading={state.sessionsLoading} error={state.sessionsError}
      isMobile={sessionOverlay} open={showSessions} onClose={() => setShowSessions(false)} />
    <div className="flex min-w-0 flex-1 flex-col">
      <ThinkingToolbar isMobile={isMobile} sessionsHidden={sessionOverlay} onShowSessions={() => setShowSessions(true)}
        enabled={state.enabled} busy={state.toggling || !state.statusSynced} onToggle={() => void state.setTracking(!state.enabled)}
        connected={state.connected} view={view} onViewChange={setView}
        onShowTools={() => setPanel("tools")} onShowProviders={() => setPanel("providers")}
        autoFollow={state.autoFollow} onToggleAutoFollow={() => { state.setSelectedNodeId(null); state.setAutoFollow(!state.autoFollow); }} />
      {state.statusError != null && <div className="p-3"><QueryError compact error={state.statusError} retry={() => void (state.statusSynced ? state.setTracking(!state.enabled) : state.initialize())} /></div>}
      {state.sessionError != null && <div className="p-3"><QueryError compact error={state.sessionError} retry={() => void state.refreshSession()} /></div>}
      {!state.enabled && state.activeSession && <p className="border-b border-border bg-elevated px-4 py-2 text-xs text-muted">{t("historyWhileDisabled")}</p>}
      {state.activeSession ? <>
        <SessionOverview compact={compact && !isMobile} session={state.activeSession} onSelect={selectNode} onSelectSession={(id) => void state.selectSession(id)} />
        <div className="relative min-h-0 flex-1">
          {view === "flow"
            ? <FlowView key={state.activeSession.id} session={{ ...state.activeSession, nodes: [...state.activeSession.nodes, ...planNodes] }} autoFollow={state.autoFollow} onNodeClick={selectNode} />
            : <TimelineView key={state.activeSession.id} session={state.activeSession} selectedNodeId={state.selectedNodeId}
              autoFollow={state.autoFollow} onSelect={selectNode} />}
        </div>
      </> : state.sessionLoading || state.sessionsLoading
        ? <div className="p-6"><PageSkeleton /></div>
        : <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <Activity size={32} className="text-accent" />
          <h2 className="text-base font-semibold text-heading">{t(state.enabled ? "waitingForActivity" : "enableTracking")}</h2>
          <p className="max-w-md text-sm leading-relaxed text-muted">{t("trackingHint")}</p>
          {!state.enabled && <Button variant="primary" loading={state.toggling} onClick={() => void state.setTracking(true)}>{t("startTracking")}</Button>}
        </div>}
    </div>
    {selected && !detailOverlay && <aside className="w-[340px] shrink-0 border-l border-border bg-panel">
      <NodeDetail key={selected.id} node={selected} onClose={() => state.setSelectedNodeId(null)} />
    </aside>}
    {detailOverlay && <DialogSurface open={!!selected} onClose={() => state.setSelectedNodeId(null)} title={t("nodeDetails")} placement="right" className="max-w-lg">
      {selected && <NodeDetail key={selected.id} node={selected} onClose={() => state.setSelectedNodeId(null)} />}
    </DialogSurface>}
    <Drawer open={panel !== null} onClose={() => setPanel(null)} title={t(panel === "tools" ? "availableTools" : "contextProviders.title")}>
      <div className="h-[70dvh] overflow-y-auto p-3">{panel === "tools" ? <ToolsPanel tools={state.activeSession?.available_tools ?? []} /> : panel === "providers" ? <ProvidersPanel /> : null}</div>
    </Drawer>
  </div>;
}

export function ExecutionTimeline({ compact = false }: { compact?: boolean }) {
  return <ReactFlowProvider><ThinkingFlow compact={compact} /></ReactFlowProvider>;
}
