import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useShallow } from "zustand/react/shallow";

import { useThinkingStore } from "@/stores/thinking-store";
import { thinkingApi } from "@/lib/api";
import { useThinkingBootstrap } from "@/pages/chat/useThinkingBootstrap";
import { SessionList } from "@/components/thinking/SessionList";
import { NodeDetail } from "@/components/thinking/NodeDetail";
import { ToolsPanel } from "@/components/thinking/ToolsPanel";
import { FlowView } from "@/components/thinking/FlowView";
import { TimelineView } from "@/components/thinking/TimelineView";
import { useIsMobile } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";
import {
  Power,
  PowerOff,
  Crosshair,
  RefreshCw,
  PanelLeftOpen,
  PanelLeftClose,
  List,
  Workflow,
  ListTree,
  Wrench,
  X,
} from "lucide-react";

type ViewMode = "flow" | "timeline";

function ThinkingFlow() {
  const { t } = useTranslation("thinking");
  const { t: tc } = useTranslation("common");
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
  const [showSessions, setShowSessions] = useState(false);
  const isMobile = useIsMobile();

  // 服务端 enabled 状态只同步一次（首次加载），切换页面不重置用户的开关选择
  useThinkingBootstrap();

  // 每次进入页面都刷新会话列表（获取离开期间产生的新会话）
  useEffect(() => {
    thinkingApi.sessions(50).then((r) => {
      setSessions(r.data.sessions ?? []);
    }).catch((e) => console.warn("[API]", e));
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
    }).catch((e) => console.warn("[API]", e));
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
      }).catch((e) => console.warn("[API]", e));
    }
  }, [sessions, activeSession, setActiveSessionId, setActiveSession, setSelectedNodeId]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId || !activeSession) return null;
    return activeSession.nodes.find((n) => n.id === selectedNodeId) ?? null;
  }, [selectedNodeId, activeSession]);

  const availableTools = activeSession?.available_tools ?? [];

  return (
    <div className="flex h-full gap-0">
      {/* 左侧：会话列表（桌面常驻，移动端抽屉） */}
      {(!isMobile || showSessions) && (
        <>
          {isMobile && (
            <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setShowSessions(false)} />
          )}
          <div className={cn(
            "border-r border-border bg-panel flex flex-col",
            isMobile ? "fixed inset-y-0 left-0 z-50 w-64" : "w-56 shrink-0",
          )}>
            <div className="px-4 py-3 border-b border-border">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-heading uppercase tracking-wider">
                  {t("sessionList")}
                </span>
                <button
                  onClick={() => {
                    thinkingApi.sessions(50).then((r) => setSessions(r.data.sessions ?? [])).catch((e) => console.warn("[API]", e));
                  }}
                  className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover"
                  title={t("refresh")}
                >
                  <RefreshCw size={12} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <SessionList
                sessions={sessions}
                activeId={activeSessionId}
                onSelect={(id) => {
                  handleSelectSession(id);
                  if (isMobile) setShowSessions(false);
                }}
              />
            </div>
          </div>
        </>
      )}

      {/* 中间：工具栏 + 视图 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 工具栏 */}
        <div className="flex items-center gap-2 px-3 md:px-4 py-2 border-b border-border bg-panel">
          {isMobile && (
            <button
              onClick={() => setShowSessions(true)}
              className="flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium text-muted hover:text-foreground transition-all"
              title={t("sessionList")}
            >
              <List size={14} />
            </button>
          )}
          <button
            onClick={handleToggle}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all",
              enabled
                ? "bg-ok-subtle text-ok border border-ok"
                : "bg-hover text-muted border border-border hover:border-border-strong",
            )}
          >
            {enabled ? <Power size={12} /> : <PowerOff size={12} />}
            <span className="hidden sm:inline">{enabled ? t("tracking") : t("disabled")}</span>
          </button>

          <div className="flex items-center gap-1 text-[10px] text-muted">
            <div
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                connected ? "bg-ok" : "bg-danger",
              )}
            />
            <span className="hidden md:inline">{connected ? tc("connected") : tc("disconnected")}</span>
          </div>

          {/* 视图切换 */}
          <div className="flex items-center rounded-md border border-border overflow-hidden">
            {(["flow", "timeline"] as ViewMode[]).map((v) => (
              <button
                key={v}
                onClick={() => { setView(v); setSelectedNodeId(null); }}
                className={cn(
                  "flex items-center gap-1 px-2.5 py-1.5 text-[10px] font-medium transition-all",
                  view === v
                    ? "bg-accent-subtle text-accent"
                    : "text-muted hover:text-foreground",
                )}
                title={t(`views.${v}`)}
              >
                {v === "flow" ? <Workflow size={11} /> : <ListTree size={11} />}
                <span className="hidden sm:inline">{t(`views.${v}`)}</span>
              </button>
            ))}
          </div>

          <div className="flex-1" />

          <button
            onClick={() => setShowTools(!showTools)}
            className={cn(
              "flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
              showTools
                ? "bg-accent-subtle text-accent"
                : "text-muted hover:text-foreground",
            )}
            title={t("toolsPanel")}
          >
            {isMobile
              ? <Wrench size={11} />
              : showTools ? <PanelLeftClose size={11} /> : <PanelLeftOpen size={11} />}
            <span className="hidden md:inline">{t("toolsPanel")}</span>
          </button>

          <button
            onClick={() => setAutoFollow(!autoFollow)}
            className={cn(
              "flex items-center gap-1 px-2 py-1 rounded-sm text-[10px] font-medium transition-all",
              autoFollow
                ? "bg-accent-subtle text-accent"
                : "text-muted hover:text-foreground",
            )}
            title={t("autoFollow")}
          >
            <Crosshair size={11} />
            <span className="hidden sm:inline">{t("autoFollow")}</span>
          </button>

          {activeSession && (
            <div className="text-[10px] text-muted font-mono">
              {t("nNodes", { count: activeSession.nodes.length })}
            </div>
          )}
        </div>

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
  return (
    <div className="h-full flex flex-col">
      <div className="px-3 md:px-6 py-4 border-b border-border">
        <h1 className="text-lg font-semibold text-heading">{t("title")}</h1>
        <p className="text-xs text-muted mt-0.5">
          {t("subtitle")}
        </p>
      </div>
      <div className="flex-1 min-h-0">
        <ReactFlowProvider>
          <ThinkingFlow />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
