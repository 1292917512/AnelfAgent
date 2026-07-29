import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useShallow } from "zustand/react/shallow";

import { useThinkingStore } from "@/stores/thinking-store";
import { warnApiError, thinkingApi } from "@/lib/api";
import { useThinkingBootstrap } from "@/pages/chat/useThinkingBootstrap";
import { NodeDetail } from "@/components/thinking/NodeDetail";
import { ToolsPanel } from "@/components/thinking/ToolsPanel";
import { ContextProvidersPanel } from "@/components/thinking/ContextProvidersPanel";
import { FlowView } from "@/components/thinking/FlowView";
import { TimelineView } from "@/components/thinking/TimelineView";
import { TabBar } from "@/components/common/TabBar";
import Context from "@/pages/Context";
import { useIsMobile } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";
import { ThinkingSessionsPanel } from "@/components/thinking/ThinkingSessionsPanel";
import { ThinkingToolbar } from "@/components/thinking/ThinkingToolbar";

type ViewMode = "flow" | "timeline";

function ThinkingFlow() {
  const { t } = useTranslation("thinking");
  const {
    enabled,
    connected,
    sessions,
    activeSessionId,
    activeSession,
    selectedNodeId,
    autoFollow,
    setEnabled,
    setSessions,
    setActiveSessionId,
    setActiveSession,
    setSelectedNodeId,
    setAutoFollow,
    startSSE,
    stopSSE,
  } = useThinkingStore(useShallow((s) => ({
    enabled: s.enabled,
    connected: s.connected,
    sessions: s.sessions,
    activeSessionId: s.activeSessionId,
    activeSession: s.activeSession,
    selectedNodeId: s.selectedNodeId,
    autoFollow: s.autoFollow,
    setEnabled: s.setEnabled,
    setSessions: s.setSessions,
    setActiveSessionId: s.setActiveSessionId,
    setActiveSession: s.setActiveSession,
    setSelectedNodeId: s.setSelectedNodeId,
    setAutoFollow: s.setAutoFollow,
    startSSE: s.startSSE,
    stopSSE: s.stopSSE,
  })));

  const [view, setView] = useState<ViewMode>("flow");
  const [showTools, setShowTools] = useState(false);
  const [showProviders, setShowProviders] = useState(false);
  const [showSessions, setShowSessions] = useState(false);
  const isMobile = useIsMobile();

  // 服务端 enabled 状态只同步一次（首次加载），切换页面不重置用户的开关选择
  useThinkingBootstrap();

  // 每次进入页面都刷新会话列表（获取离开期间产生的新会话）
  useEffect(() => {
    thinkingApi.sessions(50).then((r) => {
      setSessions(r.data.sessions ?? []);
    }).catch(warnApiError);
  }, [setSessions]);

  const handleToggle = useCallback(() => {
    const next = !enabled;
    thinkingApi.toggle(next).then(() => {
      setEnabled(next);
      if (next) {
        startSSE();
      } else {
        stopSSE();
      }
    }).catch(warnApiError);
  }, [enabled, setEnabled, startSSE, stopSSE]);

  const handleSelectSession = useCallback((id: string) => {
    setActiveSessionId(id);
    setSelectedNodeId(null);
    const local = sessions.find((s) => s.id === id);
    if (local && activeSession?.id !== id) {
      thinkingApi.session(id).then((r) => {
        if (r.data && !r.data.error) {
          setActiveSession(r.data);
        }
      }).catch(warnApiError);
    }
  }, [sessions, activeSession, setActiveSessionId, setActiveSession, setSelectedNodeId]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId || !activeSession) return null;
    return activeSession.nodes.find((n) => n.id === selectedNodeId) ?? null;
  }, [selectedNodeId, activeSession]);

  const availableTools = activeSession?.available_tools ?? [];

  return (
    <div className="flex h-full gap-0">
      <ThinkingSessionsPanel
        sessions={sessions}
        activeId={activeSessionId}
        onSelect={handleSelectSession}
        onRefresh={() => {
          thinkingApi.sessions(50).then((r) => setSessions(r.data.sessions ?? [])).catch(warnApiError);
        }}
        isMobile={isMobile}
        open={showSessions}
        onClose={() => setShowSessions(false)}
      />

      {/* 中间：工具栏 + 视图 */}
      <div className="flex-1 flex flex-col min-w-0">
        <ThinkingToolbar
          isMobile={isMobile}
          onShowSessions={() => setShowSessions(true)}
          enabled={enabled}
          onToggle={handleToggle}
          connected={connected}
          view={view}
          onViewChange={setView}
          showTools={showTools}
          onToggleTools={() => setShowTools(!showTools)}
          showProviders={showProviders}
          onToggleProviders={() => setShowProviders(!showProviders)}
          autoFollow={autoFollow}
          onToggleAutoFollow={() => setAutoFollow(!autoFollow)}
          nodeCount={activeSession?.nodes.length}
        />

        {/* 主区域：工具面板 + 视图 */}
        <div className="flex-1 flex min-h-0">
          {/* 工具面板（桌面常驻，移动端抽屉） */}
          {showTools && !isMobile && (
            <div className="w-56 shrink-0 border-r border-border bg-panel">
              <ToolsPanel tools={availableTools} />
            </div>
          )}
          {showTools && isMobile && (
            <>
              <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setShowTools(false)} />
              <div className="fixed inset-y-0 right-0 z-50 w-64 border-l border-border bg-panel flex flex-col">
                <div className="flex items-center justify-between px-4 py-2 border-b border-border">
                  <span className="text-xs font-semibold text-heading">{t("toolsPanel")}</span>
                  <button
                    onClick={() => setShowTools(false)}
                    className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="flex-1 min-h-0">
                  <ToolsPanel tools={availableTools} />
                </div>
              </div>
            </>
          )}

          {/* 上下文提供者面板（右侧） */}
          {showProviders && !isMobile && (
            <div className="w-56 shrink-0 border-l border-border bg-panel">
              <ContextProvidersPanel />
            </div>
          )}
          {showProviders && isMobile && (
            <>
              <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setShowProviders(false)} />
              <div className="fixed inset-y-0 right-0 z-50 w-64 border-l border-border bg-panel flex flex-col">
                <div className="flex items-center justify-between px-4 py-2 border-b border-border">
                  <span className="text-xs font-semibold text-heading">{t("contextProviders.title")}</span>
                  <button
                    onClick={() => setShowProviders(false)}
                    className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="flex-1 min-h-0">
                  <ContextProvidersPanel />
                </div>
              </div>
            </>
          )}

          <div className="flex-1 relative min-w-0">
            {!activeSession ? (
              <div className="flex items-center justify-center h-full text-sm text-muted px-4 text-center">
                {enabled
                  ? t("waitingForActivity")
                  : t("enableTracking")}
              </div>
            ) : view === "flow" ? (
              <FlowView
                key={activeSession.id}
                session={activeSession}
                autoFollow={autoFollow}
                onNodeClick={setSelectedNodeId}
              />
            ) : (
              <TimelineView
                session={activeSession}
                selectedNodeId={selectedNodeId}
                autoFollow={autoFollow}
                onSelect={setSelectedNodeId}
              />
            )}
          </div>
        </div>
      </div>

      {/* 右侧：节点详情（仅流程图视图；时间线视图内联展开） */}
      {view === "flow" && selectedNode && (
        <div className={cn(
          "border-l border-border bg-panel",
          isMobile ? "fixed inset-y-0 right-0 z-50 w-full max-w-sm shadow-lg" : "w-72 shrink-0",
        )}>
          <NodeDetail
            node={selectedNode}
            onClose={() => setSelectedNodeId(null)}
          />
        </div>
      )}
    </div>
  );
}

export default function Thinking() {
  const { t } = useTranslation("thinking");
  const [pageTab, setPageTab] = useState<"chain" | "context">("chain");

  const pageTabs = [
    { key: "chain" as const, label: t("title") },
    { key: "context" as const, label: t("contextTab") },
  ];

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 md:px-6 pt-4 border-b border-border">
        <TabBar tabs={pageTabs} activeTab={pageTab} onChange={setPageTab} />
      </div>
      <div className="flex-1 min-h-0">
        {pageTab === "chain" ? (
          <ReactFlowProvider>
            <ThinkingFlow />
          </ReactFlowProvider>
        ) : (
          <Context hideHeader />
        )}
      </div>
    </div>
  );
}
