import { lazy, Suspense } from "react";
import { Activity, Bot, MessageSquare } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { delegationApi } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useThinkingBootstrap } from "@/hooks/useThinking";
import { useThinkingStore } from "@/stores/thinking-store";
import { TabBar } from "@/components/common/TabBar";
import { SectionBoundary } from "@/components/common/SectionBoundary";
import { ExecutionTimeline } from "@/components/thinking/ExecutionTimeline";

const DelegationsPanel = lazy(() => import("@/components/delegation/DelegationsPanel").then((module) => ({ default: module.DelegationsPanel })));
const WebActivityPanel = lazy(() => import("./WebActivityPanel"));

/** 全频道运行观察、全局子代理管理与 Web 操作记录的独立宿主。 */
export default function ExecutionWorkspace() {
  const { t } = useTranslation("workbench");
  useThinkingBootstrap();
  const tab = useWorkbenchStore((state) => state.executionTab);
  const setTab = useWorkbenchStore((state) => state.setExecutionTab);
  const sessions = useThinkingStore((state) => state.sessions);
  const running = sessions.filter((session) => !session.ended).length;
  const agents = useQuery({ queryKey: ["delegations", "overview"], queryFn: () => delegationApi.overview().then((response) => response.data), refetchInterval: 3000, throwOnError: false });
  const count = agents.data?.running.length ?? 0;
  return <section aria-label={t("execution.region")} className="execution-workspace flex h-full min-h-0 flex-col">
    <header className="execution-heading">
      <div className="min-w-0"><h1>{t("execution.heading")}</h1><p>{t("execution.description")}</p></div>
      <span className="execution-count">{t("execution.running", { count: running })}</span>
    </header>
    <div className="shrink-0 px-3 pb-2"><TabBar activeTab={tab} onChange={setTab} tabs={[
      { key: "runs", label: t("execution.runs"), icon: Activity },
      { key: "agents", label: `${t("execution.agents")}${count ? ` · ${count}` : ""}`, icon: Bot },
      { key: "web", label: t("execution.webRecords"), icon: MessageSquare },
    ]} /></div>
    {count > 0 && tab !== "agents" && <button className="execution-agents-banner" onClick={() => setTab("agents")}>
      <Bot size={16} /><span>{t("execution.agentsRunning", { count })}</span><span className="ml-auto truncate text-muted">{agents.data?.running[0]?.current_tool || agents.data?.running[0]?.agent}</span>
    </button>}
    <div className="min-h-0 flex-1"><SectionBoundary><Suspense fallback={<div role="status" className="p-5 text-sm text-muted">{t("common:loading")}</div>}>
      {tab === "runs" ? <ExecutionTimeline compact /> : tab === "agents" ? <div className="h-full overflow-y-auto p-3 md:p-5"><DelegationsPanel /></div> : <WebActivityPanel />}
    </Suspense></SectionBoundary></div>
  </section>;
}
